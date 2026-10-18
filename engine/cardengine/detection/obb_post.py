"""Vor- und Nachverarbeitung für YOLO-OBB-Modelle im ONNX-Format – reines NumPy/OpenCV, ohne Ultralytics/PyTorch.

Nachgebaut nach Ultralytics 8.4 (Predict-Pfad), damit ONNX- und PyTorch-Backend dieselben Karten liefern:
- `letterbox`: seitenverhältnistreu skalieren (INTER_LINEAR), mit Grau 114 auffüllen; `auto=True` füllt nur bis zum
  nächsten Vielfachen von `stride` auf (wie Ultralytics bei .pt-Modellen und dynamischen ONNX-Modellen)
- `decode`: Ausgabe (4 + Klassen + 1, Anker) = Mitte x/y, Breite, Höhe (Pixel im Netzbild), Klassenwerte, Winkel (rad)
  → beste Klasse je Anker, Schwelle auf deren Wert
- `nms_rotated`: klassenweise „Fast-NMS“ mit probabilistischer IoU (ProbIoU, Gauß-Näherung gedrehter Boxen)
  – gleiche Semantik wie `TorchNMS.fast_nms(..., iou_func=batch_probiou)`: Eine Box fällt weg, sobald irgendeine
  höher bewertete Box (auch eine selbst verworfene) sie mit IoU > Schwelle überdeckt
- `scale_boxes`: zurück in Koordinaten des Originalbilds; `xywhr_to_corners`: vier Ecken
"""
from __future__ import annotations

import cv2
import numpy as np

MAX_WH = 7680  # Versatz je Klasse, damit sich Boxen verschiedener Klassen nicht unterdrücken
MAX_NMS = 30000


def letterbox(img: np.ndarray, new_shape: int | tuple[int, int] = 640, auto: bool = True, stride: int = 32,
              pad_value: int = 114) -> tuple[np.ndarray, tuple[float, float], tuple[int, int]]:
    """Bild (BGR, uint8) auf Netzgröße bringen. Liefert Bild, (gain_y, gain_x) und (pad_x, pad_y) links/oben."""
    if isinstance(new_shape, int):
        new_shape = (new_shape, new_shape)
    h, w = img.shape[:2]
    r = min(new_shape[0] / h, new_shape[1] / w)
    new_w, new_h = round(w * r), round(h * r)
    dw, dh = new_shape[1] - new_w, new_shape[0] - new_h
    if auto:
        dw, dh = dw % stride, dh % stride
    dw /= 2
    dh /= 2
    if (w, h) != (new_w, new_h):
        img = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    top, bottom = round(dh - 0.1), round(dh + 0.1)
    left, right = round(dw - 0.1), round(dw + 0.1)
    img = cv2.copyMakeBorder(img, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(pad_value,) * 3)
    return img, (new_h / h, new_w / w), (left, top)


def to_input(img: np.ndarray) -> np.ndarray:
    """Letterbox-Bild (BGR, HWC, uint8) → Netzeingabe (1, 3, H, W) RGB float32 0..1."""
    return np.ascontiguousarray(img[..., ::-1].transpose(2, 0, 1))[None].astype(np.float32) / 255.0


def decode(pred: np.ndarray, conf: float, nc: int) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Rohausgabe eines Bildes (4 + nc + 1, N) → Boxen (M, 5) xywhr, Scores (M,), Klassen (M,) mit Score > conf."""
    pred = np.asarray(pred, np.float32)
    if pred.ndim != 2 or pred.shape[0] != 4 + nc + 1:
        raise ValueError(f"unerwartete OBB-Ausgabe {pred.shape} für {nc} Klassen")
    cls_scores = pred[4:4 + nc]
    best = cls_scores.argmax(0)
    score = cls_scores[best, np.arange(pred.shape[1])]
    keep = score > conf
    boxes = np.concatenate([pred[:4, keep], pred[4 + nc:4 + nc + 1, keep]], 0).T  # (M, 5) x, y, w, h, Winkel
    return boxes.astype(np.float32), score[keep].astype(np.float32), best[keep].astype(np.int64)


def _covariance(boxes: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    a = boxes[:, 2] ** 2 / 12
    b = boxes[:, 3] ** 2 / 12
    c = boxes[:, 4]
    cos, sin = np.cos(c), np.sin(c)
    return a * cos ** 2 + b * sin ** 2, a * sin ** 2 + b * cos ** 2, (a - b) * cos * sin


def probiou_matrix(b1: np.ndarray, b2: np.ndarray, eps: float = 1e-7) -> np.ndarray:
    """Probabilistische IoU (Hellinger-Abstand zweier Gaußverteilungen) zwischen (N, 5) und (M, 5) xywhr → (N, M)."""
    b1 = np.asarray(b1, np.float64)
    b2 = np.asarray(b2, np.float64)
    x1, y1 = b1[:, 0:1], b1[:, 1:2]
    x2, y2 = b2[None, :, 0], b2[None, :, 1]
    a1, bb1, c1 = (v[:, None] for v in _covariance(b1))
    a2, bb2, c2 = (v[None, :] for v in _covariance(b2))
    num = (a1 + a2) * (bb1 + bb2) - (c1 + c2) ** 2
    t1 = ((a1 + a2) * (y1 - y2) ** 2 + (bb1 + bb2) * (x1 - x2) ** 2) / (num + eps) * 0.25
    t2 = ((c1 + c2) * (x2 - x1) * (y1 - y2)) / (num + eps) * 0.5
    det = np.clip(a1 * bb1 - c1 ** 2, 0, None) * np.clip(a2 * bb2 - c2 ** 2, 0, None)
    t3 = np.log(num / (4 * np.sqrt(det) + eps) + eps) * 0.5
    bd = np.clip(t1 + t2 + t3, eps, 100.0)
    hd = np.sqrt(1.0 - np.exp(-bd) + eps)
    return 1.0 - hd


def nms_rotated(boxes: np.ndarray, scores: np.ndarray, classes: np.ndarray | None = None, iou: float = 0.7,
                max_det: int = 300, agnostic: bool = False) -> np.ndarray:
    """Indizes der behaltenen Boxen, nach Score absteigend (Fast-NMS mit ProbIoU, klassenweise)."""
    n = len(boxes)
    if n == 0:
        return np.zeros(0, np.int64)
    order = np.argsort(-scores, kind="stable")
    if n > MAX_NMS:
        order = order[:MAX_NMS]
    b = np.asarray(boxes, np.float64)[order].copy()
    if classes is not None and not agnostic:
        b[:, :2] += np.asarray(classes, np.float64)[order, None] * MAX_WH
    ious = np.triu(probiou_matrix(b, b), k=1)
    keep = (ious > iou).sum(0) <= 0
    return order[keep][:max_det]


def scale_boxes(boxes: np.ndarray, gain: tuple[float, float], pad: tuple[int, int]) -> np.ndarray:
    """xywhr-Boxen aus dem Netzbild ins Originalbild (Mitte um den Rand verschieben, Maße durch den Faktor)."""
    out = np.asarray(boxes, np.float32).copy()
    gy, gx = gain
    out[:, 0] = (out[:, 0] - pad[0]) / gx
    out[:, 1] = (out[:, 1] - pad[1]) / gy
    out[:, 2] /= gx
    out[:, 3] /= gy
    return out


def xywhr_to_corners(boxes: np.ndarray) -> np.ndarray:
    """(N, 5) Mitte/Breite/Höhe/Winkel → (N, 4, 2) Ecken (Reihenfolge wie Ultralytics `xywhr2xyxyxyxy`)."""
    boxes = np.asarray(boxes, np.float32)
    ctr = boxes[:, :2]
    w, h, a = boxes[:, 2:3], boxes[:, 3:4], boxes[:, 4:5]
    cos, sin = np.cos(a), np.sin(a)
    v1 = np.concatenate([w / 2 * cos, w / 2 * sin], -1)
    v2 = np.concatenate([-h / 2 * sin, h / 2 * cos], -1)
    return np.stack([ctr + v1 + v2, ctr + v1 - v2, ctr - v1 - v2, ctr - v1 + v2], -2)


def postprocess(pred: np.ndarray, nc: int, conf: float, iou: float, gain: tuple[float, float], pad: tuple[int, int],
                max_det: int = 300) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Rohausgabe eines Bildes → Ecken (K, 4, 2) im Originalbild, Scores (K,), Klassen (K,)."""
    boxes, scores, classes = decode(pred, conf, nc)
    keep = nms_rotated(boxes, scores, classes, iou=iou, max_det=max_det)
    boxes = scale_boxes(boxes[keep], gain, pad)
    return xywhr_to_corners(boxes), scores[keep], classes[keep]
