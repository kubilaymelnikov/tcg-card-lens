"""Verfügbare Kameras (DirectShow-Indizes) mit Auflösung auflisten.

    python tools/list_cameras.py
"""
import cv2

for idx in range(8):
    cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap.release()
        continue
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 3840)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 2160)
    ok, frame = cap.read()
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    mean = float(frame.mean()) if ok else -1
    print(f"Index {idx}: {w}x{h} @ {fps:.0f} fps, Bild gelesen: {ok}, mittlere Helligkeit {mean:.0f}")
    cap.release()
