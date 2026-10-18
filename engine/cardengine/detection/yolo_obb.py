"""YOLO-OBB-Detektor (gedrehte Boxen) – zwei gleichwertige Wege:

- `OnnxObbDetector` (Standard): exportiertes ONNX-Modell über ONNX Runtime (CUDA → DirectML → CPU), Vor- und
  Nachverarbeitung in NumPy/OpenCV (`obb_post`), kein PyTorch nötig
- `YoloObbDetector`: PyTorch-Gewichte (.pt) über Ultralytics (Entwicklung, Training, Export)

`create_obb_detector()` wählt nach dem Backend (siehe cardengine.backend).

Lizenzhinweis: Ultralytics und damit trainierte Gewichte stehen unter AGPL-3.0 (siehe docs/ARCHITEKTUR.md und
NOTICE.md) – das gilt auch für die daraus exportierte ONNX-Datei.
Die Schnittstelle (4 Ecken + Score + Klasse) ist modellunabhängig, damit das Modell später ersetzt werden kann.
"""
from __future__ import annotations

import numpy as np

from ..geometry import order_corners
from . import obb_post
from .base import Detection, Detector

CLASS_NAMES = {0: "mtg", 1: "pokemon"}
DEFAULT_IMGSZ = 640
NMS_IOU = 0.7  # Ultralytics-Standard (cfg iou)
MAX_DET = 300


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


class OnnxObbDetector(Detector):
    """YOLO-OBB als ONNX (von Ultralytics exportiert, Ausgabe ohne eingebautes NMS)."""

    name = "yolo-obb"

    def __init__(self, model_path: str, imgsz: int = DEFAULT_IMGSZ, conf: float = 0.35, iou: float = NMS_IOU,
                 max_det: int = MAX_DET, provider: str | None = None):
        from ..backend import OnnxRunner

        self.runner = OnnxRunner(model_path, provider)
        self.provider = self.runner.provider
        shape = self.runner.input_shape  # [1, 3, 640, 640] oder dynamisch ['batch', 3, 'height', 'width']
        self.dynamic = not all(isinstance(d, int) for d in shape[2:])
        self.imgsz = imgsz if self.dynamic else (int(shape[2]), int(shape[3]))
        meta = self.runner.session.get_modelmeta().custom_metadata_map
        self.stride = int(float(meta.get("stride", 32)))
        self.names = dict(CLASS_NAMES)  # wie der PyTorch-Weg (Klassennamen der Modell-Metadaten ungenutzt)
        self.conf = conf
        self.iou = iou
        self.max_det = max_det

    def raw(self, frame: np.ndarray, conf: float | None = None) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Ecken (K, 4, 2) in Ultralytics-Reihenfolge, Scores, Klassen – für Vergleiche mit dem PyTorch-Weg."""
        img, gain, pad = obb_post.letterbox(frame, self.imgsz, auto=self.dynamic, stride=self.stride)
        pred = self.runner.run(obb_post.to_input(img))[0][0]
        nc = pred.shape[0] - 5
        return obb_post.postprocess(pred, nc, self.conf if conf is None else conf, self.iou, gain, pad, self.max_det)

    def detect(self, frame: np.ndarray, conf: float | None = None) -> list[Detection]:
        """`conf` überschreibt die Schwelle für diesen Aufruf (gezielte Suche im Dock)."""
        corners, scores, classes = self.raw(frame, conf)
        return [
            Detection(corners=order_corners(c), score=float(s), game=self.names.get(int(k)))
            for c, s, k in zip(corners, scores, classes)
        ]


def create_obb_detector(conf: float = 0.4, imgsz: int = DEFAULT_IMGSZ, backend: str | None = None) -> Detector:
    """Standard-Kartendetektor (occ YOLO11n-OBB) für das gewählte Backend; lädt fehlende Modelle nicht nach
    (dafür `models_fetch.ensure_models`)."""
    from ..backend import resolve_backend
    from ..models_fetch import DETECTOR_PATH, onnx_path

    if resolve_backend(backend) == "torch":
        return YoloObbDetector(DETECTOR_PATH, imgsz=imgsz, conf=conf)
    return OnnxObbDetector(onnx_path("detector"), imgsz=imgsz, conf=conf)
