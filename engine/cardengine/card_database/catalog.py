"""Lokaler Kartenkatalog (SQLite): alle Drucke mit Set, Nummer, Cardmarket-ID und Referenzbild-Pfad."""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass

from ..paths import DATA_DIR

DB_PATH = os.path.join(DATA_DIR, "catalog.sqlite")
IMG_DIR = os.path.join(DATA_DIR, "refimg")

SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
  game            TEXT NOT NULL,      -- 'mtg' | 'pokemon' | 'ygo'
  id              TEXT NOT NULL,      -- Scryfall-ID, TCGdex-ID bzw. Yu-Gi-Oh "<passcode>-<set-code>-<seltenheit>"
  name            TEXT NOT NULL,
  set_code        TEXT NOT NULL,      -- Yu-Gi-Oh: Präfix des Set-Codes ("LOB"), leer bei reinen Artwork-Einträgen
  set_name        TEXT,
  number          TEXT NOT NULL,      -- Sammlernummer wie gedruckt (ohne Setgröße); Yu-Gi-Oh: "EN001"
  set_total       INTEGER,            -- gedruckte Setgröße (Pokémon "123/165"), sonst NULL
  lang            TEXT,
  released        TEXT,
  cardmarket_id   INTEGER,
  illustration_id TEXT,               -- gleiches Artwork über Reprints hinweg (Magic; Yu-Gi-Oh: Bild-ID)
  finishes        TEXT,               -- z. B. 'nonfoil,foil' / 'normal,holo,reverse'; Yu-Gi-Oh: Seltenheit
  image_url       TEXT,
  PRIMARY KEY (game, id)
);
CREATE INDEX IF NOT EXISTS cards_name ON cards (game, name);
CREATE INDEX IF NOT EXISTS cards_set_number ON cards (game, set_code, number);
CREATE INDEX IF NOT EXISTS cards_illustration ON cards (illustration_id);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


@dataclass
class CardRecord:
    game: str
    id: str
    name: str
    set_code: str
    set_name: str | None
    number: str
    set_total: int | None
    lang: str | None
    released: str | None
    cardmarket_id: int | None
    illustration_id: str | None
    finishes: str | None
    image_url: str | None

    @property
    def image_path(self) -> str:
        return image_path(self.game, self.id)

    @property
    def printed_number(self) -> str:
        if self.game == "ygo":
            return f"{self.set_code}-{self.number}" if self.set_code else ""  # wie gedruckt: "LOB-EN001"
        return f"{self.number}/{self.set_total}" if self.set_total else self.number


def image_path(game: str, card_id: str) -> str:
    if game == "ygo":
        # Viele Drucke teilen ein Artwork → eine Bilddatei je Artwork (Bild-ID von YGOPRODeck):
        # "<passcode>-<set-code>-<seltenheit>" → <passcode>.jpg, "<passcode>-art<bild-id>" → <bild-id>.jpg
        head, _, rest = card_id.partition("-")
        card_id = rest[3:] if rest.startswith("art") else head
    safe = card_id.replace("/", "_").replace("\\", "_").replace(":", "_")
    return os.path.join(IMG_DIR, game, f"{safe}.jpg")


class Catalog:
    def __init__(self, path: str = DB_PATH):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.db_path = path
        self.db = sqlite3.connect(path, timeout=30, check_same_thread=False)
        self.db.executescript(SCHEMA)
        # Zeitstempel der letzten Cardmarket-Zuordnung je Karte (auch "kein Produkt" wird gemerkt)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS cardmarket_map (
              game TEXT NOT NULL, id TEXT NOT NULL, id_product INTEGER, finishes TEXT, checked_at REAL NOT NULL,
              PRIMARY KEY (game, id)
            );
        """)

    def upsert(self, records: list[CardRecord]) -> None:
        self.db.executemany(
            "INSERT OR REPLACE INTO cards VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (r.game, r.id, r.name, r.set_code, r.set_name, r.number, r.set_total, r.lang, r.released,
                 r.cardmarket_id, r.illustration_id, r.finishes, r.image_url)
                for r in records
            ],
        )
        self.db.commit()

    def _rows(self, sql: str, args=()) -> list[CardRecord]:
        return [CardRecord(*row) for row in self.db.execute(f"SELECT * FROM cards {sql}", args)]

    def get(self, game: str, card_id: str) -> CardRecord | None:
        rows = self._rows("WHERE game = ? AND id = ?", (game, card_id))
        return rows[0] if rows else None

    def all(self, game: str | None = None) -> list[CardRecord]:
        return self._rows("WHERE game = ?", (game,)) if game else self._rows("")

    def same_card(self, rec: CardRecord) -> list[CardRecord]:
        """Alle Drucke mit gleichem Namen (Kandidaten für die Editionsunterscheidung)."""
        return self._rows("WHERE game = ? AND name = ?", (rec.game, rec.name))

    def by_name(self, game: str, name: str) -> list[CardRecord]:
        return self._rows("WHERE game = ? AND name = ?", (game, name))

    def by_set_number(self, game: str, set_code: str, number: str) -> list[CardRecord]:
        return self._rows("WHERE game = ? AND upper(set_code) = upper(?) AND number = ?", (game, set_code, number))

    def count(self, game: str | None = None) -> int:
        sql = "SELECT count(*) FROM cards" + (" WHERE game = ?" if game else "")
        return self.db.execute(sql, (game,) if game else ()).fetchone()[0]
