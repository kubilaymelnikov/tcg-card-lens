"""Eckenverfeinerung: Ein Detektor mit gedrehten Rechtecken (OBB) kennt keine Perspektive. Im Ausschnitt um jede
Detektion wird der echte Kartenumriss per Kanten/Konturen gesucht (klassischer Detektor auf dem Ausschnitt).
Nur wenn der gefundene Umriss gut zur Detektion passt, ersetzt er die Ecken – sonst bleibt die Detektion.
"""
from __future__ import annotations

import numpy as np

from ..geometry import iou_quads
from .base import Detection, Detector
from .classical import ClassicalDetector


class RefinedDetector(Detector):
    def __init__(self, base: Detector, min_iou: float = 0.85, margin: float = 0.15):
        self.base = base
        self.name = f"{base.name}+refine"
        self.min_iou = min_iou
        self.margin = margin
        # Im Ausschnitt ist die Karte groß → Mindestfläche relativ zum Ausschnitt anheben
        self.local = ClassicalDetector(max_side=640, min_area_frac=0.25, ratio_tol=0.18)

    def detect(self, frame: np.ndarray, conf: float | None = None) -> list[Detection]:
        dets = self.base.detect(frame) if conf is None else self.base.detect(frame, conf=conf)
        h, w = frame.shape[:2]
        for d in dets:
            q = d.corners
            lo = q.min(axis=0)
            hi = q.max(axis=0)
            pad = (hi - lo) * self.margin
            x0, y0 = np.maximum(lo - pad, 0).astype(int)
            x1, y1 = np.minimum(hi + pad, [w, h]).astype(int)
            if x1 - x0 < 20 or y1 - y0 < 20:
                continue
            local = self.local.detect(frame[y0:y1, x0:x1])
            best, best_iou = None, 0.0
            for cand in local:
                c = cand.corners + np.float32([x0, y0])
                iou = iou_quads(c, q)
                if iou > best_iou:
                    best, best_iou = c, iou
            if best is not None and best_iou >= self.min_iou:
                d.corners = best.astype(np.float32)
                d.extra["refined"] = True
        return dets
