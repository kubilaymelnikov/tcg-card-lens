"""Detektor und Embedder nach ONNX exportieren (für die schlanke Installation ohne PyTorch).

    python tools/export_onnx.py                       # beide Modelle nach models/onnx (bzw. TCGLENS_ONNX_DIR)
    python tools/export_onnx.py --out D:\\release --only embedder

Braucht requirements-dev.txt (torch, ultralytics, transformers, onnx) und die PyTorch-Gewichte (werden bei Bedarf von
Hugging Face geladen). Ausgabe: Dateinamen wie in `cardengine.models_fetch.ONNX_MODELS`, dazu Größe und SHA256 –
diese Werte in ONNX_MODELS eintragen, die Dateien als Assets an den GitHub-Release hängen (docs/ARCHITEKTUR.md, 7).
Gleichwertigkeit prüfen: tools/compare_backends.py.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cardengine.models_fetch import (  # noqa: E402
    DETECTOR_PATH, EMBEDDER_REPO, EMBEDDER_REVISION, ONNX_MODELS, _ensure_torch_models, onnx_dir, sha256_file,
)
from cardengine.onnx_export import export_detector, export_embedder  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None, help="Zielordner (Standard: models/onnx bzw. TCGLENS_ONNX_DIR)")
    ap.add_argument("--only", choices=list(ONNX_MODELS))
    ap.add_argument("--no-download", action="store_true", help="PyTorch-Gewichte nicht von Hugging Face nachladen")
    args = ap.parse_args()
    out = args.out or onnx_dir()
    os.makedirs(out, exist_ok=True)
    if not args.no_download:
        _ensure_torch_models()
    for kind, model in ONNX_MODELS.items():
        if args.only and kind != args.only:
            continue
        dest = os.path.join(out, model.file)
        print(f"[export] {kind} → {dest}", flush=True)
        if kind == "detector":
            export_detector(DETECTOR_PATH, dest)
        else:
            export_embedder(EMBEDDER_REPO, dest, revision=EMBEDDER_REVISION)
        print(f'    "{kind}": size={os.path.getsize(dest)}, sha256="{sha256_file(dest)}"', flush=True)


if __name__ == "__main__":
    main()
