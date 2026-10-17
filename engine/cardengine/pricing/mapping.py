"""Zuordnung Karte → Cardmarket-Produkt (idProduct) mit dauerhaftem Cache und Vorab-Laden im Hintergrund.

- Magic: steht bereits im Katalog (Scryfall `cardmarket_id`) – kein Netzwerkzugriff.
- Pokémon: TCGdex-Kartendetails (`pricing.cardmarket.idProduct`, Varianten). Ergebnis wird mit Zeitstempel in
  `cardmarket_map` gespeichert (auch „kein Produkt“), erneute Prüfung erst nach `MAX_AGE`.
- Ein Hintergrund-Thread lädt alle fehlenden Zuordnungen vorab. Karten, die gerade im Kamerabild sind, werden mit
  höchster Priorität vorgezogen; wer auf eine Zuordnung wartet, wartet nur auf genau diese.
"""
from __future__ import annotations

import heapq
import itertools
import sqlite3
import threading
import time

import requests

from ..card_database.catalog import DB_PATH, Catalog, CardRecord
from ..paths import USER_AGENT

MAX_AGE = 30 * 86400
PRIO_CAMERA, PRIO_BACKGROUND = 0, 10


class CardmarketMapping:
    def __init__(self, catalog: Catalog, workers: int = 4):
        self.catalog = catalog
        self._cache: dict[tuple[str, str], tuple[int | None, str | None, float]] = {
            (g, i): (p, f, t) for g, i, p, f, t in catalog.db.execute("SELECT * FROM cardmarket_map")
        }
        self._queue: list = []
        self._queued: set[tuple[str, str]] = set()
        self._done = threading.Condition()
        self._seq = itertools.count()
        self._session = requests.Session()
        self._session.headers["User-Agent"] = USER_AGENT
        self._db_lock = threading.Lock()
        # Eigene Verbindung für die Worker (SQLite-Verbindungen nicht über Threads teilen); Wartezeit bei Sperren
        self._db = sqlite3.connect(catalog.db_path, timeout=30, check_same_thread=False)
        self._workers = [threading.Thread(target=self._work, daemon=True, name=f"cm-map-{i}") for i in range(workers)]
        for w in self._workers:
            w.start()

    # ---------- öffentlich ----------
    def lookup(self, rec: CardRecord, wait_s: float = 0.0) -> int | None:
        """idProduct der Karte. Fehlt sie im Cache, wird sie vorgezogen; optional kurz darauf gewartet."""
        if rec.cardmarket_id:
            return rec.cardmarket_id
        key = (rec.game, rec.id)
        hit = self._cache.get(key)
        if hit and time.time() - hit[2] < MAX_AGE:
            return hit[0]
        if rec.game != "pokemon":
            return None
        self.request(rec, PRIO_CAMERA)
        if wait_s > 0:
            deadline = time.time() + wait_s
            with self._done:
                while key not in self._cache and time.time() < deadline:
                    self._done.wait(timeout=max(0.0, deadline - time.time()))
            hit = self._cache.get(key)
            return hit[0] if hit else None
        return None

    def request(self, rec: CardRecord, priority: int = PRIO_BACKGROUND) -> None:
        key = (rec.game, rec.id)
        with self._done:
            if key in self._queued and priority >= PRIO_BACKGROUND:
                return
            self._queued.add(key)
            heapq.heappush(self._queue, (priority, next(self._seq), rec))
            self._done.notify_all()

    def prefetch_all(self, game: str = "pokemon") -> int:
        """Alle Karten ohne (aktuelle) Zuordnung im Hintergrund nachladen."""
        now = time.time()
        todo = [r for r in self.catalog.all(game)
                if not r.cardmarket_id and now - self._cache.get((r.game, r.id), (None, None, 0))[2] >= MAX_AGE]
        for r in todo:
            self.request(r, PRIO_BACKGROUND)
        return len(todo)

    def pending(self) -> int:
        with self._done:
            return len(self._queue)

    # ---------- intern ----------
    def _work(self) -> None:
        while True:
            with self._done:
                while not self._queue:
                    self._done.wait()
                _, _, rec = heapq.heappop(self._queue)
            try:
                self._resolve(rec)
            except Exception as err:  # ein Fehler darf den Worker nie beenden
                print(f"[preise] Zuordnung {rec.id}: {err}")

    def _resolve(self, rec: CardRecord) -> None:
        key = (rec.game, rec.id)
        hit = self._cache.get(key)
        if hit and time.time() - hit[2] < MAX_AGE:
            return  # inzwischen erledigt (Karte war doppelt eingereiht: Kamera + Hintergrund)
        pid, finishes = self._fetch(rec)
        if pid is None and finishes is None:
            return  # Netzwerkfehler → nicht cachen, später erneut
        entry = (pid, finishes, time.time())
        with self._db_lock:
            self._db.execute("INSERT OR REPLACE INTO cardmarket_map VALUES (?,?,?,?,?)", (*key, *entry))
            self._db.commit()
        with self._done:
            self._cache[key] = entry
            self._done.notify_all()

    def _fetch(self, rec: CardRecord) -> tuple[int | None, str | None]:
        for attempt in range(3):
            try:
                r = self._session.get(f"https://api.tcgdex.net/v2/{rec.lang or 'en'}/cards/{rec.id}", timeout=20)
                if r.status_code == 404:
                    return None, ""
                if r.status_code == 429:
                    time.sleep(2 + attempt * 2)
                    continue
                r.raise_for_status()
                data = r.json()
                pid = ((data.get("pricing") or {}).get("cardmarket") or {}).get("idProduct")
                finishes = ",".join(k for k, v in (data.get("variants") or {}).items() if v is True)
                return (int(pid) if pid else None), finishes
            except (requests.RequestException, ValueError):
                time.sleep(1 + attempt)
        return None, None
