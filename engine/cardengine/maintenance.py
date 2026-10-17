"""Daten aufbauen und aktuell halten – einmalig bei der Installation und danach im Hintergrund der Engine.

    python -m cardengine.maintenance --initial   # Setup: Pokémon + Magic-Katalog + Preise (blockierend)
    python -m cardengine.maintenance             # einmal alles prüfen/ergänzen (auch Magic-Bilder)
    python -m cardengine.maintenance --ygo       # Yu-Gi-Oh-Katalog + Bilder aufbauen (optional, ~15k Bilder)

Im Engine-Prozess läuft `Maintenance.start()` als Hintergrund-Thread:
- Pokémon-Katalog und -Bilder wöchentlich auf neue Sets prüfen
- Magic-Bilder schrittweise nachladen (gedrosselt), danach den Embedding-Index ergänzen und neu laden
- MTGJSON-Preisverlauf wöchentlich ergänzen (verlängert die Historie über 90 Tage hinaus)
- Yu-Gi-Oh (nur wenn einmal mit `--ygo` aufgebaut): Katalog, Cardmarket-Zuordnung und Bilder wöchentlich ergänzen
Fortschritt steht in `status` und wird dem OBS-Plugin angezeigt.
"""
from __future__ import annotations

import argparse
import os
import threading
import time

from .card_database.build import build_mtg, build_pokemon, download_images
from .card_database.catalog import Catalog
from .paths import DATA_DIR

WEEK = 7 * 86400
MTG_CHUNK = 1500  # Magic-Bilder je Durchgang, danach Index-Update


def _age(path: str) -> float:
    return time.time() - os.path.getmtime(path) if os.path.exists(path) else float("inf")


def _stamp(name: str) -> str:
    return os.path.join(DATA_DIR, f".{name}.stamp")


def _touch(name: str) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(_stamp(name), "w") as f:
        f.write(time.strftime("%Y-%m-%dT%H:%M:%S"))


class Maintenance:
    def __init__(self, embedder_factory=None, on_index_updated=None, gpu_lock: threading.Lock | None = None):
        """`embedder_factory()` liefert den (geteilten) Embedder, `on_index_updated(index)` lädt den neuen Index."""
        self.embedder_factory = embedder_factory
        self.on_index_updated = on_index_updated
        self.gpu_lock = gpu_lock or threading.Lock()
        self.status = ""
        self._thread: threading.Thread | None = None

    # ---------- Bausteine ----------
    def pokemon(self, force: bool = False) -> None:
        cat = Catalog()
        if force or cat.count("pokemon") == 0 or _age(_stamp("pokemon")) > WEEK:
            self.status = "Pokémon-Katalog wird aktualisiert"
            recs = build_pokemon(cat, None)
            self.status = "Pokémon-Bilder werden geladen"
            download_images(recs, 8, progress=self._progress("Pokémon-Bilder"))
            _touch("pokemon")

    def mtg_catalog(self, force: bool = False) -> None:
        cat = Catalog()
        if force or cat.count("mtg") == 0 or _age(_stamp("mtg")) > WEEK:
            self.status = "Magic-Katalog wird aktualisiert"
            build_mtg(cat, sets=[], all_images=False)
            _touch("mtg")

    def ygo(self, force: bool = False) -> int:
        """Yu-Gi-Oh-Katalog + fehlende Artworks; ohne `force` nur, wenn der Katalog schon existiert. Liefert die
        Zahl neu geladener Bilder (> 0 → Index ergänzen)."""
        from .card_database.ygo import build_ygo, download_images as ygo_images

        cat = Catalog()
        if not force and (cat.count("ygo") == 0 or _age(_stamp("ygo")) <= WEEK):
            return 0
        self.status = "Yu-Gi-Oh-Katalog wird aktualisiert"
        recs = build_ygo(cat)
        self.status = "Yu-Gi-Oh-Bilder werden geladen"
        n = ygo_images(recs, progress=self._progress("Yu-Gi-Oh-Bilder"))
        _touch("ygo")
        return n

    def mtg_images_pending(self) -> list:
        return [r for r in Catalog().all("mtg") if r.image_url and not os.path.exists(r.image_path)]

    def mtg_images_chunk(self) -> int:
        todo = self.mtg_images_pending()
        if not todo:
            return 0
        total_missing = len(todo)
        chunk = todo[:MTG_CHUNK]
        download_images(chunk, 6, progress=self._progress("Magic-Bilder", offset_left=total_missing))
        return len(chunk)

    def index(self) -> None:
        from .recognition.index import build_index

        if self.embedder_factory is None:
            from .recognition.embedder import Embedder

            emb = Embedder()
            self.embedder_factory = lambda: emb
        self.status = "Erkennungs-Index wird ergänzt"
        index = build_index(_LockedEmbedder(self.embedder_factory(), self.gpu_lock), batch=32)
        if self.on_index_updated:
            self.on_index_updated(index)

    def prices(self) -> None:
        from .pricing.cardmarket import CardmarketPriceGuide

        self.status = "Cardmarket-Preise werden geladen"
        CardmarketPriceGuide().refresh()

    def price_history(self, force: bool = False) -> None:
        if force or _age(_stamp("mtgjson")) > WEEK:
            from .pricing.mtgjson import import_history

            self.status = "Magic-Preisverlauf (MTGJSON) wird ergänzt"
            import_history()
            _touch("mtgjson")

    def _progress(self, label: str, offset_left: int | None = None):
        def cb(done: int, total: int) -> None:
            if offset_left:
                self.status = f"{label}: noch {offset_left - done} fehlen"
            else:
                self.status = f"{label}: {done}/{total}"

        return cb

    # ---------- Abläufe ----------
    def initial(self) -> None:
        """Setup: alles, was für die ersten Erkennungen nötig ist (Pokémon komplett, Magic ohne Bilder)."""
        self.pokemon()
        self.mtg_catalog()
        self.prices()
        self.index()
        self.status = "bereit (Magic-Bilder werden im Hintergrund ergänzt)"

    def run_once(self) -> None:
        self.pokemon()
        self.mtg_catalog()
        self.ygo()
        while self.mtg_images_chunk():
            self.index()
        self.index()
        self.price_history()
        self.status = ""

    def start(self, delay_s: float = 30.0) -> "Maintenance":
        """Hintergrund-Thread: zuerst kurz warten (Engine-Start nicht ausbremsen), dann regelmäßig ergänzen."""

        def loop():
            time.sleep(delay_s)
            while True:
                try:
                    self.pokemon()
                    self.mtg_catalog()
                    if self.ygo():
                        self.index()
                    if self.mtg_images_chunk():
                        self.index()
                        continue  # nächster Block sofort
                    if _age(_stamp("index")) > WEEK:
                        self.index()
                        _touch("index")
                    self.price_history()
                    self.status = ""
                except Exception as err:  # Wartung darf die Erkennung nie stören
                    self.status = f"Datenaktualisierung fehlgeschlagen: {err}"
                    print(f"[wartung] {err}", flush=True)
                time.sleep(3600)

        self._thread = threading.Thread(target=loop, daemon=True, name="maintenance")
        self._thread.start()
        return self


class _LockedEmbedder:
    """Embedding-Berechnung für den Index in kleinen Blöcken unter der GPU-Sperre (Erkennung hat Vorrang)."""

    def __init__(self, embedder, lock):
        self._e = embedder
        self._lock = lock
        self.index_name = embedder.index_name

    def embed(self, imgs, batch: int = 32):
        with self._lock:
            return self._e.embed(imgs, batch=batch)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--initial", action="store_true", help="Setup: Grunddaten ohne Magic-Bilder")
    ap.add_argument("--ygo", action="store_true", help="Yu-Gi-Oh-Katalog, -Bilder und -Index aufbauen")
    args = ap.parse_args()
    m = Maintenance()
    stop = threading.Event()

    def report():
        last = ""
        while not stop.wait(5):
            if m.status != last:
                print(f"[daten] {m.status}", flush=True)
                last = m.status

    threading.Thread(target=report, daemon=True).start()
    if args.ygo:
        m.ygo(force=True)
        m.index()
    else:
        m.initial() if args.initial else m.run_once()
    stop.set()
    print("[daten] fertig", flush=True)


if __name__ == "__main__":
    main()
