"""Daten aufbauen und aktuell halten – einmalig bei der Installation und danach im Hintergrund der Engine.

    python -m cardengine.maintenance --initial [--pack-dir D:\\packs]   # Setup (blockierend)
    python -m cardengine.maintenance                                   # einmal alles prüfen/ergänzen

Welche Spiele gepflegt werden, steht in `data/config.json` (`games`, Auswahl im Installer).
Fehlt einem gewählten Spiel der Erkennungs-Index, wird zuerst ein Datenpaket installiert (cardengine/data_packs.py,
lokaler Ordner oder Paket-URL). Klappt das nicht (kein Netz, Platzhalter-URL), wird wie bisher lokal aufgebaut:
Katalog laden, Referenzbilder laden, einbetten.

Im Engine-Prozess läuft `Maintenance.start()` als Hintergrund-Thread:
- fehlende Spiele per Datenpaket ergänzen (z. B. nach Änderung der Auswahl)
- Kataloge wöchentlich auf neue Sets prüfen; Bilder nur für Karten ohne Index-Eintrag laden (gedrosselt, in
  Blöcken), danach den Embedding-Index ergänzen und neu laden
- MTGJSON-Preisverlauf wöchentlich ergänzen (verlängert die Historie über 90 Tage hinaus)
Fortschritt steht in `status` und wird dem OBS-Plugin angezeigt.
"""
from __future__ import annotations

import argparse
import os
import threading
import time

from .card_database.build import build_mtg, build_pokemon, download_images
from .card_database.catalog import Catalog
from .config import GAMES, load_config
from .paths import DATA_DIR

DAY = 24 * 3600
WEEK = 7 * 86400
IMAGE_CHUNK = 1500  # Bilder je Durchgang, danach Index-Update

# Katalog-Aufbau je Spiel (neue Spiele hier ergänzen): Funktion(catalog) -> Liste der Karten
def _build_ygo(cat):
    from .card_database.ygo import build_ygo

    return build_ygo(cat)


CATALOG_BUILDERS = {
    "pokemon": lambda cat: build_pokemon(cat, None),
    "mtg": lambda cat: build_mtg(cat, sets=[], all_images=False),
    "ygo": _build_ygo,  # YGOPRODeck + Cardmarket-Zuordnung (docs/YUGIOH.md)
}
# Spiele mit sehr großem Katalog: Bilder nicht im Setup, sondern schrittweise im Hintergrund
BACKGROUND_IMAGE_GAMES = {"mtg", "ygo"}


def _age(path: str) -> float:
    return time.time() - os.path.getmtime(path) if os.path.exists(path) else float("inf")


def _stamp(name: str) -> str:
    return os.path.join(DATA_DIR, f".{name}.stamp")


def _touch(name: str, when: float | None = None) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(_stamp(name), "w") as f:
        f.write(time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(when)))
    if when is not None:
        os.utime(_stamp(name), (when, when))


def _label(game: str) -> str:
    return GAMES.get(game, game)


class Maintenance:
    def __init__(self, embedder_factory=None, on_index_updated=None, gpu_lock: threading.Lock | None = None,
                 games: list[str] | None = None, pack_dir: str | None = None, pack_url: str | None = None):
        """`embedder_factory()` liefert den (geteilten) Embedder, `on_index_updated(index)` lädt den neuen Index.
        `games` Standard: config.json; `pack_dir`/`pack_url` überschreiben die Paketquelle."""
        cfg = load_config()
        self.embedder_factory = embedder_factory
        self.on_index_updated = on_index_updated
        self.gpu_lock = gpu_lock or threading.Lock()
        self.games = list(games or cfg["games"])
        self.pack_dir = pack_dir
        self.pack_url = pack_url
        self.status = ""
        self._thread: threading.Thread | None = None

    def index_name(self) -> str:
        if self.embedder_factory is not None:
            return self.embedder_factory().index_name
        from .recognition.embedder import Embedder

        return Embedder.default_index_name()

    # ---------- Bausteine ----------
    def missing_games(self) -> list[str]:
        """Gewählte Spiele ohne Index-Datei (alte gemeinsame Datei wird dafür zuerst aufgeteilt)."""
        from .recognition.index import has_index, migrate_legacy

        name = self.index_name()
        migrate_legacy(name)
        return [g for g in self.games if not has_index(name, g)]

    def packs(self) -> list[str]:
        """Für Spiele ohne Index zuerst ein Datenpaket installieren. Liefert die installierten Spiele."""
        missing = self.missing_games()
        if not missing:
            return []
        from .data_packs import install_packs

        def log(msg):
            self.status = msg
            print(f"[pakete] {msg}", flush=True)

        name = self.index_name()
        try:
            done = install_packs(missing, name, pack_dir=self.pack_dir, url=self.pack_url, log=log)
        except Exception as err:  # Paket-Probleme dürfen den lokalen Aufbau nie verhindern
            log(f"Datenpakete fehlgeschlagen: {err}")
            done = {}
        for game, manifest in done.items():
            # Katalog-Stempel auf den Paketstand: ist das Paket älter als eine Woche, wird sofort nachgeprüft
            try:
                created = time.mktime(time.strptime(manifest["created"], "%Y-%m-%dT%H:%M:%S"))
            except (KeyError, ValueError):
                created = None
            _touch(game, created)
        if done:
            self._reload()
        return list(done)

    def catalog(self, game: str, force: bool = False) -> None:
        builder = CATALOG_BUILDERS.get(game)
        if builder is None:
            print(f"[wartung] kein Katalog-Aufbau für Spiel {game!r} bekannt", flush=True)
            return
        cat = Catalog()
        if force or cat.count(game) == 0 or _age(_stamp(game)) > WEEK:
            self.status = f"{_label(game)}-Katalog wird aktualisiert"
            builder(cat)
            _touch(game)

    def images_pending(self, game: str) -> list:
        """Karten mit Bild-URL, ohne lokales Bild und ohne Index-Eintrag (nur diese müssen eingebettet werden)."""
        from .recognition.index import index_keys

        indexed = index_keys(self.index_name(), game)
        return [r for r in Catalog().all(game)
                if r.image_url and f"{game}|{r.id}" not in indexed and not os.path.exists(r.image_path)]

    def images_chunk(self, games: list[str] | None = None, limit: int | None = IMAGE_CHUNK) -> int:
        """Einen Block fehlender Bilder laden; liefert die Anzahl tatsächlich geladener Bilder (0 = nichts mehr zu tun).

        Bilder, die sich nicht laden lassen (Quelle liefert 404 o. ä.), werden einen Tag lang übersprungen – sonst hielte
        `while images_chunk(): index()` sie für Fortschritt und baute den Index endlos neu auf."""
        now = time.time()
        failed = self.__dict__.setdefault("_failed_images", {})
        for game in games if games is not None else self.games:
            todo = [r for r in self.images_pending(game) if now - failed.get((game, r.id), -1e12) > DAY]
            if not todo:
                continue
            chunk = todo[:limit] if limit else todo
            download_images(chunk, 6, progress=self._progress(f"{_label(game)}-Bilder", offset_left=len(todo)))
            loaded = 0
            for r in chunk:
                if os.path.exists(r.image_path):
                    loaded += 1
                else:
                    failed[(game, r.id)] = now
            if loaded:
                return loaded
            print(f"[wartung] {_label(game)}: {len(chunk)} Bilder nicht ladbar – nächster Versuch in einem Tag", flush=True)
        return 0

    def index(self) -> None:
        from .recognition.index import build_index

        if self.embedder_factory is None:
            from .recognition.embedder import Embedder

            emb = []  # erst laden, wenn wirklich etwas einzubetten ist (nach Datenpaketen oft nicht nötig)
            self._own_embedder = lambda: emb[0] if emb else (emb.append(Embedder()) or emb[0])
        self.status = "Erkennungs-Index wird ergänzt"
        factory = self.embedder_factory or self._own_embedder
        index = build_index(_LockedEmbedder(factory, self.gpu_lock, self.index_name()), games=self.games, batch=32)
        if self.on_index_updated and len(index.keys):
            self.on_index_updated(index)

    def _reload(self) -> None:
        if not self.on_index_updated:
            return
        from .recognition.index import EmbeddingIndex

        try:
            self.on_index_updated(EmbeddingIndex.load(self.index_name(), games=self.games))
        except FileNotFoundError:
            pass

    def prices(self) -> None:
        from .pricing.cardmarket import CardmarketPriceGuide

        self.status = "Cardmarket-Preise werden geladen"
        CardmarketPriceGuide().refresh()

    def price_history(self, force: bool = False) -> None:
        if "mtg" in self.games and (force or _age(_stamp("mtgjson")) > WEEK):
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
        """Setup: alles, was für die ersten Erkennungen nötig ist. Datenpakete zuerst; ohne Paket lokal aufbauen
        (Spiele mit großem Katalog wie Magic: erst Katalog, Bilder folgen im Hintergrund)."""
        self.packs()
        for game in self.games:
            self.catalog(game)
        for game in self.games:
            if game not in BACKGROUND_IMAGE_GAMES:
                self.images_chunk([game], limit=None)
        self.prices()
        self.index()
        missing = self.missing_games()
        self.status = "bereit" + (f" ({', '.join(map(_label, missing))}: Bilder werden im Hintergrund ergänzt)"
                                  if missing else "")

    def run_once(self) -> None:
        self.packs()
        for game in self.games:
            self.catalog(game)
        while self.images_chunk():
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
                    self.packs()
                    for game in self.games:
                        self.catalog(game)
                    if self.images_chunk():
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

    def __init__(self, factory, lock, index_name: str):
        self._factory = factory  # Embedder erst beim ersten Einbetten holen/laden
        self._lock = lock
        self.index_name = index_name

    def embed(self, imgs, batch: int = 32):
        embedder = self._factory()
        with self._lock:
            return embedder.embed(imgs, batch=batch)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--initial", action="store_true", help="Setup: Datenpakete bzw. Grunddaten")
    ap.add_argument("--games", help="kommagetrennt (Standard: config.json)")
    ap.add_argument("--pack-dir", help="lokaler Ordner mit Datenpaketen (Offline-Installation)")
    ap.add_argument("--pack-url", help="Basis-URL der Datenpakete (Standard: TCGLENS_PACK_URL / config.json)")
    args = ap.parse_args()
    games = [g.strip() for g in args.games.split(",") if g.strip()] if args.games else None
    m = Maintenance(games=games, pack_dir=args.pack_dir, pack_url=args.pack_url)
    print(f"[daten] Spiele: {', '.join(map(_label, m.games))}", flush=True)
    stop = threading.Event()

    def report():
        last = ""
        while not stop.wait(5):
            if m.status != last:
                print(f"[daten] {m.status}", flush=True)
                last = m.status

    threading.Thread(target=report, daemon=True).start()
    m.initial() if args.initial else m.run_once()
    stop.set()
    print(f"[daten] fertig – {m.status}" if m.status else "[daten] fertig", flush=True)


if __name__ == "__main__":
    main()
