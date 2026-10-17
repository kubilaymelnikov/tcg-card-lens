"""Ruhe des Overlays messen: Wie stark zittern die angezeigten Ecken bei unbewegtem Bild?

    python tools/eval_stability.py                       # Standbild (synthetisch oder --image) als „Video“
    python tools/eval_stability.py --image data/obs-test-cards.jpg --seconds 6

Kennzahlen je Track (über die zweite Hälfte des Laufs, also nach dem Einschwingen):
- Zittern: mittlere Standardabweichung der angezeigten Ecken in Pixeln (Ziel: < 0,5 px)
- Sprünge: größte Bewegung einer Ecke von einem Frame zum nächsten
- Detektionen: wie oft der Detektor tatsächlich lief (bei Standbild soll das stark sinken)
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from cardengine.pipeline import CardPipeline  # noqa: E402
from cardengine.tracking import TrackerConfig  # noqa: E402
from run import build_detector  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default=os.path.join(HERE, "data", "obs-test-cards.jpg"))
    ap.add_argument("--seconds", type=float, default=6.0)
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--mode", default="table")
    ap.add_argument("--noise", type=float, default=2.0, help="Sensorrauschen (σ) je Frame, wie bei einer echten Kamera")
    args = ap.parse_args()

    base = cv2.imread(args.image)
    cfg = TrackerConfig.table() if args.mode == "table" else TrackerConfig.facecam()
    pipe = CardPipeline(build_detector("default"), None, cfg, detect_hz=8.0)
    rng = np.random.default_rng(0)
    hist: dict[int, list[np.ndarray]] = {}
    n = int(args.seconds * args.fps)
    t0 = time.perf_counter()
    detections = 0
    last_hz_frames = 0
    for i in range(n):
        frame = np.clip(base.astype(np.int16) + rng.normal(0, args.noise, base.shape), 0, 255).astype(np.uint8)
        before = pipe.stats.detect_ms, len(pipe._detect_times)
        tracks = pipe.process(frame, now=i / args.fps)
        time.sleep(max(0.0, (i + 1) / args.fps - (time.perf_counter() - t0)))
        for t in tracks:
            hist.setdefault(t.id, []).append(t.display.copy())
    final = {t.id: t for t in pipe.tracker.tracks}
    pipe.close()
    print(f"{n} Frames, Detektor-Durchläufe: {pipe.stats.detect_runs if hasattr(pipe.stats, 'detect_runs') else 'n/a'}")
    print(f"{'Track':>5} {'Frames':>6} {'Zittern px':>10} {'max. Sprung px':>14}")
    jit_all = []
    for tid, seq in sorted(hist.items()):
        if len(seq) < n // 3:
            continue
        a = np.stack(seq[-int(2 * args.fps):])  # letzte 2 Sekunden (nach dem Einschwingen)
        full = np.stack(seq)
        conv_jump = float(np.abs(np.diff(full, axis=0)).max()) if len(full) > 1 else 0.0
        jitter = float(a.std(axis=0).mean())
        jump = float(np.abs(np.diff(a, axis=0)).max()) if len(a) > 1 else 0.0
        jit_all.append(jitter)
        t = final.get(tid)
        info = "" if t is None else (f"  lock={t.locked} fertig={t.finalized} kanten={t.edge_refined} "
                                      f"proben={len(t.samples)} entsperrt={t.unlocks} bewegung={t.motion:.2f}")
        print(f"{tid:>5} {len(seq):>6} {jitter:>10.2f} {jump:>14.2f}  (größter Sprung gesamt {conv_jump:.2f}){info}")
    if jit_all:
        print(f"Mittleres Zittern: {np.mean(jit_all):.2f} px")


if __name__ == "__main__":
    main()
