# TCG Card Lens

**OBS-Plugin, das Sammelkarten im Kamerabild erkennt und ihren Cardmarket-Preis einblendet** – für
**Pokémon**, **Magic: The Gathering** und **Yu-Gi-Oh!**. Läuft lokal auf deiner NVIDIA-GPU, ohne Cloud-Dienst.

- Mehrere Karten gleichzeitig, auch schräg, gedreht oder im Toploader
- Rahmen um jede Karte, darüber der Name (auf Wunsch auf Deutsch), darunter Preis, Trend und Preisverlauf
- **Keine falschen Preise:** Ist der genaue Druck nicht sicher, steht „Druck unsicher – kein Preis“
- **Karten-Dock in OBS:** unsichere Karten bestätigen oder korrigieren, Karte im Video hervorheben, Cardmarket-Link
- **Lernt aus Bestätigungen:** einmal bestätigt, wird dieselbe Karte unter deinem Licht sofort sicher erkannt

> Status: Version 0.2 – im Alltag nutzbar, aber jung. Rückmeldungen und Fehlerberichte sind willkommen.

## Installation (Windows)

Voraussetzungen: Windows 10/11, OBS Studio 32 oder neuer (getestet mit 32.2), NVIDIA-Grafikkarte ab RTX 20xx (Turing) mit aktuellem
Treiber, etwa 6 GB freier Speicher.

1. Aus dem [neuesten Release](https://github.com/kubilaymelnikov/tcg-card-lens/releases/latest)
   `TCGCardLens-Setup-<version>.exe` herunterladen und starten (keine Administratorrechte nötig).
   Windows SmartScreen warnt bei unsignierten Programmen: „Weitere Informationen → Trotzdem ausführen“.
2. Auf der Seite **Spiele und Sprachen** auswählen, was erkannt werden soll und in welchen Sprachen Kartennamen
   angezeigt werden.
3. Das Setup richtet Python, die KI-Modelle und die Kartendaten ein (einige Minuten, lädt ca. 4 GB).
   Die Erkennungsdaten kommen als fertige Pakete aus dem Release; Kartenbilder werden nicht verteilt.
4. In OBS: Rechtsklick auf deine Kamera-Quelle → **Filter** → **+** → **TCG Card Lens**.
   Die Engine startet automatisch im Hintergrund.
5. Karten-Dock: **Docks → Benutzerdefinierte Browser-Docks…** → Name `Karten`, URL `http://127.0.0.1:47830/`.

Offline-Installation: Den Ordner `packs` (Release-Dateien `tcglens-*.zip` und `packs.json`) neben die Setup-Datei
legen – dann installiert das Setup die Pakete daraus.

## Bedienung

- **Filtereinstellungen:** Spiele, Sprachen, Anzeigesprache, Name/Preis/Verlauf ein- und ausblenden, Größe,
  Rahmenfarben, Tisch- oder Facecam-Modus, Haltezeit, Glättung, Debug und Snapshots.
- **Dock:** Jede Karte hat ein Kennzeichen (A, B, C …), das bei offenem Dock auch im Video steht. Fährt man im Dock
  über eine Karte, leuchtet sie im Video auf.
  - *Ist das die Karte?* – **Ja**, eine **Alternative** wählen (Vorschau beim Überfahren), **suchen**
    (z. B. „Glurak 4/102“, „Sol Ring 409“) oder **Keine Karte**.
  - **↻ Drehen**, wenn eine Karte quer erkannt wurde, **⟳** neu erkennen, **✕** ausblenden.
- Bestätigungen werden lokal als Referenz gelernt und als Trainingsbeispiele gespeichert
  (`engine/data/learned`, bleibt auf deinem Rechner).

## Wie es funktioniert

```
OBS-Filter (C++) ──Bild per Shared Memory──► Engine (Python, GPU)
      ▲                                          │ YOLO11-OBB: Karten finden (gedreht, perspektivisch)
      │                                          │ Tracking + Positions-Lock (ruhiges Overlay)
      └────── Overlay-Panels (RGBA) ◄────────────┤ DINOv3-Embedding → Suche im Kartenindex
                                                 │ OCR der Fußzeile / des Set-Codes → genauer Druck
            Dock (Browser, HTTP 47830) ◄─────────┤ Cardmarket-Preisdatei + Preisverlauf
                                                 └ Lernspeicher aus Bestätigungen
```

Details: [docs/ARCHITEKTUR.md](docs/ARCHITEKTUR.md) · Plugin und Protokoll: [docs/PLUGIN.md](docs/PLUGIN.md) ·
Dock und Lernen: [docs/DOCK.md](docs/DOCK.md) · Datenpakete: [docs/DATENPAKETE.md](docs/DATENPAKETE.md) ·
Yu-Gi-Oh!: [docs/YUGIOH.md](docs/YUGIOH.md) · Bewertung bestehender Projekte: [docs/EVALUATION.md](docs/EVALUATION.md)

## Entwicklung

```
engine/        Python-Engine (Erkennung, Tracking, Preise, Dock-Server, Tests)
obs-plugin/    OBS-Filter in C++ (CMake, OBS-Plugin-Vorlage)
installer/     Inno-Setup-Skript
docs/          Architektur und Entscheidungen
```

Engine einrichten und testen: [engine/README.md](engine/README.md). Kurz:

```powershell
cd engine
.\.venv\Scripts\python.exe -m pytest tests -q
.\.venv\Scripts\python.exe server.py --port 47820 --dock-port 47830
```

## Grenzen

- Nur Windows mit NVIDIA-GPU getestet.
- Drucke mit identischem Artwork unterscheidet die Engine nur über die Fußzeile bzw. den Set-Code; ist der zu klein
  oder spiegelt, gibt es bewusst keinen Preis. Eine Kamera näher am Tisch hilft (Karte ≥ 600 px hoch).
- Seltenheiten im selben Set (Yu-Gi-Oh!) sind optisch kaum zu unterscheiden.
- Preise sind Richtwerte aus der öffentlichen Cardmarket-Preisdatei (einmal täglich aktualisiert), ohne Gewähr.

## Lizenz

[AGPL-3.0](LICENSE). Verwendete Modelle, Code und Datenquellen samt Lizenzen: [NOTICE.md](NOTICE.md).
Nicht verbunden mit Cardmarket, Wizards of the Coast, The Pokémon Company, Konami, Scryfall, TCGdex, YGOPRODeck
oder MTGJSON.

---

**English (short):** OBS Studio filter that detects Pokémon, Magic: The Gathering and Yu-Gi-Oh! cards in a camera
feed, identifies the exact print locally on an NVIDIA GPU and overlays the Cardmarket price and price history.
Uncertain prints never show a price. A browser dock lets you confirm or correct cards; confirmations are learned
locally. Windows installer in the releases. UI and docs are German for now. License: AGPL-3.0.
