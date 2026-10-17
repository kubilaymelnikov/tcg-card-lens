"""Lokale Preis-Historie (SQLite). Speichert je Produkt/Variante nur Änderungen (Tageswerte), damit die Datenbank
trotz ~200 000 Produkten bei täglichen Snapshots klein bleibt."""
from __future__ import annotations

import os
import sqlite3
import threading

from ..paths import DATA_DIR

DB_PATH = os.path.join(DATA_DIR, "prices.sqlite")

SCHEMA = """
CREATE TABLE IF NOT EXISTS price_history (
  game       TEXT NOT NULL,
  id_product INTEGER NOT NULL,
  variant    TEXT NOT NULL,     -- normal | foil | reverse
  day        TEXT NOT NULL,     -- YYYY-MM-DD
  cents      INTEGER NOT NULL,  -- Trendpreis in Cent
  source     TEXT NOT NULL,     -- cardmarket | mtgjson
  PRIMARY KEY (game, id_product, variant, day, source)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


class PriceStore:
    def __init__(self, path: str = DB_PATH):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript(SCHEMA)
        self.lock = threading.Lock()

    def meta(self, k: str) -> str | None:
        row = self.db.execute("SELECT v FROM meta WHERE k = ?", (k,)).fetchone()
        return row[0] if row else None

    def set_meta(self, k: str, v: str) -> None:
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (k, v))
            self.db.commit()

    def latest_cents(self, game: str, source: str) -> dict[tuple[int, str], int]:
        """Letzter gespeicherter Wert je (Produkt, Variante) – Grundlage für „nur Änderungen speichern“."""
        rows = self.db.execute(
            """SELECT id_product, variant, cents FROM price_history h
               WHERE game = ? AND source = ? AND day = (
                 SELECT max(day) FROM price_history x
                 WHERE x.game = h.game AND x.id_product = h.id_product AND x.variant = h.variant AND x.source = h.source)""",
            (game, source),
        )
        return {(p, v): c for p, v, c in rows}

    def add_many(self, rows: list[tuple[str, int, str, str, int, str]]) -> None:
        with self.lock:
            self.db.executemany("INSERT OR REPLACE INTO price_history VALUES (?,?,?,?,?,?)", rows)
            self.db.commit()

    def compact(self) -> int:
        """Aufeinanderfolgende gleiche Tageswerte je Reihe entfernen (Lücken bedeuten „unverändert“)."""
        with self.lock:
            cur = self.db.execute(
                """DELETE FROM price_history WHERE (game, id_product, variant, day, source) IN (
                     SELECT game, id_product, variant, day, source FROM (
                       SELECT *, LAG(cents) OVER (PARTITION BY game, id_product, variant, source ORDER BY day) AS prev
                       FROM price_history) WHERE prev = cents)"""
            )
            self.db.commit()
            n = cur.rowcount
            self.db.execute("VACUUM")
            return n

    def series(self, game: str, id_product: int, variant: str) -> list[tuple[str, int, str]]:
        return self.db.execute(
            "SELECT day, cents, source FROM price_history WHERE game = ? AND id_product = ? AND variant = ? ORDER BY day",
            (game, id_product, variant),
        ).fetchall()
