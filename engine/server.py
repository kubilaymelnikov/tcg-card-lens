"""Engine-Server für das OBS-Plugin (Protokoll: docs/PLUGIN.md).

    python server.py                 # lauscht auf 127.0.0.1:47820
    python server.py --port 47820 --preload-languages de,en

Ein Prozess für alle Filterinstanzen: Modelle (Detektor, Embedder, OCR) und Preisdaten liegen einmal im Speicher,
GPU-Aufrufe werden über eine Sperre serialisiert. Jede Verbindung (= OBS-Quelle) hat eigene Einstellungen,
eigenes Tracking, eigenen Panel-Cache und eigene Snapshot-Regeln.
"""
from __future__ import annotations

import argparse
import base64
import json
import mmap
import os
import socket
import struct
import threading
import time
import traceback

import numpy as np

from cardengine import __version__
from cardengine.card_database.names import CardNames
from cardengine.overlay import OverlayRenderer
from cardengine.pipeline import CardPipeline, Locked
from cardengine.snapshots import SnapshotStore
from cardengine.tracking import TrackerConfig

HERE = os.path.dirname(os.path.abspath(__file__))
PROTO = 1
HEADER = 64

DEFAULT_SETTINGS = {
    "enabled": True,
    "games": "both",  # both | pokemon | mtg
    "mode": "facecam",  # facecam | table
    "sensitivity": 0.4,  # Detektor-Schwelle 0..1
    "hold_time": 1.2,  # s
    "smoothing": 0.5,  # 0..0.9
    "languages": ["de", "en"],
    "display_language": "de",
    "show_name": True,
    "show_price": True,
    "show_history": True,
    "history_days": 90,
    "scale": 1.0,
    "thickness": 3.0,
    "color_pokemon": [255, 210, 63],
    "color_mtg": [255, 138, 61],
    "debug": False,
    "auto_snapshots": True,
    "snapshot_limit_mb": 500,
    "show_labels": "dock",  # Kennzeichen (A, B …) im Video: dock = nur solange das Dock offen ist | always | never
}
DOCK_ACTIVE_S = 5.0  # so lange nach der letzten Dock-Abfrage gilt das Dock als offen


# ---------------------------------------------------------------- gemeinsame Modelle
class Shared:
    """Lädt Modelle und Daten einmal; mehrere Sitzungen teilen sie."""

    def __init__(self, preload_languages: list[str]):
        self.lock = threading.Lock()
        self.ready = threading.Event()
        self._status = "Modelle werden geladen …"
        self.maintenance = None
        self.sessions = 0
        self.session_list: list = []  # verbundene Sitzungen (für das Dock)
        self._next_sid = 0
        self._sid_lock = threading.Lock()
        self.learned = None  # cardengine.learning.LearnedStore
        self.dock_seen = 0.0  # letzte Abfrage des Docks
        self.last_activity = time.time()
        self.names = CardNames()
        self.detector = None
        self.identifier = None
        self.prices = None
        threading.Thread(target=self._load, args=(preload_languages,), daemon=True).start()

    @property
    def status(self) -> str:
        m = self.maintenance.status if self.maintenance else ""
        return f"{self._status} · {m}" if m else self._status

    @status.setter
    def status(self, value: str) -> None:
        self._status = value

    def new_sid(self) -> str:
        with self._sid_lock:
            self._next_sid += 1
            return f"s{self._next_sid}"

    def _swap_index(self, index) -> None:
        """Neuer Embedding-Index aus der Hintergrund-Wartung – ohne Neustart übernehmen."""
        if self.identifier is not None:
            with self.lock:
                self.identifier._obj.index = index
            print(f"[engine] Index neu geladen: {len(index.keys)} Karten", flush=True)

    def _load(self, languages):
        try:
            from cardengine.maintenance import Maintenance
            from cardengine.models_fetch import ensure_models
            from cardengine.pricing.service import PriceService
            from cardengine.recognition.embedder import Embedder
            from cardengine.recognition.index import index_path
            from cardengine.recognition.identifier import CardIdentifier
            from run import build_detector

            t0 = time.time()
            self.status = "Modelle werden geprüft/geladen …"
            ensure_models(verbose=False)
            if not os.path.exists(index_path(Embedder.default_index_name())):
                # Erster Start ohne Setup-Daten: Grunddaten jetzt aufbauen (dauert einige Minuten)
                self.status = "Erster Start: Kartendaten werden aufgebaut"
                self.maintenance = Maintenance(gpu_lock=self.lock)
                self.maintenance.initial()
            self.prices = PriceService(history_days=180).start()
            threading.Thread(target=self.names.preload, args=(languages,), daemon=True).start()
            det = build_detector("default")
            # Gemeinsamer Detektor mit niedriger Schwelle; jede Sitzung filtert nach ihrer Empfindlichkeit
            det.base.conf = 0.2
            self.detector = Locked(det, self.lock)
            identifier = CardIdentifier.load_default()
            try:
                from cardengine.learning import LearnedStore

                self.learned = LearnedStore()
                identifier.learned = self.learned
                print(f"[engine] gelernte Referenzen: {self.learned.counts()}", flush=True)
            except Exception as err:
                print(f"[engine] Lernspeicher nicht verfügbar: {err}", flush=True)
            self.identifier = Locked(identifier, self.lock)
            self.status = "bereit"
            self.ready.set()
            print(f"[engine] Modelle geladen in {time.time() - t0:.1f} s", flush=True)
            # Daten im Hintergrund aktuell halten (neue Sets, Magic-Bilder, Preisverlauf)
            self.maintenance = Maintenance(
                embedder_factory=lambda: self.identifier._obj.embedder,
                on_index_updated=self._swap_index, gpu_lock=self.lock,
            ).start()
        except Exception as err:
            self.status = f"Fehler beim Laden: {err}"
            traceback.print_exc()


# ---------------------------------------------------------------- Verbindung
def _label_name(n: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA …"""
    s = ""
    n += 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def send(sock: socket.socket, msg: dict, lock: threading.Lock) -> None:
    data = json.dumps(msg, separators=(",", ":")).encode("utf-8")
    with lock:
        sock.sendall(struct.pack("<I", len(data)) + data)


def recv(sock: socket.socket) -> dict | None:
    head = _recv_exact(sock, 4)
    if head is None:
        return None
    (n,) = struct.unpack("<I", head)
    if n > 64 * 1024 * 1024:
        raise ValueError("Nachricht zu groß")
    body = _recv_exact(sock, n)
    return None if body is None else json.loads(body.decode("utf-8"))


def _recv_exact(sock, n):
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return bytes(buf)


class Session:
    """Eine OBS-Filterinstanz."""

    def __init__(self, sock: socket.socket, shared: Shared):
        self.sock = sock
        self.shared = shared
        self.send_lock = threading.Lock()
        self.settings = dict(DEFAULT_SETTINGS)
        self.shm: mmap.mmap | None = None
        self.source = "quelle"
        self.pipeline: CardPipeline | None = None
        self.overlay: OverlayRenderer | None = None
        self.snapshots: SnapshotStore | None = None
        self.sent_versions: dict[str, int] = {}
        self.latest_frame: dict | None = None
        self.frame_cv = threading.Condition()
        self.alive = True
        self.last_frame_img: np.ndarray | None = None
        self._text_cache: dict[str, tuple[float, int, int, int]] = {}
        self._text_data: dict[str, np.ndarray] = {}
        self.counts = {"empfangen": 0, "verarbeitet": 0, "gesendet": 0, "fehler": 0}
        self.sid = shared.new_sid()
        self.labels: dict[int, str] = {}  # Track-ID -> Kennzeichen (A, B …)
        self.highlight: tuple[str, float] | None = None  # (Kennzeichen, gültig bis)
        self._last_count_log = time.time()

    # -------- Einstellungen
    def apply_settings(self, s: dict) -> None:
        self.settings = {**DEFAULT_SETTINGS, **(s or {})}
        st = self.settings
        if self.pipeline is not None:
            cfg = TrackerConfig.facecam() if st["mode"] == "facecam" else TrackerConfig.table()
            cfg.lost_after_s = float(st["hold_time"])
            cfg.smoothing = float(np.clip(st["smoothing"], 0, 0.9))
            self.pipeline.tracker.cfg = cfg
            self.pipeline.min_score = float(st["sensitivity"])
            self.pipeline.game = None if st["games"] == "both" else st["games"]
        self.overlay = OverlayRenderer(
            scale=float(st["scale"]), show_name=bool(st["show_name"]), show_price=bool(st["show_price"]),
            show_history=bool(st["show_history"]), frame_thickness=float(st["thickness"]),
            colors={"pokemon": tuple(st["color_pokemon"]), "mtg": tuple(st["color_mtg"])},
            history_days=int(st["history_days"]),
        )
        self.sent_versions.clear()  # Panels mit neuen Einstellungen neu senden
        self.snapshots = SnapshotStore(self.source, max_mb=float(st["snapshot_limit_mb"]))
        threading.Thread(target=self.shared.names.preload, args=(list(st["languages"]),), daemon=True).start()
        # Anzeigenamen bestehender Karten an die neue Sprache anpassen
        if self.pipeline is not None:
            for t in self.pipeline.tracker.tracks:
                if t.identity is not None:
                    self._localize(t.identity)

    def _localize(self, identity) -> None:
        cat = self.shared.identifier.catalog if self.shared.identifier else None
        rec = cat.get(identity.game, identity.card_id) if (cat and identity.card_id) else None
        identity.display_name = self.shared.names.name(rec, self.settings["display_language"]) if rec else identity.name

    def ensure_pipeline(self) -> bool:
        if self.pipeline is not None:
            return True
        if not self.shared.ready.is_set():
            return False
        self.pipeline = CardPipeline(self.shared.detector, self.shared.identifier, TrackerConfig.facecam(),
                                     detect_hz=8.0, prices=self.shared.prices)
        self.pipeline.on_identity = self._localize
        self.apply_settings(self.settings)
        return True

    # -------- Kennzeichen / Hervorhebung (Dock)
    def label_for(self, track_id: int) -> str:
        if track_id not in self.labels:
            used = set(self.labels.values())
            n = 0
            while True:
                lab = _label_name(n)
                if lab not in used:
                    break
                n += 1
            self.labels[track_id] = lab
        return self.labels[track_id]

    def _prune_labels(self, alive: set[int]) -> None:
        for tid in [k for k in self.labels if k not in alive]:
            del self.labels[tid]

    def current_highlight(self) -> str | None:
        h = self.highlight
        return h[0] if h is not None and time.time() < h[1] else None

    def _labels_visible(self) -> bool:
        mode = self.settings.get("show_labels", "dock")
        if mode == "always":
            return True
        return mode == "dock" and time.time() - self.shared.dock_seen < DOCK_ACTIVE_S

    # -------- Schleifen
    def run(self) -> None:
        self.shared.sessions += 1
        self.shared.session_list.append(self)
        worker = threading.Thread(target=self._process_loop, daemon=True)
        worker.start()
        try:
            while self.alive:
                msg = recv(self.sock)
                if msg is None:
                    break
                self._handle(msg)
        except (ConnectionError, OSError, ValueError) as err:
            print(f"[engine] {self.source}: Verbindung beendet ({err})")
        finally:
            self.alive = False
            with self.frame_cv:
                self.frame_cv.notify_all()
            if self.pipeline:
                self.pipeline.close()
            if self.shm:
                self.shm.close()
            self.sock.close()
            self.shared.sessions -= 1
            if self in self.shared.session_list:
                self.shared.session_list.remove(self)
            self.shared.last_activity = time.time()
            print(f"[engine] {self.source}: getrennt")

    def _handle(self, msg: dict) -> None:
        if msg.get("type") not in ("frame",):
            print(f"[engine] <- {msg.get('type')} {self.source}", flush=True)
        t = msg.get("type")
        if t == "hello":
            self.source = msg.get("source") or msg.get("instance", "quelle")
            self.shm = mmap.mmap(-1, int(msg["shm_size"]), tagname=msg["shm"])
            if self.shm[:4] != b"TCGL":
                raise ValueError("Shared Memory ohne gültigen Header")
            self.slot_size = int(msg["slot_size"])
            self.apply_settings(msg.get("settings", {}))
            send(self.sock, {"type": "welcome", "proto": PROTO, "engine": __version__, "status": self.shared.status}, self.send_lock)
            print(f"[engine] verbunden: {self.source}")
        elif t == "settings":
            self.apply_settings(msg.get("settings", {}))
        elif t == "frame":
            self.counts["empfangen"] += 1
            with self.frame_cv:
                self.latest_frame = msg  # ältere, noch nicht verarbeitete Frames verwerfen
                self.frame_cv.notify()
        elif t == "snapshot":
            self._snapshot("manuell", force=True)
        elif t == "bye":
            self.alive = False

    def _read_frame(self, msg: dict) -> np.ndarray:
        w, h, stride, slot = int(msg["width"]), int(msg["height"]), int(msg["stride"]), int(msg["slot"])
        off = HEADER + slot * self.slot_size
        buf = np.frombuffer(self.shm, dtype=np.uint8, count=stride * h, offset=off).reshape(h, stride)
        bgra = buf[:, : w * 4].reshape(h, w, 4)
        return np.ascontiguousarray(bgra[..., :3])  # Kopie: das Plugin schreibt den Slot gleich wieder

    def _process_loop(self) -> None:
        while self.alive:
            with self.frame_cv:
                while self.alive and self.latest_frame is None:
                    self.frame_cv.wait(timeout=1.0)
                msg, self.latest_frame = self.latest_frame, None
            if msg is None or not self.alive:
                continue
            try:
                if not self.settings.get("enabled", True):
                    continue
                if not self.ensure_pipeline():
                    send(self.sock, {"type": "status", "text": self.shared.status}, self.send_lock)
                    continue
                frame = self._read_frame(msg)
                self.last_frame_img = frame
                if self.settings.get("debug") and not getattr(self, "_first_saved", False):
                    # Prüfbild: so kommt das Bild der OBS-Quelle bei der Engine an (Format/Farben/Ausschnitt)
                    self._first_saved = True
                    self.snapshots.save(frame, "erstes-bild", {"source": self.source, "frame_msg": msg}, force=True)
                tracks = self.pipeline.process(frame)
                self.counts["verarbeitet"] += 1
                self._auto_snapshots()
                send(self.sock, self._result(msg["seq"], tracks, frame.shape), self.send_lock)
                self.counts["gesendet"] += 1
                if time.time() - self._last_count_log >= 5:
                    self._last_count_log = time.time()
                    print(f"[engine] {self.source}: {self.counts}, Helligkeit {frame.mean():.0f}, Karten {len(tracks)}", flush=True)
            except (ConnectionError, OSError):
                self.alive = False
            except Exception:
                self.counts["fehler"] += 1
                traceback.print_exc()

    # -------- Ergebnis
    def _result(self, seq: int, tracks, shape) -> dict:
        st = self.settings
        out = []
        self._prune_labels({t.id for t in self.pipeline.tracker.tracks if t.state != "rejected"})
        hl = self.current_highlight()
        show_labels = self._labels_visible()
        for t in tracks:
            label = self.label_for(t.id) if t.state != "rejected" else ""
            highlighted = bool(hl) and label == hl
            if t.identity is None and not st["debug"] and not highlighted and not (show_labels and t.state == "failed"):
                continue
            panels = []
            for key, ver, anchor, img in self.overlay.panels(t, shape[1]):
                p = {"key": key, "ver": ver, "anchor": anchor, "w": int(img.shape[1]), "h": int(img.shape[0])}
                if self.sent_versions.get(key) != ver:
                    p["data"] = base64.b64encode(np.ascontiguousarray(img).tobytes()).decode("ascii")
                    self.sent_versions[key] = ver
                panels.append(p)
            if show_labels and label:
                card_w = float(np.linalg.norm(t.display[0] - t.display[1]))
                ver, img = self.overlay.label_badge(label, self.overlay.track_color(t), card_w)
                key = f"{t.id}:label"
                p = {"key": key, "ver": ver, "anchor": "corner", "w": int(img.shape[1]), "h": int(img.shape[0])}
                if self.sent_versions.get(key) != ver:
                    p["data"] = base64.b64encode(np.ascontiguousarray(img).tobytes()).decode("ascii")
                    self.sent_versions[key] = ver
                panels.append(p)
            out.append({
                "id": t.id,
                "corners": t.display.round(1).tolist(),
                "color": list(self.overlay.track_color(t)),
                "thickness": float(st["thickness"]),
                "state": t.state,
                "label": label,
                "highlight": highlighted,
                "panels": panels,
            })
        # Nicht gesendete Karten gibt das Plugin samt Texturen frei → beim nächsten Auftauchen Panels neu senden
        sent = {str(o["id"]) for o in out}
        for key in [k for k in self.sent_versions if ":" in k and k.split(":", 1)[0] not in sent]:
            del self.sent_versions[key]
        res = {"type": "result", "seq": seq, "tracks": out, "status": self.shared.status}
        if st["debug"]:
            s = self.pipeline.stats
            text = (f"Detektion {s.detect_ms:.0f} ms @ {s.detect_hz:.1f} Hz · Identifikation {s.identify_ms:.0f} ms · "
                    f"Karten {len(tracks)} · {self.shared.status}")
            res["debug"] = {"detections": [d.corners.round(1).tolist() for d in self.pipeline.last_detections], "text": text}
            res["screen_panels"] = [self._text_panel("debug", text, shape[1])]
        return res

    def _text_panel(self, key: str, text: str, frame_w: int) -> dict:
        """Kleines Textpanel (RGBA) – das Plugin setzt selbst keinen Text. Höchstens einmal pro Sekunde neu
        gerendert/gesendet, dazwischen wird das vorhandene Panel weiterverwendet."""
        now = time.time()
        cached = self._text_cache.get(key)  # (Zeitpunkt, Version, Breite, Höhe)
        if cached is None or now - cached[0] >= 1.0:
            from PIL import Image, ImageDraw

            from cardengine.overlay import _font

            size = max(12, frame_w // 90)
            font = _font(size)
            w = int(ImageDraw.Draw(Image.new("RGBA", (1, 1))).textlength(text, font=font)) + 16
            img = Image.new("RGBA", (w, size + 12), (10, 12, 18, 200))
            ImageDraw.Draw(img).text((8, 4), text, font=font, fill=(200, 255, 200, 255))
            arr = np.array(img)
            cached = (now, hash(text) & 0x7FFFFFFF, int(arr.shape[1]), int(arr.shape[0]))
            self._text_cache[key] = cached
            self._text_data[key] = arr
        _, ver, w, h = cached
        p = {"key": key, "ver": ver, "anchor": "screen", "w": w, "h": h}
        if self.sent_versions.get(key) != ver:
            p["data"] = base64.b64encode(self._text_data[key].tobytes()).decode("ascii")
            self.sent_versions[key] = ver
        return p

    # -------- Snapshots
    def _auto_snapshots(self) -> None:
        events, self.pipeline.events = self.pipeline.events, []
        if not self.settings.get("auto_snapshots"):
            return
        for reason, _ in events:
            if reason in ("unsicher", "fehlgeschlagen", "tausch"):
                self._snapshot(reason)

    def _snapshot(self, reason: str, force: bool = False) -> None:
        if self.last_frame_img is None or self.snapshots is None or self.pipeline is None:
            return
        tracks = [{
            "id": t.id, "state": t.state, "corners": t.corners, "score": t.score,
            "identity": None if t.identity is None else {
                k: getattr(t.identity, k) for k in ("game", "card_id", "name", "set_code", "number", "certain",
                                                     "similarity", "method", "candidates", "reason", "footer_text")
            },
        } for t in self.pipeline.tracker.tracks]
        meta = {"source": self.source, "settings": self.settings, "tracks": tracks,
                "detections": [{"corners": d.corners, "score": d.score} for d in self.pipeline.last_detections]}
        path = self.snapshots.save(self.last_frame_img, reason, meta, force=force)
        if path and reason == "manuell":
            send(self.sock, {"type": "status", "text": f"Snapshot gespeichert: {os.path.relpath(path, HERE)}"}, self.send_lock)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=47820)
    ap.add_argument("--preload-languages", default="de,en")
    ap.add_argument("--idle-exit", type=float, default=0,
                    help="Engine beenden, wenn so viele Sekunden kein OBS-Filter verbunden war (0 = nie)")
    ap.add_argument("--dock-port", type=int, default=47830, help="HTTP-Port für das OBS-Dock (0 = aus)")
    args = ap.parse_args()
    shared = Shared([l for l in args.preload_languages.split(",") if l])
    if args.dock_port:
        from dock_server import start_dock_server

        start_dock_server(shared, args.host, args.dock_port)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((args.host, args.port))
    srv.listen(8)
    print(f"[engine] TCG Card Lens Engine {__version__} lauscht auf {args.host}:{args.port}", flush=True)
    if args.idle_exit > 0:
        def idle_watch():
            while True:
                time.sleep(15)
                if shared.sessions <= 0 and time.time() - shared.last_activity > args.idle_exit:
                    print(f"[engine] {args.idle_exit:.0f} s ohne OBS-Verbindung – beende", flush=True)
                    os._exit(0)

        threading.Thread(target=idle_watch, daemon=True).start()
    while True:
        conn, _ = srv.accept()
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        threading.Thread(target=Session(conn, shared).run, daemon=True).start()


if __name__ == "__main__":
    main()
