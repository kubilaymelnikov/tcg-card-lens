"""Gezielte Suche an einer Bildstelle (Dock-Vorschau: Klick / Rahmen)."""
import threading

import numpy as np

from cardengine.detection.base import Detection
from cardengine.geometry import iou_quads, order_corners
from cardengine.pipeline import CardPipeline
from cardengine.probe import probe_point, probe_rect
from test_core import QUAD, scene_with_card


class BlindDetector:
    """Übersieht alles (wie bei Spiegelung in der Mappe) – nur die Kontur-Suche kann helfen."""

    def detect(self, frame, conf=None):
        return []


class LowConfDetector:
    """Findet die Karte nur mit niedriger Schwelle (wie ein unsicherer Detektor bei Glanz)."""

    def __init__(self, quad_fn):
        self.quad_fn = quad_fn
        self.calls = []

    def detect(self, frame, conf=None):
        self.calls.append(conf)
        if conf is None or conf > 0.15:
            return []
        return [Detection(order_corners(self.quad_fn(frame)), 0.12)]


def test_point_finds_card_by_contour_when_detector_is_blind():
    img = scene_with_card(QUAD)
    quad, how = probe_point(BlindDetector(), img, *QUAD.mean(axis=0))
    assert quad is not None and how == "kontur"
    assert iou_quads(quad, order_corners(QUAD)) > 0.8


def test_point_uses_low_confidence_detector_in_zoomed_crop():
    img = scene_with_card(QUAD)

    def card_in_crop(crop):  # Karte im (vergrößerten) Ausschnitt: grob über Helligkeit finden
        import cv2
        g = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        _, m = cv2.threshold(g, 200, 255, cv2.THRESH_BINARY)
        cnt = max(cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)[0], key=cv2.contourArea)
        return cv2.boxPoints(cv2.minAreaRect(cnt)).astype(np.float32)

    det = LowConfDetector(card_in_crop)
    quad, how = probe_point(det, img, *QUAD.mean(axis=0))
    assert how == "detektor" and det.calls and det.calls[0] < 0.15
    assert iou_quads(quad, order_corners(QUAD)) > 0.7


def test_point_on_empty_table_finds_nothing():
    img = scene_with_card(QUAD)
    quad, _ = probe_point(BlindDetector(), img, 100, 650)
    assert quad is None


def test_rect_always_returns_a_quad_close_to_the_card():
    img = scene_with_card(QUAD)
    lo, hi = QUAD.min(axis=0) - 10, QUAD.max(axis=0) + 10
    quad, how = probe_rect(BlindDetector(), img, lo[0], lo[1], hi[0], hi[1])
    assert how == "rahmen" and quad is not None
    assert iou_quads(quad, order_corners(QUAD)) > 0.7


def _run_probe(p, img, **kw):
    out = {}
    th = threading.Thread(target=lambda: out.update(p.probe(**kw)))
    th.start()
    for i in range(40):  # Verarbeitungs-Thread simulieren
        p.process(img, now=0.05 * (i + 1))
        if not th.is_alive():
            break
        th.join(0.02)
    th.join(2)
    return out


def test_pipeline_probe_creates_track_and_reuses_existing():
    img = scene_with_card(QUAD)
    p = CardPipeline(BlindDetector(), detect_hz=1)
    res = _run_probe(p, img, point=tuple(QUAD.mean(axis=0)))
    assert res["found"] and res["how"] == "kontur"
    assert len(p.tracker.tracks) == 1 and p.tracker.tracks[0].score == 1.0
    # Zweiter Klick auf dieselbe Karte: keine Doppelung, sondern Neuerkennung
    t = p.tracker.tracks[0]
    t.state, t.user_rejected = "rejected", True
    res = _run_probe(p, img, point=tuple(QUAD.mean(axis=0)))
    assert res == {"found": True, "track": t.id, "how": "vorhanden"}
    assert len(p.tracker.tracks) == 1 and t.state == "new" and not t.user_rejected
    p.close()


def test_auto_detect_off_skips_detector_but_probe_still_works():
    class Counting(BlindDetector):
        calls = 0

        def detect(self, frame, conf=None):
            Counting.calls += 1 if conf is None else 0  # nur reguläre Läufe zählen (Suche nutzt conf)
            return []

    img = scene_with_card(QUAD)
    p = CardPipeline(Counting(), detect_hz=30)
    p.auto_detect = False
    for i in range(10):
        p.process(img, now=0.05 * (i + 1))
    import time
    time.sleep(0.1)
    assert Counting.calls == 0  # keine automatische Suche
    res = _run_probe(p, img, point=tuple(QUAD.mean(axis=0)))
    assert res["found"] and len(p.tracker.tracks) == 1
    p.close()


def test_dock_settings_are_whitelisted_and_sent_to_plugin(monkeypatch):
    import server

    sent = []
    monkeypatch.setattr(server, "send", lambda sock, msg, lock: sent.append(msg))
    s = server.Session.__new__(server.Session)
    s.settings, s.sock, s.send_lock = dict(server.DEFAULT_SETTINGS), None, None
    s.apply_settings = lambda st: s.settings.update(st)
    assert s.patch_settings({"auto_detect": 0, "pack_enabled": 1, "engine_port": 1}) == \
        {"auto_detect": False, "pack_enabled": True}
    assert sent == [{"type": "settings_patch", "settings": {"auto_detect": False, "pack_enabled": True}}]
    assert s.settings["auto_detect"] is False and s.settings["pack_enabled"] is True
