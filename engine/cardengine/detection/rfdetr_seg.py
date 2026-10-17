"""RF-DETR-Seg-Kartendetektor (ONNX) – Gewichte von Ximilar cardstream, Apache-2.0.

Export-Vertrag (siehe cardstream model/README.md): Eingang `input` (1,3,S,S), einfache Größenänderung, /255,
ImageNet-Normierung. Ausgänge nach Name: `dets` (1,Q,4) cxcywh normiert, `labels` (1,Q,C+1) Logits mit
„kein Objekt“ als letzter Spalte, `masks` (1,Q,S/4,S/4) Masken-Logits je Abfrage über das ganze Bild.
Anders als cardstream (nur die beste Karte) werden hier alle Abfragen über der Schwelle ausgewertet.
Ecken: größte Kontur der Maske → Polygonvereinfachung auf 4 Punkte (Ansatz aus cardstream core/quad.py).
"""
from __future__ import annotations

import cv2
import numpy as np

from ..geometry import order_corners, quad_area
from .base import Detection, Detector

MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)


def mask_to_quad(mask: np.ndarray) -> np.ndarray | None:
    contours, _ = cv2.findContours(mask.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None
    c = max(contours, key=cv2.contourArea)
    if cv2.contourArea(c) <= 0:
        return None
    hull = cv2.convexHull(c)
    peri = cv2.arcLength(hull, True)
    for eps in np.arange(0.01, 0.10, 0.005):
        approx = cv2.approxPolyDP(hull, float(eps) * peri, True)
        if len(approx) == 4:
            return approx.reshape(4, 2).astype(np.float32)
    return cv2.boxPoints(cv2.minAreaRect(hull)).astype(np.float32)


class RfDetrSegDetector(Detector):
    name = "rfdetr-seg"

    def __init__(self, model_path: str, conf: float = 0.5, providers: list[str] | None = None):
        import onnxruntime as ort

        self.session = ort.InferenceSession(
            model_path, providers=providers or ["CUDAExecutionProvider", "CPUExecutionProvider"]
        )
        inp = self.session.get_inputs()[0]
        self.input_name = inp.name
        self.size = int(inp.shape[2]) if isinstance(inp.shape[2], int) else 312
        self.output_names = [o.name for o in self.session.get_outputs()]
        self.conf = conf

    def detect(self, frame: np.ndarray) -> list[Detection]:
        h, w = frame.shape[:2]
        rgb = cv2.cvtColor(cv2.resize(frame, (self.size, self.size), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB)
        blob = ((rgb.astype(np.float32) / 255.0 - MEAN) / STD).transpose(2, 0, 1)[None]
        out = dict(zip(self.output_names, self.session.run(None, {self.input_name: blob})))
        logits = out["labels"][0][:, :-1]
        scores = 1 / (1 + np.exp(-logits.max(axis=1)))
        dets: list[Detection] = []
        for q in np.flatnonzero(scores >= self.conf):
            m = cv2.resize(out["masks"][0][q].astype(np.float32), (w, h), interpolation=cv2.INTER_LINEAR) > 0
            quad = mask_to_quad(m)
            if quad is None or quad_area(quad) < 0.002 * w * h:
                continue
            dets.append(Detection(corners=order_corners(quad), score=float(scores[q])))
        # Doppelte Abfragen für dieselbe Karte zusammenfassen (DETR braucht normalerweise kein NMS)
        dets.sort(key=lambda d: d.score, reverse=True)
        kept: list[Detection] = []
        from ..geometry import iou_quads

        for d in dets:
            if all(iou_quads(d.corners, k.corners) < 0.6 for k in kept):
                kept.append(d)
        return kept
