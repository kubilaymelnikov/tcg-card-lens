"""Kartenidentifikation: Entzerren → Embedding-Suche (Karte/Artwork) → OCR der Fußzeile (Edition) → Entscheidung.

Grundsatz: Ist die Edition nicht eindeutig, wird das als `certain=False` gemeldet. Für unsichere Identitäten
zeigt das Overlay später keinen Preis an.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import numpy as np

from ..card_database.catalog import Catalog, CardRecord
from ..geometry import RECT_H, RECT_W, rectify
from .footer import FooterInfo, matches, parse_footer
from .index import EmbeddingIndex


@dataclass
class Thresholds:
    min_similarity: float = 0.55  # darunter: keine bekannte Karte
    certain_similarity: float = 0.70  # darunter: Karte nur vermutet
    name_margin: float = 0.03  # Abstand zur besten Karte mit anderem Namen
    edition_delta: float = 0.08  # Drucke gleichen Namens innerhalb dieses Abstands gelten als verwechselbar
    distinct_edition_gap: float = 0.05  # Abstand, ab dem ein Druck auch ohne OCR als eindeutig gilt


@dataclass
class Identity:
    game: str
    card_id: str | None  # None, wenn nur der Name sicher ist
    name: str
    set_code: str | None
    set_name: str | None
    number: str | None
    certain: bool
    similarity: float
    method: str
    candidates: list[str] = field(default_factory=list)  # verbleibende Drucke bei Unsicherheit
    reason: str = ""
    footer_text: str = ""
    ms: float = 0.0
    price: object | None = None  # pricing.base.PriceInfo – nur bei sicherer Identifikation

    @property
    def label(self) -> str:
        return self.name if self.certain else f"{self.name} ?"

    def detail_line(self) -> str:
        if self.certain:
            return f"{self.set_name} #{self.number}  ({self.method}, {self.similarity:.2f})"
        return f"Druck unsicher: {len(self.candidates)} Kandidaten – {self.reason}"


class CardIdentifier:
    def __init__(self, index: EmbeddingIndex, catalog: Catalog, embedder, ocr=None, thresholds: Thresholds | None = None):
        self.index = index
        self.catalog = catalog
        self.embedder = embedder
        self.ocr = ocr
        self.t = thresholds or Thresholds()

    @classmethod
    def load_default(cls) -> "CardIdentifier":
        from .embedder import Embedder
        from .ocr import FooterOcr

        embedder = Embedder()
        return cls(EmbeddingIndex.load(embedder.index_name), Catalog(), embedder, FooterOcr())

    def _record(self, key: str) -> CardRecord | None:
        game, cid = key.split("|", 1)
        return self.catalog.get(game, cid)

    def identify(self, frame: np.ndarray, corners: np.ndarray, game: str | None = None) -> Identity | None:
        t0 = time.perf_counter()
        # Beide Lagen (0°/180°) einbetten; die ähnlichere bestimmt die Ausrichtung
        views = [rectify(frame, corners, rotate180=r) for r in (False, True)]
        emb = self.embedder.embed(views)
        results = [self.index.search(e, k=40, game=game) for e in emb]
        orient = 0 if results[0][0][1] >= results[1][0][1] else 1
        hits = results[orient]
        best_key, best_sim = hits[0]
        if best_sim < self.t.min_similarity:
            return None
        best = self._record(best_key)
        if best is None:
            return None

        other_name = next((s for k, s in hits[1:] if (r := self._record(k)) and r.name != best.name), -1.0)
        same_name = [(self._record(k), s) for k, s in hits if s >= best_sim - self.t.edition_delta]
        same_name = [(r, s) for r, s in same_name if r and r.name == best.name and r.game == best.game]

        ident = Identity(
            game=best.game, card_id=best.id, name=best.name, set_code=best.set_code, set_name=best.set_name,
            number=best.printed_number, certain=False, similarity=best_sim, method="embedding",
        )
        if best_sim - other_name < self.t.name_margin:
            ident.card_id = None
            ident.reason = "Name nicht eindeutig"
            ident.candidates = [k for k, _ in hits[:5]]
            ident.ms = (time.perf_counter() - t0) * 1000
            return ident

        second_edition = same_name[1][1] if len(same_name) > 1 else -1.0
        if len(same_name) == 1 or best_sim - second_edition >= self.t.distinct_edition_gap:
            ident.certain = best_sim >= self.t.certain_similarity
            ident.reason = "" if ident.certain else "geringe Ähnlichkeit"
        elif self.ocr is not None:
            # Mehrere Drucke sehen gleich aus → Fußzeile lesen
            text = self.ocr.read_footer(rectify(frame, corners, rotate180=bool(orient), margin=0.03))
            ident.footer_text = text
            info = parse_footer(text)
            scored = sorted(((matches(r, info), s, r) for r, s in same_name), key=lambda x: (x[0], x[1]), reverse=True)
            top = scored[0]
            if top[0] > 0 and (len(scored) == 1 or scored[1][0] < top[0]):
                r = top[2]
                ident.card_id, ident.set_code, ident.set_name, ident.number = r.id, r.set_code, r.set_name, r.printed_number
                ident.similarity = top[1]
                ident.certain = best_sim >= self.t.min_similarity
                ident.method = "embedding+ocr"
            else:
                ident.card_id = None
                ident.candidates = [r.id for r, _ in same_name]
                ident.reason = "Fußzeile nicht eindeutig lesbar"
        else:
            ident.card_id = None
            ident.candidates = [r.id for r, _ in same_name]
            ident.reason = "mehrere Drucke, keine OCR"
        ident.ms = (time.perf_counter() - t0) * 1000
        return ident
