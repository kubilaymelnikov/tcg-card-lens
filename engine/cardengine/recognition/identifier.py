"""Kartenidentifikation: Entzerren → Embedding-Suche (Karte/Artwork) → OCR der Fußzeile (Edition) → Entscheidung.

Yu-Gi-Oh: Dasselbe Artwork steht auf vielen Drucken; das Embedding bestimmt nur den Namen, den Druck allein der
Set-Code unter dem Artwork (OCR, deutsche „LOB-DE001“/„LOB-G001“ → Katalogcode „LOB-EN001“). Gibt es im selben Set
mehrere Seltenheiten, bleibt der Druck unsicher (optisch nicht verlässlich unterscheidbar).

Grundsatz: Ist die Edition nicht eindeutig, wird das als `certain=False` gemeldet. Für unsichere Identitäten
zeigt das Overlay später keinen Preis an.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

from ..card_database.catalog import Catalog, CardRecord
from ..geometry import RECT_H, RECT_W, rectify
from .footer import FooterInfo, matches, parse_footer, parse_ygo_codes, ygo_code_key
from .index import EmbeddingIndex

if TYPE_CHECKING:
    from ..learning import LearnedStore

MAX_ALTERNATIVES = 8
MAX_CANDIDATES = 20  # so viele Drucke höchstens als Kandidaten melden (Yu-Gi-Oh-Karten haben teils > 50)


@dataclass
class Thresholds:
    min_similarity: float = 0.55  # darunter: keine bekannte Karte
    certain_similarity: float = 0.70  # darunter: Karte nur vermutet
    name_margin: float = 0.03  # Abstand zur besten Karte mit anderem Namen
    edition_delta: float = 0.08  # Drucke gleichen Namens innerhalb dieses Abstands gelten als verwechselbar
    quarter_margin: float = 0.05  # eine 90°-Lage muss die beste 0°/180°-Lage um so viel schlagen
    distinct_edition_gap: float = 0.05  # Abstand, ab dem ein Druck auch ohne OCR als eindeutig gilt
    learned_certain: float = 0.90  # gelernte Referenz ab dieser Ähnlichkeit gilt als sicher
    learned_margin: float = 0.05  # Abstand zur nächsten anderen gelernten Karte
    negative_similarity: float = 0.93  # ab hier wie ein „Keine Karte“-Beispiel → nicht melden


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
    method: str  # embedding | embedding+ocr | gelernt
    candidates: list[str] = field(default_factory=list)  # verbleibende Drucke bei Unsicherheit ("game|id")
    reason: str = ""
    footer_text: str = ""
    ms: float = 0.0
    price: object | None = None  # pricing.base.PriceInfo – nur bei sicherer Identifikation
    rotate180: bool = False  # gewählte Lage der entzerrten Karte
    turns: int = 0  # Vierteldrehungen der Eckenzuordnung (0/2 nach Übernahme in den Track; 1/3 = Karte lag quer)
    alternatives: list = field(default_factory=list)  # [("game|id", Ähnlichkeit)], beste zuerst, max. 8
    embedding: object | None = field(default=None, repr=False)  # Embedding der gewählten Lage

    @property
    def label(self) -> str:
        return self.name if self.certain else f"{self.name} ?"

    def detail_line(self) -> str:
        if self.certain:
            return f"{self.set_name} #{self.number}  ({self.method}, {self.similarity:.2f})"
        return f"Druck unsicher: {len(self.candidates)} Kandidaten – {self.reason}"


class CardIdentifier:
    def __init__(self, index: EmbeddingIndex, catalog: Catalog, embedder, ocr=None, thresholds: Thresholds | None = None,
                 learned: "LearnedStore | None" = None):
        self.index = index
        self.catalog = catalog
        self.embedder = embedder
        self.ocr = ocr
        self.t = thresholds or Thresholds()
        self.learned = learned  # gelernte Referenzen/Negativbeispiele aus Bestätigungen (optional)

    @classmethod
    def load_default(cls) -> "CardIdentifier":
        from .embedder import Embedder
        from .ocr import FooterOcr

        embedder = Embedder()
        return cls(EmbeddingIndex.load(embedder.index_name), Catalog(), embedder, FooterOcr())

    def _record(self, key: str) -> CardRecord | None:
        game, cid = key.split("|", 1)
        return self.catalog.get(game, cid)

    def embed_view(self, frame: np.ndarray, corners: np.ndarray, turns: int | bool) -> tuple[np.ndarray, np.ndarray]:
        """Embedding und entzerrtes BGR-Bild (RECT_W × RECT_H) einer Lage – zum Lernen beim Bestätigen.
        `turns` in Vierteldrehungen (True/False wie früher = 180°/0°)."""
        view = rectify(frame, corners, turns=(2 if turns is True else int(turns)))
        return np.asarray(self.embedder.embed([view])[0], np.float32), view

    @staticmethod
    def _alternatives(hits: list[tuple[str, float]], first: tuple[str, float] | None = None) -> list[tuple[str, float]]:
        """Bis zu 8 unterschiedliche Schlüssel, beste zuerst; `first` (gelernte Karte) wird vorn eingereiht."""
        out: list[tuple[str, float]] = [] if first is None else [(first[0], float(first[1]))]
        seen = {k for k, _ in out}
        for k, s in hits:
            if len(out) >= MAX_ALTERNATIVES:
                break
            if k not in seen:
                seen.add(k)
                out.append((k, float(s)))
        return out

    def identify(self, frame: np.ndarray, corners: np.ndarray, game: str | None = None,
                 allow_quarter: bool = True) -> Identity | None:
        t0 = time.perf_counter()
        # Lagen einbetten; die ähnlichste bestimmt die Ausrichtung. Schräg gefilmt kann die lange Kartenseite
        # kürzer wirken als die kurze – dann stimmt die Hochformat-Annahme nicht, daher auch 90°/270° prüfen.
        turn_list = (0, 2, 1, 3) if allow_quarter else (0, 2)
        views = [rectify(frame, corners, turns=k) for k in turn_list]
        emb = self.embedder.embed(views)
        results = [self.index.search(e, k=40, game=game) for e in emb]
        top = [r[0][1] if r else -1.0 for r in results]
        orient = 0 if top[0] >= top[1] else 1
        if allow_quarter:
            q = 2 if top[2] >= top[3] else 3
            if top[q] >= top[orient] + self.t.quarter_margin:
                orient = q
        learned_hits = [None] * len(turn_list)
        if self.learned is not None:
            learned_hits = [self.learned.match(e, game=game) for e in emb]
            # Eine Lage mit sicherem gelerntem Treffer hat Vorrang
            lsim = [m[1] if m else -1.0 for m in learned_hits]
            good = [i for i in range(len(turn_list)) if lsim[i] >= self.t.learned_certain]
            if good:
                orient = max(good, key=lambda i: lsim[i])
        hits = results[orient]
        chosen = np.asarray(emb[orient], np.float32)
        turns = turn_list[orient]

        def finish(ident: Identity, first: tuple[str, float] | None = None) -> Identity:
            ident.turns = turns
            ident.rotate180 = turns == 2
            ident.alternatives = self._alternatives(hits, first)
            ident.embedding = chosen
            ident.ms = (time.perf_counter() - t0) * 1000
            return ident

        if self.learned is not None:
            # „Keine Karte“-Beispiel wiedererkannt → nicht melden
            if self.learned.negative_similarity(chosen) >= self.t.negative_similarity:
                return None
            m = learned_hits[orient]
            if m is not None and m[1] >= self.t.learned_certain and m[2] >= self.t.learned_margin:
                rec = self._record(m[0])
                if rec is not None:
                    ident = Identity(
                        game=rec.game, card_id=rec.id, name=rec.name, set_code=rec.set_code, set_name=rec.set_name,
                        number=rec.printed_number, certain=True, similarity=m[1], method="gelernt",
                    )
                    return finish(ident, first=(m[0], m[1]))

        if not hits:
            return None
        best_key, best_sim = hits[0]
        if best_sim < self.t.min_similarity:
            return None
        best = self._record(best_key)
        if best is None:
            return None
        if best.game == "ygo":
            return finish(self._identify_ygo(frame, corners, turns, hits, best, best_sim))

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
            return finish(ident)

        second_edition = same_name[1][1] if len(same_name) > 1 else -1.0
        if len(same_name) == 1 or best_sim - second_edition >= self.t.distinct_edition_gap:
            ident.certain = best_sim >= self.t.certain_similarity
            ident.reason = "" if ident.certain else "geringe Ähnlichkeit"
        elif self.ocr is not None:
            # Mehrere Drucke sehen gleich aus → Fußzeile lesen
            text = self.ocr.read_footer(rectify(frame, corners, turns=turns, margin=0.03))
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
                ident.candidates = [f"{r.game}|{r.id}" for r, _ in same_name]
                ident.reason = "Fußzeile nicht eindeutig lesbar"
        else:
            ident.card_id = None
            ident.candidates = [f"{r.game}|{r.id}" for r, _ in same_name]
            ident.reason = "mehrere Drucke, keine OCR"
        return finish(ident)

    def _identify_ygo(self, frame: np.ndarray, corners: np.ndarray, turns: int, hits: list[tuple[str, float]],
                      best: CardRecord, best_sim: float) -> Identity:
        """Yu-Gi-Oh: Name über das Artwork, Druck nur über den gelesenen Set-Code.

        - Set-Code passt zu genau einem Druck einer der in Frage kommenden Karten → sicher (embedding+ocr).
          Damit wird auch ein knapper Name (zwei ähnliche Artworks) über den Set-Code entschieden.
        - Set-Code passt zu mehreren Drucken (Seltenheiten im selben Set, alte Regionsdrucke) → Name sicher, Druck nicht.
        - Kein Set-Code lesbar: nur eine Karte mit genau einem TCG-Druck gilt als sicher (wie bei Magic/Pokémon).
        - Gelesener Set-Code gehört zu keinem Druck der Karte → unsicher (Name oder OCR falsch).
        """
        recs: dict[str, CardRecord | None] = {}
        for k, _ in hits:
            recs.setdefault(k, self._record(k))
        name_sim: dict[str, float] = {}
        for k, sim in hits:
            r = recs[k]
            if r is not None and r.game == "ygo" and r.name not in name_sim:
                name_sim[r.name] = sim
        other = max((s for n, s in name_sim.items() if n != best.name), default=-1.0)
        name_ok = best_sim - other >= self.t.name_margin
        ident = Identity(game="ygo", card_id=None, name=best.name, set_code=None, set_name=None, number=None,
                         certain=False, similarity=best_sim, method="embedding")
        # In Frage kommende Namen: bei knappem Abstand auch die ähnlichsten anderen (der Set-Code entscheidet)
        names = [best.name] if name_ok else [n for n, s in name_sim.items()
                                              if s >= max(self.t.min_similarity, best_sim - self.t.name_margin)][:3]
        prints = [r for n in names for r in self.catalog.by_name("ygo", n) if r.set_code]

        keys: list[str] = []
        if self.ocr is not None and prints:
            card = rectify(frame, corners, turns=turns, margin=0.03)
            read = getattr(self.ocr, "read_set_code", None) or self.ocr.read_footer
            ident.footer_text = read(card)
            keys = parse_ygo_codes(ident.footer_text)
        matched = [r for r in prints if ygo_code_key(r.printed_number) in keys]

        def take(r: CardRecord, method: str) -> Identity:
            ident.card_id, ident.name, ident.set_code = r.id, r.name, r.set_code
            ident.set_name, ident.number, ident.method = r.set_name, r.printed_number, method
            ident.similarity = name_sim.get(r.name, best_sim)
            return ident

        if matched:
            if len({r.name for r in matched}) > 1:
                ident.reason = "Set-Code passt zu mehreren Karten"
                ident.candidates = [f"ygo|{r.id}" for r in matched][:MAX_CANDIDATES]
                return ident
            if len(matched) == 1:
                take(matched[0], "embedding+ocr")
                ident.certain = ident.similarity >= self.t.min_similarity
                return ident
            # Gleicher Set-Code, verschiedene Seltenheiten (oder alte Regionsdrucke) → Druck unsicher
            r = matched[0]
            ident.name, ident.similarity = r.name, name_sim.get(r.name, best_sim)
            ident.set_code, ident.set_name, ident.number = r.set_code, r.set_name, r.printed_number
            ident.candidates = [f"ygo|{x.id}" for x in matched][:MAX_CANDIDATES]
            ident.reason = f"{len(matched)} Seltenheiten mit Set-Code {r.printed_number}"
            return ident
        if not name_ok:
            ident.reason = "Name nicht eindeutig"
            ident.candidates = [k for k, _ in hits[:5]]
            return ident
        own = [r for r in prints if r.name == best.name]
        ident.candidates = [f"ygo|{r.id}" for r in own][:MAX_CANDIDATES]
        if keys:
            ident.reason = "gelesener Set-Code passt zu keinem Druck dieser Karte"
        elif not own:
            ident.reason = "kein TCG-Druck bekannt"
        elif len(own) == 1:
            take(own[0], "embedding")
            ident.certain = best_sim >= self.t.certain_similarity
            ident.reason = "" if ident.certain else "geringe Ähnlichkeit"
        else:
            ident.reason = f"Set-Code nicht lesbar ({len(own)} Drucke)" if self.ocr is not None else "mehrere Drucke, keine OCR"
        return ident
