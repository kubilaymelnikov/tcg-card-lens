# cardengine – KI-Engine (Phase-1-Prototyp)

Erkennt, verfolgt und identifiziert Pokémon- und Magic-Karten im Kamerabild. Später Backend des nativen OBS-Filters.
Architektur und Entscheidungen: [../docs/ARCHITEKTUR.md](../docs/ARCHITEKTUR.md), [../docs/EVALUATION.md](../docs/EVALUATION.md).

## Installation (Windows, NVIDIA-GPU ab Turing / RTX 20xx)

```powershell
winget install --id Python.Python.3.12 --scope user
cd tcg-card-lens\engine
& "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe" -m venv .venv
.\.venv\Scripts\python.exe -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu130
.\.venv\Scripts\python.exe -m pip install "opencv-python<5" numpy requests tqdm pillow pytest ultralytics onnxruntime-gpu onnx rapidocr transformers huggingface_hub
```

Getestet: Python 3.12.10, PyTorch 2.14.1+cu130 (sm_75 enthalten), ONNX Runtime 1.31, Ultralytics 8.4, Treiber 617.42.

## Daten aufbauen (einmalig, nur lokal – Bilder werden nicht weitergegeben)

```powershell
.\.venv\Scripts\python.exe -m cardengine.card_database.build pokemon              # ~22k Karten, ~0,7 GB
.\.venv\Scripts\python.exe -m cardengine.card_database.build mtg --all-images     # ~104k Drucke, dauert Stunden
.\.venv\Scripts\python.exe -m cardengine.recognition.index                        # Embedding-Index (GPU, ~5 min je 30k Bilder)
```

Modelle (werden beim ersten Start geladen bzw. liegen unter `models/`):
- Embedder `matteot11/collector-mtg-pkm-embedder-dinov3-small` (Hugging Face, DINOv3-Lizenz)
- Detektor `models/occ/detector-yolo11n-obb.pt` (open-card-collector, AGPL-3.0)
- optional `models/cardstream/...` RF-DETR-Seg (Ximilar, Apache-2.0)

## Ausführen

```powershell
.\.venv\Scripts\python.exe run.py --source 0 --mode table      # Kamera 0 (z. B. Elgato Virtual Camera)
.\.venv\Scripts\python.exe run.py --source 0 --mode facecam
.\.venv\Scripts\python.exe run.py --source aufnahme.mp4
.\.venv\Scripts\python.exe run.py --source 0 --grab 20 --interval 1   # Testbilder nach data/frames speichern
```

Tasten: `s` Bild speichern, `d` Roh-Detektionen ein/aus, `q` Ende. Optionen: `--detector default|rfdetr|classical`,
`--detect-hz`, `--headless --save-vis ORDNER`, `--max-frames N` (Benchmark).

## Messen

```powershell
.\.venv\Scripts\python.exe tools\eval_identify.py --game pokemon --n 400            # Embedding-Genauigkeit
.\.venv\Scripts\python.exe tools\eval_identify.py --pipeline --game pokemon --n 300 # inkl. Unsicherheitslogik
.\.venv\Scripts\python.exe tools\eval_detect.py --n 150 --images data\frames       # Detektoren vergleichen
.\.venv\Scripts\python.exe -m pytest                                               # Unit-Tests
```

## Aufbau

| Modul | Aufgabe |
|---|---|
| `cardengine/detection/` | Detektor-Schnittstelle; YOLO-OBB, RF-DETR-Seg, klassisch; Eckenverfeinerung |
| `cardengine/tracking.py` | Tracker: optischer Fluss je Karte + IoU-Zuordnung, Facecam-/Tisch-Profil |
| `cardengine/pipeline.py` | asynchrone Detektion/Identifikation, blockiert den Aufrufer nie |
| `cardengine/recognition/` | Embedder, Index, Fußzeilen-OCR, Identifikation mit Unsicherheits-Flag |
| `cardengine/card_database/` | lokaler Katalog (SQLite) + Referenzbilder |
| `tools/` | Messwerkzeuge, synthetischer Datengenerator, Detektor-Training |
