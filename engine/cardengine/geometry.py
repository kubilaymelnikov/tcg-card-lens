"""Geometrie für Kartenvierecke: Ecken ordnen, entzerren, Kennzahlen."""
from __future__ import annotations

import cv2
import numpy as np

# Standard-Kartenformat 63 × 88 mm (Magic und Pokémon identisch)
CARD_RATIO = 63 / 88
RECT_W, RECT_H = 488, 680  # entzerrte Karte (entspricht Scryfall "normal")


def order_corners(pts: np.ndarray) -> np.ndarray:
    """Ecken im Uhrzeigersinn ab oben links ordnen; erste Kante ist eine kurze Kante (Hochformat).

    Welche der beiden kurzen Kanten "oben" ist, bleibt offen (180°-Mehrdeutigkeit) –
    das entscheidet erst die Identifikation.
    """
    pts = np.asarray(pts, dtype=np.float32).reshape(4, 2)
    c = pts.mean(axis=0)
    ang = np.arctan2(pts[:, 1] - c[1], pts[:, 0] - c[0])
    pts = pts[np.argsort(ang)]
    start = int(np.argmin(pts.sum(axis=1)))
    pts = np.roll(pts, -start, axis=0)
    if np.linalg.norm(pts[0] - pts[1]) > np.linalg.norm(pts[1] - pts[2]):
        pts = np.roll(pts, -1, axis=0)
    return pts


def side_lengths(q: np.ndarray) -> tuple[float, float]:
    """(mittlere kurze Seite, mittlere lange Seite)."""
    short = (np.linalg.norm(q[0] - q[1]) + np.linalg.norm(q[2] - q[3])) / 2
    long = (np.linalg.norm(q[1] - q[2]) + np.linalg.norm(q[3] - q[0])) / 2
    return float(short), float(long)


def quad_area(q: np.ndarray) -> float:
    return float(cv2.contourArea(np.asarray(q, dtype=np.float32)))


def rectify(frame: np.ndarray, quad: np.ndarray, rotate180: bool = False, margin: float = 0.0) -> np.ndarray:
    """Karte perspektivisch entzerren (RECT_W × RECT_H, optional mit Rand in Kartenanteilen)."""
    q = np.asarray(quad, dtype=np.float32)
    if rotate180:
        q = np.roll(q, 2, axis=0)
    mx, my = RECT_W * margin, RECT_H * margin
    dst = np.float32([[mx, my], [mx + RECT_W, my], [mx + RECT_W, my + RECT_H], [mx, my + RECT_H]])
    m = cv2.getPerspectiveTransform(q, dst)
    size = (int(round(RECT_W + 2 * mx)), int(round(RECT_H + 2 * my)))
    return cv2.warpPerspective(frame, m, size, flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def iou_quads(a: np.ndarray, b: np.ndarray) -> float:
    """IoU zweier konvexer Vierecke."""
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    inter, _ = cv2.intersectConvexConvex(a, b)
    union = quad_area(a) + quad_area(b) - inter
    return float(inter / union) if union > 0 else 0.0
