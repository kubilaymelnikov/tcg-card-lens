"""YOLO-OBB-Kartendetektor auf dem synthetischen Datensatz trainieren und nach ONNX exportieren.

    python tools/train_detector.py --data data/synth/data.yaml --model yolo26n-obb.pt --epochs 40

Ergebnis: models/card-obb.pt und models/card-obb.onnx (+ Ultralytics-Laufordner unter data/runs).
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cardengine.paths import DATA_DIR, MODELS_DIR  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.path.join(DATA_DIR, "synth", "data.yaml"))
    ap.add_argument("--model", default="yolo26n-obb.pt")
    ap.add_argument("--epochs", type=int, default=40)
    ap.add_argument("--imgsz", type=int, default=960)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--name", default="card-obb")
    args = ap.parse_args()

    from ultralytics import YOLO

    os.makedirs(MODELS_DIR, exist_ok=True)
    model = YOLO(args.model)
    model.train(
        data=args.data, epochs=args.epochs, imgsz=args.imgsz, batch=args.batch, device=0, workers=4,
        project=os.path.join(DATA_DIR, "runs"), name=args.name, exist_ok=True,
        # Geometrie-Augmentierung macht der Generator selbst; Ultralytics ergänzt Farbe/Mosaic.
        degrees=0, fliplr=0.0, flipud=0.0, mosaic=0.5, close_mosaic=5, patience=15, plots=True,
    )
    best = os.path.join(DATA_DIR, "runs", args.name, "weights", "best.pt")
    target = os.path.join(MODELS_DIR, f"{args.name}.pt")
    shutil.copy(best, target)
    onnx = YOLO(target).export(format="onnx", imgsz=args.imgsz, dynamic=False, simplify=True, opset=17)
    shutil.move(onnx, os.path.join(MODELS_DIR, f"{args.name}.onnx"))
    print("fertig:", target)


if __name__ == "__main__":
    main()
