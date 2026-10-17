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

from .tracking import Track

GAME_COLORS = {"mtg": (255, 138, 61), "pokemon": (255, 210, 63)}  # RGB
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


def fmt_eur(v: float | None) -> str:
    if v is None:
        return "–"
    return f"{v:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")


class OverlayRenderer:
    def __init__(self, scale: float = 1.0, show_name: bool = True, show_price: bool = True, show_history: bool = True,
                 frame_thickness: float = 3.0, show_pending: bool = False, colors: dict | None = None,
                 history_days: int = 90):
        self.scale = scale
        self.colors = {**GAME_COLORS, **(colors or {})}
        self.history_days = history_days
        self.show_name = show_name
        self.show_price = show_price
        self.show_history = show_history
        self.frame_thickness = frame_thickness
        self.show_pending = show_pending
        self._cache: dict[int, tuple[tuple, np.ndarray]] = {}  # track_id -> (Schlüssel, RGBA)

    # ---------- Panels ----------
    def _panel_key(self, t: Track, width: int):
        ident = t.identity
        price = getattr(ident, "price", None)
        return (width, t.state, getattr(ident, "card_id", None), getattr(ident, "display_name", None),
                id(price), getattr(ident, "certain", None), tuple(self.colors.items()), self.history_days,
                self.show_history)

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
        if not ident.certain or not ident.card_id:
            d.text((pad, pad), "Druck unsicher – kein Preis", font=f_small, fill=UNCERTAIN + (255,))
            d.text((pad, pad + 22 * s), ident.reason or "", font=f_tiny, fill=(200, 200, 210, 255))
            return np.array(img.crop((0, 0, width, int(60 * s))))
        if price is None:
            d.text((pad, pad), "Preis wird geladen …", font=f_small, fill=PENDING + (255,))
            return np.array(img.crop((0, 0, width, int(46 * s))))
        q = price.primary
        if q is None:
            d.text((pad, pad), price.note or "kein Preis", font=f_small, fill=(200, 200, 210, 255))
            return np.array(img.crop((0, 0, width, int(46 * s))))
        d.text((pad, pad - 4 * s), fmt_eur(q.main), font=f_big, fill=(255, 255, 255, 255))
        x2 = pad + d.textlength(fmt_eur(q.main), font=f_big) + 10 * s
        if price.changes:
            label, pct = next(iter(price.changes.items()))
            col = UP if pct >= 0 else DOWN
            d.text((x2, pad + 2 * s), f"{'▲' if pct >= 0 else '▼'} {abs(pct):.1f} % {label}", font=f_small, fill=col + (255,))
        extras = []
        for variant, label in (("foil", "Foil"), ("reverse", "Reverse")):
            if variant in price.quotes:
                extras.append(f"{label} {fmt_eur(price.quotes[variant].main)}")
        sub = " · ".join([f"ab {fmt_eur(q.low)}"] + extras)
        d.text((pad, pad + 38 * s), sub, font=f_small, fill=(210, 214, 225, 255))
        d.text((pad, pad + 58 * s), f"{ident.set_name} #{ident.number} · Cardmarket", font=f_tiny, fill=(150, 156, 170, 255))
        if self.show_history and history:
            self._sparkline(d, history, pad, int(92 * s), width - 2 * pad, int(48 * s), self.colors.get(ident.game, PENDING))
        return np.array(img)

    @staticmethod
    def _sparkline(d: ImageDraw.ImageDraw, hist, x, y, w, h, color):
        prices = [p.price for p in hist]
        if len(prices) < 2:
            d.text((x, y + h * 0.3), "Verlauf wird ab heute aufgezeichnet", font=_font(max(10, h // 4)), fill=(150, 156, 170, 255))
            return
        lo, hi = min(prices), max(prices)
        rng = (hi - lo) or max(hi * 0.05, 0.01)
        pts = [(x + i / (len(prices) - 1) * w, y + h - (p - lo) / rng * h) for i, p in enumerate(prices)]
        d.polygon(pts + [(x + w, y + h), (x, y + h)], fill=color + (50,))
        d.line(pts, fill=color + (255,), width=2)
        d.text((x, y + h + 2), f"{len(prices)} Tage", font=_font(max(10, h // 5)), fill=(150, 156, 170, 255))

    def _panel(self, t: Track, kind: str, width: int):
        key = (kind,) + self._panel_key(t, width)
        cached = self._cache.get((t.id, kind))
        if cached and cached[0] == key:
            return cached[1]
        img = self._render_name(t, width) if kind == "name" else self._render_price(t, width)
        self._cache[(t.id, kind)] = (key, img)
        return img

    # ---------- Export für das OBS-Plugin ----------
    def track_color(self, t: Track) -> tuple[int, int, int]:
        ident = t.identity
        if ident is None:
            return PENDING
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
