"""Benötigte Modelle herunterladen (einmalig, danach lokal).

    python -m cardengine.models_fetch

- Detektor: matteot11/collector-mtg-pkm-detector-yolo11n-obb (Hugging Face, AGPL-3.0)
- Embedder: matteot11/collector-mtg-pkm-embedder-dinov3-small (Hugging Face, DINOv3-Lizenz)
- OCR: RapidOCR lädt seine PP-OCR-Modelle beim ersten Start selbst
"""
from __future__ import annotations

import os
import shutil

from .paths import MODELS_DIR

DETECTOR_REPO = "matteot11/collector-mtg-pkm-detector-yolo11n-obb"
EMBEDDER_REPO = "matteot11/collector-mtg-pkm-embedder-dinov3-small"
DETECTOR_PATH = os.path.join(MODELS_DIR, "occ", "detector-yolo11n-obb.pt")


def ensure_models(verbose: bool = True) -> None:
    os.environ.setdefault("HF_HOME", os.path.join(MODELS_DIR, "hf"))
    from huggingface_hub import hf_hub_download, snapshot_download

    if not os.path.exists(DETECTOR_PATH):
        if verbose:
            print("[modelle] lade Kartendetektor …", flush=True)
        os.makedirs(os.path.dirname(DETECTOR_PATH), exist_ok=True)
        shutil.copy(hf_hub_download(DETECTOR_REPO, "best.pt"), DETECTOR_PATH)
    if verbose:
        print("[modelle] prüfe Karten-Embedder …", flush=True)
    snapshot_download(EMBEDDER_REPO)
    if verbose:
        print("[modelle] bereit", flush=True)


if __name__ == "__main__":
    ensure_models()
