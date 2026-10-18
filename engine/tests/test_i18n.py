"""Oberflächensprache (Deutsch/Englisch): i18n-Hilfe, Overlay-Texte, Engine-Status, Dock-API (ohne Modelle/Netz)."""
import copy
import csv
import io
import pickle
import threading
from types import SimpleNamespace

import numpy as np

from cardengine import i18n
from cardengine.i18n import Msg, t
from cardengine.overlay import OverlayRenderer, fmt_eur, render_pack_total
from cardengine.pack_opening import PackSession
from cardengine.pricing.base import PriceInfo, PriceQuote
from cardengine.tracking import Track
from dock_server import DockApi
from server import DEFAULT_SETTINGS, Session


# ---------------------------------------------------------------- i18n-Hilfe
def test_lang_of_follows_obs_locale_and_defaults_to_german():
    assert i18n.lang_of("de-DE") == "de" and i18n.lang_of("de") == "de" and i18n.lang_of("de_AT") == "de"
    assert i18n.lang_of("en-US") == "en" and i18n.lang_of("fr-FR") == "en" and i18n.lang_of("ja-JP") == "en"
    assert i18n.lang_of(None) == "de" and i18n.lang_of("") == "de"  # alte Plugins: Deutsch wie bisher


def test_t_keys_messages_and_unknown_text():
    assert t("overlay.uncertain", "de") == "Druck unsicher – kein Preis"
    assert t("overlay.uncertain", "en") == "Print uncertain – no price"
    assert t("overlay.uncertain", "en-US") == "Print uncertain – no price"  # Locale direkt
    assert t("overlay.days", "en", n=30) == "30 days"
    assert t("irgendein Text", "en") == "irgendein Text"  # unbekannt → unverändert
    assert t(None, "en") == ""
    # Ältere, als reiner deutscher Text gespeicherte Meldungen werden ebenfalls übersetzt
    assert t("Fußzeile nicht eindeutig lesbar", "en") == "Footer not clearly readable"


def test_msg_is_german_str_and_translates_later():
    m = Msg("reason.code_unreadable", n=4)
    assert isinstance(m, str) and m == "Set-Code nicht lesbar (4 Drucke)" and "Set-Code nicht lesbar" in m
    assert t(m, "en") == "Set code not readable (4 prints)"
    assert t(m, "de") == m
    # Kopieren/Pickeln (Snapshots, Mock) behält die Übersetzbarkeit
    for c in (copy.deepcopy(m), pickle.loads(pickle.dumps(m))):
        assert isinstance(c, Msg) and t(c, "en") == "Set code not readable (4 prints)"
    joined = i18n.join([Msg("status.ready"), Msg("maint.index")])
    assert joined == "bereit · Erkennungs-Index wird ergänzt"
    assert t(joined, "en") == "ready · Updating recognition index"
    nested = Msg("maint.progress_left", label=Msg("maint.images", game="Magic"), n=12)
    assert t(nested, "en") == "Magic images: 12 remaining"


def test_all_keys_exist_in_both_languages():
    assert set(i18n.TEXTS["de"]) == set(i18n.TEXTS["en"])


def test_numbers_and_prices_locale_aware():
    assert fmt_eur(0.15) == "0,15 €" and fmt_eur(0.15, "en") == "€0.15"
    assert fmt_eur(1234.5) == "1.234,50 €" and fmt_eur(1234.5, "en") == "€1,234.50"
    assert fmt_eur(None, "en") == "–"
    assert i18n.fmt_percent(12.34, "de") == "12,3 %" and i18n.fmt_percent(12.34, "en") == "12.3%"
    assert i18n.change_label("30T", "en") == "30d" and i18n.change_label("vs Ø30T", "en") == "vs Ø30d"
    assert i18n.change_label("30T", "de") == "30T"


# ---------------------------------------------------------------- Overlay
def _track(ident):
    quad = np.float32([[100, 100], [340, 100], [340, 440], [100, 440]])
    tr = Track(corners=quad, display=quad.copy(), score=0.9, game="pokemon", first_seen=0, last_confirmed=0)
    tr.identity, tr.state = ident, "uncertain"
    return tr


def _uncertain_ident():
    return SimpleNamespace(game="ygo", card_id=None, name="Dunkler Magier", display_name="Dunkler Magier",
                           certain=False, reason=Msg("reason.code_unreadable", n=4), price=None, set_name=None,
                           number=None)


def _texts_drawn(monkeypatch, fn):
    """Texte, die beim Rendern gezeichnet werden (ImageDraw.text abgefangen)."""
    from PIL import ImageDraw

    seen = []
    orig = ImageDraw.ImageDraw.text

    def spy(self, xy, text, *a, **kw):
        seen.append(text)
        return orig(self, xy, text, *a, **kw)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", spy)
    fn()
    return seen


def test_overlay_uncertain_panel_in_both_languages(monkeypatch):
    tr = _track(_uncertain_ident())
    de = _texts_drawn(monkeypatch, lambda: OverlayRenderer()._render_price(tr, 300))
    assert "Druck unsicher – kein Preis" in de and "Set-Code nicht lesbar (4 Drucke)" in de
    en = _texts_drawn(monkeypatch, lambda: OverlayRenderer(lang="en")._render_price(tr, 300))
    assert "Print uncertain – no price" in en and "Set code not readable (4 prints)" in en


def test_overlay_price_panel_english(monkeypatch):
    price = PriceInfo(id_product=1, quotes={"normal": PriceQuote(source="t", variant="normal", trend=0.15, low=0.05)})
    ident = SimpleNamespace(game="pokemon", card_id="c1", name="Pikachu", display_name="Pikachu", certain=True,
                            reason="", price=price, set_name="151", number="025/165")
    tr = _track(ident)
    en = _texts_drawn(monkeypatch, lambda: OverlayRenderer(lang="en", show_history=False)._render_price(tr, 300))
    assert "€0.15" in en and "from €0.05" in en
    de = _texts_drawn(monkeypatch, lambda: OverlayRenderer(show_history=False)._render_price(tr, 300))
    assert "0,15 €" in de and "ab 0,05 €" in de


def test_pack_panels_english(monkeypatch):
    en = _texts_drawn(monkeypatch, lambda: render_pack_total(213.4, 12, 1, 1920, lang="en"))
    assert "Session value" in en and "€213.40" in en and "12 cards · 1 hit" in en
    de = _texts_drawn(monkeypatch, lambda: render_pack_total(213.4, 1, 2, 1920))
    assert "Session-Wert" in de and "213,40 €" in de and "1 Karte · 2 Hits" in de


def test_csv_english_format():
    ps = PackSession(threshold=10)
    ident = SimpleNamespace(game="pokemon", card_id="c1", name="Charizard ex", display_name="Charizard ex",
                            set_name="151", number="006/165", certain=True,
                            price=PriceInfo(id_product=1, quotes={"normal": PriceQuote(source="t", variant="normal",
                                                                                       trend=168.73)}))
    ps.observe(1, ident, now=1_760_000_000)
    rows = list(csv.reader(io.StringIO(ps.to_csv("en"))))
    assert rows[0][:3] == ["Time", "Game", "Card"] and rows[1][6] == "168.73" and rows[1][8] == "yes"
    assert ps.to_csv().startswith("Zeit;Spiel;Karte")  # Standard bleibt deutsch


# ---------------------------------------------------------------- Engine-Sitzung und Dock
def _session(**settings):
    shared = SimpleNamespace(new_sid=lambda: "s1", pack_sessions={}, pack_lock=threading.RLock(), dock_seen=0.0,
                             status=i18n.join([Msg("status.ready"), Msg("maint.index")]), session_list=[],
                             identifier=None, learned=None, names=SimpleNamespace(preload=lambda langs: None))
    sess = Session(None, shared)
    sess.source = "Kamera"
    sess.settings = {**DEFAULT_SETTINGS, **settings}
    shared.session_list = [sess]
    return sess


def test_session_language_default_german_without_ui_language():
    assert DEFAULT_SETTINGS["ui_language"] == "de"
    sess = _session()
    assert sess.lang == "de" and sess.status_text() == "bereit · Erkennungs-Index wird ergänzt"
    sess.settings.pop("ui_language")  # ganz ohne Angabe (alte Plugins)
    assert sess.lang == "de"


def test_session_language_english_from_obs_locale():
    sess = _session(ui_language="en-US")
    assert sess.lang == "en" and sess.status_text() == "ready · Updating recognition index"
    sess.overlay = None
    sess.apply_settings({**sess.settings})
    assert sess.overlay.lang == "en"


def test_dock_state_reports_language_and_translates(monkeypatch):
    sess = _session(ui_language="en-US")
    sess.pipeline = None
    api = DockApi(sess.shared)
    st = api.state()
    assert st["ui_language"] == "en" and st["sessions"][0]["ui_language"] == "en"
    assert st["sessions"][0]["ui_locale"] == "en-US"
    assert st["status"] == "ready · Updating recognition index"
    assert api.state(lang="de")["status"] == "bereit · Erkennungs-Index wird ergänzt"  # ?lang= überschreibt
    assert api.lang_for() == "en" and api.lang_for(override="de") == "de"
    assert t(api.highlight("gibt-es-nicht", None)["error"], api.lang_for()) == "Source not connected"


def test_dock_without_sessions_has_no_language():
    api = DockApi(SimpleNamespace(session_list=[], learned=None, status=Msg("status.ready"), dock_seen=0.0))
    st = api.state()
    assert st["ui_language"] is None and st["status"] == "bereit" and api.lang_for() == "de"
