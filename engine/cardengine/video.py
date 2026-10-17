"""Videoeingänge: Kamera (Index oder DirectShow-Name), Videodatei oder Bildordner."""
from __future__ import annotations

import glob
import time
import os

import cv2


def open_source(source: str, width: int = 1920, height: int = 1080, warmup_s: float = 2.0):
    """`source`: "0" (Kameraindex), "cam:Elgato Virtual Camera" (DirectShow-Name), Videodatei oder Bildordner."""
    if os.path.isdir(source):
        return ImageFolder(source)
    if source.isdigit() or source.startswith("cam:"):
        if source.startswith("cam:"):
            cap = cv2.VideoCapture(f"video={source[4:]}", cv2.CAP_DSHOW)
        else:
            cap = cv2.VideoCapture(int(source), cv2.CAP_DSHOW)
            if not cap.isOpened():
                cap = cv2.VideoCapture(int(source), cv2.CAP_MSMF)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        # Keine Bildrate erzwingen: Die Elgato Virtual Camera fällt bei CAP_PROP_FPS=60 auf 1 fps zurück
        # (gemessen: ohne Vorgabe 76 fps).
        if not cap.isOpened():
            raise RuntimeError(f"Kamera {source!r} lässt sich nicht öffnen")
        # Virtuelle Kameras (z. B. Elgato Virtual Camera) liefern in der ersten ~1 s ein Platzhalterbild,
        # echte Kameras brauchen die Zeit für Belichtung/Weißabgleich → Anlaufphase verwerfen.
        t_end = time.time() + warmup_s
        while time.time() < t_end:
            cap.read()
        return cap
    cap = cv2.VideoCapture(source)
    if not cap.isOpened():
        raise RuntimeError(f"Video {source!r} lässt sich nicht öffnen")
    return cap


class ImageFolder:
    """Liefert die Bilder eines Ordners wie ein Video (zum Testen mit gespeicherten Kamerabildern)."""

    def __init__(self, folder: str):
        self.files = sorted(f for ext in ("jpg", "jpeg", "png") for f in glob.glob(os.path.join(folder, f"*.{ext}")))
        self.i = 0

    def read(self):
        if self.i >= len(self.files):
            return False, None
        img = cv2.imread(self.files[self.i])
        self.i += 1
        return img is not None, img

    def isOpened(self):
        return bool(self.files)

    def release(self):
        pass
