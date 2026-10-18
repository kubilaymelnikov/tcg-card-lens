"""PyTorch-Modelle nach ONNX exportieren (braucht requirements-dev.txt). Aufruf über tools/export_onnx.py oder
automatisch aus models_fetch, wenn die Release-Dateien nicht erreichbar sind.

- Detektor: Ultralytics-Export, fp32, dynamische Eingabegröße (wie der .pt-Weg: Letterbox nur bis zum nächsten
  Vielfachen von 32), ohne eingebautes NMS – das macht `detection/obb_post.py` in NumPy
- Embedder: DINOv3-S (Transformers), Ausgabe = CLS-Token von `last_hidden_state` (vor der L2-Normierung), fp32,
  dynamische Batchgröße, Eingabe 224 × 224 (normiert mit ImageNet-Mittelwert/-Streuung)

fp32 statt fp16: Die veröffentlichten Erkennungs-Indizes (`occ-dinov3s-norm`) müssen zum Embedder passen; fp32 liegt
nachweislich innerhalb der Toleranz (docs/ARCHITEKTUR.md, Abschnitt 7) und läuft auf CUDA, DirectML und CPU gleich.
"""
from __future__ import annotations

import os
import shutil
import tempfile

OPSET = 17


def export_detector(pt_path: str, out_path: str, imgsz: int = 640) -> str:
    """YOLO-OBB (.pt) → ONNX. Exportiert in einem Temp-Ordner (Ultralytics schreibt neben die Gewichte)."""
    os.environ.setdefault("YOLO_AUTOINSTALL", "false")  # nie Pakete nachinstallieren
    os.environ.setdefault("YOLO_OFFLINE", "true")
    from ultralytics import YOLO

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="tcglens-export-") as tmp:
        src = os.path.join(tmp, "detector.pt")
        shutil.copy(pt_path, src)
        f = YOLO(src, task="obb").export(format="onnx", imgsz=imgsz, dynamic=True, simplify=False, half=False,
                                          nms=False, opset=OPSET, device="cpu", verbose=False)
        shutil.move(f, out_path)
    return out_path


def export_embedder(repo: str, out_path: str, revision: str | None = None) -> str:
    """Hugging-Face-Bildmodell → ONNX (CLS-Token)."""
    from .paths import MODELS_DIR

    os.environ.setdefault("HF_HOME", os.path.join(MODELS_DIR, "hf"))  # vor dem Import von transformers setzen
    import torch
    from transformers import AutoModel

    model = AutoModel.from_pretrained(repo, revision=revision).eval().float()

    class ClsToken(torch.nn.Module):
        def __init__(self, m):
            super().__init__()
            self.m = m

        def forward(self, pixel_values):
            return self.m(pixel_values=pixel_values).last_hidden_state[:, 0]

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    dummy = torch.randn(2, 3, 224, 224)
    tmp = f"{out_path}.{os.getpid()}.tmp"
    with torch.inference_mode():
        torch.onnx.export(ClsToken(model), (dummy,), tmp, input_names=["pixel_values"], output_names=["embedding"],
                          dynamic_axes={"pixel_values": {0: "batch"}, "embedding": {0: "batch"}},
                          opset_version=OPSET, do_constant_folding=True, dynamo=False)
    _merge_external_data(tmp, out_path)
    return out_path


def _merge_external_data(src: str, dest: str) -> None:
    """Eine einzelne Datei erzeugen (der Exporter legt Gewichte über 2 GB extern ab – hier nie nötig, aber sicher)."""
    import onnx

    m = onnx.load(src, load_external_data=True)
    onnx.save_model(m, dest, save_as_external_data=False)
    os.remove(src)
    data = src + ".data"
    if os.path.exists(data):
        os.remove(data)
