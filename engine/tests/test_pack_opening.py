"""Pack-Opening: Hit-Logik, Session-Zählung ohne Duplikate, CSV-Export, Engine-Ergebnis und Dock-API (ohne Modelle/Netz)."""
import csv
import io
import threading
from types import SimpleNamespace

import numpy as np

from cardengine.pack_opening import PackSession, is_hit, price_of, save_csv
from cardengine.pricing.base import PriceInfo, PriceQuote
from cardengine.tracking import Track
from dock_server import DockApi
from server import DEFAULT_SETTINGS, Session


def _price(value, variant="normal"):
    return PriceInfo(id_product=1, quotes={variant: PriceQuote(source="test", variant=variant, trend=value)})


def _ident(card_id="sv3pt5-6", price=168.73, certain=True, name="Glurak ex", game="pokemon"):
    return SimpleNamespace(game=game, card_id=card_id, name=name, display_name=name, set_name="151",
                           number="006/165", certain=certain, price=None if price is None else _price(price))


# ---------------------------------------------------------------- Hit-Logik
def test_hit_needs_certain_identity_and_price_above_threshold():
    assert is_hit(_ident(price=168.73), 10)
    assert is_hit(_ident(price=10.0), 10)  # ab Schwelle
    assert not is_hit(_ident(price=9.99), 10)
    assert not is_hit(_ident(price=168.73, certain=False), 10)  # unsicherer Druck: nie ein Preis, nie ein Hit
    assert not is_hit(_ident(card_id=None), 10)
    assert not is_hit(_ident(price=None), 10)
    assert not is_hit(None, 10)
    assert price_of(_ident(price=None)) is None


def test_hit_is_reported_once_and_also_when_price_arrives_later():
    ps = PackSession(threshold=10)
    ident = _ident(price=None)
    assert ps.observe(1, ident, now=100) is None  # erkannt, Preis noch unbekannt → schon gezählt, kein Hit
    assert len(ps.cards) == 1 and ps.cards[0].price is None
    ident.price = _price(25.0)
    hit = ps.observe(1, ident, now=101)
    assert hit is not None and hit.hit and hit.price == 25.0
    assert ps.observe(1, ident, now=102) is None  # nicht noch einmal
    assert len(ps.hits) == 1


def test_cheap_card_is_counted_but_no_hit():
    ps = PackSession(threshold=10)
    assert ps.observe(1, _ident(card_id="c1", price=0.12)) is None
    assert len(ps.cards) == 1 and not ps.cards[0].hit and ps.total == 0.12


# ---------------------------------------------------------------- Session-Zählung
def test_same_physical_card_counts_once():
    ps = PackSession()
    for i in range(30):  # jeder Frame meldet dieselbe Karte
        ps.observe(7, _ident(), now=100 + i)
    assert len(ps.cards) == 1 and ps.total == 168.73


def test_uncertain_cards_are_not_counted():
    ps = PackSession()
    ps.observe(1, _ident(certain=False))
    ps.observe(2, _ident(card_id=None))
    ps.observe(3, None)
    assert ps.cards == []


def test_two_copies_and_swaps_count_separately():
    ps = PackSession()
    ps.observe(1, _ident(card_id="c1", price=1.0))
    ps.observe(2, _ident(card_id="c1", price=1.0))  # zweites Exemplar (anderer Track)
    ps.observe(2, _ident(card_id="c2", price=2.0))  # nächste Karte an derselben Stelle (Tausch)
    assert [c.key for c in ps.cards] == ["pokemon|c1", "pokemon|c1", "pokemon|c2"]
    assert ps.total == 4.0


def test_user_correction_replaces_instead_of_double_counting():
    ps = PackSession()
    ps.observe(5, _ident(card_id="falsch", price=50.0))
    ps.observe(5, _ident(card_id="richtig", price=3.0), confirmed=True)  # im Dock korrigiert
    assert [c.key for c in ps.cards] == ["pokemon|richtig"] and ps.total == 3.0
    ps.observe(5, _ident(card_id="richtig", price=3.0), confirmed=True)
    assert len(ps.cards) == 1


def test_reset_and_remove():
    ps = PackSession()
    ps.observe(1, _ident(card_id="c1", price=1.0))
    ps.observe(2, _ident(card_id="c2", price=2.0))
    assert ps.remove(ps.cards[0].id) and len(ps.cards) == 1 and not ps.remove(99999)
    ps.observe(1, _ident(card_id="c1", price=1.0))  # entfernt → bei erneuter Meldung wieder gezählt
    assert len(ps.cards) == 2
    ps.reset(now=500)
    assert ps.cards == [] and ps.total == 0 and ps.started == 500
    ps.observe(1, _ident(card_id="c1", price=1.0))  # nach Neustart zählt dieselbe Karte neu
    assert len(ps.cards) == 1


# ---------------------------------------------------------------- CSV
def test_csv_export_is_excel_friendly(tmp_path):
    ps = PackSession(threshold=10)
    ps.observe(1, _ident(card_id="c1", name="Glurak ex", price=168.73), now=1_760_000_000)
    ps.observe(2, _ident(card_id="c2", name="Pikachu; „Spezial“", price=None), now=1_760_000_010)
    text = ps.to_csv()
    rows = list(csv.reader(io.StringIO(text), delimiter=";"))
    assert rows[0][:7] == ["Zeit", "Spiel", "Karte", "Set", "Nummer", "Variante", "Preis"]
    assert rows[1][1:5] == ["Pokémon", "Glurak ex", "151", "006/165"]
    assert rows[1][6] == "168,73" and rows[1][8] == "ja" and rows[1][9] == "pokemon|c1"
    assert rows[2][2] == "Pikachu; „Spezial“" and rows[2][6] == "" and rows[2][8] == ""  # Semikolon sauber gequotet
    path = save_csv(ps, "Kamera: Tisch", root=str(tmp_path))
    raw = open(path, "rb").read()
    assert raw.startswith(b"\xef\xbb\xbf") and "Glurak ex".encode() in raw
    assert "Kamera_ Tisch" in path


# ---------------------------------------------------------------- Engine-Sitzung: Ereignis + Panels
def _shared():
    return SimpleNamespace(new_sid=lambda: "s1", pack_sessions={}, pack_lock=threading.RLock(), dock_seen=0.0,
                           status="bereit", session_list=[], identifier=None)


def _session(**settings):
    from cardengine.overlay import OverlayRenderer

    sess = Session(None, _shared())
    sess.source = "Kamera"
    sess.settings = {**DEFAULT_SETTINGS, "pack_enabled": True, **settings}
    sess.overlay = OverlayRenderer(show_name=False, show_price=False)
    quad = np.float32([[100, 100], [340, 100], [340, 440], [100, 440]])
    t = Track(corners=quad, display=quad.copy(), score=0.9, game="pokemon", first_seen=0, last_confirmed=0)
    t.identity, t.state = _ident(), "identified"
    sess.pipeline = SimpleNamespace(tracker=SimpleNamespace(tracks=[t]))
    return sess, t


def test_session_emits_hit_event_gold_frame_and_panels():
    sess, t = _session()
    events = sess.pack_observe([t], now=1000.0)
    assert events == [{"type": "hit", "track": t.id, "key": "pokemon|sv3pt5-6", "name": "Glurak ex",
                       "price": 168.73, "currency": "EUR"}]
    assert sess.pack_observe([t], now=1000.5) == []  # einmalig
    res = sess._result(1, [t], (1080, 1920, 3), events, now=1000.5)
    assert res["events"] == events
    tr = res["tracks"][0]
    assert tr["hit"] is True and tr["color"] == [255, 200, 40]
    keys = {p["key"]: p for p in res["screen_panels"]}
    assert set(keys) == {"pack:banner", "pack:total", "pack:hits"}
    assert keys["pack:banner"]["pos"] == "top_center" and keys["pack:total"]["pos"] == "top_right"
    banner = keys["pack:banner"]
    assert len(banner["data"]) > 0 and banner["w"] > 200
    # Folgeframe: unverändert → keine Bilddaten erneut senden
    res2 = sess._result(2, [t], (1080, 1920, 3), now=1001.0)
    assert all("data" not in p for p in res2["screen_panels"]) and "events" not in res2


def test_hit_effect_ends_and_disabled_mode_counts_nothing():
    sess, t = _session(hit_duration=0.0)
    sess.pack_observe([t], now=1.0)
    res = sess._result(1, [t], (1080, 1920, 3), now=1.5)
    assert res["tracks"][0]["hit"] is False
    assert {p["key"] for p in res["screen_panels"]} == {"pack:total", "pack:hits"}

    off, t2 = _session(pack_enabled=False)
    assert off.pack_observe([t2]) == [] and off.shared.pack_sessions == {}
    assert "screen_panels" not in off._result(1, [t2], (1080, 1920, 3))


def test_effect_none_counts_but_shows_no_effect():
    sess, t = _session(hit_effect="none", pack_show_hits=False)
    assert len(sess.pack_observe([t])) == 1  # Ereignis trotzdem (Szene/Sound regelt das Plugin)
    res = sess._result(1, [t], (1080, 1920, 3))
    assert res["tracks"][0]["hit"] is False
    assert [p["key"] for p in res["screen_panels"]] == ["pack:total"]


# ---------------------------------------------------------------- Dock-API
def test_dock_pack_state_reset_export_remove(tmp_path, monkeypatch):
    import cardengine.pack_opening as po

    sess, t = _session()
    sess.labels, sess.highlight, sess.last_frame_img = {}, None, None
    sess.shared.session_list = [sess]
    sess.shared.names = SimpleNamespace(name_nowait=lambda rec, lang: rec.name)
    sess.shared.learned = None
    sess.pack_observe([t], now=1000.0)
    api = DockApi(sess.shared)

    state = api._pack_state(sess)
    assert state["enabled"] and state["count"] == 1 and state["hits"] == 1 and state["total"] == 168.73
    assert state["cards"][0]["image"] == "/api/card-image/pokemon/sv3pt5-6" and state["csv"] == "/api/pack/csv/s1"

    data, name = api.pack_csv("s1")
    assert data.startswith("﻿".encode()) and name.endswith(".csv")

    monkeypatch.setattr(po, "save_csv", lambda ps, source, root=None, **kw: save_csv(ps, source, root=str(tmp_path), **kw))
    r = api.pack_export("s1")
    assert r["ok"] and r["path"].startswith(str(tmp_path))

    entry = state["cards"][0]["id"]
    assert api.pack_remove("s1", entry) == {"ok": True}
    assert api.pack_remove("s1", entry)["ok"] is False
    sess.pack_observe([t], now=1001.0)  # wieder gezählt
    r = api.pack_reset("s1")
    assert r["ok"] and r["saved"]  # nicht leere Session vorher gesichert
    assert api._pack_state(sess)["count"] == 0
    assert api.pack_export("s1")["ok"] is False  # leer
