"""Preisdienst: Karte → Cardmarket-Produkt → aktuelle Preise, Verlauf, Veränderungen.

Zuordnung Karte → Cardmarket-Produkt (idProduct):
- Magic: `cardmarket_id` aus den Scryfall-Daten (bereits im Katalog)
- Pokémon: einmalig je Karte aus den TCGdex-Kartendetails (`pricing.cardmarket.idProduct`), danach im Katalog gecacht
- Yu-Gi-Oh: `cardmarket_id` aus dem Katalogaufbau (Cardmarket-Produktliste, nur eindeutige Zuordnungen)
"""
from __future__ import annotations

import threading
import time
from datetime import date, timedelta

from ..card_database.catalog import Catalog, CardRecord
from .base import PriceInfo, PricePoint, PriceProvider
from .cardmarket import CardmarketPriceGuide
from .mapping import CardmarketMapping
from .store import PriceStore


class PriceService:
    """Hält Preisdateien und Zuordnungen im Speicher; alles Laden passiert im Hintergrund.

    `start()` lädt beim Programmstart die (lokal zwischengespeicherten) Cardmarket-Preisdateien, startet das
    Vorab-Laden aller fehlenden Pokémon-Zuordnungen und prüft danach alle `refresh_s` Sekunden auf neue Tagesdateien.
    `info()` blockiert höchstens kurz (Preisdatei beim allerersten Start, Zuordnung einer gerade gesehenen Karte).
    """

    def __init__(self, provider: PriceProvider | None = None, store: PriceStore | None = None,
                 catalog: Catalog | None = None, history_days: int = 90, refresh_s: float = 3 * 3600):
        self.store = store or PriceStore()
        self.provider = provider or CardmarketPriceGuide(self.store)
        self.catalog = catalog or Catalog()
        self.mapping = CardmarketMapping(self.catalog)
        self.history_days = history_days
        self.refresh_s = refresh_s
        self._loaded = threading.Event()
        self._started = False

    def start(self, prefetch: bool = True) -> "PriceService":
        if not self._started:
            self._started = True
            threading.Thread(target=self._background, args=(prefetch,), daemon=True, name="prices").start()
        return self

    def _background(self, prefetch: bool) -> None:
        while True:
            try:
                self.provider.refresh()
            except Exception as err:  # Preisquelle darf die Erkennung nie stören
                print(f"[preise] Aktualisierung fehlgeschlagen: {err}")
            self._loaded.set()
            if prefetch:
                n = self.mapping.prefetch_all("pokemon")
                if n:
                    print(f"[preise] lade {n} Pokémon-Zuordnungen im Hintergrund vor")
                prefetch = False
            time.sleep(self.refresh_s)

    def ensure_loaded(self, timeout: float = 30.0) -> bool:
        self.start()
        return self._loaded.wait(timeout)

    def id_product(self, rec: CardRecord, wait_s: float = 3.0) -> int | None:
        return self.mapping.lookup(rec, wait_s=wait_s)

    # ---------- Preise ----------
    def info(self, rec: CardRecord, wait_s: float = 3.0) -> PriceInfo:
        if not self.ensure_loaded():
            return PriceInfo(id_product=None, note="Preisdaten werden geladen")
        pid = self.id_product(rec, wait_s=wait_s)
        if not pid:
            return PriceInfo(id_product=None, note="kein Cardmarket-Produkt gefunden")
        quotes = self.provider.quotes(rec.game, pid)
        info = PriceInfo(id_product=pid, quotes=quotes)
        if not quotes:
            info.note = "kein Preis auf Cardmarket"
            return info
        main_variant = "normal" if "normal" in quotes else next(iter(quotes))
        info.history = self.history(rec.game, pid, main_variant)
        info.changes = self._changes(info.history, quotes[main_variant])
        return info

    def history(self, game: str, pid: int, variant: str) -> list[PricePoint]:
        """Tageswerte der letzten `history_days` Tage; Lücken werden mit dem letzten bekannten Wert gefüllt
        (die Historie speichert nur Änderungen). Eigene Cardmarket-Snapshots haben Vorrang vor MTGJSON."""
        rows = self.store.series(game, pid, variant)
        if not rows:
            return []
        by_day: dict[str, tuple[int, str]] = {}
        for day, cents, source in rows:
            if day not in by_day or source == "cardmarket":
                by_day[day] = (cents, source)
        start = date.today() - timedelta(days=self.history_days)
        days = sorted(by_day)
        first = max(date.fromisoformat(days[0]), start)
        out, last = [], None
        for d in sorted(by_day):  # Wert vor dem Startfenster als Ausgangswert
            if date.fromisoformat(d) < first:
                last = by_day[d]
        cur = first
        while cur <= date.today():
            key = cur.isoformat()
            if key in by_day:
                last = by_day[key]
            if last:
                out.append(PricePoint(day=key, price=last[0] / 100, source=last[1]))
            cur += timedelta(days=1)
        return out

    @staticmethod
    def _changes(history: list[PricePoint], quote) -> dict[str, float]:
        changes: dict[str, float] = {}
        if len(history) >= 2:
            now = history[-1].price
            for label, days in (("7T", 7), ("30T", 30), ("90T", 90)):
                if len(history) > days:
                    then = history[-1 - days].price
                    if then > 0:
                        changes[label] = (now - then) / then * 100
        if not changes and quote.trend and quote.avg30:
            # Ohne eigene Historie: Trend im Vergleich zum 30-Tage-Durchschnitt (klar so beschriftet)
            changes["vs Ø30T"] = (quote.trend - quote.avg30) / quote.avg30 * 100
        return changes
