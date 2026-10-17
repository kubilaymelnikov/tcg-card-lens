# TCG Card Lens – Architektur, Entscheidungen, Risiken, Plan

Stand: 2026-10-17. Ziel: natives OBS-Studio-Filter-Plugin, das Pokémon- und Magic-Karten lokal per GPU erkennt,
identifiziert, verfolgt und mit Cardmarket-Preisen direkt im Videobild überlagert.

Der frühere Web-Prototyp (Browser + Node.js, `server/`, `public/`) ist nicht mehr Teil des Stands; er liegt in der Git-Historie (vor Version 0.2.0). Die neue Pipeline entsteht in
`engine/` (Python-Prototyp, Phase 1–2) und später `obs-plugin/` (C++, Phase 3).

## 1. Zielsystem

| | |
|---|---|
| GPU | laut `nvidia-smi` RTX 2080 (Super/Ti), 8 GB – Turing, Compute Capability 7.5 (der Auftrag nennt RTX 2070: gleiche Architektur, gleiche Kompatibilität) |
| Treiber | 617.42 (CUDA 13.4 UMD) |
| OS | Windows 11 |
| OBS | aktuell 32.2.x (33.0 im RC) |

## 2. Recherche-Ergebnisse (verifiziert, mit Quellen in den Abschnitten)

### GPU-Kompatibilität (Turing / sm_75)
- **CUDA 13.x**: unterstützt Turing; CUDA 13 hat nur Maxwell/Pascal/Volta gestrichen. Turing ist jetzt die
  *unterste* unterstützte Architektur → Risiko, dass CUDA 14 sie streicht.
- **PyTorch 2.14** (Wheels für CUDA 13.0/13.2): enthält sm_75 – wird nach der Installation per
  `torch.cuda.get_arch_list()` geprüft.
- **ONNX Runtime 1.31**, CUDA Execution Provider: Builds für CUDA 13 und 12.8 enthalten `75-real`.
- **TensorRT 11.4**: „SM 7.5 or higher“ → geeignet. *Nicht* geeignet: das neue „NV TensorRT RTX“-EP (erst Ampere).
- **DirectML**: nur noch Wartungsmodus – allenfalls Notfall-Fallback.

### Kartenerkennung (Lokalisierung)
- **Ultralytics YOLO26-OBB** (aktuell) bzw. YOLO11-OBB: native gedrehte Boxen, ONNX/TensorRT-Export, sehr schnell
  (n-Modell ~3 ms mit TensorRT auf T4).
  **Lizenz: AGPL-3.0** – laut Ultralytics gilt das auch für selbst trainierte Gewichte. Für einen privaten
  Prototyp unproblematisch; für ein *verteiltes* Plugin heißt das: Plugin unter AGPL veröffentlichen oder eine
  Enterprise-Lizenz kaufen. → **Entscheidung vor Phase 3 nötig** (siehe Risiken).
- Permissive Alternativen: **RF-DETR-Seg** (Apache-2.0, ONNX-Export; Ecken aus der Maske), DEIM/RT-DETRv2
  (Apache-2.0, nur achsparallele Boxen + klassischer Viereck-Fit). MMRotate/RTMDet-R ist faktisch nicht mehr gepflegt.
- **Es gibt kein fertiges, gepflegtes Kartendetektor-Modell.** Roboflow-Universe-Datensätze sind klein und
  uneinheitlich. → **Eigenes Training nötig**, primär mit synthetischen Daten (siehe Abschnitt 5).

### Kartenidentifikation
- **Perceptual Hash** scheitert an Reprints mit identischem Artwork, Spiegelungen und Perspektive.
- **Bild-Embeddings**: DINOv2 ViT-S/14 (Apache-2.0) gilt als stark für Instanz-Retrieval; CLIP schwächer bei
  Nischeninhalten. Embeddings erkennen *Karte/Artwork*, aber **nicht zuverlässig die Edition** bei gleichem Artwork.
- **Edition/Druck** unterscheiden kommerzielle Scanner über **Sammlernummer + Set-Code per OCR** (Magic ab 2015,
  Pokémon „123/198“ + Set-Code), sonst Set-Symbol. Alte Reprints bleiben teils mehrdeutig → Unsicherheit anzeigen.
- **OCR**: PaddleOCR PP-OCRv5/v6 bzw. RapidOCR (Apache-2.0, ONNX). Da Positionen auf der entzerrten Karte fest sind,
  genügt das **Erkennungsmodell auf festen Ausschnitten** (kein Textdetektor nötig).

### Cardmarket-Daten
- **Offizielle API: keine neuen Zugänge** („not accepting applications“, help.cardmarket.com).
- **Öffentliche Preisdateien von Cardmarket** (ohne Login, nächtlich ~02:44 MEZ aktualisiert):
  - `https://downloads.s3.cardmarket.com/productCatalog/priceGuide/price_guide_{1|6}.json` (1 = Magic, 6 = Pokémon)
    Felder je `idProduct`: `avg, low, trend, avg1, avg7, avg30` + `-foil` (Magic) bzw. `-holo` (Pokémon), EUR
  - `.../productList/products_singles_{1|6}.json`: `idProduct, name, idExpansion, idMetacard, …`
  - Nutzungsbedingungen in den Dateien nicht enthalten; Download-Seite war nicht abrufbar → **Anzeige im Stream mit
    Quellenangabe „Cardmarket, Stand …“; Rechtslage für öffentliche Anzeige ggf. bei Cardmarket bestätigen lassen.**
- Zuordnung Karte → `idProduct`: Scryfall `cardmarket_id` (Magic), TCGdex `variants_detailed[].thirdParty.cardmarket`
  (Pokémon).
- Historie: Magic über MTGJSON (90 Tage), sonst **eigene tägliche Snapshots** der Preisdatei.
- pokemontcg.io ist für Preise veraltet (Stand 2025) → nicht verwenden.

### OBS-Integration
- Plugin-Basis: `obsproject/obs-plugintemplate` (Visual Studio 2022, CMake ≥ 3.30).
- **Frames lesen**: Effekt-Filter mit `gs_texrender` → Ring aus 2–3 `gs_stagesurface`, Map mit 1–2 Frames
  Versatz (kein GPU-Stall), auf ~10 fps drosseln, Inferenz im Worker-Thread. Async-Filter (`filter_video`) gingen nur
  bei Webcam/Medien, nicht bei Capture-Quellen.
- **Zeichnen**: Linien/Flächen über `OBS_EFFECT_SOLID`; Text über private `text_gdiplus`-Quelle oder eigenes
  FreeType-Atlas.
- **DLL-Laden** (ab OBS 32.2 kein PATH-Suchen mehr): ONNX Runtime/CUDA-DLLs neben das Plugin legen und per
  Delay-Load + `LoadLibraryExW` laden (Muster aus obs-backgroundremoval).
- Referenz: royshil/obs-backgroundremoval (gepflegt), locaal-ai/obs-detect (archiviert, aber gutes Beispiel).

## 3. Architektur

```
                 ┌────────────── OBS (Phase 3) ──────────────┐
 Videoquelle ──▶ │ obs-plugin: Filter je Quelle               │
                 │  • Frame-Readback (Ring, ~10 fps)          │
                 │  • overlay-renderer (jeder Frame, 60 fps)  │◀── Ergebnisse (Tracks, Identitäten, Preise)
                 └──────────────┬────────────────────────────┘
                                │ Shared Memory + Events (Prototyp: ZeroMQ)
                 ┌──────────────▼──────── KI-Engine (1 Prozess, alle Quellen) ─────────────┐
                 │ detection-engine   Detektor (YOLO-OBB / klassisch) + Tracker (KLT + IoU) │
                 │ card-recognition   Entzerren → Embedding (Top-k) → OCR Nummer/Set → Entscheid│
                 │ card-database      Katalog + Referenz-Embeddings (lokal gebaut)        │
                 │ pricing-provider   Cardmarket-Preisdatei, MTGJSON, Snapshots          │
                 │ cache              Identitäten je Track, Preise, Referenzbilder        │
                 └──────────────────────────────────────────────────────────────────────┘
```

**Entscheidungen**
1. **Eine Engine für alle Filterinstanzen**: Modelle liegen einmal im VRAM; jede Quelle hat ihre eigene
   Tracker-Instanz und ihren Modus (Facecam/Tisch).
2. **Detektion asynchron und selten, Tracking jeden Frame**: KI ~5–10 Hz; dazwischen optischer Fluss (KLT) auf
   verkleinerten Graubildern, der die vier Ecken nachführt → Overlay folgt flüssig bei 60 fps.
3. **Identifikation nur bei neuen/geänderten Tracks**: Ergebnis wird am Track gecacht; erneute Prüfung nur bei
   Bildänderung im Kartenbereich (Tisch-Modus) oder nach Track-Verlust.
4. **Mehrstufige Identifikation mit expliziter Unsicherheit**:
   `Embedding-Top-k` → bei mehreren Drucken gleicher Karte `OCR Nummer/Set` → bei verbleibender Mehrdeutigkeit
   **Status „Druck unsicher“ und kein Preis** (Anforderung: keine falschen Preise).
5. **Referenzbilder werden nicht ausgeliefert** (Bildrechte TPCi/WotC, Scryfall-Richtlinien). Katalog und
   Erkennungs-Index (Embeddings) kommen als Datenpaket je Spiel, sonst baut die Engine sie lokal
   (siehe DATENPAKETE.md).
6. **Preisprovider abstrahiert** (`PriceProvider`-Schnittstelle); Standard: Cardmarket-Preisdatei.
7. **Detektor austauschbar** hinter `Detector`-Schnittstelle (ONNX-Ausgabe = 4 Ecken + Score + Klasse), damit eine
   Lizenzentscheidung gegen Ultralytics später nur das Modell betrifft.
8. **Prototyp in Python, Plugin in C++ mit ONNX Runtime**: dieselben ONNX-Modelle, dieselbe Logik; die
   Python-Engine kann in Phase 3 zunächst als separater Prozess weiterlaufen.

## 4. Risiken (nach Schwere)

| # | Risiko | Gegenmaßnahme |
|---|---|---|
| 1 | **Edition/Druck bei gleichem Artwork** nicht unterscheidbar (v. a. Magic-Reprints, kleine Fußzeile bei Facecam) | OCR Nummer/Set, Set-Symbol-Abgleich; sonst „unsicher“ + kein Preis; manuelle Festlegung je Track |
| 2 | **Kein fertiges Detektormodell** | Synthetischer Datensatz + Fine-Tuning; Validierung mit echten Frames |
| 3 | **Facecam-Auflösung**: Karte in Schärfeentfernung (MK.2 Fixfokus ≥ 30 cm) nur ~250–300 px hoch | Erkennung/Name ok, Fußzeile grenzwertig → Tisch-/Dokumentenkamera oder 4K für Edition |
| 4 | **AGPL (Ultralytics)** bei Verteilung | Vor Phase 3 entscheiden: AGPL-Plugin, Lizenz kaufen oder RF-DETR-Seg (Apache) |
| 5 | Spiegelungen bei Hüllen/Foils | Augmentierung im Training, mehrere Frames mitteln |
| 6 | Turing am unteren Ende von CUDA 13 | Versionen pinnen; DirectML/CPU-Fallback |
| 7 | 8 GB VRAM geteilt mit OBS/NVENC/Spielen | kleine Modelle (n/s, FP16), ein Engine-Prozess, Ziel < 1 GB |
| 8 | Cardmarket-Nutzungsrechte für Stream-Anzeige unklar | Quellenangabe, Stand-Datum; ggf. Nachfrage bei Cardmarket |
| 9 | OBS 32.2 DLL-Ladeverhalten | Delay-Load-Muster aus obs-backgroundremoval |

## 5. Detektor-Training (Plan)

- **Synthetische Daten**: Kartenbilder (Scryfall/TCGdex, lokal geladen) auf Hintergründe (Tische, Räume, Hände,
  Playmats) mit zufälliger Perspektive, Rotation, Skalierung (Karte 3–60 % Bildhöhe), Überlappung, Hüllen-Glanz,
  Bewegungsunschärfe, Rauschen, JPEG-Artefakten, Lichtfarbe. Label: 4 Ecken + Klasse (`mtg`, `pokemon`) im
  YOLO-OBB-Format. Ziel: ~20–50k Bilder.
- **Echte Daten**: einige hundert Frames von Facecam und Tischkamera, halbautomatisch gelabelt (Vorschläge des
  synthetisch trainierten Modells, manuell korrigiert) – vor allem als **Validierungsset**.
- **Training**: YOLO-OBB n/s auf der RTX (640–960 px), Export ONNX (FP16), Metriken: mAP50-95 (OBB),
  Eckfehler in px, Recall bei kleinen Karten.

## 6. Priorisierter Plan

### Phase 1 – Evaluation & Prototyp (jetzt)
1. ✅ Recherche, GPU-Kompatibilität, dieses Dokument
2. Python-Umgebung (CUDA prüfen, `get_arch_list`)
3. Engine-Grundgerüst: Detektor-Schnittstelle, klassischer Detektor (Basislinie), Tracker (KLT + Zuordnung),
   Debug-Overlay, Video/Webcam-Eingabe, asynchrone Detektion
4. Katalog + Referenzbilder für ein Test-Subset (einige Sets je Spiel), DINOv2-Embeddings, Top-k-Suche
5. Identifikation: Embedding + OCR Nummer/Set + Unsicherheitslogik
6. Synthetischer Datengenerator + erstes YOLO-OBB-Training, Vergleich mit klassischem Detektor
7. Messung mit **echten Kamerabildern** (Facecam + Tisch): Erkennungsrate, Identifikationsgenauigkeit,
   Latenz/FPS → Bericht

### Phase 2 – Kartendaten & Preise
Vollständige Kataloge (Magic ~100k Drucke, Pokémon ~20k), Varianten/Foil, Cardmarket-Preisdatei + Snapshots,
MTGJSON-Historie, Preis-Overlay mit Verlauf.
Yu-Gi-Oh! (`ygo`, ~45k Drucke, YGOPRODeck + Cardmarket `price_guide_3`): Druck nur über den Set-Code – siehe
[YUGIOH.md](YUGIOH.md).

### Phase 3 – OBS-Plugin
Plugin-Template, Frame-Readback-Ring, IPC zur Engine (Shared Memory), Overlay-Rendering in C++, Einstellungen,
mehrere Instanzen. Danach schrittweise Inferenz direkt im Plugin (ONNX Runtime C++).

### Phase 4 – Optimierung & Verteilung
TensorRT, Stabilisierung, Mehrkamera-Tests, Fehlerbehandlung, Installer.
