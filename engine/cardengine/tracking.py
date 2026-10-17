"""Tracking von Karten zwischen den (selteneren) Detektor-Durchläufen.

- Zwischen Detektionen: optischer Fluss (Lucas-Kanade) auf Merkmalen innerhalb jeder Karte; daraus eine
  Homographie, die die vier Ecken nachführt → Overlay folgt der Karte bei voller Bildrate.
- Bei jeder Detektion: Zuordnung per IoU (Ungarische Methode wäre möglich; gierig reicht bei wenigen Karten),
  Ecken werden zur Detektion hin korrigiert, ohne zu springen.
- Tracks überleben kurze Aussetzer (Hand vor der Karte, Bewegungsunschärfe), bevor sie verschwinden.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import cv2
import numpy as np

from .detection.base import Detection
from .geometry import iou_quads, order_corners


@dataclass
class TrackerConfig:
    # Facecam: schnell bewegte Karten → kurzer Puffer, starke Nachführung. Tisch: ruhig, viele Karten.
    flow_scale: float = 0.5  # optischer Fluss auf verkleinertem Bild
    match_iou: float = 0.25
    lost_after_s: float = 1.2  # so lange ohne Bestätigung sichtbar bleiben
    detection_weight: float = 0.6  # wie stark eine neue Detektion die geschätzten Ecken korrigiert
    smoothing: float = 0.5  # Glättung der angezeigten Ecken (0 = keine)

    @classmethod
    def facecam(cls) -> "TrackerConfig":
        return cls(lost_after_s=1.0, detection_weight=0.7, smoothing=0.35)

    @classmethod
    def table(cls) -> "TrackerConfig":
        return cls(lost_after_s=2.5, detection_weight=0.5, smoothing=0.6)


_ids = itertools.count(1)


@dataclass
class Track:
    corners: np.ndarray  # geschätzte Ecken (Bildkoordinaten)
    display: np.ndarray  # geglättete Ecken für die Anzeige
    score: float
    game: str | None
    first_seen: float
    last_confirmed: float
    id: int = field(default_factory=lambda: next(_ids))
    hits: int = 1
    points: np.ndarray | None = None  # Merkmalspunkte für den optischen Fluss (verkleinerte Koordinaten)
    # Identifikation (wird von card-recognition gesetzt und am Track gecacht)
    identity: object | None = None
    state: str = "new"  # new | identifying | identified | uncertain | failed
    attempts: int = 0
    next_attempt: float = 0.0
    signature: np.ndarray | None = None  # kleines Graubild der Karte zur Änderungserkennung (Tisch-Modus)

    @property
    def stable(self) -> bool:
        return self.hits >= 2


class CardTracker:
    def __init__(self, config: TrackerConfig | None = None):
        self.cfg = config or TrackerConfig()
        self.tracks: list[Track] = []
        self._prev_gray: np.ndarray | None = None

    # ---------- jeder Frame ----------
    def step(self, frame: np.ndarray, now: float) -> list[Track]:
        """Ecken aller Tracks per optischem Fluss nachführen und abgelaufene Tracks entfernen."""
        s = self.cfg.flow_scale
        gray = cv2.cvtColor(cv2.resize(frame, None, fx=s, fy=s, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
        if self._prev_gray is not None and self._prev_gray.shape == gray.shape:
            for t in self.tracks:
                self._flow(t, self._prev_gray, gray)
        self._prev_gray = gray
        self.tracks = [t for t in self.tracks if now - t.last_confirmed <= self.cfg.lost_after_s]
        for t in self.tracks:
            a = self.cfg.smoothing
            t.display = (a * t.display + (1 - a) * t.corners).astype(np.float32)
        return self.tracks

    def _flow(self, t: Track, prev: np.ndarray, cur: np.ndarray) -> None:
        s = self.cfg.flow_scale
        if t.points is None or len(t.points) < 8:
            t.points = _features_in(prev, t.corners * s)
            if t.points is None:
                return
        nxt, status, _ = cv2.calcOpticalFlowPyrLK(prev, cur, t.points, None, winSize=(21, 21), maxLevel=3)
        if nxt is None:
            t.points = None
            return
        good_old = t.points[status.ravel() == 1]
        good_new = nxt[status.ravel() == 1]
        if len(good_new) < 6:
            t.points = None
            return
        h, inliers = cv2.findHomography(good_old, good_new, cv2.RANSAC, 3.0)
        if h is None or inliers is None or inliers.sum() < 6:
            t.points = None
            return
        moved = cv2.perspectiveTransform((t.corners * s).reshape(-1, 1, 2).astype(np.float32), h).reshape(4, 2) / s
        # Plausibilität: keine Sprünge um mehr als die halbe Kartendiagonale pro Frame
        if np.linalg.norm(moved - t.corners, axis=1).max() < np.linalg.norm(t.corners[0] - t.corners[2]) * 0.5:
            t.corners = moved.astype(np.float32)
        t.points = good_new[inliers.ravel() == 1].reshape(-1, 1, 2)

    # ---------- bei jeder Detektion ----------
    def update(self, detections: list[Detection], now: float) -> list[Track]:
        pairs = sorted(
            ((iou_quads(t.corners, d.corners), ti, di) for ti, t in enumerate(self.tracks) for di, d in enumerate(detections)),
            reverse=True,
        )
        used_t, used_d = set(), set()
        for iou, ti, di in pairs:
            if iou < self.cfg.match_iou or ti in used_t or di in used_d:
                continue
            used_t.add(ti)
            used_d.add(di)
            t, d = self.tracks[ti], detections[di]
            w = self.cfg.detection_weight
            t.corners = (w * _align(d.corners, t.corners) + (1 - w) * t.corners).astype(np.float32)
            t.last_confirmed = now
            t.hits += 1
            t.score = d.score
            t.game = d.game or t.game
            t.points = None  # Merkmale neu suchen (Karte kann sich verändert haben)
        for di, d in enumerate(detections):
            if di not in used_d:
                c = d.corners.astype(np.float32)
                self.tracks.append(Track(corners=c, display=c.copy(), score=d.score, game=d.game, first_seen=now, last_confirmed=now))
        return self.tracks


def _align(new: np.ndarray, ref: np.ndarray) -> np.ndarray:
    """Eckenreihenfolge der Detektion an den Track anpassen (Rotation um 0/90/180/270°), damit nichts springt."""
    new = order_corners(new)
    best = min(range(4), key=lambda k: np.linalg.norm(np.roll(new, -k, axis=0) - ref))
    return np.roll(new, -best, axis=0)


def _features_in(gray: np.ndarray, quad: np.ndarray) -> np.ndarray | None:
    mask = np.zeros_like(gray)
    cv2.fillConvexPoly(mask, quad.astype(np.int32), 255)
    # Rand etwas einziehen, damit keine Hintergrundpunkte mitgetrackt werden
    mask = cv2.erode(mask, np.ones((5, 5), np.uint8))
    pts = cv2.goodFeaturesToTrack(gray, maxCorners=60, qualityLevel=0.01, minDistance=5, mask=mask)
    return pts
