"""Kartennamen in mehreren Sprachen (Anzeige im Overlay).

- Pokémon: TCGdex liefert je Sprache eine Liste aller Karten mit Namen; Karten-IDs sind sprachübergreifend gleich
  (z. B. „sv03.5-151“). Die Listen der gewählten Sprachen werden vorab geladen und 7 Tage zwischengespeichert.
- Magic: Scryfall `/cards/{set}/{nummer}/{sprache}` → `printed_name`, nur für erkannte Karten, dauerhaft gecacht
  (auch „gibt es nicht in dieser Sprache“). Der Komplett-Download aller Sprachen (~2 GB) wird so vermieden.
- Yu-Gi-Oh: YGOPRODeck `cardinfo.php?language=xx` (nur de/fr/it/pt) liefert alle Karten mit Namen je Passcode
  (~19 MB je Sprache, 7 Tage zwischengespeichert). Wird nur geladen, wenn der Yu-Gi-Oh-Katalog aufgebaut ist.
Fallback ist immer der englische Name.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

import requests

from ..paths import DATA_DIR, USER_AGENT
from .catalog import CardRecord

LANGUAGES = {  # Code -> (Anzeigename, TCGdex-Code, Scryfall-Code)
    "de": ("Deutsch", "de", "de"),
    "en": ("Englisch", "en", "en"),
    "fr": ("Französisch", "fr", "fr"),
    "it": ("Italienisch", "it", "it"),
    "es": ("Spanisch", "es", "es"),
    "pt": ("Portugiesisch", "pt", "pt"),
    "ja": ("Japanisch", "ja", "ja"),
}
YGO_LANGUAGES = {"de", "fr", "it", "pt"}  # von YGOPRODeck angebotene Übersetzungen
DIR = os.path.join(DATA_DIR, "names")
MAX_AGE = 7 * 86400


class CardNames:
    def __init__(self):
        os.makedirs(DIR, exist_ok=True)
        self._pokemon: dict[str, dict[str, str]] = {}
        self._ygo: dict[str, dict[str, str]] = {}  # Sprache -> Passcode -> Name
        self._lock = threading.Lock()
        self._db = sqlite3.connect(os.path.join(DIR, "mtg_names.sqlite"), timeout=30, check_same_thread=False)
        self._db.execute("CREATE TABLE IF NOT EXISTS names (id TEXT, lang TEXT, name TEXT, PRIMARY KEY (id, lang))")
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
        self._pending: dict[tuple[str, str], CardRecord] = {}  # (id, lang) -> Karte, Namen im Hintergrund laden
        self._pending_cv = threading.Condition()
        threading.Thread(target=self._fetch_loop, daemon=True, name="namen").start()

    def preload(self, languages: list[str]) -> None:
        """Pokémon-Namenslisten der gewählten Sprachen laden (Hintergrund-Thread empfohlen)."""
        for lang in languages:
            if lang in self._pokemon or lang not in LANGUAGES:
                continue
            path = os.path.join(DIR, f"pokemon-{lang}.json")
            try:
                if not os.path.exists(path) or time.time() - os.path.getmtime(path) > MAX_AGE:
                    r = self._session.get(f"https://api.tcgdex.net/v2/{LANGUAGES[lang][1]}/cards", timeout=120)
                    r.raise_for_status()
                    data = {c["id"]: c["name"] for c in r.json()}
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False)
                with open(path, encoding="utf-8") as f:
                    names = json.load(f)
                with self._lock:
                    self._pokemon[lang] = names
                print(f"[namen] Pokémon {LANGUAGES[lang][0]}: {len(names)} Namen")
            except (requests.RequestException, ValueError, OSError) as err:
                print(f"[namen] Pokémon {lang}: {err}")
        if os.path.isdir(os.path.join(DATA_DIR, "ygo")):
            for lang in languages:
                if lang in YGO_LANGUAGES and lang not in self._ygo:
                    self._preload_ygo(lang)

    def _preload_ygo(self, lang: str) -> None:
        path = os.path.join(DIR, f"ygo-{lang}.json")
        try:
            if not os.path.exists(path) or time.time() - os.path.getmtime(path) > MAX_AGE:
                r = self._session.get(f"https://db.ygoprodeck.com/api/v7/cardinfo.php?language={lang}", timeout=180)
                r.raise_for_status()
                data = {str(c["id"]): c["name"] for c in r.json().get("data", [])}
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False)
            with open(path, encoding="utf-8") as f:
                names = json.load(f)
            with self._lock:
                self._ygo[lang] = names
            print(f"[namen] Yu-Gi-Oh {LANGUAGES[lang][0]}: {len(names)} Namen")
        except (requests.RequestException, ValueError, OSError) as err:
            print(f"[namen] Yu-Gi-Oh {lang}: {err}")

    def name(self, rec: CardRecord, lang: str) -> str:
        """Name der Karte in `lang`, sonst der englische Katalogname."""
        if lang == "en" or lang not in LANGUAGES:
            return rec.name
        if rec.game == "pokemon":
            return self._pokemon.get(lang, {}).get(rec.id) or rec.name
        if rec.game == "ygo":
            return self._ygo.get(lang, {}).get(rec.id.split("-", 1)[0]) or rec.name  # Schlüssel: Passcode
        return self._mtg_name(rec, lang) or rec.name

    def name_nowait(self, rec: CardRecord, lang: str) -> str:
        """Wie `name()`, aber nie mit Netzwerkzugriff (für das Dock): fehlende Magic-Namen werden im Hintergrund
        nachgeladen, bis dahin gilt der englische Name."""
        if lang == "en" or lang not in LANGUAGES or rec.game in ("pokemon", "ygo"):
            return self.name(rec, lang)
        row = self._cached(rec.id, lang)
        if row is not None:
            return row[0] or rec.name
        with self._pending_cv:
            self._pending[(rec.id, lang)] = rec
            self._pending_cv.notify()
        return rec.name

    def _fetch_loop(self) -> None:
        while True:
            with self._pending_cv:
                while not self._pending:
                    self._pending_cv.wait()
                (_, lang), rec = self._pending.popitem()
            try:
                self._mtg_name(rec, lang)
            except Exception as err:
                print(f"[namen] {rec.name} ({lang}): {err}")
            time.sleep(0.1)  # Scryfall bittet um höchstens 10 Anfragen pro Sekunde

    def _cached(self, card_id: str, lang: str):
        with self._lock:
            return self._db.execute("SELECT name FROM names WHERE id = ? AND lang = ?", (card_id, lang)).fetchone()

    def _mtg_name(self, rec: CardRecord, lang: str) -> str | None:
        row = self._cached(rec.id, lang)
        if row:
            return row[0] or None
        if not rec.set_code or not rec.number:
            return None
        name = ""
        try:
            r = self._session.get(f"https://api.scryfall.com/cards/{rec.set_code.lower()}/{rec.number}/{LANGUAGES[lang][2]}", timeout=15)
            if r.ok:
                d = r.json()
                name = d.get("printed_name") or " // ".join(f.get("printed_name", "") for f in d.get("card_faces", [])) or ""
            elif r.status_code != 404:
                return None  # vorübergehender Fehler → nicht cachen
        except requests.RequestException:
            return None
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO names VALUES (?, ?, ?)", (rec.id, lang, name))
            self._db.commit()
        return name or None
