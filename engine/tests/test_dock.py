"""Dock: Kennzeichen, Eingriffe in die Pipeline (bestätigen / verwerfen / neu erkennen), Zustand für die Oberfläche."""
import time
from types import SimpleNamespace

import numpy as np

from cardengine.detection.base import Detection
from cardengine.geometry import order_corners
from cardengine.pipeline import CardPipeline
from dock_server import DockApi
from server import Session, _label_name
from test_core import QUAD, scene_with_card


class NoDetector:
    def detect(self, frame):
        return []


def _pipeline_with_track():
    p = CardPipeline(NoDetector(), detect_hz=1)
    p.tracker.update([Detection(order_corners(QUAD), 0.9)], 0.0)
    return p, p.tracker.tracks[0]


def _identity(card_id="c1", certain=True, **kw):
    base = dict(game="pokemon", card_id=card_id, name="Pikachu", set_code="MEW", set_name="151", number="025/165",
                certain=certain, similarity=0.8, method="embedding", candidates=[], reason="", price=None,
                rotate180=False, alternatives=[])
    base.update(kw)
    return SimpleNamespace(**base)


def test_label_names():
    assert [_label_name(i) for i in (0, 1, 25, 26, 27)] == ["A", "B", "Z", "AA", "AB"]


def test_labels_are_stable_and_reused():
    s = SimpleNamespace(labels={})
    assert Session.label_for(s, 10) == "A"
    assert Session.label_for(s, 11) == "B"
    assert Session.label_for(s, 10) == "A"  # stabil
    Session._prune_labels(s, {11})
    assert Session.label_for(s, 12) == "A"  # frei gewordener Buchstabe wird wiederverwendet


def test_confirm_reject_refresh_run_in_processing_thread():
    p, t = _pipeline_with_track()
    img = scene_with_card(QUAD)
    ident = _identity(similarity=1.0, method="bestätigt")
    p.confirm(t.id, ident)
    assert t.identity is None  # erst beim nächsten Frame
    p.process(img, now=0.05)
    assert t.identity is ident and t.confirmed and t.state == "identified"

    p.refresh(t.id)
    p.process(img, now=0.1)
    assert t.identity is None and not t.confirmed and t.state in ("new", "identifying")

    p.reject(t.id)
    p.process(img, now=0.15)
    assert t.state == "rejected" and len(p.rejected_zones) == 1
    p.close()


class FakeCatalog:
    recs = {
        "c1": SimpleNamespace(game="pokemon", id="c1", name="Pikachu", set_name="151", set_code="MEW",
                              printed_number="025/165", image_url=None),
        "c2": SimpleNamespace(game="pokemon", id="c2", name="Pikachu", set_name="151", set_code="MEW",
                              printed_number="173/165", image_url=None),
    }

    def get(self, game, cid):
        return self.recs.get(cid)


def test_dock_state_lists_uncertain_card_with_alternatives():
    p, t = _pipeline_with_track()
    t.identity = _identity(card_id=None, certain=False, reason="Druck unsicher",
                           alternatives=[("pokemon|c1", 0.8), ("pokemon|c2", 0.79)])
    t.state = "uncertain"
    sess = SimpleNamespace(sid="s1", source="Kamera", pipeline=p, settings={"display_language": "de"},
                           labels={}, highlight=("A", time.time() + 3), last_frame_img=None)
    sess.label_for = lambda tid: Session.label_for(sess, tid)
    sess.current_highlight = lambda: Session.current_highlight(sess)
    shared = SimpleNamespace(session_list=[sess], identifier=SimpleNamespace(catalog=FakeCatalog()),
                             names=SimpleNamespace(name=lambda rec, lang: rec.name), learned=None,
                             status="bereit", dock_seen=0.0)
    state = DockApi(shared).state()
    s = state["sessions"][0]
    assert s["highlight"] == "A"
    card = s["cards"][0]
    assert card["label"] == "A" and card["needs_confirmation"] and card["key"] is None
    assert [c["key"] for c in card["candidates"]] == ["pokemon|c1", "pokemon|c2"]
    assert card["price"] is None  # unsicher → kein Preis
    assert shared.dock_seen > 0
    p.close()
