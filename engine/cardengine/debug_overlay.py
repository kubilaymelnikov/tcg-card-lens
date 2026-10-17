"""Debug-Overlay mit OpenCV (Phase 1). Das endgültige Overlay entsteht in Phase 2/3."""
from __future__ import annotations

import cv2
import numpy as np

from .tracking import Track

COLORS = {  # BGR
    "mtg": (61, 138, 255),
    "pokemon": (63, 210, 255),
    None: (252, 211, 125),
}
STATE_COLORS = {"failed": (113, 113, 248), "uncertain": (36, 191, 251)}

_TRANSLIT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss", "é": "e", "É": "E"})


def _text(img, text, org, scale, color, thick=1):
    text = str(text).translate(_TRANSLIT).encode("ascii", "replace").decode()
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, (0, 0, 0), thick + 3, cv2.LINE_AA)
    cv2.putText(img, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale, color, thick, cv2.LINE_AA)


def draw(frame: np.ndarray, tracks: list[Track], detections=None, stats=None, show_raw: bool = True) -> np.ndarray:
    out = frame
    h = out.shape[0]
    s = max(0.5, h / 1080)
    if show_raw and detections:
        for d in detections:
            cv2.polylines(out, [d.corners.astype(np.int32)], True, (255, 255, 255), 1, cv2.LINE_AA)
    for t in tracks:
        identity = t.identity
        game = getattr(identity, "game", None) or t.game
        color = STATE_COLORS.get(t.state, COLORS.get(game, COLORS[None]))
        q = t.display.astype(np.int32)
        cv2.polylines(out, [q], True, color, max(2, round(3 * s)), cv2.LINE_AA)
        top = q[:, 1].min()
        left = q[:, 0].min()
        bottom = q[:, 1].max()
        label = getattr(identity, "label", None) or f"#{t.id} {t.state}"
        _text(out, label, (int(left), int(top - 10 * s)), 0.7 * s, color, max(1, round(2 * s)))
        if identity is not None:
            info = identity.detail_line() if hasattr(identity, "detail_line") else ""
            _text(out, info, (int(left), int(bottom + 25 * s)), 0.55 * s, (235, 235, 235), 1)
    if stats is not None:
        _text(
            out,
            f"Detektion {stats.detect_ms:.0f} ms @ {stats.detect_hz:.1f} Hz | Identifikation {stats.identify_ms:.0f} ms | Tracks {len(tracks)}",
            (int(12 * s), int(h - 14 * s)), 0.55 * s, (200, 255, 200), 1,
        )
    return out
