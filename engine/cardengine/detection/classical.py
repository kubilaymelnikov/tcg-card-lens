"""Klassischer Kartendetektor (Kanten/Konturen). Dient als Basislinie und Fallback ohne trainiertes Modell.

Bekannte Grenzen: braucht Kontrast zwischen Karte und Hintergrund, verliert Karten, deren Rand
verdeckt ist (Finger), und unterscheidet Magic/Pokémon nicht.
"""
from __future__ import annotations

import cv2
import numpy as np

from ..geometry import CARD_RATIO, order_corners, quad_area, side_lengths
from .base import Detection, Detector


class ClassicalDetector(Detector):
    name = "classical"

    def __init__(self, max_side: int = 960, min_area_frac: float = 0.003, ratio_tol: float = 0.14):
        self.max_side = max_side
        self.min_area_frac = min_area_frac
        self.ratio_tol = ratio_tol

    def detect(self, frame: np.ndarray) -> list[Detection]:
        h, w = frame.shape[:2]
        scale = min(1.0, self.max_side / max(h, w))
        small = cv2.resize(frame, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA) if scale < 1 else frame
        gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        edges = cv2.dilate(cv2.Canny(gray, 30, 100), kernel, iterations=2)
        found = self._collect(edges, scale)
        if not found:
            # Zweiter Versuch: Karte als Fläche gegen den Hintergrund (Otsu, beide Polaritäten)
            _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
            binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
            found = self._collect(binary, scale) + self._collect(255 - binary, scale)
        return _suppress_nested(found)

    def _collect(self, binary: np.ndarray, scale: float) -> list[Detection]:
        h, w = binary.shape[:2]
        contours, _ = cv2.findContours(binary, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        out: list[Detection] = []
        for cnt in contours:
            hull = cv2.convexHull(cnt)
            area = cv2.contourArea(hull)
            if area < w * h * self.min_area_frac or area > w * h * 0.9:
                continue
            approx = cv2.approxPolyDP(hull, 0.02 * cv2.arcLength(hull, True), True)
            pts = approx.reshape(-1, 2) if len(approx) == 4 else cv2.boxPoints(cv2.minAreaRect(hull))
            q = order_corners(pts)
            short, long = side_lengths(q)
            if long <= 0:
                continue
            ratio = short / long
            if not (CARD_RATIO * (1 - self.ratio_tol) <= ratio <= CARD_RATIO * (1 + self.ratio_tol)):
                continue
            qa = quad_area(q)
            if qa <= 0 or area / qa < 0.85:  # Kontur füllt das Viereck nicht -> kein Kartenumriss
                continue
            # Konfidenz: wie gut Seitenverhältnis und Füllgrad passen
            score = float(np.clip(1 - abs(ratio - CARD_RATIO) / (CARD_RATIO * self.ratio_tol), 0, 1) * min(1.0, area / qa))
            out.append(Detection(corners=q / scale, score=score))
        return out


def _suppress_nested(dets: list[Detection]) -> list[Detection]:
    """Innere Rahmen (Bildfenster, Textbox) und doppelte Umrisse verwerfen: größte Kontur gewinnt."""
    dets = sorted(dets, key=lambda d: quad_area(d.corners), reverse=True)
    kept: list[Detection] = []
    for d in dets:
        c = d.corners.mean(axis=0)
        if not any(cv2.pointPolygonTest(k.corners.astype(np.float32), (float(c[0]), float(c[1])), False) >= 0 for k in kept):
            kept.append(d)
    return kept
