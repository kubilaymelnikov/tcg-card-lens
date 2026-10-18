"""Benötigte Modelle bereitstellen (einmalig, danach lokal).

    python -m cardengine.models_fetch                  # für das gewählte Backend (siehe cardengine.backend)
    python -m cardengine.models_fetch --backend onnx   # ONNX-Modelle laden (Fallback: lokal exportieren)

ONNX-Backend (Standard der schlanken Installation): zwei ONNX-Dateien, die als Assets am GitHub-Release liegen
(gleiche Basis-URL wie die Datenpakete, `TCGLENS_MODEL_URL` > `TCGLENS_PACK_URL` > `config.json: pack_url` >
`data_packs.DEFAULT_PACK_URL`). Dateiname = Modell + Exportversion; die SHA256 ist hier fest eingetragen und wird nach
dem Download geprüft. Ist keine Quelle erreichbar und PyTorch installiert, werden die Dateien lokal exportiert
(`cardengine.onnx_export`, gleiches Verfahren wie `tools/export_onnx.py`).
Zielordner: `models/onnx` (umlenkbar mit `TCGLENS_ONNX_DIR`).

PyTorch-Backend (Entwicklung):
- Detektor: matteot11/collector-mtg-pkm-detector-yolo11n-obb (Hugging Face, AGPL-3.0)
- Embedder: matteot11/collector-mtg-pkm-embedder-dinov3-small (Hugging Face, DINOv3-Lizenz)

OCR: RapidOCR bringt seine PP-OCR-Modelle (ONNX) im Paket mit.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
from dataclasses import dataclass

from .paths import MODELS_DIR

DETECTOR_REPO = "matteot11/collector-mtg-pkm-detector-yolo11n-obb"
EMBEDDER_REPO = "matteot11/collector-mtg-pkm-embedder-dinov3-small"
EMBEDDER_REVISION = "aa5368ce0724d44449bc4b418de2df03a9c778d0"  # Stand des Exports (Hugging-Face-Commit)
DETECTOR_PATH = os.path.join(MODELS_DIR, "occ", "detector-yolo11n-obb.pt")
MODEL_URL_ENV = "TCGLENS_MODEL_URL"
ONNX_DIR_ENV = "TCGLENS_ONNX_DIR"


@dataclass(frozen=True)
class OnnxModel:
    kind: str  # detector | embedder
    file: str  # Dateiname = Asset-Name am Release
    sha256: str  # der veröffentlichten Datei; lokal exportierte Dateien weichen byteweise ab (nicht geprüft)
    size: int
    source: str  # Herkunft (Hugging Face) – für Lizenzhinweise


# Exportversion im Dateinamen erhöhen, sobald sich Export oder Quellmodell ändern (dann neue Assets hochladen).
ONNX_MODELS = {
    "detector": OnnxModel("detector", "tcglens-detector-occ-yolo11n-obb-v1.onnx",
                          "08fe33734fd352ce7c152d9d7ea40fdec15d55a8f14dc29e665c8f49168ae5a7", 10718183, DETECTOR_REPO),
    "embedder": OnnxModel("embedder", "tcglens-embedder-occ-dinov3s-v1.onnx",
                          "878755d3dc39c531c526d93919c9decb2b8a8465d0eb088115052d92d3cc695e", 86710922, EMBEDDER_REPO),
}


class ModelError(Exception):
    pass


def onnx_dir() -> str:
    return os.environ.get(ONNX_DIR_ENV) or os.path.join(MODELS_DIR, "onnx")


def onnx_path(kind: str) -> str:
    return os.path.join(onnx_dir(), ONNX_MODELS[kind].file)


def model_url() -> str:
    if os.environ.get(MODEL_URL_ENV):
        return os.environ[MODEL_URL_ENV]
    from .data_packs import pack_url

    return pack_url()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download_model(model: OnnxModel, base_url: str, dest: str, log=print) -> None:
    """Asset laden, SHA256 prüfen, erst dann an den Zielort verschieben (keine halben Dateien)."""
    import requests

    from .paths import USER_AGENT

    url = f"{base_url.rstrip('/')}/{model.file}"
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    tmp = f"{dest}.{os.getpid()}.part"
    h = hashlib.sha256()
    try:
        with requests.get(url, stream=True, timeout=60, headers={"User-Agent": USER_AGENT}) as r:
            r.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
                    h.update(chunk)
        if h.hexdigest() != model.sha256:
            raise ModelError(f"{model.file}: Prüfsumme stimmt nicht ({h.hexdigest()[:12]}… statt {model.sha256[:12]}…)")
        os.replace(tmp, dest)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
    log(f"[modelle] {model.file} geladen")


def ensure_onnx_models(verbose: bool = True, allow_export: bool = True, base_url: str | None = None) -> dict[str, str]:
    """Pfade der ONNX-Modelle; fehlende werden geladen bzw. (mit PyTorch) lokal exportiert."""
    from .backend import torch_installed
    from .data_packs import is_placeholder

    log = print if verbose else (lambda *a, **k: None)
    paths, missing = {}, []
    for kind in ONNX_MODELS:
        paths[kind] = onnx_path(kind)
        if not os.path.exists(paths[kind]):
            missing.append(kind)
    if not missing:
        return paths
    base_url = base_url if base_url is not None else model_url()
    errors = []
    if base_url and not is_placeholder(base_url):
        for kind in list(missing):
            log(f"[modelle] lade {ONNX_MODELS[kind].file} …", flush=True)
            try:
                download_model(ONNX_MODELS[kind], base_url, paths[kind], log)
                missing.remove(kind)
            except Exception as err:  # offline, Asset fehlt, Prüfsumme falsch
                errors.append(f"{ONNX_MODELS[kind].file}: {err}")
    if missing and allow_export and torch_installed():
        from .onnx_export import export_detector, export_embedder

        for kind in list(missing):
            log(f"[modelle] exportiere {ONNX_MODELS[kind].file} lokal (PyTorch) …", flush=True)
            _ensure_torch_models(verbose)
            if kind == "detector":
                export_detector(DETECTOR_PATH, paths[kind])
            else:
                export_embedder(EMBEDDER_REPO, paths[kind])
            missing.remove(kind)
    if missing:
        detail = "; ".join(errors) or "keine Download-Quelle"
        raise ModelError(f"ONNX-Modelle fehlen ({', '.join(ONNX_MODELS[k].file for k in missing)}): {detail}. "
                         f"Ohne PyTorch ist kein lokaler Export möglich.")
    return paths


def _ensure_torch_models(verbose: bool = True) -> None:
    """PyTorch-Gewichte von Hugging Face (nur Entwicklung / Export)."""
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


def ensure_models(verbose: bool = True, backend: str | None = None) -> None:
    from .backend import resolve_backend

    if resolve_backend(backend) == "torch":
        _ensure_torch_models(verbose)
    else:
        ensure_onnx_models(verbose)
    if verbose:
        print("[modelle] bereit", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="Modelle für die Engine bereitstellen")
    ap.add_argument("--backend", choices=["auto", "onnx", "torch"], default=None)
    args = ap.parse_args()
    try:
        ensure_models(backend=args.backend)
    except ModelError as err:
        raise SystemExit(f"[modelle] {err}")


if __name__ == "__main__":
    main()
