"""Gezielte Suche an einer Bildstelle (Dock: Klick in die Kamerabild-Vorschau oder aufgezogener Rahmen).

Für Karten, die der Detektor im Gesamtbild übersieht (Spiegelung in Sammelmappen, starke Schräge, Toploader):
1. Klick: Ausschnitt um die Stelle (etwa 1,6 Kartenhöhen) vergrößert mit niedriger Schwelle durch den Detektor;
   genommen wird die Karte, die den Klickpunkt enthält. Sonst klassische Kantensuche im selben Ausschnitt.
2. Rahmen: zuerst Detektor im leicht erweiterten Rahmen (passt eine Karte gut zum Rahmen, wird sie genommen),
   sonst der Rahmen selbst, an die echten Kartenkanten angepasst (geometry.refine_quad_edges).
"""
from __future__ import annotations

import cv2
import numpy as np

from .detection.classical import ClassicalDetector
from .geometry import CARD_RATIO, iou_quads, order_corners, refine_quad_edges

PROBE_CONF = 0.08  # Detektor-Schwelle bei der gezielten Suche (normal 0,2–0,4)
REGION_CARDS = 1.6  # Ausschnitthöhe in Kartenhöhen
MIN_REGION = 160  # px
RECT_MATCH_IOU = 0.5


def _detect(detector, img: np.ndarray, conf: float) -> list:
    try:
        return detector.detect(img, conf=conf)
    except TypeError:  # Detektor ohne einstellbare Schwelle (z. B. klassisch)
        return detector.detect(img)


def _crop(frame: np.ndarray, x0: float, y0: float, x1: float, y1: float):
    h, w = frame.shape[:2]
    x0, y0 = int(max(0, x0)), int(max(0, y0))
    x1, y1 = int(min(w, x1)), int(min(h, y1))
    if x1 - x0 < 20 or y1 - y0 < 20:
        return None, (0, 0)
    return frame[y0:y1, x0:x1], (x0, y0)


def _upscale(img: np.ndarray, target: int = 640) -> tuple[np.ndarray, float]:
    """Kleine Ausschnitte vergrößern, damit der Detektor die Karte in gewohnter Größe sieht."""
    s = target / max(img.shape[:2])
    if s <= 1.05:
        return img, 1.0
    return cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC), s


def _plausible(q: np.ndarray, card_h: float | None) -> bool:
    """Seitenverhältnis wie eine Karte (großzügig: Perspektive), Größe nicht völlig daneben."""
    a = np.linalg.norm(q[0] - q[1]) + np.linalg.norm(q[2] - q[3])
    b = np.linalg.norm(q[1] - q[2]) + np.linalg.norm(q[3] - q[0])
    if min(a, b) <= 0:
        return False
    ratio = min(a, b) / max(a, b)
    if not 0.45 <= ratio <= 0.95:
        return False
    if card_h:
        size = max(a, b) / 2
        return 0.4 * card_h <= size <= 2.2 * card_h
    return True


def probe_point(detector, frame: np.ndarray, x: float, y: float, card_h: float | None = None,
                conf: float = PROBE_CONF) -> tuple[np.ndarray | None, str]:
    """Karte an Pixelposition (x, y) suchen. Liefert (Ecken im Bild, Verfahren) oder (None, "")."""
    h, w = frame.shape[:2]
    ch = card_h or 0.4 * h
    half_h = max(MIN_REGION, REGION_CARDS * ch) / 2
    half_w = max(MIN_REGION, REGION_CARDS * ch * max(CARD_RATIO, 0.9)) / 2
    crop, (ox, oy) = _crop(frame, x - half_w, y - half_h, x + half_w, y + half_h)
    if crop is None:
        return None, ""
    big, s = _upscale(crop)
    pt = (float((x - ox) * s), float((y - oy) * s))
    for how, dets in (("detektor", _detect(detector, big, conf)),
                      ("kontur", ClassicalDetector(max_side=960, min_area_frac=0.05).detect(big))):
        best, best_score = None, -1.0
        for d in dets:
            q = np.asarray(d.corners, np.float32)
            if cv2.pointPolygonTest(q.reshape(-1, 1, 2), pt, False) < 0:
                continue
            q = q / s + np.float32([ox, oy])
            if _plausible(q, card_h) and d.score > best_score:
                best, best_score = q, d.score
        if best is not None:
            return order_corners(best), how
    return None, ""


def probe_rect(detector, frame: np.ndarray, x0: float, y0: float, x1: float, y1: float,
               conf: float = PROBE_CONF) -> tuple[np.ndarray | None, str]:
    """Karte im aufgezogenen Rahmen (Pixel). Liefert immer Ecken, notfalls den angepassten Rahmen."""
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    rect = order_corners(np.float32([[x0, y0], [x1, y0], [x1, y1], [x0, y1]]))
    pad_x, pad_y = (x1 - x0) * 0.15, (y1 - y0) * 0.15
    crop, (ox, oy) = _crop(frame, x0 - pad_x, y0 - pad_y, x1 + pad_x, y1 + pad_y)
    if crop is not None:
        big, s = _upscale(crop)
        cands = [np.asarray(d.corners, np.float32) / s + np.float32([ox, oy]) for d in _detect(detector, big, conf)]
        scored = sorted(((iou_quads(order_corners(q), rect), q) for q in cands), key=lambda c: c[0], reverse=True)
        if scored and scored[0][0] >= RECT_MATCH_IOU:
            return order_corners(scored[0][1]), "detektor"
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    refined = None
    try:
        refined = refine_quad_edges(gray, rect, search=0.08)
    except Exception:
        refined = None
    return (refined if refined is not None else rect), "rahmen"
