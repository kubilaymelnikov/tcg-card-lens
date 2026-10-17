"""Cardmarket-Preisprovider über die öffentlichen Preisdateien von Cardmarket.

Die offizielle API nimmt keine neuen Nutzer an (help.cardmarket.com, Stand 10/2026). Cardmarket stellt aber
täglich (~02:44 MEZ) Preisdateien je Spiel ohne Login bereit:
    https://downloads.s3.cardmarket.com/productCatalog/priceGuide/price_guide_{1|6}.json   (1 = Magic, 6 = Pokémon)
Felder je idProduct: avg, low, trend, avg1, avg7, avg30 sowie Varianten mit Suffix
"-foil" (Magic: Foil) bzw. "-holo" (Pokémon: Reverse Holo). 0 bzw. null = keine Daten.

Bei jedem neuen Tagesstand werden die Trendpreise als Snapshot in die lokale Historie geschrieben (nur Änderungen).
"""
from __future__ import annotations

import json
import os
import threading
import time
from email.utils import formatdate

import requests

from ..paths import DATA_DIR, USER_AGENT
from .base import PriceProvider, PriceQuote
from .store import PriceStore

URL = "https://downloads.s3.cardmarket.com/productCatalog/priceGuide/price_guide_{game_id}.json"
GAME_IDS = {"mtg": 1, "pokemon": 6}
VARIANT_SUFFIX = {"mtg": {"normal": "", "foil": "-foil"}, "pokemon": {"normal": "", "reverse": "-holo"}}
FIELDS = ("trend", "low", "avg", "avg1", "avg7", "avg30")
MAX_AGE_S = 6 * 3600  # spätestens alle 6 h prüfen, ob es eine neue Tagesdatei gibt (If-Modified-Since)


def _num(v) -> float | None:
    return float(v) if isinstance(v, (int, float)) and v > 0 else None


class CardmarketPriceGuide(PriceProvider):
    name = "cardmarket-priceguide"

    def __init__(self, store: PriceStore | None = None, data_dir: str = os.path.join(DATA_DIR, "prices")):
        self.store = store or PriceStore()
        self.dir = data_dir
        os.makedirs(self.dir, exist_ok=True)
        self._guides: dict[str, dict[int, dict]] = {}
        self._created: dict[str, str] = {}
        self._lock = threading.Lock()

    def _path(self, game: str) -> str:
        return os.path.join(self.dir, f"price_guide_{GAME_IDS[game]}.json")

    def _download(self, game: str, force: bool) -> bool:
        path = self._path(game)
        headers = {"User-Agent": USER_AGENT}
        if os.path.exists(path) and not force:
            if time.time() - os.path.getmtime(path) < MAX_AGE_S:
                return False
            headers["If-Modified-Since"] = formatdate(os.path.getmtime(path), usegmt=True)
        r = requests.get(URL.format(game_id=GAME_IDS[game]), headers=headers, timeout=120)
        if r.status_code == 304:
            os.utime(path)
            return False
        r.raise_for_status()
        tmp = path + ".part"
        with open(tmp, "wb") as f:
            f.write(r.content)
        os.replace(tmp, path)
        return True

    def _load(self, game: str) -> None:
        with open(self._path(game), encoding="utf-8") as f:
            data = json.load(f)
        guide = {e["idProduct"]: e for e in data["priceGuides"]}
        with self._lock:
            self._guides[game] = guide
            self._created[game] = data.get("createdAt", "")

    def refresh(self, force: bool = False) -> bool:
        changed = False
        for game in GAME_IDS:
            try:
                new = self._download(game, force)
            except requests.RequestException as err:
                print(f"[preise] {game}: Download fehlgeschlagen ({err}) – nutze vorhandene Datei")
                new = False
            if not os.path.exists(self._path(game)):
                continue
            if new or game not in self._guides:
                self._load(game)
                self._snapshot(game)
            changed |= new
        return changed

    def _snapshot(self, game: str) -> None:
        """Trendpreise des Tagesstands in die Historie übernehmen (nur geänderte Werte)."""
        day = (self._created.get(game) or time.strftime("%Y-%m-%d"))[:10]
        key = f"snapshot_day_{game}"
        if self.store.meta(key) == day:
            return
        latest = self.store.latest_cents(game, "cardmarket")
        rows = []
        for pid, e in self._guides[game].items():
            for variant, suffix in VARIANT_SUFFIX[game].items():
                v = _num(e.get("trend" + suffix))
                if v is None:
                    continue
                cents = round(v * 100)
                if latest.get((pid, variant)) != cents:
                    rows.append((game, pid, variant, day, cents, "cardmarket"))
        self.store.add_many(rows)
        self.store.set_meta(key, day)
        print(f"[preise] {game}: Snapshot {day}, {len(rows)} geänderte Preise gespeichert")

    def quotes(self, game: str, id_product: int) -> dict[str, PriceQuote]:
        e = self._guides.get(game, {}).get(id_product)
        if not e:
            return {}
        out = {}
        for variant, suffix in VARIANT_SUFFIX[game].items():
            q = PriceQuote(source=self.name, variant=variant, as_of=self._created.get(game),
                           **{f: _num(e.get(f + suffix)) for f in FIELDS})
            if q.main is not None:
                out[variant] = q
        return out
