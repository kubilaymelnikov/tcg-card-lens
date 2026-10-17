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
    # Positions-Lock: Ruht der Kartenbereich, wird die Position eingefroren und nur noch feinjustiert
    motion_still: float = 1.5  # mittlere Bildänderung (Graustufen, verkleinertes Bild), darunter gilt "ruhig"
    lock_after_frames: int = 8  # so viele ruhige Frames in Folge -> Lock
    lock_tolerance: float = 0.06  # Detektionen mit < 6 % Abweichung (Kartenbreite) bestätigen den Lock
    refine_samples: int = 20  # so viele bestätigende Detektionen werden gemittelt, dann steht die Position
    # Wiederaufnahme: Eine kurz verlorene Karte (z. B. beim Verschieben) behält Nummer und Erkennung
    revive_s: float = 4.0  # so lange werden verlorene Tracks gemerkt
    revive_iou: float = 0.3  # neue Detektion mit dieser Überlappung zum letzten Ort → derselbe Track

    @classmethod
    def facecam(cls) -> "TrackerConfig":
        return cls(lost_after_s=1.0, detection_weight=0.7, smoothing=0.35)

    @classmethod
    def table(cls) -> "TrackerConfig":
        return cls(lost_after_s=2.5, detection_weight=0.5, smoothing=0.6)


_ids = itertools.count(1)
LOCK_GLIDE = 0.15  # Anteil, um den sich die Anzeige je Frame der fixierten Position nähert


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
    state: str = "new"  # new | identifying | identified | uncertain | failed | rejected
    confirmed: bool = False  # Identität vom Nutzer bestätigt (Dock) – wird nicht mehr überschrieben
    user_rejected: bool = False  # vom Nutzer als „keine Karte“ markiert (Dock)
    hidden: bool = False  # im Dock ohne Angabe von Gründen entfernt: nicht anzeigen, nichts lernen
    swap_hits: int = 0  # Swap-Prüfungen in Folge mit deutlich anderem Inhalt
    attempts: int = 0
    next_attempt: float = 0.0
    signature: np.ndarray | None = None  # kleines Graubild der Karte zur Änderungserkennung (Tisch-Modus)
    # Bewegung / Lock
    motion: float = 0.0  # mittlere Bildänderung im Kartenbereich (letzter Frame)
    still_frames: int = 0
    locked: bool = False
    finalized: bool = False  # Lock-Position fertig gemittelt
    edge_refined: bool = False  # Ecken an die echten Kanten angepasst
    samples: list = field(default_factory=list)
    outliers: int = 0
    unlocks: dict = field(default_factory=dict)  # Grund -> Anzahl (Diagnose)

    @property
    def stable(self) -> bool:
        return self.hits >= 2


class CardTracker:
    def __init__(self, config: TrackerConfig | None = None):
        self.cfg = config or TrackerConfig()
        self.tracks: list[Track] = []
        self._prev_gray: np.ndarray | None = None
        self._prev_motion: np.ndarray | None = None
        self.global_motion = 0.0  # mittlere Bildänderung des ganzen Bildes (für adaptive Detektionsrate)
        self._now = 0.0
        self._lost: list[tuple[Track, float]] = []  # (Track, verloren seit) – für die Wiederaufnahme

    # ---------- jeder Frame ----------
    def step(self, frame: np.ndarray, now: float) -> list[Track]:
        """Ecken aller Tracks per optischem Fluss nachführen und abgelaufene Tracks entfernen."""
        s = self.cfg.flow_scale
        gray = cv2.cvtColor(cv2.resize(frame, None, fx=s, fy=s, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
        self._now = now
        self._update_motion(gray, frame.shape[1])
        if self._prev_gray is not None and self._prev_gray.shape == gray.shape:
            for t in self.tracks:
                if not t.locked:  # eingefrorene Karten nicht per optischem Fluss verschieben (kein Driften)
                    self._flow(t, self._prev_gray, gray)
        self._prev_gray = gray
        kept, lost = [], []
        for t in self.tracks:
            (kept if now - t.last_confirmed <= self.cfg.lost_after_s else lost).append(t)
        self.tracks = kept
        self._lost = [(t, ts) for t, ts in self._lost if now - ts <= self.cfg.revive_s]
        self._lost += [(t, now) for t in lost if t.identity is not None or t.hits >= 3]
        for t in self.tracks:
            if t.locked:
                # Ruhende Karte: sanft auf die (fertig gemittelte, feinjustierte) Position gleiten und dort
                # exakt stehen bleiben – keine harten Sprünge beim Mitteln oder bei der Kantenanpassung
                delta = t.corners - t.display
                if np.abs(delta).max() < 0.05:
                    t.display = t.corners.copy()
                else:
                    t.display = (t.display + LOCK_GLIDE * delta).astype(np.float32)
            else:
                a = self.cfg.smoothing
                t.display = (a * t.display + (1 - a) * t.corners).astype(np.float32)
        return self.tracks

    def _update_motion(self, gray: np.ndarray, frame_w: int) -> None:
        """Bildänderung zum Vorframe, global und je Karte; steuert Lock/Unlock.

        Gemessen auf stark verkleinertem, geglättetem Bild (1/8 der Originalbreite): Sensorrauschen mittelt
        sich weg, echte Bewegung (Karte verschoben, Hand im Bild) bleibt deutlich sichtbar.
        """
        small = cv2.GaussianBlur(cv2.resize(gray, None, fx=0.25, fy=0.25, interpolation=cv2.INTER_AREA), (3, 3), 0)
        small = small.astype(np.int16)
        prev, self._prev_motion = self._prev_motion, small
        if prev is None or prev.shape != small.shape:
            return
        diff = np.abs(small - prev).astype(np.float32)
        self.global_motion = float(diff.mean())
        k = small.shape[1] / frame_w  # Bild- -> Bewegungskoordinaten
        cfg = self.cfg
        for t in self.tracks:
            lo = np.maximum((t.corners.min(axis=0) * k).astype(int), 0)
            hi = (t.corners.max(axis=0) * k).astype(int) + 1
            region = diff[lo[1]:hi[1], lo[0]:hi[0]]
            t.motion = float(region.mean()) if region.size else 0.0
            if t.motion < cfg.motion_still:
                t.still_frames += 1
                if t.locked:
                    # Fixierte Karte, Bildbereich unverändert → sie liegt noch da, auch wenn der (bei ruhigem
                    # Bild seltene) Detektor gerade nichts meldet. Ohne das verfiel sie nach der Haltezeit und
                    # wurde neu erkannt ("Zurücksetzen").
                    t.last_confirmed = max(t.last_confirmed, self._now)
                if not t.locked and t.still_frames >= cfg.lock_after_frames:
                    t.locked, t.samples, t.outliers = True, [t.corners.copy()], 0
            else:
                t.still_frames = 0
                if t.locked:
                    t.unlocks["bewegung"] = t.unlocks.get("bewegung", 0) + 1
                    self._unlock(t)

    @staticmethod
    def _unlock(t: Track) -> None:
        t.locked = t.finalized = t.edge_refined = False
        t.samples, t.outliers, t.points = [], 0, None
        if t.state == "rejected" and t.user_rejected:
            pass  # vom Nutzer verworfen: bleibt verworfen; ein echter Kartentausch wird über die Signatur erkannt
        elif t.state == "rejected":  # an dieser Stelle hat sich etwas bewegt -> erneut prüfen
            t.state, t.attempts, t.next_attempt = "new", 0, 0.0
        elif t.state in ("uncertain", "failed"):  # neue Perspektive -> wieder Versuche erlauben
            t.attempts, t.next_attempt = 0, 0.0

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
            aligned = _align(d.corners, t.corners)
            t.last_confirmed = now
            t.hits += 1
            t.score = d.score
            t.game = d.game or t.game
            if t.locked:
                self._confirm_locked(t, aligned)
                continue
            w = self.cfg.detection_weight
            t.corners = (w * aligned + (1 - w) * t.corners).astype(np.float32)
            t.points = None  # Merkmale neu suchen (Karte kann sich verändert haben)
        for di, d in enumerate(detections):
            if di not in used_d:
                c = d.corners.astype(np.float32)
                old = self._revive(c, now)
                if old is not None:
                    old.corners = _align(c, old.corners).astype(np.float32)
                    old.last_confirmed, old.score, old.game = now, d.score, d.game or old.game
                    old.hits += 1
                    self.tracks.append(old)
                    continue
                self.tracks.append(Track(corners=c, display=c.copy(), score=d.score, game=d.game, first_seen=now, last_confirmed=now))
        return self.tracks

    def _revive(self, corners: np.ndarray, now: float) -> Track | None:
        """Kürzlich verlorenen Track an (fast) gleicher Stelle zurückholen – gleiche ID, gleiche Erkennung.
        Ob dort inzwischen eine andere Karte liegt, prüft die Tausch-Erkennung der Pipeline."""
        best, best_iou = None, self.cfg.revive_iou
        for i, (t, ts) in enumerate(self._lost):
            if now - ts > self.cfg.revive_s:
                continue
            iou = iou_quads(t.corners, corners)
            if iou >= best_iou:
                best, best_iou = i, iou
        if best is None:
            return None
        t, _ = self._lost.pop(best)
        self._unlock(t)  # Karte wurde bewegt: Position neu einmessen
        t.still_frames = 0
        return t


    def _confirm_locked(self, t: Track, aligned: np.ndarray) -> None:
        """Ruhende Karte: Detektion nur als Bestätigung; anfangs robust mitteln (Median), dann Position halten."""
        card_w = float(np.linalg.norm(t.corners[0] - t.corners[1]))
        dev = float(np.linalg.norm(aligned - t.corners, axis=1).max())
        if dev > self.cfg.lock_tolerance * card_w:
            # Deutlich andere Detektion trotz ruhigem Bild: einzelne Ausreißer ignorieren, wiederholt -> neu ausrichten
            t.outliers += 1
            if t.outliers >= 3:
                t.unlocks["ausreisser"] = t.unlocks.get("ausreisser", 0) + 1
                self._unlock(t)
                t.corners = aligned.astype(np.float32)
            return
        t.outliers = 0
        if not t.finalized:
            t.samples.append(aligned.astype(np.float32))
            t.corners = np.median(np.stack(t.samples), axis=0).astype(np.float32)
            if len(t.samples) >= self.cfg.refine_samples:
                t.finalized = True


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
