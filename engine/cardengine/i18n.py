"""Sprache der Oberfläche (Deutsch/Englisch) für alles, was die Engine anzeigt oder ausliefert.

Die Sprache folgt der OBS-Sprache: Das Plugin sendet `ui_language` (z. B. "de-DE", "en-US"); beginnt sie mit "de",
ist die Oberfläche deutsch, sonst englisch. Fehlt sie (ältere Plugins), bleibt es bei Deutsch.

Texte, die erst später angezeigt werden (Gründe, Statusmeldungen, Fehlermeldungen), werden als `Msg` gespeichert:
ein `str` mit dem deutschen Text (verhält sich überall wie bisher – Logs, Vergleiche, JSON), der zusätzlich
Schlüssel und Parameter kennt und erst bei der Ausgabe mit `t(msg, lang)` übersetzt wird. Ein unbekannter Text
wird unverändert ausgegeben.

    t("overlay.uncertain", "en")                 -> "Print uncertain – no price"
    reason = Msg("reason.code_unreadable", n=4)  -> str: "Set-Code nicht lesbar (4 Drucke)"
    t(reason, "en")                              -> "Set code not readable (4 prints)"
"""
from __future__ import annotations

import re

DEFAULT_LANG = "de"
LANGS = ("de", "en")

TEXTS: dict[str, dict[str, str]] = {
    "de": {
        # Overlay (cardengine/overlay.py)
        "overlay.uncertain": "Druck unsicher – kein Preis",
        "overlay.price_loading": "Preis wird geladen …",
        "overlay.no_price": "kein Preis",
        "overlay.from": "ab {price}",
        "overlay.history_today": "Verlauf wird ab heute aufgezeichnet",
        "overlay.days": "{n} Tage",
        "pack.session_value": "Session-Wert",
        "pack.cards_hits": "{cards} · {hits}",
        "pack.card_one": "{n} Karte",
        "pack.card_many": "{n} Karten",
        "pack.hit_one": "{n} Hit",
        "pack.hit_many": "{n} Hits",
        "pack.recent_hits": "Letzte Hits",
        # Preise (pricing/service.py)
        "price.loading_data": "Preisdaten werden geladen",
        "price.no_product": "kein Cardmarket-Produkt gefunden",
        "price.no_price": "kein Preis auf Cardmarket",
        # Gründe (recognition/identifier.py)
        "reason.name_ambiguous": "Name nicht eindeutig",
        "reason.low_similarity": "geringe Ähnlichkeit",
        "reason.footer_unreadable": "Fußzeile nicht eindeutig lesbar",
        "reason.no_ocr": "mehrere Drucke, keine OCR",
        "reason.asian_print": "OCG-/Asien-Druck (kein TCG-Preis)",
        "reason.code_several_cards": "Set-Code passt zu mehreren Karten",
        "reason.code_ambiguous": "Set-Code nicht eindeutig gelesen",
        "reason.rarities": "{n} Seltenheiten mit Set-Code {code}",
        "reason.code_no_print": "gelesener Set-Code passt zu keinem Druck dieser Karte",
        "reason.no_tcg_print": "kein TCG-Druck bekannt",
        "reason.code_unreadable": "Set-Code nicht lesbar ({n} Drucke)",
        "reason.not_recognized": "nicht erkannt",
        "card.unknown": "Unbekannte Karte",
        # Status (server.py, maintenance.py, data_packs.py)
        "status.loading_models": "Modelle werden geladen …",
        "status.checking_models": "Modelle werden geprüft/geladen …",
        "status.first_start": "Erster Start: Kartendaten werden geladen",
        "status.ready": "bereit",
        "status.ready_missing": "bereit ({games}: Bilder werden im Hintergrund ergänzt)",
        "status.load_error": "Fehler beim Laden: {err}",
        "status.snapshot_saved": "Snapshot gespeichert: {path}",
        "status.debug": "Detektion {det_ms} ms @ {det_hz} Hz · Identifikation {id_ms} ms · Karten {n} · {status}",
        "maint.catalog": "{game}-Katalog wird aktualisiert",
        "maint.images": "{game}-Bilder",
        "maint.progress_left": "{label}: noch {n} fehlen",
        "maint.progress": "{label}: {done}/{total}",
        "maint.index": "Erkennungs-Index wird ergänzt",
        "maint.prices": "Cardmarket-Preise werden geladen",
        "maint.mtgjson": "Magic-Preisverlauf (MTGJSON) wird ergänzt",
        "maint.failed": "Datenaktualisierung fehlgeschlagen: {err}",
        "maint.packs_failed": "Datenpakete fehlgeschlagen: {err}",
        "packs.downloading": "Datenpaket wird geladen: {done} MB",
        "packs.downloading_of": "Datenpaket wird geladen: {done} / {total} MB",
        "packs.none_in_dir": "{game}: kein passendes Paket in {dir}",
        "packs.installing_file": "{game}: Paket wird installiert ({file})",
        "packs.game_error": "{game}: {err}",
        "packs.placeholder": "Datenpaket-Server noch nicht eingerichtet (Platzhalter-URL) – Daten werden lokal aufgebaut",
        "packs.list_unreachable": "Paketliste nicht erreichbar ({err}) – Daten werden lokal aufgebaut",
        "packs.no_pack_for_model": "{game}: kein Paket für Modell {model} angeboten",
        "packs.downloading_game": "{game}: Datenpaket wird geladen ({mb} MB)",
        "packs.verifying": "{game}: Paket wird geprüft und installiert",
        "packs.failed": "{game}: Paket fehlgeschlagen ({err})",
        "packs.installed": "{game}: {cards} Karten, {vectors} Index-Einträge (Stand {date})",
        # Gezielte Suche (pipeline.py) und Dock-API (dock_server.py)
        "probe.timeout": "Zeitüberschreitung – läuft das Kamerabild?",
        "dock.source_not_connected": "Quelle nicht verbunden",
        "dock.session_empty": "Session ist leer",
        "dock.entry_not_found": "Eintrag nicht gefunden",
        "dock.no_frame": "Kein Kamerabild",
        "dock.no_card_found": "Keine Karte gefunden – Rahmen um die Karte ziehen",
        "dock.card_not_in_view": "Karte gerade nicht im Bild – kurz warten oder Karte ruhig hinlegen",
        "dock.card_not_in_catalog": "Karte nicht im Katalog",
        "dock.card_gone": "Karte nicht mehr im Bild",
        "dock.learned_not_loaded": "Lernspeicher nicht geladen",
        "dock.no_setting": "keine änderbare Einstellung",
        "dock.unknown_action": "unbekannte Aktion",
        # CSV-Export (pack_opening.py)
        "csv.columns": "Zeit|Spiel|Karte|Set|Nummer|Variante|Preis|Währung|Hit|Schlüssel",
        "csv.yes": "ja",
    },
    "en": {
        "overlay.uncertain": "Print uncertain – no price",
        "overlay.price_loading": "Loading price …",
        "overlay.no_price": "no price",
        "overlay.from": "from {price}",
        "overlay.history_today": "Price history is recorded from today",
        "overlay.days": "{n} days",
        "pack.session_value": "Session value",
        "pack.cards_hits": "{cards} · {hits}",
        "pack.card_one": "{n} card",
        "pack.card_many": "{n} cards",
        "pack.hit_one": "{n} hit",
        "pack.hit_many": "{n} hits",
        "pack.recent_hits": "Recent hits",
        "price.loading_data": "Loading price data",
        "price.no_product": "no Cardmarket product found",
        "price.no_price": "no price on Cardmarket",
        "reason.name_ambiguous": "Name ambiguous",
        "reason.low_similarity": "low similarity",
        "reason.footer_unreadable": "Footer not clearly readable",
        "reason.no_ocr": "several prints, no OCR",
        "reason.asian_print": "OCG/Asian print (no TCG price)",
        "reason.code_several_cards": "Set code matches several cards",
        "reason.code_ambiguous": "Set code not read unambiguously",
        "reason.rarities": "{n} rarities with set code {code}",
        "reason.code_no_print": "Set code read matches no print of this card",
        "reason.no_tcg_print": "no TCG print known",
        "reason.code_unreadable": "Set code not readable ({n} prints)",
        "reason.not_recognized": "not recognized",
        "card.unknown": "Unknown card",
        "status.loading_models": "Loading models …",
        "status.checking_models": "Checking/loading models …",
        "status.first_start": "First start: loading card data",
        "status.ready": "ready",
        "status.ready_missing": "ready ({games}: images are being added in the background)",
        "status.load_error": "Error while loading: {err}",
        "status.snapshot_saved": "Snapshot saved: {path}",
        "status.debug": "Detection {det_ms} ms @ {det_hz} Hz · identification {id_ms} ms · cards {n} · {status}",
        "maint.catalog": "Updating {game} catalog",
        "maint.images": "{game} images",
        "maint.progress_left": "{label}: {n} remaining",
        "maint.progress": "{label}: {done}/{total}",
        "maint.index": "Updating recognition index",
        "maint.prices": "Loading Cardmarket prices",
        "maint.mtgjson": "Updating Magic price history (MTGJSON)",
        "maint.failed": "Data update failed: {err}",
        "maint.packs_failed": "Data packs failed: {err}",
        "packs.downloading": "Downloading data pack: {done} MB",
        "packs.downloading_of": "Downloading data pack: {done} / {total} MB",
        "packs.none_in_dir": "{game}: no matching pack in {dir}",
        "packs.installing_file": "{game}: installing pack ({file})",
        "packs.game_error": "{game}: {err}",
        "packs.placeholder": "Data pack server not set up yet (placeholder URL) – building data locally",
        "packs.list_unreachable": "Pack list not reachable ({err}) – building data locally",
        "packs.no_pack_for_model": "{game}: no pack offered for model {model}",
        "packs.downloading_game": "{game}: downloading data pack ({mb} MB)",
        "packs.verifying": "{game}: verifying and installing pack",
        "packs.failed": "{game}: pack failed ({err})",
        "packs.installed": "{game}: {cards} cards, {vectors} index entries (as of {date})",
        "probe.timeout": "Timed out – is the camera image running?",
        "dock.source_not_connected": "Source not connected",
        "dock.session_empty": "Session is empty",
        "dock.entry_not_found": "Entry not found",
        "dock.no_frame": "No camera image",
        "dock.no_card_found": "No card found – drag a box around the card",
        "dock.card_not_in_view": "Card not in view right now – wait a moment or lay the card down steadily",
        "dock.card_not_in_catalog": "Card not in catalog",
        "dock.card_gone": "Card no longer in view",
        "dock.learned_not_loaded": "Learning store not loaded",
        "dock.no_setting": "no changeable setting",
        "dock.unknown_action": "unknown action",
        "csv.columns": "Time|Game|Card|Set|Number|Variant|Price|Currency|Hit|Key",
        "csv.yes": "yes",
    },
}

# Deutscher Text ohne Parameter -> Schlüssel (für alte, als reiner Text gespeicherte Meldungen)
_REVERSE = {v: k for k, v in TEXTS["de"].items() if "{" not in v}


def lang_of(ui_language: str | None) -> str:
    """"de-DE"/"de" -> "de", jede andere Sprache -> "en"; fehlt sie ganz, bleibt es beim Standard (Deutsch)."""
    if not ui_language:
        return DEFAULT_LANG
    return "de" if str(ui_language).strip().lower().startswith("de") else "en"


def _render(lang: str, key: str, params: dict) -> str:
    lang = lang if lang in TEXTS else lang_of(lang)
    if key == "_join":
        return params["sep"].join(t(p, lang) for p in params["parts"])
    template = TEXTS[lang].get(key) or TEXTS[DEFAULT_LANG].get(key)
    if template is None:
        return key
    args = {k: (t(v, lang) if isinstance(v, str) else v) for k, v in params.items()}
    try:
        return template.format(**args)
    except (KeyError, IndexError, ValueError):
        return template


def _restore(key: str, params: dict) -> "Msg":
    return Msg(key, **params)


class Msg(str):
    """Übersetzbarer Text. Als `str` der deutsche Text; `tr(lang)` liefert ihn in einer anderen Sprache."""

    def __new__(cls, key: str, **params):
        obj = super().__new__(cls, _render(DEFAULT_LANG, key, params))
        obj.key = key
        obj.params = params
        return obj

    def tr(self, lang: str) -> str:
        return _render(lang, self.key, self.params)

    def __reduce__(self):
        return _restore, (self.key, self.params)


def join(parts, sep: str = " · ") -> Msg | str:
    """Mehrere (übersetzbare) Texte verbinden, z. B. Status „bereit · Erkennungs-Index wird ergänzt“."""
    parts = [p for p in parts if p]
    if len(parts) == 1:
        return parts[0]
    return Msg("_join", parts=tuple(parts), sep=sep)


def t(key_or_text, lang: str | None = None, **fmt) -> str:
    """Text in der Sprache `lang` ("de"/"en" oder ein Locale wie "en-US").

    - `Msg`: mit seinen Parametern übersetzt
    - bekannter Schlüssel ("overlay.uncertain"): mit `fmt` formatiert
    - bekannter deutscher Text ohne Parameter (ältere Daten): übersetzt
    - alles andere: unverändert
    """
    lang = lang_of(lang) if lang not in LANGS else lang
    if key_or_text is None:
        return ""
    if isinstance(key_or_text, Msg):
        return key_or_text.tr(lang)
    text = str(key_or_text)
    if text in TEXTS[DEFAULT_LANG]:
        return _render(lang, text, fmt)
    if text in _REVERSE:
        return _render(lang, _REVERSE[text], {})
    return text


# ---------------------------------------------------------------- Zahlen und Preise
def fmt_number(v: float, lang: str | None = None, decimals: int = 2) -> str:
    """1234.5 -> "1.234,50" (de) bzw. "1,234.50" (en)."""
    s = f"{v:,.{decimals}f}"
    if t_lang(lang) == "de":
        s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return s


def fmt_eur(v: float | None, lang: str | None = None) -> str:
    """Preis in Euro: "0,15 €" (de) bzw. "€0.15" (en); ohne Wert "–"."""
    if v is None:
        return "–"
    if t_lang(lang) == "de":
        return f"{fmt_number(v, 'de')} €"
    return f"-€{fmt_number(-v, 'en')}" if v < 0 else f"€{fmt_number(v, 'en')}"


def fmt_percent(v: float, lang: str | None = None, decimals: int = 1) -> str:
    """12.34 -> "12,3 %" (de) bzw. "12.3%" (en)."""
    if t_lang(lang) == "de":
        return f"{fmt_number(v, 'de', decimals)} %"
    return f"{fmt_number(v, 'en', decimals)}%"


def change_label(label: str, lang: str | None = None) -> str:
    """Zeitraum der Preisänderung: "30T" (de) bzw. "30d" (en), auch in "vs Ø30T"."""
    if t_lang(lang) == "de":
        return label
    return re.sub(r"(\d+)T\b", r"\1d", label)


def plural(n: int, one: str, many: str, lang: str | None = None) -> str:
    return t(one if n == 1 else many, lang, n=n)


def t_lang(lang: str | None) -> str:
    """Sprache normalisieren ("de"/"en"); None = Standard (Deutsch)."""
    return lang if lang in LANGS else lang_of(lang)
