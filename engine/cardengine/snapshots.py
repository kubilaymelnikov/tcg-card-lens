"""Debug-Snapshots: Kamerabild + alle Erkennungsdaten, für Auswertung und Nachtraining.

- manuell (Button im OBS-Filter) oder automatisch bei auffälligen Ereignissen (unsicher, fehlgeschlagen, Tausch)
- Rate-Limit je Grund, damit eine dauerhaft unsichere Karte nicht den Speicher füllt
- Gesamtgröße begrenzt: Beim Überschreiten werden die ältesten Snapshots gelöscht
"""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, is_dataclass

import cv2
import numpy as np

from . import __version__
from .paths import DATA_DIR

ROOT = os.path.join(DATA_DIR, "snapshots")
MIN_INTERVAL_S = {"manuell": 0.0, "unsicher": 20.0, "fehlgeschlagen": 20.0, "tausch": 10.0, "erkannt": 120.0}


def _safe(s: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in s)[:40] or "quelle"


def _jsonable(o):
    if is_dataclass(o):
        return {k: _jsonable(v) for k, v in asdict(o).items()}
    if isinstance(o, np.ndarray):
        return o.round(1).tolist()
    if isinstance(o, dict):
        return {k: _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    if isinstance(o, (np.floating, np.integer)):
        return o.item()
    return o


class SnapshotStore:
    def __init__(self, source: str, max_mb: float = 500.0, root: str = ROOT):
        self.dir = os.path.join(root, _safe(source))
        self.max_bytes = int(max_mb * 1024 * 1024)
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def save(self, frame: np.ndarray, reason: str, meta: dict, force: bool = False) -> str | None:
        """Speichert (asynchron) Bild + Metadaten. Liefert den Basis-Pfad oder None (Rate-Limit)."""
        now = time.time()
        with self._lock:
            if not force and now - self._last.get(reason, 0) < MIN_INTERVAL_S.get(reason, 30.0):
                return None
            self._last[reason] = now
        day = time.strftime("%Y-%m-%d")
        base = os.path.join(self.dir, day, f"{time.strftime('%H%M%S')}-{int(now * 1000) % 1000:03d}-{reason}")
        frame = frame.copy()
        meta = {"reason": reason, "time": time.strftime("%Y-%m-%dT%H:%M:%S"), "engine": __version__, **meta}
        threading.Thread(target=self._write, args=(base, frame, meta), daemon=True).start()
        return base

    def _write(self, base: str, frame: np.ndarray, meta: dict) -> None:
        os.makedirs(os.path.dirname(base), exist_ok=True)
        cv2.imwrite(base + ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 92])
        with open(base + ".json", "w", encoding="utf-8") as f:
            json.dump(_jsonable(meta), f, ensure_ascii=False, indent=1, default=str)
        self.enforce_limit()

    def enforce_limit(self) -> None:
        files = []
        for dirpath, _, names in os.walk(self.dir):
            for n in names:
                p = os.path.join(dirpath, n)
                files.append((os.path.getmtime(p), os.path.getsize(p), p))
        total = sum(s for _, s, _ in files)
        for _, size, path in sorted(files):
            if total <= self.max_bytes:
                break
            try:
                os.remove(path)
                total -= size
            except OSError:
                pass
