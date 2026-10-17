"""Engine-Pipeline: asynchrone Detektion + Tracking je Quelle + Identifikation neuer Tracks.

Der Aufrufer (Debug-Programm, später der OBS-Filter) liefert jeden Frame an `CardPipeline.process()` und bekommt
sofort die aktuellen Tracks zurück – die Detektion läuft in einem eigenen Thread mit begrenzter Rate, sodass das
Rendering nie auf die KI wartet.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import numpy as np

from .detection.base import Detection, Detector
from .tracking import CardTracker, Track, TrackerConfig


@dataclass
class PipelineStats:
    detect_ms: float = 0.0
    detect_hz: float = 0.0
    identify_ms: float = 0.0
    frames: int = 0
    detect_runs: int = 0  # tatsächliche Detektor-Durchläufe (sinkt bei ruhigem Bild)
    locked_tracks: int = 0


IDLE_DETECT_HZ = 1.0  # Detektionsrate, wenn das ganze Bild ruht und alle Karten fixiert sind
SCENE_STILL = 1.0  # globale Bildänderung, darunter gilt die Szene als ruhig
SCENE_STILL_S = 1.0  # so lange muss die Szene ruhen, bevor die Rate sinkt
REJECT_AFTER_FAILS = 3  # so viele gescheiterte Identifikationen einer ruhenden Stelle → "keine Karte"
ZONE_TTL_S = 15 * 60  # gemerkte "keine Karte"-Stellen verfallen nach 15 min


SWAP_CHECK_S = 1.0
SWAP_THRESHOLD = 0.5  # Korrelation der Vorschaubilder; gleiche Karte bei Bewegung/Licht typ. > 0.8


def _signature(frame: np.ndarray, corners: np.ndarray) -> np.ndarray:
    """Normiertes 24×34-Graubild der entzerrten Karte (beide Lagen gleich behandelt: Betrag der Korrelation)."""
    import cv2

    from .geometry import rectify

    g = cv2.cvtColor(cv2.resize(rectify(frame, corners), (24, 34), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
    v = g.astype(np.float32).ravel()
    v -= v.mean()
    return v / (np.linalg.norm(v) + 1e-6)


def _better(new, old) -> bool:
    """Neue Identifikation übernehmen? Sicher schlägt unsicher, sonst höhere Ähnlichkeit."""
    if old is None:
        return True
    if new.certain != old.certain:
        return new.certain
    return new.similarity >= old.similarity


class Locked:
    """Serialisiert Aufrufe eines gemeinsam genutzten Modells (eine GPU, mehrere OBS-Quellen)."""

    def __init__(self, obj, lock: threading.Lock):
        self._obj = obj
        self._lock = lock

    def __getattr__(self, name):
        attr = getattr(self._obj, name)
        if name not in ("detect", "identify") or not callable(attr):
            return attr

        def call(*a, **kw):
            with self._lock:
                return attr(*a, **kw)

        return call


class AsyncWorker:
    """Hält immer nur den neuesten Auftrag vor (ältere werden verworfen) und arbeitet ihn im Hintergrund ab."""

    def __init__(self, fn, name: str):
        self._fn = fn
        self._job = None
        self._result = None
        self._cv = threading.Condition()
        self._stop = False
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._thread.start()

    def submit(self, job) -> bool:
        with self._cv:
            if self._job is not None:
                return False
            self._job = job
            self._cv.notify()
            return True

    def busy(self) -> bool:
        with self._cv:
            return self._job is not None

    def take_result(self):
        with self._cv:
            r, self._result = self._result, None
            return r

    def _run(self):
        while True:
            with self._cv:
                while self._job is None and not self._stop:
                    self._cv.wait()
                if self._stop:
                    return
                job = self._job
            try:
                result = self._fn(job)
            except Exception as err:  # Fehler im Worker dürfen den Aufrufer nie blockieren
                result = err
            with self._cv:
                self._result = result
                self._job = None

    def close(self):
        with self._cv:
            self._stop = True
            self._cv.notify()


class CardPipeline:
    def __init__(self, detector: Detector, identifier=None, tracker_config: TrackerConfig | None = None,
                 detect_hz: float = 8.0, prices=None):
        self.detector = detector
        self.identifier = identifier
        self.prices = prices  # pricing.service.PriceService (optional)
        self.game: str | None = None  # nur in diesem Spiel suchen ("pokemon" | "mtg" | None = beide)
        self.min_score = 0.0  # Erkennungsempfindlichkeit: Detektionen darunter werden verworfen
        self.events: list[tuple[str, int]] = []  # (Ereignis, Track-ID) für Debug-Snapshots; vom Aufrufer geleert
        self.on_identity = None  # optionaler Rückruf (identity) im Identifikations-Thread, z. B. Namen übersetzen
        # Stellen, an denen nachweislich keine Karte liegt (Spielmatte, Monitor …): (Ecken, Signatur, Zeit)
        self.rejected_zones: list[tuple[np.ndarray, np.ndarray, float]] = []
        self._scene_still_since: float | None = None
        self.tracker = CardTracker(tracker_config)
        self.detect_interval = 1.0 / detect_hz
        self.stats = PipelineStats()
        self._last_detect = 0.0
        self._detect_times: list[float] = []
        self._det_worker = AsyncWorker(self._detect_job, "detector")
        self._id_worker = AsyncWorker(self._identify_job, "identifier") if identifier else None
        self.last_detections: list[Detection] = []

    # ---------- Hintergrundjobs ----------
    def _detect_job(self, job):
        frame, ts = job
        t0 = time.perf_counter()
        dets = [d for d in self.detector.detect(frame) if d.score >= self.min_score]
        self.stats.detect_runs += 1
        return dets, ts, (time.perf_counter() - t0) * 1000

    def _identify_job(self, job):
        track_id, frame, corners = job
        t0 = time.perf_counter()
        identity = self.identifier.identify(frame, corners, game=self.game)
        # Preise nur für sicher identifizierte Drucke (keine falschen Preise bei unsicherer Edition)
        if identity is not None and identity.certain and identity.card_id and self.prices is not None:
            rec = self.identifier.catalog.get(identity.game, identity.card_id)
            if rec is not None:
                identity.price = self.prices.info(rec)
        if identity is not None and self.on_identity is not None:
            self.on_identity(identity)
        return track_id, identity, (time.perf_counter() - t0) * 1000

    # ---------- pro Frame ----------
    def process(self, frame: np.ndarray, now: float | None = None) -> list[Track]:
        now = time.perf_counter() if now is None else now
        self.stats.frames += 1
        tracks = self.tracker.step(frame, now)

        res = self._det_worker.take_result()
        if isinstance(res, Exception):
            print(f"[detector] Fehler: {res}")
        elif res is not None:
            dets, _, ms = res
            self.last_detections = dets
            known = {t.id for t in self.tracker.tracks}
            tracks = self.tracker.update(dets, now)
            self._apply_rejected_zones([t for t in tracks if t.id not in known], frame, now)
            self.stats.detect_ms = ms
            self._detect_times = [t for t in self._detect_times if now - t < 2.0] + [now]
            self.stats.detect_hz = (len(self._detect_times) - 1) / max(1e-6, self._detect_times[-1] - self._detect_times[0])

        self._refine_edges(frame, tracks)
        interval = self._current_detect_interval(now, tracks)
        if now - self._last_detect >= interval and self._det_worker.submit((frame.copy(), now)):
            self._last_detect = now

        if self._id_worker:
            self._handle_identification(frame, now)
        return tracks

    def _handle_identification(self, frame: np.ndarray, now: float) -> None:
        res = self._id_worker.take_result()
        if isinstance(res, Exception):
            print(f"[identifier] Fehler: {res}")
        elif res is not None:
            track_id, identity, ms = res
            self.stats.identify_ms = ms
            for t in self.tracker.tracks:
                if t.id == track_id:
                    t.attempts += 1
                    old = t.identity
                    if identity is None and old is None:
                        self.events.append(("fehlgeschlagen", t.id))
                        t.state = "failed"
                        t.next_attempt = now + min(5.0, 0.5 * 2 ** t.attempts)
                        if t.attempts >= REJECT_AFTER_FAILS and t.locked:
                            # Ruhende Stelle, mehrfach keine Karte gefunden → als Hintergrund merken, nicht weiter versuchen
                            t.state = "rejected"
                            self.rejected_zones.append((t.corners.copy(), _signature(frame, t.corners), now))
                            self.events.append(("keine-karte", t.id))
                    elif identity is not None and _better(identity, old):
                        t.identity = identity
                        t.state = "identified" if identity.certain else "uncertain"
                        self.events.append(("erkannt" if identity.certain else "unsicher", t.id))
                        t.signature = _signature(frame, t.corners)
                        # Unsichere Karten später erneut versuchen (andere Perspektive/Beleuchtung)
                        t.next_attempt = now + 1.5
                    else:
                        # Ergebnis nicht besser als das vorhandene → behalten, später erneut versuchen
                        t.state = "identified" if old is not None and old.certain else "uncertain"
                        t.next_attempt = now + 2.0
        self._check_swaps(frame, now)
        if self._id_worker.busy():
            return
        # Nächsten Kandidaten wählen: neue Tracks zuerst, dann fehlgeschlagene/unsichere zur Wiederholung
        order = {"new": 0, "failed": 1, "uncertain": 2}
        candidates = [
            t for t in self.tracker.tracks
            if t.stable and t.state in order and now >= t.next_attempt and t.attempts < 8
            # Standbild bereits mehrfach geprüft: erst bei Bewegung (neue Perspektive) erneut versuchen
            and not (t.finalized and t.attempts >= 3)
        ]
        if candidates:
            t = min(candidates, key=lambda x: (order[x.state], x.first_seen))
            if t.state == "new":
                t.state = "identifying"
            self._id_worker.submit((t.id, frame.copy(), t.corners.copy()))

    # ---------- Ruhe, Feinjustierung, Hintergrund ----------
    def _current_detect_interval(self, now: float, tracks: list[Track]) -> float:
        """Bewegungsabhängige Detektionsrate: ruht die ganze Szene und sind alle Karten fixiert, reicht 1×/s."""
        if self.tracker.global_motion < SCENE_STILL:
            if self._scene_still_since is None:
                self._scene_still_since = now
        else:
            self._scene_still_since = None
        self.stats.locked_tracks = sum(t.locked for t in tracks)
        still = self._scene_still_since is not None and now - self._scene_still_since >= SCENE_STILL_S
        if still and all(t.finalized or t.state == "rejected" for t in tracks):
            # Nie seltener als zweimal pro Haltezeit – sonst verfallen Karten, die nicht fixiert sind
            return max(self.detect_interval, min(1.0 / IDLE_DETECT_HZ, self.tracker.cfg.lost_after_s / 2))
        return self.detect_interval

    def _refine_edges(self, frame: np.ndarray, tracks: list[Track]) -> None:
        """Einmalig je ruhender Karte: Ecken subpixelgenau an die echten Kanten anpassen (Perspektive)."""
        todo = [t for t in tracks if t.finalized and not t.edge_refined]
        if not todo:
            return
        import cv2

        from .geometry import refine_quad_edges

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        for t in todo:
            t.edge_refined = True
            try:
                refined = refine_quad_edges(gray, t.corners)
            except cv2.error as err:  # Feinjustierung ist optional – nie die Erkennung stoppen
                print(f"[tracking] Kantenanpassung übersprungen: {err}")
                refined = None
            if refined is not None:
                t.corners = refined  # die Anzeige gleitet dorthin (siehe CardTracker.step)

    def _apply_rejected_zones(self, new_tracks: list[Track], frame: np.ndarray, now: float) -> None:
        """Neue Detektion an einer bekannten "keine Karte"-Stelle mit unverändertem Inhalt → gleich verwerfen."""
        from .geometry import iou_quads

        self.rejected_zones = [z for z in self.rejected_zones if now - z[2] < ZONE_TTL_S]
        if not self.rejected_zones:
            return
        for t in new_tracks:
            for corners, sig, _ in self.rejected_zones:
                if iou_quads(t.corners, corners) > 0.6 and float(np.dot(sig, _signature(frame, t.corners))) > 0.85:
                    t.state = "rejected"
                    break

    def _check_swaps(self, frame: np.ndarray, now: float) -> None:
        """Ausgetauschte Karte an gleicher Stelle erkennen: Das Tracking folgt dem Bereich, nicht der Karte. Etwa
        einmal pro Sekunde wird ein kleines Vorschaubild mit dem Stand bei der Identifikation verglichen."""
        for t in self.tracker.tracks:
            if t.signature is None or now < getattr(t, "next_swap_check", 0.0):
                continue
            t.next_swap_check = now + SWAP_CHECK_S
            sim = float(np.dot(t.signature, _signature(frame, t.corners)))
            if sim < SWAP_THRESHOLD:
                self.events.append(("tausch", t.id))
                t.identity, t.signature, t.state, t.attempts, t.next_attempt = None, None, "new", 0, 0.0

    def close(self):
        self._det_worker.close()
        if self._id_worker:
            self._id_worker.close()
