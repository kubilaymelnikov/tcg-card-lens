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
    t = p.tracker.tracks[0]
    t.locked = True  # ruhig liegende Karte (nur dann fragt das Dock)
    return p, t


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
                             names=SimpleNamespace(name=lambda rec, lang: rec.name, name_nowait=lambda rec, lang: rec.name), learned=None,
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


def _dock_with(p):
    sess = SimpleNamespace(sid="s1", source="Kamera", pipeline=p, settings={"display_language": "de"},
                           labels={}, highlight=None, last_frame_img=None)
    sess.label_for = lambda tid: Session.label_for(sess, tid)
    sess.current_highlight = lambda: None
    shared = SimpleNamespace(session_list=[sess], identifier=SimpleNamespace(catalog=FakeCatalog()),
                             names=SimpleNamespace(name=lambda rec, lang: rec.name, name_nowait=lambda rec, lang: rec.name),
                             learned=None, status="bereit", dock_seen=0.0)
    return DockApi(shared)


def _cards(api):
    return api.state()["sessions"][0]["cards"]


def test_question_stays_while_card_is_rechecked_or_gone(monkeypatch):
    import dock_server

    p, t = _pipeline_with_track()
    api = _dock_with(p)
    t.identity, t.state = _identity(card_id="c1", certain=False, reason="geringe Ähnlichkeit"), "uncertain"
    assert _cards(api)[0]["needs_confirmation"]
    for a in api._asked.values():
        a["first"] -= 5  # Frage war schon eine Weile zu sehen
    # Engine prüft neu (Identität weg) → Frage bleibt mit letztem Inhalt stehen
    t.identity, t.state = None, "identifying"
    c = _cards(api)[0]
    assert c["needs_confirmation"] and c["recheck"] and c["name"] == "Pikachu"
    # Automatisch verworfen → Frage bleibt
    t.state = "rejected"
    assert _cards(api)[0]["sticky"]
    # Karte ganz weg → Frage bleibt noch ASK_GONE_S stehen
    p.tracker.tracks.clear()
    assert _cards(api)[0]["gone"]
    now = time.time()
    monkeypatch.setattr(dock_server.time, "time", lambda: now + dock_server.ASK_GONE_S + 1)
    assert _cards(api) == []
    p.close()


def test_question_releases_after_card_is_certain_for_a_while(monkeypatch):
    import dock_server

    p, t = _pipeline_with_track()
    api = _dock_with(p)
    t.identity, t.state = _identity(card_id="c1", certain=False), "uncertain"
    _cards(api)
    t.identity, t.state = _identity(card_id="c1", certain=True), "identified"
    assert _cards(api)[0]["sticky"]  # gerade sicher geworden: Frage noch offen
    now = time.time()
    monkeypatch.setattr(dock_server.time, "time", lambda: now + dock_server.ASK_RELEASE_S + 1)
    assert "sticky" not in _cards(api)[0]
    p.close()


def test_swap_needs_calm_region_and_two_checks():
    p, t = _pipeline_with_track()
    img = scene_with_card(QUAD)
    from cardengine.pipeline import _signature

    t.identity, t.state = _identity(), "identified"
    t.signature = -_signature(img, t.corners)  # ganz anderer Inhalt
    t.motion = 99.0  # Hand über der Karte
    for i in range(5):
        p._check_swaps(img, now=float(i * 2))
    assert t.identity is not None  # bei Bewegung kein Urteil
    t.motion = 0.0
    p._check_swaps(img, now=20.0)
    assert t.identity is not None  # ein Treffer reicht nicht
    p._check_swaps(img, now=22.0)
    assert t.identity is None and t.state == "new"
    p.close()


def test_user_rejected_spot_stays_rejected_on_motion():
    from cardengine.tracking import CardTracker

    p, t = _pipeline_with_track()
    p.reject(t.id)
    p.process(scene_with_card(QUAD), now=0.05)
    assert t.user_rejected and t.state == "rejected"
    CardTracker._unlock(t)
    assert t.state == "rejected"
    p.close()


def test_moving_card_is_not_asked_yet():
    p, t = _pipeline_with_track()
    t.locked, t.still_frames = False, 0
    t.identity, t.state = _identity(certain=False), "uncertain"
    assert not _cards(_dock_with(p))[0]["needs_confirmation"]
    p.close()


def test_lost_card_is_revived_with_same_id_and_identity():
    from cardengine.tracking import CardTracker, TrackerConfig

    tr = CardTracker(TrackerConfig.facecam())
    tr.update([Detection(order_corners(QUAD), 0.9)], 0.0)
    t = tr.tracks[0]
    t.identity, t.state = _identity(), "identified"
    blank = np.full((720, 1280, 3), (60, 90, 120), np.uint8)
    assert not tr.step(blank, 1.5)  # verloren (z. B. Hand darüber)
    moved = QUAD + np.float32([25, 10])
    tr.update([Detection(order_corners(moved), 0.9)], 2.0)
    assert len(tr.tracks) == 1 and tr.tracks[0] is t and t.identity is not None
    # Nach Ablauf der Merkzeit entsteht ein neuer Track
    tr.step(blank, 4.0)
    tr.step(blank, 9.0)
    tr.update([Detection(order_corners(QUAD), 0.9)], 9.1)
    assert tr.tracks[0] is not t


def test_dismiss_hides_card_without_learning():
    p, t = _pipeline_with_track()
    api = _dock_with(p)
    api.shared.learned = SimpleNamespace(add_negative=lambda *a, **k: (_ for _ in ()).throw(AssertionError("gelernt")),
                                         counts=lambda: {})
    t.identity, t.state = _identity(certain=False), "uncertain"
    assert _cards(api)
    assert api.dismiss("s1", t.id)["ok"]
    p.process(scene_with_card(QUAD), now=0.05)
    assert t.hidden and _cards(api) == []
    p.close()


def test_rotate_turns_track_and_fixes_orientation():
    p, t = _pipeline_with_track()
    before = t.corners.copy()
    t.identity, t.state = _identity(), "identified"
    p.rotate(t.id)
    p.process(scene_with_card(QUAD), now=0.05)
    assert np.allclose(t.corners, np.roll(before, 1, axis=0), atol=1.0)
    assert t.orientation_fixed and t.user_turns == 1 and t.identity is None and t.orient_rev == 1
    p.close()


def test_identifier_prefers_quarter_turn_only_with_clear_margin():
    from cardengine.recognition.identifier import CardIdentifier

    class Emb:
        def __init__(self, sims):
            self.sims = sims  # Ähnlichkeit je Lage in Identifier-Reihenfolge 0°, 180°, 90°, 270°

        def embed(self, images):
            return np.stack([np.array([s, np.sqrt(max(0.0, 1 - s * s))], np.float32) for s in self.sims[:len(images)]])

    class Index:
        def search(self, q, k=10, game=None):
            return [("pokemon|c1", float(q[0]))]

    rec = FakeCatalog.recs["c1"]
    cat = SimpleNamespace(get=lambda g, i: SimpleNamespace(**{**vars(rec), "number": "25", "set_total": 165}))
    quad = order_corners(QUAD)
    ident = CardIdentifier(Index(), cat, Emb([0.55, 0.50, 0.85, 0.40])).identify(np.zeros((720, 1280, 3), np.uint8), quad)
    assert ident.turns == 1
    ident = CardIdentifier(Index(), cat, Emb([0.80, 0.50, 0.83, 0.40])).identify(np.zeros((720, 1280, 3), np.uint8), quad)
    assert ident.turns == 0  # 90° nur knapp besser → aufrecht bleibt
    ident = CardIdentifier(Index(), cat, Emb([0.55, 0.50, 0.85, 0.40])).identify(
        np.zeros((720, 1280, 3), np.uint8), quad, allow_quarter=False)
    assert ident.turns == 0  # vom Nutzer festgelegte Lage: keine Querlage


def test_duplicate_tracks_of_same_card_are_merged():
    from cardengine.tracking import CardTracker, TrackerConfig

    tr = CardTracker(TrackerConfig.table())
    # Karte und (fast deckungsgleich) Toploader im selben Durchlauf erkannt → zwei Tracks
    tr.update([Detection(order_corners(QUAD), 0.9), Detection(order_corners(QUAD + np.float32([4, 3])), 0.8)], 0.0)
    assert len(tr.tracks) == 1
    # Ein bereits erkannter Track gewinnt gegen einen neuen an gleicher Stelle
    t = tr.tracks[0]
    t.identity = _identity()
    tr.tracks.append(type(t)(corners=t.corners + 2, display=t.corners + 2, score=0.9, game=None, first_seen=1.0,
                             last_confirmed=1.0))
    tr.update([], 1.0)
    assert tr.tracks == [t]


def test_auto_rejected_but_likely_card_is_still_asked():
    p, t = _pipeline_with_track()
    t.state, t.score = "rejected", 0.95  # Engine hat aufgegeben, Detektor ist sich der Kartenform sicher
    c = _cards(_dock_with(p))[0]
    assert c["needs_confirmation"] and c["state"] == "failed"
    t.score = 0.3  # unsichere Form (Spielmatte o. ä.) → stumm
    assert _cards(_dock_with(p)) == []
    t.score, t.user_rejected = 0.95, True  # vom Nutzer verworfen → stumm
    assert _cards(_dock_with(p)) == []
    p.close()


def test_single_detection_does_not_stay_forever_on_still_image():
    """Einmalige Fehldetektion (z. B. Mattenkante) darf nicht als ruhender Geister-Track ewig bleiben."""
    from cardengine.tracking import CardTracker, TrackerConfig

    cfg = TrackerConfig.table()
    cfg.lost_after_s = 1.0
    tr = CardTracker(cfg)
    img = scene_with_card(QUAD)
    tr.update([Detection(order_corners(QUAD), 0.5)], 0.0)  # genau einmal gesehen
    t = 0.0
    for _ in range(60):  # 2 s ruhiges Bild, keine weitere Detektion
        t += 1 / 30
        tr.step(img, t)
    assert tr.tracks == []
