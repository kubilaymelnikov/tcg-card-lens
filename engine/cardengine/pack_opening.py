"""Pack-Opening-Modus: Hits erkennen und alle sicher erkannten Karten einer Session sammeln.

Grundsatz wie im Overlay: Nur Karten mit **sicherer** Identität (`certain` und `card_id`) zählen, Preise nur von
dort. Eine physische Karte zählt einmal – Schlüssel ist (Track-ID, Kartenschlüssel). Bestätigt der Nutzer im Dock
eine *andere* Karte für denselben Track, ersetzt das den bisherigen Eintrag (es war eine Fehlerkennung), statt
doppelt zu zählen. Wird an derselben Stelle eine neue Karte gezeigt (gleicher Track, neue Identität ohne
Bestätigung), ist das eine weitere Karte – so läuft ein Pack-Opening vor der Facecam typischerweise ab.

Ein **Hit** ist ein Eintrag mit Preis ab der Schwelle (`price >= threshold`). Er wird genau einmal gemeldet, auch
wenn der Preis erst nach der Identität bekannt wird.
"""
from __future__ import annotations

import csv
import io
import itertools
import time
from dataclasses import dataclass, field

from .i18n import t as tr, t_lang

DEFAULT_THRESHOLD = 10.0
CSV_COLUMNS = ["Zeit", "Spiel", "Karte", "Set", "Nummer", "Variante", "Preis", "Währung", "Hit", "Schlüssel"]
GAME_NAMES = {"pokemon": "Pokémon", "mtg": "Magic", "ygo": "Yu-Gi-Oh!"}


def certain_key(identity) -> str | None:
    """„game|id“ einer sicheren Identität, sonst None."""
    if identity is None or not getattr(identity, "certain", False) or not getattr(identity, "card_id", None):
        return None
    return f"{identity.game}|{identity.card_id}"


def price_of(identity) -> tuple[float, str, str] | None:
    """(Preis, Währung, Variante) – nur bei sicherer Identität und vorhandenem Preis."""
    if certain_key(identity) is None:
        return None
    price = getattr(identity, "price", None)
    q = price.primary if price is not None else None
    if q is None or q.main is None:
        return None
    return float(q.main), q.currency or "EUR", q.variant or "normal"


def is_hit(identity, threshold: float = DEFAULT_THRESHOLD) -> bool:
    p = price_of(identity)
    return p is not None and p[0] >= float(threshold)


@dataclass
class PackCard:
    id: int  # laufende Nummer innerhalb der Engine (für das Dock: Eintrag entfernen)
    track: int
    key: str
    game: str
    name: str
    set_name: str | None
    number: str | None
    time: float
    price: float | None = None
    currency: str = "EUR"
    variant: str | None = None
    hit: bool = False

    def to_dict(self) -> dict:
        return {"id": self.id, "track": self.track, "key": self.key, "game": self.game, "name": self.name,
                "set_name": self.set_name, "number": self.number, "time": self.time,
                "price": None if self.price is None else round(self.price, 2), "currency": self.currency,
                "variant": self.variant, "hit": self.hit}


_entry_ids = itertools.count(1)


@dataclass
class PackSession:
    """Session einer Quelle: alle seit dem Start sicher erkannten Karten."""

    threshold: float = DEFAULT_THRESHOLD
    started: float = field(default_factory=time.time)
    cards: list[PackCard] = field(default_factory=list)
    _by_track_key: dict = field(default_factory=dict)  # (Track, Schlüssel) -> PackCard

    # ---------- Erfassen
    def observe(self, track_id: int, identity, confirmed: bool = False, now: float | None = None) -> PackCard | None:
        """Track mit aktueller Identität melden. Gibt den Eintrag zurück, wenn er **gerade** zum Hit wurde."""
        key = certain_key(identity)
        if key is None:
            return None
        now = time.time() if now is None else now
        entry = self._by_track_key.get((track_id, key))
        if entry is None:
            if confirmed:
                self._drop_corrected(track_id, key)
            entry = PackCard(id=next(_entry_ids), track=track_id, key=key, game=identity.game,
                             name=getattr(identity, "display_name", None) or identity.name,
                             set_name=identity.set_name, number=identity.number, time=now)
            self.cards.append(entry)
            self._by_track_key[(track_id, key)] = entry
        else:
            entry.name = getattr(identity, "display_name", None) or entry.name  # Anzeigesprache kann wechseln
        p = price_of(identity)
        if p is not None:
            entry.price, entry.currency, entry.variant = p
        if not entry.hit and entry.price is not None and entry.price >= float(self.threshold):
            entry.hit = True
            return entry
        return None

    def _drop_corrected(self, track_id: int, key: str) -> None:
        """Nutzer hat für diesen Track eine andere Karte bestätigt: den zuletzt gezählten Eintrag des Tracks ersetzen."""
        old = next((c for c in reversed(self.cards) if c.track == track_id and c.key != key), None)
        if old is not None:
            self.remove(old.id)

    def remove(self, entry_id: int) -> bool:
        entry = next((c for c in self.cards if c.id == int(entry_id)), None)
        if entry is None:
            return False
        self.cards.remove(entry)
        self._by_track_key.pop((entry.track, entry.key), None)
        return True

    def reset(self, now: float | None = None) -> None:
        self.started = time.time() if now is None else now
        self.cards.clear()
        self._by_track_key.clear()

    # ---------- Auswerten
    @property
    def total(self) -> float:
        return round(sum(c.price for c in self.cards if c.price is not None), 2)

    @property
    def hits(self) -> list[PackCard]:
        return [c for c in self.cards if c.hit]

    def recent_hits(self, n: int = 3) -> list[PackCard]:
        return self.hits[-n:][::-1]

    def to_dict(self) -> dict:
        return {"started": self.started, "threshold": self.threshold, "total": self.total,
                "count": len(self.cards), "hits": len(self.hits),
                "cards": [c.to_dict() for c in reversed(self.cards)]}  # neueste zuerst

    def to_csv(self, lang: str = "de") -> str:
        """CSV für Excel, Zeit lokal. Deutsch: Semikolon und Dezimalkomma; Englisch: Komma und Dezimalpunkt.
        Ohne BOM – den setzt der Aufrufer bei Bedarf."""
        de = t_lang(lang) == "de"
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";" if de else ",", lineterminator="\r\n")
        w.writerow(CSV_COLUMNS if de else tr("csv.columns", lang).split("|"))
        yes = tr("csv.yes", lang)
        for c in self.cards:
            price = "" if c.price is None else f"{c.price:.2f}"
            w.writerow([
                time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(c.time)), GAME_NAMES.get(c.game, c.game), c.name,
                c.set_name or "", c.number or "", c.variant or "",
                price.replace(".", ",") if de else price, c.currency,
                yes if c.hit else "", c.key,
            ])
        return buf.getvalue()


def save_csv(pack: PackSession, source: str, root: str | None = None, lang: str = "de") -> str:
    """CSV der Session unter data/sessions/<quelle>/<start>.csv ablegen (UTF-8 mit BOM für Excel); Pfad zurück."""
    import os
    import re

    from .paths import DATA_DIR

    folder = os.path.join(root or os.path.join(DATA_DIR, "sessions"), re.sub(r"[^\w\-. ]", "_", source).strip() or "quelle")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, time.strftime("%Y-%m-%d_%H%M%S", time.localtime(pack.started)) + ".csv")
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        f.write(pack.to_csv(lang))
    return path
