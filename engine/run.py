"""Phase-1-Prototyp: Karten im Kamerabild/Video erkennen, verfolgen, identifizieren, Debug-Overlay anzeigen.

    python run.py --source 0                              # Kamera-Index
    python run.py --source "cam:Elgato Virtual Camera"    # Kamera per DirectShow-Name
    python run.py --source aufnahme.mp4 --mode table
    python run.py --source 0 --grab 40 --interval 1.5     # ohne Fenster: 40 Testbilder speichern

Tasten im Fenster: s = Bild speichern, d = Detektionen ein/aus, q = Ende
"""
from __future__ import annotations

import argparse
import os
import time

import cv2

from cardengine import debug_overlay
from cardengine.overlay import OverlayRenderer
from cardengine.detection import ClassicalDetector
from cardengine.pipeline import CardPipeline
from cardengine.tracking import TrackerConfig
from cardengine.video import open_source

HERE = os.path.dirname(os.path.abspath(__file__))
FRAMES_DIR = os.path.join(HERE, "data", "frames")


DEFAULT_DETECTOR = os.path.join(HERE, "models", "occ", "detector-yolo11n-obb.pt")


def build_detector(name: str):
    if name == "classical":
        return ClassicalDetector()
    if name == "default":
        from cardengine.detection.refine import RefinedDetector
        from cardengine.detection.yolo_obb import YoloObbDetector

        return RefinedDetector(YoloObbDetector(DEFAULT_DETECTOR, imgsz=640, conf=0.4))
    if name == "rfdetr":
        from cardengine.detection.rfdetr_seg import RfDetrSegDetector

        return RfDetrSegDetector(os.path.join(HERE, "models", "cardstream", "segmentation_model", "onnx", "model.onnx"))
    if name.endswith(".onnx") or name.endswith(".pt"):
        from cardengine.detection.yolo_obb import YoloObbDetector

        return YoloObbDetector(name)
    raise SystemExit(f"Unbekannter Detektor: {name}")


def build_identifier(enabled: bool):
    if not enabled:
        return None
    from cardengine.recognition.identifier import CardIdentifier

    return CardIdentifier.load_default()


def save_frame(frame, tag="frame") -> str:
    os.makedirs(FRAMES_DIR, exist_ok=True)
    path = os.path.join(FRAMES_DIR, f"{tag}-{time.strftime('%Y%m%d-%H%M%S')}-{int(time.time() * 1000) % 1000:03d}.jpg")
    cv2.imwrite(path, frame, [cv2.IMWRITE_JPEG_QUALITY, 95])
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="0")
    ap.add_argument("--mode", choices=["facecam", "table"], default="facecam")
    ap.add_argument("--detector", default="default",
                    help="default (YOLO11n-OBB + Eckenverfeinerung), rfdetr, classical oder Pfad zu einem YOLO-OBB-Modell")
    ap.add_argument("--detect-hz", type=float, default=8.0)
    ap.add_argument("--no-identify", action="store_true")
    ap.add_argument("--no-prices", action="store_true")
    ap.add_argument("--debug", action="store_true", help="Debug-Overlay (Roh-Detektionen, Track-Status, Messwerte)")
    ap.add_argument("--scale", type=float, default=1.0, help="Overlay-Skalierung")
    ap.add_argument("--grab", type=int, default=0, help="nur N Bilder speichern (ohne Fenster)")
    ap.add_argument("--interval", type=float, default=1.0)
    ap.add_argument("--max-frames", type=int, default=0, help="Abbruch nach N Frames (Benchmarks)")
    ap.add_argument("--headless", action="store_true")
    ap.add_argument("--save-vis", help="Ordner: annotierte Frames speichern (z. B. für headless-Läufe)")
    ap.add_argument("--save-every", type=int, default=1)
    args = ap.parse_args()

    cap = open_source(args.source)

    if args.grab:
        for i in range(args.grab):
            t_end = time.time() + args.interval
            ok, frame = cap.read()
            while ok and time.time() < t_end:  # Puffer leeren, damit das Bild aktuell ist
                ok, frame = cap.read()
            if not ok:
                raise SystemExit("Kein Bild von der Quelle")
            print(f"{i + 1}/{args.grab}: {save_frame(frame, 'grab')}  ({frame.shape[1]}x{frame.shape[0]})", flush=True)
        return

    cfg = TrackerConfig.facecam() if args.mode == "facecam" else TrackerConfig.table()
    prices = None
    if not args.no_identify and not args.no_prices:
        from cardengine.pricing.service import PriceService

        prices = PriceService().start()  # lädt Preisdateien + Zuordnungen im Hintergrund
    pipeline = CardPipeline(build_detector(args.detector), build_identifier(not args.no_identify), cfg, args.detect_hz, prices)
    overlay = OverlayRenderer(scale=args.scale, show_pending=args.debug)
    show_raw = True
    n, t_start = 0, time.perf_counter()
    frame_times = []
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            t0 = time.perf_counter()
            tracks = pipeline.process(frame)
            frame_times.append((time.perf_counter() - t0) * 1000)
            n += 1
            render = (args.save_vis and n % args.save_every == 0) or not args.headless
            if render:
                if args.debug:
                    vis = debug_overlay.draw(frame.copy(), tracks, pipeline.last_detections if show_raw else None, pipeline.stats)
                else:
                    vis = overlay.draw(frame.copy(), tracks)
            if args.save_vis and n % args.save_every == 0:
                os.makedirs(args.save_vis, exist_ok=True)
                cv2.imwrite(os.path.join(args.save_vis, f"vis-{n:05d}.jpg"), vis)
            if not args.headless:
                cv2.imshow("TCG Card Lens – Engine", vis)
                key = cv2.waitKey(1) & 0xFF
                if key == ord("q"):
                    break
                if key == ord("s"):
                    print("gespeichert:", save_frame(frame))
                if key == ord("d"):
                    show_raw = not show_raw
            if args.max_frames and n >= args.max_frames:
                break
    finally:
        cap.release()
        pipeline.close()
        cv2.destroyAllWindows()
        if n:
            dur = time.perf_counter() - t_start
            ft = sorted(frame_times)
            print(f"{n} Frames in {dur:.1f} s = {n / dur:.1f} fps | Hauptthread pro Frame: "
                  f"Median {ft[len(ft) // 2]:.1f} ms, p95 {ft[int(len(ft) * 0.95)]:.1f} ms | "
                  f"Detektion {pipeline.stats.detect_ms:.0f} ms @ {pipeline.stats.detect_hz:.1f} Hz")


if __name__ == "__main__":
    main()
