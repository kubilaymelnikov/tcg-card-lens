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
| `hello` | `proto`, `instance`, `source` (Quellname), `shm`, `shm_size`, `slot_size`, `settings` |
| `settings` | `settings` (bei jeder Änderung vollständig) |
| `frame` | `seq`, `slot`, `width`, `height`, `stride`, `ts` – Bild liegt im Slot; Engine verarbeitet nur das jeweils neueste |
| `snapshot` | manueller Debug-Snapshot (Button) |
| `bye` | – |

**Engine → Plugin**

| Typ | Felder |
|---|---|
| `welcome` | `proto`, `engine`, `status` |
| `result` | `seq`, `tracks: [{id, corners: [[x,y]×4] (Analyse-Koordinaten), color: [r,g,b], thickness, panels: [{key, ver, anchor: "top"|"bottom", w, h, data?}]}]`, `status` |
| `status` | `text` (z. B. „Modelle werden geladen“, „Preise: Stand 17.10.“) |

Panel-Bilder (`data`, RGBA, Base64) werden nur gesendet, wenn sich `ver` für einen `key` ändert; das Plugin
hält eine Textur je `key` vor. Positionen berechnet das Plugin pro Frame aus den Ecken (Name über, Preis unter der Karte).

## Einstellungen im OBS-Filter

| Gruppe | Einstellung | Wirkung |
|---|---|---|
| Allgemein | Erkennung aktiv | Filter sendet keine Frames, wenn aus |
| | Kartenspiele: Pokémon / Magic / beide | Suche nur im gewählten Katalog |
| | Modus: Facecam / Tischkamera | Tracking-Profil (schnelle Bewegung vs. viele ruhende Karten) |
| | Erkennungsempfindlichkeit | Schwelle des Detektors |
| | Haltezeit bei Verlust (s), Glättung | Tracking-Verhalten |
| | Analyse-Bildrate | Wie oft Bilder an die Engine gehen (5–30/s) |
| Sprachen | Kartennamen laden: Deutsch, Englisch, Französisch, Italienisch, Spanisch, Portugiesisch, Japanisch | Namen (und künftig Referenzbilder) dieser Sprachen werden geladen |
| | Anzeigesprache | Sprache des Namens im Overlay (Fallback Englisch) |
| Overlay | Kartenname / Preis / Preisverlauf anzeigen | |
| | Zeitraum Preisverlauf (30/90/180 Tage) | |
| | Skalierung, Rahmenstärke, Farbe Pokémon, Farbe Magic | |
| Debug | Debug-Anzeige | Roh-Detektionen, Track-Status, Messwerte |
| | Snapshot jetzt (Button) | speichert Bild + Erkennungsdaten |
| | Automatische Snapshots, Speicherlimit (MB) | Engine speichert auffällige Situationen (unsicher, fehlgeschlagen, Kartentausch), älteste werden bei Überschreiten gelöscht |
| Engine | Adresse/Port, Engine automatisch starten | |

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
