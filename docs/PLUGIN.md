# Phase 3 – OBS-Plugin: Architektur, Protokoll, Einstellungen

## Aufbau

```
OBS ── Filter "TCG Card Lens" (C++, je Quelle eine Instanz) ─────────────────────────────┐
  video_render:  Quelle → gs_texrender (Analyse-Auflösung) → Ring aus 3 gs_stagesurface │
                 (Map mit 2 Frames Versatz, kein GPU-Stall) → Shared Memory              │
                 Overlay: Rahmen (OBS_EFFECT_SOLID) + Panel-Texturen an geglätteten Ecken │
  Netzwerk-Thread: TCP 127.0.0.1:47820 ↔ Engine                                          │
                                                                                          │
Engine (Python, ein Prozess für alle Instanzen) ◀────────────────────────────────────────┘
  gemeinsame Modelle (Detektor, Embedder, OCR, Preise) – GPU-Zugriffe über eine Sperre serialisiert
  je Instanz: Tracker, Einstellungen, Panel-Cache, Snapshot-Regeln
```

Warum zwei Prozesse (Prototyp-Entscheidung, siehe Auftrag): Die Erkennungslogik bleibt unverändert in Python,
OBS bleibt stabil, auch wenn die KI abstürzt oder neu startet (Plugin verbindet sich automatisch neu). Später kann
die Inferenz per ONNX Runtime ins Plugin wandern, ohne das Protokoll zu ändern.

## Protokoll (Version 1)

**Transport:** TCP `127.0.0.1:47820`. Jede Nachricht = 4 Byte Länge (little endian, uint32) + UTF-8-JSON.

**Shared Memory** (vom Plugin angelegt, Name `Local\tcglens_<instanz-id>`):

| Offset | Inhalt |
|---|---|
| 0 | Header 64 Byte: `magic "TCGL"`, `uint32 version=1`, `uint32 slots=2`, `uint32 slot_size`, Rest reserviert |
| 64 + i·slot_size | Slot i: Bild BGRA, Zeilenabstand `stride` (aus der Frame-Nachricht) |

**Plugin → Engine**

| Typ | Felder |
|---|---|
| `hello` | `proto`, `instance`, `source` (Quellname), `shm`, `shm_size`, `slot_size`, `settings`, `ui_language` |
| `settings` | `settings` (bei jeder Änderung vollständig, enthält `ui_language`) |
| `frame` | `seq`, `slot`, `width`, `height`, `stride`, `ts` – Bild liegt im Slot; Engine verarbeitet nur das jeweils neueste |
| `snapshot` | manueller Debug-Snapshot (Button) |
| `pack_reset` | Pack-Opening: neue Session für diese Quelle (Button „Neue Session starten“) |
| `bye` | – |

**Engine → Plugin**

| Typ | Felder |
|---|---|
| `welcome` | `proto`, `engine`, `status` |
| `result` | `seq`, `tracks: [{id, corners: [[x,y]×4] (Analyse-Koordinaten), color: [r,g,b], thickness, panels: [{key, ver, anchor: "top"|"bottom", w, h, data?}]}]`, `status` |
| `status` | `text` (z. B. „Modelle werden geladen“, „Preise: Stand 17.10.“) |

**Sprache der Oberfläche (`ui_language`):** Das Plugin sendet `obs_get_locale()` (z. B. `"de-DE"`, `"en-US"`) in
jeder `settings`-Nachricht (Feld `settings.ui_language`) und zusätzlich direkt im `hello`. Die Engine wählt daraus
Deutsch (Locale beginnt mit `de`) oder Englisch (alle anderen) für alles, was sie rendert oder meldet: Overlay-Panels
(„Druck unsicher – kein Preis“ / „Print uncertain – no price“, Gründe, „ab …“ / „from …“, Tage-Achse, Hit-Banner,
Session-Panels), `status`/`welcome`-Texte, Gründe und Fehlermeldungen im Dock; Preise locale-gerecht (`0,15 €` /
`€0.15`). Fehlt das Feld (ältere Plugins), bleibt alles deutsch. Umsetzung: `cardengine/i18n.py` – Gründe und
Statusmeldungen werden als `Msg` (deutscher Text + Schlüssel/Parameter) gespeichert und erst bei der Ausgabe
übersetzt; unbekannte Texte werden unverändert ausgegeben. Die Texte des Plugins selbst (Eigenschaften, eigene
Statusmeldungen) stehen in `data/locale/de-DE.ini` bzw. `en-US.ini`.

Panel-Bilder (`data`, RGBA, Base64) werden nur gesendet, wenn sich `ver` für einen `key` ändert; das Plugin
hält eine Textur je `key` vor. Positionen berechnet das Plugin pro Frame aus den Ecken (Name über, Preis unter der Karte).

**Optionale Felder im `result`** (ältere Plugins ignorieren sie, ältere Engines lassen sie weg):

| Feld | Inhalt |
|---|---|
| `tracks[].hit` | `true`, solange der Hit-Effekt der Karte läuft → Plugin zeichnet einen goldenen, pulsierenden Rahmen (~3 Hz, Dicke × 1,5…3,5) mit zwei Lagen Goldschein. `color` ist dann schon Gold `[255,200,40]`. |
| `screen_panels[]` | Bildschirm-Panels (`anchor: "screen"`), zusätzlich `pos`: `top_left` · `top_center` · `top_right` · `bottom_left` · `bottom_right` (fehlt = `bottom_left`). Panels mit gleicher `pos` stapelt das Plugin in Listenreihenfolge (oben → nach unten, unten → nach oben), 10 px Rand. Keys: `debug` (Statuszeile), `pack:banner` (Hit-Banner, `top_center`), `pack:total` (Session-Wert), `pack:hits` (Letzte Hits). |
| `events[]` | Einmalige Ereignisse, derzeit nur `{"type": "hit", "track", "key", "name", "price", "currency"}`. Das Plugin sammelt sie in einer eigenen Warteschlange (gehen nicht verloren, wenn ein Ergebnis vom nächsten überholt wird) und löst Szenenwechsel/Sound aus. |

## Einstellungen im OBS-Filter

| Gruppe | Einstellung | Wirkung |
|---|---|---|
| Allgemein | Erkennung aktiv | Filter sendet keine Frames, wenn aus |
| | Kartenspiele: Pokémon / Magic / Yu-Gi-Oh! (je eine Checkbox) | Suche nur in den gewählten Katalogen; an die Engine als `games` = Kommaliste (`"pokemon,mtg"`), alte Werte `both`/`pokemon`/`mtg` bleiben gültig, `all` = alle (siehe `cardengine/games.py`, `docs/YUGIOH.md`) |
| | Modus: Facecam / Tischkamera | Tracking-Profil (schnelle Bewegung vs. viele ruhende Karten) |
| | Erkennungsempfindlichkeit | Schwelle des Detektors |
| | Haltezeit bei Verlust (s), Glättung | Tracking-Verhalten |
| | Analyse-Bildrate | Wie oft Bilder an die Engine gehen (5–30/s) |
| Sprachen | Kartennamen laden: Deutsch, Englisch, Französisch, Italienisch, Spanisch, Portugiesisch, Japanisch | Namen (und künftig Referenzbilder) dieser Sprachen werden geladen |
| | Anzeigesprache | Sprache des Namens im Overlay (Fallback Englisch) – unabhängig von der Oberflächensprache, die der OBS-Sprache folgt (`ui_language`) |
| Overlay | Kartenname / Preis / Preisverlauf anzeigen | |
| | Zeitraum Preisverlauf (30/90/180 Tage) | |
| | Skalierung, Rahmenstärke, Farbe Pokémon, Farbe Magic, Farbe Yu-Gi-Oh | |
| Pack-Opening | Pack-Opening-Modus aktiv (`pack_enabled`, Standard aus) | Hits erkennen, Session zählen, Panels zeigen |
| | Hit-Schwelle (`pack_threshold`, Standard 10 €) | sicher erkannte Karte mit Preis **ab** diesem Wert = Hit |
| | Hit-Effekt (`hit_effect`): goldener Rahmen + Banner / nur Rahmen / nur Banner / kein Effekt | |
| | Dauer des Hit-Effekts (`hit_duration`, 1–10 s, Standard 3 s) | |
| | Panels „Session-Wert“ / „Letzte Hits“ (`pack_show_total`, `pack_show_hits`), Position (`pack_panel_pos`) | |
| | Bei Hit zu Szene wechseln, nach Sekunden zurückwechseln (0 = bleiben, Standard 8 s) | nur Plugin (obs-frontend-api), nicht an die Engine |
| | Bei Hit Medienquelle abspielen | nur Plugin: `obs_source_media_restart` auf die gewählte Medienquelle |
| | Neue Session starten (Button) | sendet `pack_reset` |
| Debug | Debug-Anzeige | Roh-Detektionen, Track-Status, Messwerte |
| | Snapshot jetzt (Button) | speichert Bild + Erkennungsdaten |
| | Automatische Snapshots, Speicherlimit (MB) | Engine speichert auffällige Situationen (unsicher, fehlgeschlagen, Kartentausch), älteste werden bei Überschreiten gelöscht |
| Engine | Adresse/Port, Engine automatisch starten | |

## Pack-Opening-Modus

Logik in `engine/cardengine/pack_opening.py`, eingebunden in `server.py` (`Session.pack_observe` /
`Session.pack_panels`), Dock-Teil in [DOCK.md](DOCK.md#pack-opening-session).

- **Was zählt:** nur Karten mit sicherer Identität (`certain` und `card_id`) – derselbe Grundsatz wie beim Preis.
  Unsichere Drucke kommen nie in die Liste und lösen nie einen Hit aus. Karten ohne Cardmarket-Preis werden gezählt
  (Preis „–“), tragen aber nichts zur Summe bei. Im Dock ausgeblendete oder als „keine Karte“ markierte Tracks zählen nicht.
- **Keine Duplikate:** Schlüssel ist (Track-ID, Kartenschlüssel). Dieselbe Karte, die liegen bleibt, kurz verdeckt
  wird oder wieder aufgenommen wird (gleiche Track-ID), zählt einmal. Ein zweites Exemplar (eigener Track) oder eine
  neue Karte an derselben Stelle (Tausch → neue Identität) zählt neu. Bestätigt der Nutzer im Dock für einen Track eine
  **andere** Karte, ersetzt das den bisherigen Eintrag (Fehlerkennung), statt doppelt zu zählen. Falsch Gezähltes
  lässt sich im Dock entfernen (✕).
- **Hit:** Eintrag mit Preis ≥ Schwelle. Wird genau einmal gemeldet – auch wenn der Preis erst nach der Identität
  ankommt. Die Schwelle gilt für neue Hits; bereits gemeldete bleiben Hits.
- **Session:** je Quellname in der Engine (überlebt ein Neuverbinden des Filters, nicht den Neustart der Engine).
  Sie beginnt beim ersten Frame mit eingeschaltetem Modus; „Neue Session“ (Filter-Button oder Dock) leert sie.
- **Effekt im Video:** goldener Pulsrahmen um die Karte und Banner „HIT! 168,73 €“ mit Name und Set oben mittig, für
  `hit_duration` Sekunden. Text rendert ausschließlich die Engine (RGBA-Panels), das Plugin zeichnet nur Texturen und
  Linien.
- **Szenenwechsel / Sound:** löst das Plugin im UI-Thread aus (`obs_queue_task(OBS_TASK_UI)`, nie aus dem
  Grafik-Thread). Szenenwechsel über `obs_frontend_set_current_scene` – im Studio-Modus ist das ein Übergang ins
  Programm. Zurückgewechselt wird nur, wenn noch die Hit-Szene läuft (sonst hat jemand bewusst umgeschaltet); bei
  mehreren Hits in Folge bleibt die ursprüngliche Szene das Ziel. Der Sound ist eine **Medienquelle**, die in einer
  laufenden Szene liegen muss (sonst wird ihr Ton nicht gemischt): z. B. in der Kamera-Szene eine Medienquelle
  `Hit-Sound` mit „Wiedergabe neu starten, wenn Quelle aktiv wird“ **aus** und ohne Schleife; das Plugin startet sie
  per `obs_source_media_restart` neu.
- Build: Das Plugin linkt `obs-frontend-api` (`ENABLE_FRONTEND_API=ON`, Standard). Ohne bleibt alles außer dem
  Szenenwechsel funktionsfähig.

### Live in OBS testen

1. Plugin bauen und installieren (`obs-plugin/tools/install-plugin.ps1`), OBS neu starten (Engine startet mit).
2. Filter **TCG Card Lens** der Kamera-Quelle öffnen → Gruppe **Pack-Opening** → Modus an. Zum schnellen Test die
   Hit-Schwelle auf z. B. 0,50 € senken.
3. Oben rechts erscheint „Session-Wert 0,00 € · 0 Karten · 0 Hits“.
4. Eine Karte mit bekanntem Preis vor die Kamera halten. Sobald sie sicher erkannt ist (Name ohne „?“, Preis im
   Overlay), steigt der Session-Wert; liegt der Preis über der Schwelle: goldener Pulsrahmen + Banner für ~3 s,
   „Letzte Hits“ erscheint, im OBS-Log steht `Hit: <Name> <Preis> EUR`.
5. Karte weglegen und wieder zeigen: Liegt sie noch im Bild (gleicher Track), zählt sie nicht erneut.
6. Dock öffnen (`http://127.0.0.1:47830/`) → Abschnitt **Session**: Liste mit Zeit, Name, Preis, ★ für Hits.
   **CSV exportieren** → `engine/data/sessions/<Quelle>/<Start>.csv` (in Excel öffnen: Semikolon, Dezimalkomma).
   **Neue Session** (zweimal klicken) sichert die alte Liste als CSV und beginnt neu.
7. Szenenwechsel: eine Szene „Hit“ anlegen, im Filter als Ziel wählen, Rückwechsel 5 s → Hit auslösen.
   Sound: Medienquelle mit kurzem Jingle in die Kamera-Szene legen, im Filter wählen → Hit auslösen.
8. Unsicherer Druck (Name mit „?“, „Druck unsicher – kein Preis“) darf **nie** zählen oder einen Hit auslösen.

## Debug-Snapshots (kontinuierliche Verbesserung)

Ablage: `engine/data/snapshots/<quelle>/<datum>/<zeit>-<grund>.jpg` + gleichnamige `.json` mit Tracks, Ecken,
Identifikationen (Top-Kandidaten, Ähnlichkeiten, OCR-Text), Einstellungen und Engine-Version.
Gründe: `manuell`, `unsicher`, `fehlgeschlagen`, `tausch`, `keine-karte`. Rate-Limit je Grund, Gesamtgröße begrenzt
(Standard 500 MB, älteste zuerst gelöscht). Diese Daten dienen als echte Testmenge für `tools/eval_*` und zum
Nachtraining (Detektor, Schwellen).

## Stand (2026-10-17)

- Plugin baut mit VS 2022 Build Tools + CMake 4.4 (Vorlage obs-plugintemplate, OBS-Quellen 31.1.1) und läuft in
  **OBS 32.2.2**. Installation: `obs-plugin/tools/install-plugin.ps1` → `C:\ProgramData\obs-studio\plugins\tcg-card-lens`.
- Live-Test (Elgato Virtual Camera, Tischansicht): Readback 1920×1080, ~12 Analysen/s, OBS 30/30 fps bei ~1 % CPU,
  Rahmen + Panels + Debug-Statuszeile direkt im Videobild; Engine-Neustart → Plugin verbindet sich selbst neu;
  Quellname kommt korrekt an; Debug-Prüfbild + automatische Snapshots werden abgelegt.
- **Gelöster Fehler:** `obs_source_video_render(target)` zeichnet bei synchronen Quellen (Bild, Spielaufnahme) innerhalb
  eines Filters nichts → schwarzes Rücklesebild. Lösung: `obs_source_skip_video_filter()` im Texrender (wie libobs).
- **Gelöster Fehler:** Elgato Virtual Camera fällt bei erzwungenen 60 fps (`CAP_PROP_FPS`) auf 1 fps.

**Offen**
1. Schwellen mit echten Snapshots kalibrieren (Kapuno live „unsicher“ bei Ähnlichkeit ~0,6–0,7).
2. Karte in der Hand: Rahmen hängt bei schneller Bewegung nach (Tracking/Latenz optimieren).
3. Magic-Index nach Abschluss des Bild-Downloads neu bauen.
4. Rücklesen ohne GPU-Stall (Ring aus Stagesurfaces) – erst messen, dann optimieren.
5. Installer (Plugin + Engine + Modelle) und Engine-Autostart als Standard.

## Ruhiges Tracking (2026-10-17)

| Maßnahme | Wirkung (Messung `tools/eval_stability.py`, Standbild mit Sensorrauschen) |
|---|---|
| Positions-Lock bei ruhendem Kartenbereich (Bewegung je Karte aus Differenzbild), 20 Detektionen per Median gemittelt, danach fix | Zittern 2,25 px → **0,00 px** nach ~2–3 s Einschwingen |
| Subpixel-Kantenanpassung (erste deutliche Kante von außen) | perspektivisch exakter Rahmen, auch bei dunklem Kartenrand |
| Anzeige gleitet auf die feste Position | größter Sprung 8,2 px → 1,5 px (einmalig beim Einschwingen) |
| „Keine Karte“-Gedächtnis für Fehlalarme (Spielmatte, Monitor) | keine Endlosversuche; erneute Prüfung erst bei Bewegung an der Stelle |
| Adaptive Detektionsrate | ruhige Szene + alle Karten fixiert → 1 Hz statt 8 Hz (live in OBS bestätigt) |

## Installer

`installer/build-installer.ps1` baut `TCGCardLens-Setup-<version>.exe` (Inno Setup, ~3 MB). Der Installer legt
das Plugin nach `%ProgramData%\obs-studio\plugins` und die Engine nach `%LOCALAPPDATA%\TCGCardLens`. Danach startet er
`setup-engine.ps1`.

**Auswahlseite „Spiele und Sprachen“** (nach den Aufgaben, nur wenn die Engine jetzt eingerichtet wird):
- Spiele: alle vorausgewählt. Die Liste steht als `GameList` im `.iss`, neue Spiele werden dort ergänzt.
- Namenssprachen: Deutsch, Englisch, Französisch, Italienisch, Spanisch, Portugiesisch, Japanisch.
  Vorausgewählt sind Deutsch und Englisch.

Die Auswahl geht als `-Games pokemon,mtg -Languages de,en` an `setup-engine.ps1`. Das Skript speichert sie über
`python -m cardengine.config` in `engine\data\config.json`. Liegt neben der Setup-exe ein Ordner `packs`, kommt
`-PackDir <ordner>` dazu (Offline-Installation).

`setup-engine.ps1` richtet Python 3.12 ein, dazu PyTorch (CUDA oder CPU), die Pakete und die Modelle. Danach
speichert es die Auswahl und ruft `cardengine.maintenance --initial` auf. Das installiert je Spiel ein
**Datenpaket**: Kartenkatalog + Erkennungs-Index, ohne Bilder, Magic ca. 87 MB, Pokémon ca. 16 MB. Fehlt ein Paket,
baut das Skript wie bisher lokal auf (siehe [DATENPAKETE.md](DATENPAKETE.md)). Neue Sets ergänzt die Engine im
Hintergrund (`cardengine/maintenance.py`), Bilder lädt sie dabei nur für Karten ohne Index-Eintrag.

Weitere Parameter: `-PackUrl` (Basis-URL der Pakete) und `-SkipData`. Ohne Parameter, etwa über die Verknüpfung
„Engine neu einrichten“, bleibt die bisherige Auswahl.

Engine-Autostart ist im Filter standardmäßig aktiv: ohne Fenster, Log in `engine\data\engine.log`, Ende nach
15 min ohne OBS.
Hinweis: PowerShell-Skripte müssen als UTF-8 **mit BOM** gespeichert sein (Windows PowerShell 5.1).
