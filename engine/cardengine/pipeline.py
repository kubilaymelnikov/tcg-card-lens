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
from .i18n import Msg
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
SWAP_CONFIRM = 2  # so viele Prüfungen in Folge (bei ruhigem Kartenbereich) bestätigen einen Tausch


def _signature(frame: np.ndarray, corners: np.ndarray) -> np.ndarray:
    """Normiertes 24×34-Graubild der entzerrten Karte (beide Lagen gleich behandelt: Betrag der Korrelation)."""
    import cv2

    from .geometry import rectify

    g = cv2.cvtColor(cv2.resize(rectify(frame, corners), (24, 34), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY)
    v = g.astype(np.float32).ravel()
    v -= v.mean()
    return v / (np.linalg.norm(v) + 1e-6)


def _turn_track(t: Track, k: int) -> None:
    """Eckenzuordnung eines Tracks um k Vierteldrehungen verschieben (Position bleibt, Lage der Karte ändert sich).
    Neue Detektionen richten sich danach (tracking._align), die Drehung bleibt also bestehen."""
    t.corners = np.roll(t.corners, k, axis=0).astype(np.float32)
    t.display = np.roll(t.display, k, axis=0).astype(np.float32)
    t.samples = [np.roll(s, k, axis=0) for s in t.samples]
    t.orient_rev += 1


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
        self.game: str | tuple[str, ...] | None = None  # nur in diesen Spielen suchen (siehe games.parse_games; None = alle)
        self.min_score = 0.0  # Erkennungsempfindlichkeit: Detektionen darunter werden verworfen
        self.events: list[tuple[str, int]] = []  # (Ereignis, Track-ID) für Debug-Snapshots; vom Aufrufer geleert
        self.on_identity = None  # optionaler Rückruf (identity) im Identifikations-Thread, z. B. Namen übersetzen
        # Stellen, an denen nachweislich keine Karte liegt (Spielmatte, Monitor …): (Ecken, Signatur, Zeit)
        self.rejected_zones: list[tuple[np.ndarray, np.ndarray, float]] = []
        self._scene_still_since: float | None = None
        # Eingriffe von außen (Dock): werden im Verarbeitungs-Thread am Anfang von process() ausgeführt
        self._commands: list = []
        self._cmd_lock = threading.Lock()
        self.tracker = CardTracker(tracker_config)
        self.detect_interval = 1.0 / detect_hz
        self.auto_detect = True  # aus: kein regelmäßiger Detektor-Lauf, Karten nur über probe() (Dock)
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
        track_id, frame, corners, allow_quarter = job
        t0 = time.perf_counter()
        if allow_quarter:
            identity = self.identifier.identify(frame, corners, game=self.game)
        else:
            identity = self.identifier.identify(frame, corners, game=self.game, allow_quarter=False)
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
        self._run_commands(frame, now)
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
        if self.auto_detect and now - self._last_detect >= interval and self._det_worker.submit((frame.copy(), now)):
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
                    elif t.confirmed:
                        pass  # vom Nutzer bestätigt: neue Ergebnisse nicht übernehmen
                    elif identity is not None and _better(identity, old):
                        if getattr(identity, "turns", 0) % 2:
                            # Karte lag „quer“ zur angenommenen Ausrichtung → Eckenzuordnung des Tracks mitdrehen
                            _turn_track(t, 1)
                            identity.turns -= 1
                            identity.rotate180 = identity.turns == 2
                            self.events.append(("gedreht", t.id))
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
            self._id_worker.submit((t.id, frame.copy(), t.corners.copy(), not t.orientation_fixed))

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
            if t.motion >= self.tracker.cfg.motion_still:
                continue  # Hand/Bewegung über der Karte: kein Urteil, erst wenn der Bereich wieder ruht
            sim = float(np.dot(t.signature, _signature(frame, t.corners)))
            t.swap_hits = t.swap_hits + 1 if sim < SWAP_THRESHOLD else 0
            if t.swap_hits >= SWAP_CONFIRM:
                t.swap_hits = 0
                self.events.append(("tausch", t.id))
                t.identity, t.signature, t.state, t.attempts, t.next_attempt = None, None, "new", 0, 0.0
                t.confirmed = t.user_rejected = t.hidden = False

    # ---------- Eingriffe aus dem Dock ----------
    def post(self, fn) -> None:
        """`fn(frame, now)` beim nächsten Frame im Verarbeitungs-Thread ausführen (kein Wettlauf mit dem Tracking)."""
        with self._cmd_lock:
            self._commands.append(fn)

    def _run_commands(self, frame: np.ndarray, now: float) -> None:
        with self._cmd_lock:
            cmds, self._commands = self._commands, []
        for fn in cmds:
            try:
                fn(frame, now)
            except Exception as err:
                print(f"[pipeline] Dock-Befehl fehlgeschlagen: {err}")

    def _track(self, track_id: int) -> Track | None:
        return next((t for t in self.tracker.tracks if t.id == track_id), None)

    def confirm(self, track_id: int, identity) -> None:
        """Vom Nutzer bestätigte Identität festsetzen (sicher, mit Preis)."""
        def run(frame, now):
            t = self._track(track_id)
            if t is None:
                return
            t.identity, t.state, t.confirmed = identity, "identified", True
            t.signature = _signature(frame, t.corners)
            self.events.append(("bestätigt", t.id))
        self.post(run)

    def reject(self, track_id: int) -> None:
        """„Keine Karte“: Track verwerfen und die Stelle als Hintergrund merken."""
        def run(frame, now):
            t = self._track(track_id)
            if t is None:
                return
            t.identity, t.state, t.confirmed = None, "rejected", False
            t.user_rejected = True
            t.signature = _signature(frame, t.corners)  # neuer Inhalt an dieser Stelle → Tausch → neu prüfen
            self.rejected_zones.append((t.corners.copy(), _signature(frame, t.corners), now))
            self.events.append(("keine-karte", t.id))
        self.post(run)

    def dismiss(self, track_id: int) -> None:
        """Karte ausblenden (Dock-✕): keine Anzeige, keine Frage, nichts gelernt – bis dort eine andere Karte liegt."""
        def run(frame, now):
            t = self._track(track_id)
            if t is None:
                return
            t.hidden = True
            if t.signature is None:  # für die Tausch-Erkennung: aktueller Inhalt an dieser Stelle
                t.signature = _signature(frame, t.corners)
        self.post(run)

    def rotate(self, track_id: int) -> None:
        """Karte um 90° drehen (Dock ↻) und neu erkennen; die Drehung des Nutzers gilt ab jetzt fest."""
        def run(frame, now):
            t = self._track(track_id)
            if t is None:
                return
            _turn_track(t, 1)
            t.user_turns = (t.user_turns + 1) % 4
            t.orientation_fixed = True
            t.identity, t.signature, t.state, t.confirmed = None, None, "new", False
            t.attempts, t.next_attempt = 0, 0.0
            self.events.append(("gedreht", t.id))
        self.post(run)

    def probe(self, point: tuple[float, float] | None = None, rect: tuple[float, float, float, float] | None = None,
              timeout: float = 4.0) -> dict:
        """Gezielt an einer Stelle nach einer Karte suchen (Pixelkoordinaten; Dock-Vorschau). Wartet auf das
        Ergebnis: {"found", "track", "how"}. Gefundene Karten werden wie normale Tracks verfolgt und identifiziert."""
        import threading as _t

        from .geometry import iou_quads, side_lengths
        from .probe import probe_point, probe_rect

        done, result = _t.Event(), {"found": False}

        def run(frame, now):
            try:
                tracks = [t for t in self.tracker.tracks if not t.hidden]
                heights = [side_lengths(t.corners)[1] for t in tracks]
                card_h = float(np.median(heights)) if heights else None
                if rect is not None:
                    quad, how = probe_rect(self.detector, frame, *rect)
                else:
                    # Liegt an der Stelle schon eine Karte, wird sie neu erkannt statt doppelt angelegt
                    import cv2

                    hit = next((t for t in tracks if cv2.pointPolygonTest(
                        t.corners.reshape(-1, 1, 2).astype(np.float32), (float(point[0]), float(point[1])), False) >= 0),
                        None)
                    if hit is not None:
                        self._reset_for_probe(hit)
                        result.update(found=True, track=hit.id, how="vorhanden")
                        return
                    quad, how = probe_point(self.detector, frame, point[0], point[1], card_h)
                if quad is None:
                    return
                quad = np.asarray(quad, np.float32)
                same = max(tracks, key=lambda t: iou_quads(t.corners, quad), default=None)
                if same is not None and iou_quads(same.corners, quad) >= 0.5:
                    same.corners = quad.copy()
                    self._reset_for_probe(same)
                    result.update(found=True, track=same.id, how="vorhanden")
                    return
                t = Track(corners=quad.copy(), display=quad.copy(), score=1.0, game=None, first_seen=now,
                          last_confirmed=now, hits=3)
                self.tracker.tracks.append(t)
                # Gemerkte „keine Karte“-Stelle hier aufheben – der Nutzer sagt ausdrücklich: hier liegt eine
                self.rejected_zones = [z for z in self.rejected_zones if iou_quads(z[0], quad) < 0.5]
                self.events.append(("gezielt", t.id))
                result.update(found=True, track=t.id, how=how)
            finally:
                done.set()

        self.post(run)
        if not done.wait(timeout):
            return {"found": False, "error": Msg("probe.timeout")}
        return result

    def _reset_for_probe(self, t: Track) -> None:
        """Vorhandene Karte an der Klickstelle: neu erkennen (auch wenn sie als „keine Karte“ abgelegt war)."""
        t.identity, t.signature, t.state, t.confirmed = None, None, "new", False
        t.user_rejected = t.hidden = False
        t.attempts, t.next_attempt = 0, 0.0
        t.score = max(t.score, 1.0)
        t.last_confirmed = self.tracker._now

    def refresh(self, track_id: int) -> None:
        """Karte neu erkennen (und damit den Preis neu laden)."""
        def run(frame, now):
            t = self._track(track_id)
            if t is None:
                return
            t.identity, t.signature, t.state, t.confirmed = None, None, "new", False
            t.attempts, t.next_attempt = 0, 0.0
        self.post(run)

    def close(self):
        self._det_worker.close()
        if self._id_worker:
            self._id_worker.close()
