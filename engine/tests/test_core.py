import os
import random
import time

import cv2
import numpy as np

from cardengine.detection import ClassicalDetector
from cardengine.detection.base import Detection
from cardengine.geometry import CARD_RATIO, iou_quads, order_corners, rectify, side_lengths
from cardengine.pipeline import CardPipeline
from cardengine.recognition.footer import matches, parse_footer
from cardengine.tracking import CardTracker, TrackerConfig


def synthetic_card(w=488, h=680, seed=0):
    rng = np.random.default_rng(seed)
    card = np.full((h, w, 3), 230, np.uint8)
    cv2.rectangle(card, (0, 0), (w - 1, h - 1), (20, 20, 20), 18)
    cv2.rectangle(card, (40, 80), (w - 40, h // 2), tuple(int(x) for x in rng.integers(0, 255, 3)), -1)
    cv2.putText(card, "TEST", (60, 60), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 0, 0), 3)
    return card


def scene_with_card(quad, size=(1280, 720)):
    w, h = size
    img = np.full((h, w, 3), (60, 90, 120), np.uint8)
    card = synthetic_card()
    src = np.float32([[0, 0], [card.shape[1], 0], [card.shape[1], card.shape[0]], [0, card.shape[0]]])
    m = cv2.getPerspectiveTransform(src, np.float32(quad))
    warped = cv2.warpPerspective(card, m, (w, h))
    mask = cv2.warpPerspective(np.full(card.shape[:2], 255, np.uint8), m, (w, h)) > 0
    img[mask] = warped[mask]
    return img


QUAD = np.float32([[500, 150], [770, 170], [750, 545], [480, 525]])


def test_order_corners_portrait_and_clockwise():
    shuffled = QUAD[[2, 0, 3, 1]]
    q = order_corners(shuffled)
    short, long = side_lengths(q)
    assert short < long
    # im Uhrzeigersinn: Kreuzprodukt der ersten beiden Kanten positiv (Bildkoordinaten, y nach unten)
    a, b = q[1] - q[0], q[2] - q[1]
    assert a[0] * b[1] - a[1] * b[0] > 0


def test_rectify_size():
    img = scene_with_card(QUAD)
    out = rectify(img, order_corners(QUAD))
    assert out.shape[:2] == (680, 488)


def test_classical_detector_finds_card():
    img = scene_with_card(QUAD)
    dets = ClassicalDetector().detect(img)
    assert len(dets) == 1
    assert iou_quads(dets[0].corners, order_corners(QUAD)) > 0.9


def test_footer_parsing():
    f = parse_footer("G  MEW EN 151/165 *")
    assert (f.number, f.total) == ("151", 165)
    m = parse_footer("U 0163\nFDC • EN  CHRISTOPHER MOELLER")
    assert (m.number, m.set_code) == ("163", "FDC")


def test_footer_parsing_real_ocr_output():
    # echte RapidOCR-Ausgaben aus tools-Test (Karten 400–680 px hoch)
    assert parse_footer("W.oky(oweb MEN151/165 Is Kaocked Out").number == "151"
    assert parse_footer("VMAXrule D020/189 is Knocked Out").total == 189
    assert parse_footer("VMAXrule D020/189 is Knocked Out").number == "20"
    m = parse_footer("U0163 TX FDC? EN CHRISTOPHER MOELLER")
    assert (m.number, m.set_code) == ("163", "FDC")
    assert parse_footer("TG05/TG30").number == "TG05"


def test_footer_digit_fallback():
    class Rec:
        number, set_total, set_code = "199", 165, "sv03.5"

    assert matches(Rec, parse_footer("@ wen) 119971658 0 5")) > 0


def test_tracker_follows_motion_and_survives_dropout():
    tr = CardTracker(TrackerConfig.facecam())
    t = 0.0
    tr.step(scene_with_card(QUAD), t)
    tr.update([Detection(order_corners(QUAD), 0.9)], t)
    # Karte bewegt sich 10 px pro Frame nach rechts, Detektor liefert nichts (nur optischer Fluss)
    for i in range(1, 11):
        t += 1 / 30
        q = QUAD + np.float32([10 * i, 0])
        tracks = tr.step(scene_with_card(q), t)
    assert len(tracks) == 1
    expected = order_corners(QUAD + np.float32([100, 0]))
    assert np.abs(tracks[0].corners - expected).max() < 12


def test_tracker_drops_lost_tracks():
    tr = CardTracker(TrackerConfig(lost_after_s=0.5))
    tr.update([Detection(order_corners(QUAD), 0.9)], 0.0)
    assert len(tr.step(scene_with_card(QUAD), 0.4)) == 1
    assert len(tr.step(scene_with_card(QUAD), 0.6)) == 0


def test_pipeline_is_async():
    class SlowDetector(ClassicalDetector):
        def detect(self, frame):
            time.sleep(0.2)
            return super().detect(frame)

    p = CardPipeline(SlowDetector(), detect_hz=30)
    img = scene_with_card(QUAD)
    t0 = time.perf_counter()
    for _ in range(10):
        p.process(img)
    # 10 Frames dürfen nicht 10 × 200 ms dauern – die Detektion blockiert den Aufrufer nicht
    assert time.perf_counter() - t0 < 0.5
    time.sleep(0.3)
    tracks = p.process(img)
    p.close()
    assert len(tracks) == 1


def test_swap_detection_signature():
    from cardengine.pipeline import _signature

    a = scene_with_card(QUAD)
    q = order_corners(QUAD)
    same = float(np.dot(_signature(a, q), _signature(cv2.GaussianBlur(a, (5, 5), 0), q)))
    other = a.copy()
    cv2.rectangle(other, (520, 200), (740, 400), (0, 0, 255), -1)  # andere "Karte" an gleicher Stelle
    swapped = float(np.dot(_signature(a, q), _signature(other, q)))
    assert same > 0.8 and swapped < same


def test_snapshot_limit_and_rate(tmp_path):
    from cardengine.snapshots import SnapshotStore

    store = SnapshotStore("Webcam Test", max_mb=0.2, root=str(tmp_path))
    img = (np.random.default_rng(0).random((400, 600, 3)) * 255).astype(np.uint8)  # schlecht komprimierbar
    assert store.save(img, "unsicher", {"x": 1}) is not None
    assert store.save(img, "unsicher", {"x": 2}) is None  # Rate-Limit
    for i in range(6):
        store.save(img, "manuell", {"i": i})
    time.sleep(1.0)
    store.enforce_limit()
    total = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(store.dir) for f in fs)
    assert total <= 0.2 * 1024 * 1024
