"""YOLO-OBB-Detektor (gedrehte Boxen) – PyTorch-Gewichte (.pt) über Ultralytics oder exportiertes ONNX-Modell.

Lizenzhinweis: Ultralytics und damit trainierte Gewichte stehen unter AGPL-3.0 (siehe docs/ARCHITEKTUR.md).
Die Schnittstelle (4 Ecken + Score + Klasse) ist modellunabhängig, damit das Modell später ersetzt werden kann.
"""
from __future__ import annotations

import numpy as np

from ..geometry import order_corners
from .base import Detection, Detector

CLASS_NAMES = {0: "mtg", 1: "pokemon"}


class YoloObbDetector(Detector):
    name = "yolo-obb"

    def __init__(self, weights: str, imgsz: int = 960, conf: float = 0.35, device: str | int = 0, half: bool = True):
        from ultralytics import YOLO

        self.model = YOLO(weights, task="obb")
        self.imgsz = imgsz
        self.conf = conf
        self.device = device
        self.half = half and str(weights).endswith(".pt")

    def detect(self, frame: np.ndarray, conf: float | None = None) -> list[Detection]:
        """`conf` überschreibt die Schwelle für diesen Aufruf (gezielte Suche im Dock)."""
        res = self.model.predict(frame, imgsz=self.imgsz, conf=self.conf if conf is None else conf,
                                 device=self.device, verbose=False)[0]
        if res.obb is None or len(res.obb) == 0:
            return []
        corners = res.obb.xyxyxyxy.cpu().numpy()  # (N, 4, 2)
        scores = res.obb.conf.cpu().numpy()
        classes = res.obb.cls.cpu().numpy().astype(int)
        return [
            Detection(corners=order_corners(c), score=float(s), game=CLASS_NAMES.get(k))
            for c, s, k in zip(corners, scores, classes)
        ]
