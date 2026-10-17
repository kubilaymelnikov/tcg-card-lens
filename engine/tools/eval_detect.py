"""Detektoren vergleichen: synthetische Szenen mit bekannten Ecken (Generator aus tools/synth.py, eigener Seed)
plus optional echte Kamerabilder (nur Laufzeit + Sichtprüfung, da ohne Label).

    python tools/eval_detect.py --n 200
    python tools/eval_detect.py --n 200 --images data/frames --save-vis data/vis
"""
from __future__ import annotations

import argparse
import glob
import os
import random
import sys
import time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "tools"))
from cardengine.detection import ClassicalDetector  # noqa: E402
from cardengine.geometry import iou_quads, order_corners  # noqa: E402
from synth import load_card_paths, make_sample  # noqa: E402

DETECTORS = {
    "classical": lambda: ClassicalDetector(),
    "occ-yolo11n-obb": lambda: __import__("cardengine.detection.yolo_obb", fromlist=["x"]).YoloObbDetector(
        os.path.join(HERE, "models", "occ", "detector-yolo11n-obb.pt"), imgsz=640, conf=0.4),
    "occ-yolo11n-obb+refine": lambda: __import__("cardengine.detection.refine", fromlist=["x"]).RefinedDetector(
        DETECTORS["occ-yolo11n-obb"]()),
    "cardstream-rfdetr-seg": lambda: __import__("cardengine.detection.rfdetr_seg", fromlist=["x"]).RfDetrSegDetector(
        os.path.join(HERE, "models", "cardstream", "segmentation_model", "onnx", "model.onnx"), conf=0.5),
}


def corner_error(pred: np.ndarray, gt: np.ndarray) -> float:
    """Mittlerer Eckfehler in Prozent der Kartenhöhe (beste der 4 Rotationen)."""
    h = np.linalg.norm(gt[1] - gt[2])
    return min(float(np.linalg.norm(np.roll(pred, k, 0) - gt, axis=1).mean()) for k in range(4)) / h * 100


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--seed", type=int, default=99)
    ap.add_argument("--detectors", default=",".join(DETECTORS))
    ap.add_argument("--images", help="Ordner mit echten Kamerabildern (nur Laufzeit/Sichtprüfung)")
    ap.add_argument("--save-vis")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    np.random.seed(args.seed)
    cards = load_card_paths()
    scenes = []
    for _ in range(args.n):
        img, lines = make_sample(cards, [], rng)
        h, w = img.shape[:2]
        gts = [order_corners(np.float32(line.split()[1:]).reshape(4, 2) * [w, h]) for line in lines]
        scenes.append((img, gts))
    n_gt = sum(len(g) for _, g in scenes)
    print(f"{args.n} synthetische Szenen, {n_gt} Karten (Facecam- und Tisch-Szenen gemischt)\n")

    real = sorted(glob.glob(os.path.join(args.images, "*.jpg"))) if args.images else []
    print(f"{'Detektor':24} {'Recall@0.75':>11} {'Recall@0.9':>10} {'Fehlalarme':>10} {'Eckfehler':>10} {'ms/Bild':>8}")
    for name in args.detectors.split(","):
        det = DETECTORS[name]()
        det.detect(scenes[0][0])  # Aufwärmen (GPU-Initialisierung)
        tp75 = tp90 = fp = 0
        errs, times = [], []
        for img, gts in scenes:
            t0 = time.perf_counter()
            preds = det.detect(img)
            times.append((time.perf_counter() - t0) * 1000)
            used = set()
            for gt in gts:
                best, bi = 0.0, -1
                for i, p in enumerate(preds):
                    if i in used:
                        continue
                    iou = iou_quads(p.corners, gt)
                    if iou > best:
                        best, bi = iou, i
                if best >= 0.75:
                    tp75 += 1
                    used.add(bi)
                    errs.append(corner_error(preds[bi].corners, gt))
                if best >= 0.9:
                    tp90 += 1
            fp += sum(1 for i, p in enumerate(preds) if i not in used and max((iou_quads(p.corners, g) for g in gts), default=0) < 0.3)
        print(f"{name:24} {tp75 / n_gt:>11.1%} {tp90 / n_gt:>10.1%} {fp:>10d} {np.mean(errs) if errs else float('nan'):>9.1f}% {np.median(times):>8.1f}")
        for path in real:
            img = cv2.imread(path)
            t0 = time.perf_counter()
            preds = det.detect(img)
            ms = (time.perf_counter() - t0) * 1000
            print(f"   echt {os.path.basename(path)}: {len(preds)} Karten, {ms:.0f} ms, Scores {[round(p.score, 2) for p in preds]}")
            if args.save_vis:
                os.makedirs(args.save_vis, exist_ok=True)
                vis = img.copy()
                for p in preds:
                    cv2.polylines(vis, [p.corners.astype(np.int32)], True, (0, 255, 0), 3)
                cv2.imwrite(os.path.join(args.save_vis, f"{name}-{os.path.basename(path)}"), vis)


if __name__ == "__main__":
    main()
