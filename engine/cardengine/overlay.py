"""Karten-Overlay (Phase 2): Name über der Karte, perspektivischer Rahmen, darunter Preis, Veränderung, Verlauf.

Jede Karte bekommt ein eigenes Panel. Panels werden nur neu gezeichnet, wenn sich Inhalt oder Größe ändern
(Cache je Track) und pro Frame lediglich per Alpha-Blending eingeblendet – derselbe Ansatz wie später im
OBS-Plugin (eine Textur je Karte). Positionen folgen den geglätteten Track-Ecken; überlappende Panels werden
nach unten/oben verschoben.
"""
from __future__ import annotations

import os

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import i18n
from .i18n import t as tr
from .tracking import Track

GAME_COLORS = {"mtg": (255, 138, 61), "pokemon": (255, 210, 63), "ygo": (176, 110, 255)}  # RGB
UNCERTAIN = (251, 191, 36)
PENDING = (125, 211, 252)
UP, DOWN = (74, 222, 128), (248, 113, 113)
FONT_DIR = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts")


def _font(size: int, bold: bool = False):
    for name in (("segoeuib.ttf", "arialbd.ttf") if bold else ("segoeui.ttf", "arial.ttf")):
        try:
            return ImageFont.truetype(os.path.join(FONT_DIR, name), size)
        except OSError:
            continue
    return ImageFont.load_default()


def fmt_eur(v: float | None, lang: str = "de") -> str:
    """Preis locale-gerecht: "0,15 €" (de) bzw. "€0.15" (en)."""
    return i18n.fmt_eur(v, lang)


class OverlayRenderer:
    def __init__(self, scale: float = 1.0, show_name: bool = True, show_price: bool = True, show_history: bool = True,
                 frame_thickness: float = 3.0, show_pending: bool = False, colors: dict | None = None,
                 history_days: int = 90, lang: str = "de"):
        self.scale = scale
        self.lang = i18n.t_lang(lang)  # Sprache der Panel-Texte (de/en)
        self.colors = {**GAME_COLORS, **(colors or {})}
        self.history_days = history_days
        self.show_name = show_name
        self.show_price = show_price
        self.show_history = show_history
        self.frame_thickness = frame_thickness
        self.show_pending = show_pending
        self._cache: dict[int, tuple[tuple, np.ndarray]] = {}  # track_id -> (Schlüssel, RGBA)
        self._badges: dict[tuple, tuple[int, np.ndarray]] = {}

    # ---------- Panels ----------
    def _panel_key(self, t: Track, width: int):
        ident = t.identity
        price = getattr(ident, "price", None)
        return (width, t.state, getattr(ident, "card_id", None), getattr(ident, "display_name", None),
                id(price), getattr(ident, "certain", None), tuple(self.colors.items()), self.history_days,
                self.show_history, self.lang, str(getattr(ident, "reason", "") or ""))

    def _render_name(self, t: Track, width: int) -> np.ndarray:
        ident = t.identity
        color = self.colors.get(ident.game, PENDING) if ident.certain else UNCERTAIN
        size = max(12, int(width * 0.085))
        font = _font(size, bold=True)
        name = getattr(ident, "display_name", None) or ident.name
        text = name if ident.certain else f"{name} ?"
        img = Image.new("RGBA", (width, int(size * 1.7)), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        tw = d.textlength(text, font=font)
        while tw > width - 12 and len(text) > 4:
            text = text[:-2] + "…"
            tw = d.textlength(text, font=font)
        x = (width - tw) / 2
        d.rounded_rectangle([x - 8, 2, x + tw + 8, size * 1.55], radius=size * 0.35, fill=(12, 14, 22, 215))
        d.text((x, size * 0.15), text, font=font, fill=color + (255,))
        return np.array(img)

    def _render_price(self, t: Track, width: int) -> np.ndarray | None:
        ident = t.identity
        price = getattr(ident, "price", None)
        s = width / 300
        f_big, f_small, f_tiny = _font(int(30 * s), True), _font(int(15 * s)), _font(int(12 * s))
        history = price.history[-(self.history_days + 1):] if price and price.history else []
        lines_h = int(150 * s) if (self.show_history and history) else int(92 * s)
        img = Image.new("RGBA", (width, lines_h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([0, 0, width - 1, lines_h - 1], radius=int(12 * s), fill=(12, 14, 22, 220))
        pad = int(12 * s)
        lang = self.lang
        if not ident.certain or not ident.card_id:
            d.text((pad, pad), tr("overlay.uncertain", lang), font=f_small, fill=UNCERTAIN + (255,))
            d.text((pad, pad + 22 * s), tr(ident.reason, lang), font=f_tiny, fill=(200, 200, 210, 255))
            return np.array(img.crop((0, 0, width, int(60 * s))))
        if price is None:
            d.text((pad, pad), tr("overlay.price_loading", lang), font=f_small, fill=PENDING + (255,))
            return np.array(img.crop((0, 0, width, int(46 * s))))
        q = price.primary
        if q is None:
            d.text((pad, pad), tr(price.note or "overlay.no_price", lang), font=f_small, fill=(200, 200, 210, 255))
            return np.array(img.crop((0, 0, width, int(46 * s))))
        main = fmt_eur(q.main, lang)
        d.text((pad, pad - 4 * s), main, font=f_big, fill=(255, 255, 255, 255))
        x2 = pad + d.textlength(main, font=f_big) + 10 * s
        if price.changes:
            label, pct = next(iter(price.changes.items()))
            col = UP if pct >= 0 else DOWN
            d.text((x2, pad + 2 * s), f"{'▲' if pct >= 0 else '▼'} {i18n.fmt_percent(abs(pct), lang)} "
                                      f"{i18n.change_label(label, lang)}", font=f_small, fill=col + (255,))
        extras = []
        for variant, label in (("foil", "Foil"), ("reverse", "Reverse")):
            if variant in price.quotes:
                extras.append(f"{label} {fmt_eur(price.quotes[variant].main, lang)}")
        sub = " · ".join([tr("overlay.from", lang, price=fmt_eur(q.low, lang))] + extras)
        d.text((pad, pad + 38 * s), sub, font=f_small, fill=(210, 214, 225, 255))
        d.text((pad, pad + 58 * s), f"{ident.set_name} #{ident.number} · Cardmarket", font=f_tiny, fill=(150, 156, 170, 255))
        if self.show_history and history:
            self._sparkline(d, history, pad, int(92 * s), width - 2 * pad, int(48 * s), self.colors.get(ident.game, PENDING),
                            lang)
        return np.array(img)

    @staticmethod
    def _sparkline(d: ImageDraw.ImageDraw, hist, x, y, w, h, color, lang: str = "de"):
        prices = [p.price for p in hist]
        if len(prices) < 2:
            d.text((x, y + h * 0.3), tr("overlay.history_today", lang), font=_font(max(10, h // 4)),
                   fill=(150, 156, 170, 255))
            return
        lo, hi = min(prices), max(prices)
        rng = (hi - lo) or max(hi * 0.05, 0.01)
        pts = [(x + i / (len(prices) - 1) * w, y + h - (p - lo) / rng * h) for i, p in enumerate(prices)]
        d.polygon(pts + [(x + w, y + h), (x, y + h)], fill=color + (50,))
        d.line(pts, fill=color + (255,), width=2)
        d.text((x, y + h + 2), tr("overlay.days", lang, n=len(prices)), font=_font(max(10, h // 5)),
               fill=(150, 156, 170, 255))

    def _panel(self, t: Track, kind: str, width: int):
        key = (kind,) + self._panel_key(t, width)
        cached = self._cache.get((t.id, kind))
        if cached and cached[0] == key:
            return cached[1]
        img = self._render_name(t, width) if kind == "name" else self._render_price(t, width)
        self._cache[(t.id, kind)] = (key, img)
        return img

    # ---------- Export für das OBS-Plugin ----------
    def label_badge(self, label: str, color: tuple[int, int, int], card_w: float) -> tuple[int, np.ndarray]:
        """Rundes Kennzeichen-Badge (A, B …) für die Ecke oben links; (Version, RGBA)."""
        size = int(np.clip(round(card_w * 0.13 * self.scale / 4) * 4, 20, 56))
        key = (label, tuple(color), size)
        cached = self._badges.get(key)
        if cached is None:
            img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            d = ImageDraw.Draw(img)
            d.ellipse((1, 1, size - 2, size - 2), fill=(*color, 240), outline=(15, 17, 22, 255), width=max(2, size // 12))
            font = _font(int(size * (0.58 if len(label) == 1 else 0.44)), bold=True)
            d.text((size / 2, size / 2), label, font=font, fill=(15, 17, 22, 255), anchor="mm")
            cached = (hash(key) & 0x7FFFFFFF, np.array(img))
            self._badges[key] = cached
        return cached

    def track_color(self, t: Track) -> tuple[int, int, int]:
        ident = t.identity
        if ident is None:
            return (130, 130, 140) if t.state == "rejected" else PENDING
        return self.colors.get(ident.game, PENDING) if ident.certain else UNCERTAIN

    def panels(self, t: Track, frame_w: int) -> list[tuple[str, int, str, np.ndarray]]:
        """(key, version, anchor, RGBA) je Panel eines Tracks. `version` ändert sich nur bei neuem Inhalt."""
        if t.identity is None:
            return []
        card_w = float(np.linalg.norm(t.display[0] - t.display[1]))
        # Breite in Stufen runden, damit leichte Größenschwankungen kein Neurendern auslösen
        pw = int(np.clip(round(card_w * 1.15 * self.scale / 20) * 20, 160, 480))
        out = []
        if self.show_name:
            out.append(("name", "top"))
        if self.show_price:
            out.append(("price", "bottom"))
        res = []
        for kind, anchor in out:
            img = self._panel(t, kind, pw)
            if img is None:
                continue
            key = self._cache[(t.id, kind)][0]
            res.append((f"{t.id}:{kind}", hash(key) & 0x7FFFFFFF, anchor, img))
        return res

    # ---------- Zeichnen ----------
    def draw(self, frame: np.ndarray, tracks: list[Track]) -> np.ndarray:
        h, w = frame.shape[:2]
        alive = {t.id for t in tracks}
        for k in [k for k in self._cache if k[0] not in alive]:
            del self._cache[k]
        occupied: list[tuple[int, int, int, int]] = []
        for t in sorted(tracks, key=lambda t: t.display[:, 0].min()):
            q = t.display
            if t.identity is None:
                if self.show_pending:
                    cv2.polylines(frame, [q.astype(np.int32)], True, PENDING[::-1], 2, cv2.LINE_AA)
                continue
            ident = t.identity
            color = (self.colors.get(ident.game, PENDING) if ident.certain else UNCERTAIN)[::-1]  # BGR
            card_w = np.linalg.norm(q[0] - q[1])
            thick = max(2, int(self.frame_thickness * card_w / 250 * self.scale))
            cv2.polylines(frame, [q.astype(np.int32)], True, color, thick, cv2.LINE_AA)
            pw = int(np.clip(card_w * 1.15 * self.scale, 170, 460))
            cx = int(q[:, 0].mean())
            top, bottom = int(q[:, 1].min()), int(q[:, 1].max())
            if self.show_name:
                img = self._panel(t, "name", pw)
                self._blit(frame, img, cx - pw // 2, top - img.shape[0] - 6, occupied, direction=-1)
            if self.show_price:
                img = self._panel(t, "price", pw)
                if img is not None:
                    self._blit(frame, img, cx - pw // 2, bottom + 8, occupied, direction=1)
        return frame

    @staticmethod
    def _blit(frame, rgba, x, y, occupied, direction: int):
        h, w = frame.shape[:2]
        ph, pw = rgba.shape[:2]
        x = int(np.clip(x, 0, max(0, w - pw)))
        y = int(np.clip(y, 0, max(0, h - ph)))
        # Überlappung mit bereits platzierten Panels vermeiden (verschieben in Leserichtung)
        for _ in range(20):
            hit = next((o for o in occupied if x < o[2] and x + pw > o[0] and y < o[3] and y + ph > o[1]), None)
            if not hit:
                break
            y = hit[3] + 4 if direction > 0 else hit[1] - ph - 4
            y = int(np.clip(y, 0, max(0, h - ph)))
        occupied.append((x, y, x + pw, y + ph))
        region = frame[y:y + ph, x:x + pw]
        src = rgba[: region.shape[0], : region.shape[1]]
        a = src[..., 3:4].astype(np.float32) / 255
        region[:] = (src[..., 2::-1] * a + region * (1 - a)).astype(np.uint8)


# ---------------------------------------------------------------- Pack-Opening (Bildschirm-Panels)
HIT_GOLD = (255, 200, 40)
_GAME_SHORT = {"pokemon": "Pokémon", "mtg": "Magic", "ygo": "Yu-Gi-Oh!"}


def render_hit_banner(name: str, price: float, set_line: str, frame_w: int, scale: float = 1.0,
                      lang: str = "de") -> np.ndarray:
    """Großes Banner „HIT! 168,73 €“ (en: „HIT!  €168.73“) mit Kartenname – goldener Rand, dunkler Grund."""
    s = max(0.5, frame_w / 1920 * scale)
    f_big, f_name, f_small = _font(int(64 * s), True), _font(int(30 * s), True), _font(int(20 * s))
    head = f"HIT!  {fmt_eur(price, lang)}"
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    if probe.textlength(name, font=f_name) > 900 * s:
        while len(name) > 4 and probe.textlength(name + "…", font=f_name) > 900 * s:
            name = name[:-1]
        name += "…"
    tw = max(probe.textlength(head, font=f_big), probe.textlength(name, font=f_name),
             probe.textlength(set_line, font=f_small))
    pad, border = int(28 * s), max(3, int(6 * s))
    w, h = int(tw + 2 * pad), int(pad * 2 + 64 * s * 1.15 + 30 * s * 1.3 + (20 * s * 1.4 if set_line else 0))
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=int(22 * s), fill=(18, 14, 4, 235),
                        outline=HIT_GOLD + (255,), width=border)
    y = pad - int(6 * s)
    d.text((w / 2, y), head, font=f_big, fill=HIT_GOLD + (255,), anchor="ma")
    y += int(64 * s * 1.15)
    d.text((w / 2, y), name, font=f_name, fill=(255, 255, 255, 255), anchor="ma")
    if set_line:
        d.text((w / 2, y + int(30 * s * 1.3)), set_line, font=f_small, fill=(220, 210, 180, 255), anchor="ma")
    return np.array(img)


def render_pack_total(total: float, count: int, hits: int, frame_w: int, scale: float = 1.0,
                      lang: str = "de") -> np.ndarray:
    """Kleines Panel „Session-Wert 213,40 €“ + „12 Karten · 2 Hits“ (en: „Session value“, „12 cards · 2 hits“)."""
    s = max(0.5, frame_w / 1920 * scale)
    f_lab, f_big, f_small = _font(int(18 * s)), _font(int(36 * s), True), _font(int(18 * s))
    sub = tr("pack.cards_hits", lang, cards=i18n.plural(count, "pack.card_one", "pack.card_many", lang),
             hits=i18n.plural(hits, "pack.hit_one", "pack.hit_many", lang))
    label, value = tr("pack.session_value", lang), fmt_eur(total, lang)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    pad = int(14 * s)
    tw = max(probe.textlength(label, font=f_lab), probe.textlength(value, font=f_big),
             probe.textlength(sub, font=f_small))
    w, h = int(tw + 2 * pad), int(pad * 2 + 18 * s * 1.3 + 36 * s * 1.2 + 18 * s * 1.2)
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=int(12 * s), fill=(12, 14, 22, 220))
    y = pad
    d.text((pad, y), label, font=f_lab, fill=(170, 176, 190, 255))
    y += int(18 * s * 1.3)
    d.text((pad, y), value, font=f_big, fill=(255, 255, 255, 255))
    y += int(36 * s * 1.2)
    d.text((pad, y), sub, font=f_small, fill=HIT_GOLD + (255,) if hits else (200, 204, 215, 255))
    return np.array(img)


def render_recent_hits(hits: list[tuple[str, float]], frame_w: int, scale: float = 1.0,
                       lang: str = "de") -> np.ndarray:
    """Liste „Letzte Hits“ (en: „Recent hits“): je Zeile Name und Preis (neueste zuerst)."""
    title = tr("pack.recent_hits", lang)
    s = max(0.5, frame_w / 1920 * scale)
    f_lab, f_row = _font(int(18 * s)), _font(int(22 * s), True)
    probe = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    pad, gap = int(14 * s), int(18 * s)
    rows = []
    for name, price in hits:
        if len(name) > 28:
            name = name[:27] + "…"
        rows.append((name, fmt_eur(price, lang)))
    star = int(22 * s * 1.05)  # Stern als Polygon (★ fehlt in Segoe UI Bold)
    name_w = max([star + probe.textlength(r[0], font=f_row) for r in rows] + [probe.textlength(title, font=f_lab)])
    price_w = max([probe.textlength(r[1], font=f_row) for r in rows] + [0])
    line = int(22 * s * 1.35)
    w, h = int(name_w + gap + price_w + 2 * pad), int(pad * 2 + 18 * s * 1.4 + line * len(rows))
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=int(12 * s), fill=(12, 14, 22, 220))
    d.text((pad, pad), title, font=f_lab, fill=(170, 176, 190, 255))
    y = pad + int(18 * s * 1.4)
    for left, right in rows:
        _star(d, pad + star * 0.4, y + 22 * s * 0.62, star * 0.42)
        d.text((pad + star, y), left, font=f_row, fill=HIT_GOLD + (255,))
        d.text((w - pad, y), right, font=f_row, fill=(255, 255, 255, 255), anchor="ra")
        y += line
    return np.array(img)


def _star(d: ImageDraw.ImageDraw, cx: float, cy: float, r: float) -> None:
    pts = []
    for i in range(10):
        a = -np.pi / 2 + i * np.pi / 5
        rr = r if i % 2 == 0 else r * 0.45
        pts.append((cx + rr * np.cos(a), cy + rr * np.sin(a)))
    d.polygon(pts, fill=HIT_GOLD + (255,))


def set_line(game: str | None, set_name: str | None, number: str | None) -> str:
    parts = [p for p in (_GAME_SHORT.get(game or "", ""), set_name, f"#{number}" if number else None) if p]
    return " · ".join(parts)
