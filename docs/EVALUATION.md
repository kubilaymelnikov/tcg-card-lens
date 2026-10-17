# Evaluation bestehender Lösungen und Architekturentscheidung

Stand: 2026-10-17. Ergänzung zu [ARCHITEKTUR.md](ARCHITEKTUR.md). Ziel: möglichst viel Bewährtes wiederverwenden.

## 1. Untersuchte Projekte

| Projekt | Status | Spiele | Mehrere Karten | Verfahren | Editionen | Echtzeit / GPU | OBS | Lizenz (Code / Gewichte) | Urteil |
|---|---|---|---|---|---|---|---|---|---|
| [ULiege-VIULab/tcg-ar](https://github.com/ULiege-VIULab/tcg-ar) ([Paper](https://arxiv.org/abs/2607.02090)) | Forschungscode, 17★, letzte Änderung 08/2026 | Pokémon | ja (14 Karten) | Oriented R-CNN (mmrotate) → affiner Ausschnitt → ResNet-50 ArcFace | **Schwachpunkt**: 85 % Top-1 auf echten Fotos, Fehler v. a. Drucke derselben Karte; kein Unsicherheits-Flag | ~4 Hz Erkennung auf 2080 Ti | RTSP über MediaMTX, kein Plugin | GPL-3.0 / unklar | **Referenz** (Benchmark-Zahlen, Idee synthetisches Training) |
| [philmantatsky/real-time-trading-card-recognizer](https://github.com/philmantatsky/real-time-trading-card-recognizer) | 1-Tages-Projekt, 0★ | Pokémon | nein (nutzt 1) | klassische Konturen → ResNet-50 ArcFace (NVIDIA TAO) über WSL/Docker | wählt Variante per fester Priorität (rät) | schnell, aber WSL-Socket | nein | MIT / NGC-EULA | **Referenz** (Annahmeregel Top-1/Top-2-Abstand) |
| [matteot11/open-card-collector](https://github.com/matteot11/open-card-collector) | WIP, aktiv (10/2026) | **Magic + Pokémon** | ja | YOLO11n-OBB → Entzerrung → DINOv3-S kontrastiv nachtrainiert → Kosinus-Suche gegen lokalen Scryfall/TCGdex-Katalog | rangiert Drucke, kein OCR | GPU, Detektor ~20 ms | nein | MIT / AGPL (Detektor), DINOv3-Lizenz (Embedder) | **Übernommen**: Embedder-Modell, Detektor-Modell, Trainings-Rezept |
| [Ximilar-com/cardstream](https://github.com/Ximilar-com/cardstream) | aktiv (10/2026) | 25+ TCGs | 1 Karte je Bild | RF-DETR-Seg → Maske → Viereck; Identifikation per kostenpflichtiger API | über API | CPU 80 ms | virtuelle Kamera / RTMP | **Apache-2.0 / Apache-2.0** | **Teilweise übernommen**: Masken→Viereck-Code; Modell als lizenzfreie Alternative (multi-card schwach) |
| [HanClinto/CollectorVision](https://github.com/HanClinto/CollectorVision) | aktiv, 53★ | Magic (andere experimentell) | 1 je Aufruf | Eckendetektor → MobileViT-ArcFace (Illustration + Set) | Set-Labels im Training | CPU < 100 ms | nein | AGPL / AGPL | Referenz / optionaler Magic-Vergleich |
| [Adrian-Sandwich/yugioh-ar](https://github.com/Adrian-Sandwich/yugioh-ar) | neu | Yu-Gi-Oh | ja, Tracking | OBB → Index; **OCR als zusätzliche Evidenz** | Konflikte werden angezeigt | RTX 4070 44 ms | nein | MIT | Idee übernommen (Evidenz-Fusion, getrennte Karten-/Artwork-/Track-IDs) |
| [ChicoCifrado/VTES-for-OBS](https://github.com/ChicoCifrado/VTES-for-OBS) | neu | VTES | ja | **natives OBS-Plugin** (C++): YOLO-OBB + ViT + Tesseract, ORT mit TensorRT/DirectML | – | ja | **nativer Filter** | GPL-2.0 | Vorlage für Phase 3 (lesen, nicht abhängig machen) |
| [royshil/obs-backgroundremoval](https://github.com/royshil/obs-backgroundremoval) | aktiv, 4,6k★ | – | – | ONNX-Runtime-Filter, Frame-Readback, Delay-Load von ORT | – | ja | **nativer Filter** | GPL-3.0 | Vorlage für Frame-Readback/ORT-Bündelung |
| ByteTrack-cpp, Norfair, hnswlib/USearch | gepflegt | – | – | Tracker / Vektorsuche | – | – | – | MIT, BSD-3, Apache | ByteTrack-cpp + hnswlib/USearch für das C++-Plugin vorgesehen |

Kommerzielle Referenzen: **Rocketrader** (Handy-App, Pokémon, bis 16 Karten pro Foto, Varianten manuell korrigierbar,
kein Streaming), **Hype Overlay**, **HitScanTCG**, **Pokemog**, **Convoke**, **Cardikyu** – jeweils eine Karte, Browserquelle
bzw. Zuschauer-Erweiterung. **„mTCG Scanner Studio“ wurde nicht gefunden** – Link bitte nachreichen.
**Kein Produkt kombiniert mehrere getrackte Karten, Editions-Unsicherheit und Cardmarket-Preise in einem nativen OBS-Filter.**

## 2. Eigene Messungen (gleiche Bedingungen für alle Kandidaten)

### Identifikation (`tools/eval_identify.py`, 400 zufällige Katalogkarten, 300 px hoch im Bild, Perspektive,
ungenaue Ecken ±1,2 %, Unschärfe, Rauschen, Glanz, dunkles Licht)

| Embedding | Spiel | exakter Druck | richtiger Name | Fehler mit gleichem Namen |
|---|---|---|---|---|
| DINOv2-S, ohne Kartentraining | Pokémon (22k) | 65,0 % | 71,5 % | 26 / 140 |
| **open-card-collector DINOv3-S** | Pokémon (22k) | **95,0 %** | **99,8 %** | 19 / 20 |
| **open-card-collector DINOv3-S** | Magic (6,9k Bilder aus 15 Sets) | **91,2 %** | **97,2 %** | 24 / 35 |

| open-card-collector DINOv3-S **+ Licht-Normierung** (Weißabgleich, Tonwertspreizung, CLAHE) | Pokémon | **96,5 %** | **99,8 %** | – |

**Komplette Identifikation inkl. Unsicherheitslogik** (Pokémon, 300 Karten, 340 px): 74,7 % sicher & richtig,
**0 % sicher & falsch**, 22,7 % „Name sicher, Druck unsicher“ (→ kein Preis), 2,3 % keine Karte.
Die Fußzeilen-OCR kann im synthetischen Test nicht greifen (Referenzbilder nur 340 px hoch); bei echten Karten
ab ~400 px Höhe liest RapidOCR Nummer/Set (Magic auch bei 300 px).

**Echtes Facecam-Bild** (dunkler Raum, deutsche Karte „Kapuno“ 30th #097, englische Referenz): richtige Karte auf
Platz 1, Ähnlichkeit ohne Normierung 0,40 (Abstand zu Platz 2: 0,04) → mit Normierung 0,53 (Abstand 0,21).
Schwellen müssen mit echten Aufnahmen kalibriert werden (synthetische Ähnlichkeiten liegen deutlich höher).

→ Ein generisches Embedding reicht nicht; das kartenspezifisch nachtrainierte Modell ist um Klassen besser. Fast alle
Restfehler sind **Drucke derselben Karte** → genau dort setzen OCR und Unsicherheits-Flag an. Falsche Namen haben
sehr niedrige Ähnlichkeit (0,34–0,46) und werden durch die Schwelle abgefangen.
Einschränkung: Abfragen stammen aus den Referenzbildern selbst → optimistisch; echte Kamerabilder folgen.

### Lokalisierung (`tools/eval_detect.py`, 150 Szenen mit 524 Karten, Facecam- und Tischszenen gemischt)

| Detektor | Recall IoU≥0,75 | Recall IoU≥0,9 | Fehlalarme | Eckfehler | ms/Bild (RTX 2080 S) |
|---|---|---|---|---|---|
| klassisch (eigen) | 33,8 % | 26,3 % | 52 | 2,9 % | 5 |
| **open-card-collector YOLO11n-OBB** | **87,8 %** | 51,3 % | 35 | 5,0 % | 19 |
| cardstream RF-DETR-Seg | 23,9 % | 18,5 % | 15 | 3,4 % | 23 |

Echtes Facecam-Bild (Elgato MK.2, eine Karte in der Hand): alle drei finden die Karte; RF-DETR-Seg ohne
Fehlalarm, YOLO meldet zusätzlich einen Monitor, klassisch einen Karton.
→ YOLO-OBB hat den besten Recall; OBB kann aber keine Perspektive → **Ecken werden lokal nachverfeinert**.
Fehlalarme sind unkritisch, weil Nicht-Karten an der Ähnlichkeitsschwelle der Identifikation scheitern.
Einschränkung: synthetische Szenen aus unserem eigenen Generator; Bewertung mit echten Tisch-/Facecam-Aufnahmen folgt.

## 3. Entscheidung: **Option 2 – Komponenten kombinieren**

| Option | Bewertung |
|---|---|
| 1. Projekt forken | Kein Projekt erfüllt mehr als ~40 % der Anforderungen (Multi-Karte + Tracking + Editionen + Preise + OBS). TCG-AR: falscher Stack (mmrotate, GPL, eine feste Kamera). open-card-collector: kein Tracking, kein OCR, keine Preise/OBS – aber die beste Basis für die KI-Modelle. |
| **2. Komponenten kombinieren** | **Gewählt.** Fertige, gemessene Modelle übernehmen; eigene Arbeit nur dort, wo nichts existiert (Tracking mehrerer Karten, Editions-Logik, Preise, OBS-Filter). |
| 3. Alles selbst | Verworfen: das eigene, untrainierte Embedding lag bei 65 % – ein eigenes Training würde das open-card-collector-Ergebnis nur nachbauen. |

| Baustein | Lösung | Herkunft |
|---|---|---|
| Kartenlokalisierung | YOLO11n-OBB (Magic+Pokémon) + lokale Eckenverfeinerung | open-card-collector (Modell), eigene Verfeinerung (vorhandener klassischer Detektor) |
| Lizenzfreie Alternative | RF-DETR-Seg | cardstream (Apache-2.0), ggf. auf Mehrkarten-Szenen nachtrainieren |
| Entzerrung | Homographie | eigen (vorhanden) |
| Kartenidentifikation | DINOv3-S Karten-Embedding, Kosinus-Suche | open-card-collector (Modell) |
| Edition / Reprints | OCR Fußzeile (RapidOCR / PP-OCR) als **Evidenz**, Top-1/Top-2-Abstand, Unsicherheits-Flag | Ideen aus yugioh-ar, foilstack, rttcr; Fußzeilen-Parser aus dem Web-Prototyp |
| Tracking | KLT + Homographie je Karte, IoU-Zuordnung (getestet) | eigen; im Plugin ByteTrack-cpp (MIT) für die Zuordnung |
| Referenzdaten | Katalog aus Scryfall-Bulk + TCGdex, Bilder lokal | eigen (vorhanden), entspricht open-card-collector |
| Preise | Cardmarket-Preisdatei (täglich) + Snapshots + MTGJSON | eigen (Phase 2) |
| OBS-Plugin | obs-plugintemplate + Muster aus obs-backgroundremoval / VTES-for-OBS | Phase 3 |

**Verworfen** wird nur das untrainierte DINOv2 als Standard (bleibt als Vergleich). Der eigene YOLO-Trainingsplan
entfällt vorerst – das fertige Modell wird übernommen; Nachtraining nur, falls echte Aufnahmen Schwächen zeigen.

## 4. Lizenzlage (wichtig vor einer Verteilung)

- Ein OBS-Plugin linkt libobs (GPL-2.0) → das Plugin ist praktisch immer **GPL**.
- YOLO11n-OBB-Gewichte: **AGPL-3.0** (Ultralytics). Mit einem quelloffenen Plugin unter GPLv3/AGPLv3 vereinbar,
  nicht mit einem geschlossenen Produkt. Lizenzfreier Ersatz: RF-DETR-Seg (Apache-2.0).
- DINOv3-Embedder: **DINOv3-Lizenz** (Meta) – Nutzung und Weitergabe mit Auflagen; Alternative: eigenes
  Nachtraining von DINOv2-S (Apache-2.0) mit dem MIT-Trainingsskript von open-card-collector.
- Kartenbilder und Preisdaten werden nur lokal geladen, nicht verteilt.
- **Entschieden (2026-10-17):** Das Projekt wird **quelloffen** im eigenen GitHub-Repo veröffentlicht, sobald das
  OBS-Plugin fertig ist. Damit bleiben die übernommenen Modelle (YOLO11n-OBB unter AGPL-3.0, DINOv3-Embedder unter
  DINOv3-Lizenz) nutzbar. Lizenz des Projekts: **AGPL-3.0** (vereinbar mit libobs GPL-2.0-or-later und den
  AGPL-Gewichten). Modelle werden nicht ins Repo eingecheckt, sondern beim Installieren geladen; Lizenzhinweise und
  Quellen kommen in eine `NOTICE`-Datei.

## 5. Aktualisierter Implementierungsplan

**Phase 1 (laufend)**
1. ✅ Recherche, Evaluation, Messwerkzeuge (`eval_identify.py`, `eval_detect.py`)
2. ✅ Katalog: 21 990 Pokémon, 103 611 Magic-Drucke (Bilder: alle Pokémon, 15 Magic-Sets)
3. ✅ Engine-Gerüst: asynchrone Pipeline, Tracker, Debug-Overlay, Tests
4. ✅ Übernahme Embedder (open-card-collector), Index für 28 833 Karten
5. ⏳ Detektor YOLO11n-OBB + Eckenverfeinerung als Standard
6. ⏳ Identifikation: Schwellen/Abstandsregel + OCR-Evidenz + Unsicherheits-Flag
7. ⏳ Echte Aufnahmen über die Elgato Virtual Cam (Facecam + Tisch), Messung, Schwellen kalibrieren
8. Restliche Magic-Bilder laden (~100k, ~7 GB) für vollständige Abdeckung

## 6. Zwischenstand Phase 1 – Live-Test (Elgato Virtual Camera, Tischansicht, 3 Karten)

| Messung | Wert |
|---|---|
| Durchsatz Kamera → Tracking → Overlay | 49,5 fps (inkl. Speichern von Debug-Bildern), Hauptthread Median 9 ms, p95 40 ms |
| Detektion (asynchron) | 7,5 Hz, 28–38 ms je Durchlauf |
| Identifikation (asynchron, je neuer Karte) | ~200 ms (Embedding beider Lagen + Suche) |
| Pokémon „Cosmog“ 30th #078, „Kapuno/Deino“ 30th #097 (deutsch) | erkannt, Druck sicher (Ähnlichkeit 0,71–0,76); Deino in einzelnen Frames „unsicher“, nie falsch |
| Magic „Unheiliger Offiziant“ (deutsch, VOW) | „keine Karte“ – Set noch nicht im Bildindex (korrektes Verhalten) |
| Fehlalarm (Ecke der Spielmatte) | vom Detektor gemeldet, von der Identifikation abgelehnt |

**Offene Punkte Phase 1**
- Alle Magic-Referenzbilder laden (läuft), Index neu bauen, dann Magic live prüfen.
- Schwellen mit mehr echten Aufnahmen kalibrieren (Facecam bewegt, mehrere Karten, Foils, Hüllen).
- Detektor: Fehlalarme (Matte, Monitor) und zu große Umrisse (Schatten) – ggf. Nachtraining mit echten Frames.
- OCR läuft auf der CPU (~850 ms); ONNX Runtime 1.31 benötigt für CUDA das separate CUDA-Plugin-EP.
- Deutsche Kartennamen: Identifikation läuft über das Bild (sprachunabhängig) – funktioniert bereits für deutsche Pokémon-Karten.

**Phase 2–4** unverändert (siehe ARCHITEKTUR.md), mit den oben genannten übernommenen Bausteinen.

## 7. Phase 2 – Kartendaten und Preise (Stand 2026-10-17)

| Baustein | Umsetzung |
|---|---|
| Preisquelle | Cardmarket-Preisdateien (öffentlich, täglich ~02:44 MEZ): Magic 128 219, Pokémon 79 782 Produkte; Felder trend/low/avg/avg1/avg7/avg30 + Foil (Magic) bzw. Reverse Holo (Pokémon). Austauschbar über `PriceProvider`. |
| Zwischenspeicher | Dateien lokal mit Zeitstempel; Prüfung auf neue Tagesdatei alle 3–6 h per `If-Modified-Since`; Einlesen in den Speicher 1,3 s. |
| Zuordnung Karte → Produkt | Magic: Scryfall `cardmarket_id`. Pokémon: TCGdex-Details, dauerhaft gecacht (`cardmarket_map`, 30 Tage gültig, auch „kein Produkt“). Vorab-Laden aller Karten im Hintergrund (~90/s); **Karten im Kamerabild werden vorgezogen** (0,4–0,8 s statt Warteschlange), bekannte sofort (0 ms). |
| Verlauf | Eigene tägliche Snapshots (nur Änderungen) + Magic-Rückblick 90 Tage aus MTGJSON (über `mcmId` direkt auf Cardmarket-Produkte). Datenbank 142 MB statt 655 MB durch Speichern nur von Änderungen. |
| Veränderung | 7/30/90 Tage aus dem Verlauf; ohne Verlauf (neue Pokémon-Karten) ausdrücklich „vs Ø30T“ (Trend gegen 30-Tage-Durchschnitt). |
| Overlay | je Karte: Name oben, perspektivischer Rahmen (Farbe je Spiel), darunter Preis, Veränderung, Varianten (Foil/Reverse), Set/Nummer, Verlaufsgraph; Panels je Track gecacht, Überlappungen werden verschoben. **Unsicherer Druck → kein Preis.** |
| Ausgetauschte Karten | Vorschaubild-Vergleich ~1×/s je Karte; bei Abweichung neue Identifikation. Spätere schlechtere Lesungen überschreiben keine bessere. |

Live (Virtual Cam, 3 Karten): 48,8 fps, Hauptthread Median 9 ms; Cosmog 0,04 € und Deino 0,05 € korrekt
(30th Celebration), Magic-Karte noch ohne Referenzbild (Download läuft).

**Offen:** deutsche Anzeigenamen (Pokémon über TCGdex `de`, Magic über Scryfall `printed_name`), Foil-/Holo-Erkennung
am Bild (derzeit werden beide Variantenpreise angezeigt), Preisanzeige-Zeitraum als Einstellung, Rechtsklärung
Cardmarket-Daten im Stream.
