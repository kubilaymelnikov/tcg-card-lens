"""Bild-Embeddings für entzerrte Karten. Backend ONNX Runtime (Standard) oder PyTorch, siehe cardengine.backend.
Austauschbare Modelle:

- `occ-dinov3s` (Standard): matteot11/collector-mtg-pkm-embedder-dinov3-small – DINOv3-S, auf Magic- und
  Pokémon-Karten kontrastiv nachtrainiert (Open Card Collector, MIT-Code; Gewichte unter DINOv3-Lizenz).
- `dinov2s`: DINOv2 ViT-S/14 ohne Kartentraining (Apache-2.0) – nur Basislinie.
- `hf:<model-id>`: beliebiges Hugging-Face-Bildmodell (CLS-Token).
Mit ONNX gibt es nur `occ-dinov3s` (exportiert mit tools/export_onnx.py); die anderen brauchen PyTorch.
"""
from __future__ import annotations

import os

import cv2
import numpy as np

from ..paths import MODELS_DIR

MODELS = {
    "occ-dinov3s": "hf:matteot11/collector-mtg-pkm-embedder-dinov3-small",
    "dinov2s": "hub:dinov2_vits14",
}
DEFAULT_MODEL = "occ-dinov3s"

MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)


def normalize_photometric(bgr: np.ndarray) -> np.ndarray:
    """Weißabgleich (Gray World), Tonwertspreizung (1./99. Perzentil) und CLAHE.

    Echte Kamerabilder sind oft dunkel und farbstichig; ohne Normierung sank die Ähnlichkeit einer korrekt
    gefundenen Karte im Facecam-Test auf 0,40 (mit: 0,53, Abstand zu Platz 2 von 0,04 auf 0,21).
    Wird identisch auf Referenzbilder und Abfragen angewendet.
    """
    f = bgr.astype(np.float32)
    f = f / (f.reshape(-1, 3).mean(0) + 1e-6) * f.mean()
    lo, hi = np.percentile(f, 1), np.percentile(f, 99)
    f = np.clip((f - lo) / max(hi - lo, 1e-6) * 255, 0, 255).astype(np.uint8)
    lab = cv2.cvtColor(f, cv2.COLOR_BGR2LAB)
    lab[..., 0] = _CLAHE.apply(lab[..., 0])
    return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)


_CLAHE = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))


def _preprocess_224(bgr_cards: list[np.ndarray]) -> np.ndarray:
    out = np.empty((len(bgr_cards), 3, 224, 224), np.float32)
    for i, img in enumerate(bgr_cards):
        rgb = cv2.cvtColor(cv2.resize(img, (224, 224), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB)
        out[i] = ((rgb.astype(np.float32) / 255.0 - MEAN) / STD).transpose(2, 0, 1)
    return out


def _preprocess_dinov3(bgr_cards: list[np.ndarray]) -> np.ndarray:
    """Wie `DINOv3ViTImageProcessor` (Transformers, Torchvision-Weg): erst auf 0..1 skalieren, dann bilinear mit
    Antialiasing auf 224 × 224, dann normieren. Pillow im Gleitkomma-Modus („F“) rechnet dasselbe Filter wie
    Torchvision (Abweichung < 1e-4), deshalb ohne PyTorch."""
    from PIL import Image

    out = np.empty((len(bgr_cards), 3, 224, 224), np.float32)
    for i, img in enumerate(bgr_cards):
        f = cv2.cvtColor(img, cv2.COLOR_BGR2RGB).astype(np.float32) * np.float32(1 / 255)
        for c in range(3):
            ch = Image.fromarray(np.ascontiguousarray(f[..., c]), mode="F").resize((224, 224), Image.BILINEAR)
            out[i, c] = (np.asarray(ch, np.float32) - MEAN[c]) / STD[c]
    return out


# ONNX-Export je Modell (models_fetch.ONNX_MODELS); andere Modelle nur mit dem PyTorch-Backend
ONNX_KIND = {"occ-dinov3s": "embedder"}


class Embedder:
    """Bild-Embeddings; Backend „onnx“ (ONNX Runtime) oder „torch“ (siehe cardengine.backend).
    Beide liefern für `occ-dinov3s` gleichwertige Vektoren (Kosinus ≥ 0,999) – derselbe Indexname, dieselben
    Datenpakete."""

    def __init__(self, model: str = DEFAULT_MODEL, device: str | None = None, normalize: bool = True,
                 backend: str | None = None, provider: str | None = None):
        from ..backend import resolve_backend

        self.normalize = normalize
        self.name = model
        self.backend = resolve_backend(backend)
        if self.backend == "onnx":
            self._init_onnx(model, provider)
        else:
            self._init_torch(model, device)

    # ------------------------------------------------------------ ONNX
    def _init_onnx(self, model: str, provider: str | None) -> None:
        from ..backend import OnnxRunner
        from ..models_fetch import onnx_path

        if model not in ONNX_KIND:
            raise ValueError(f"Embedding-Modell {model!r} gibt es nur mit dem PyTorch-Backend (TCGLENS_BACKEND=torch)")
        self.runner = OnnxRunner(onnx_path(ONNX_KIND[model]), provider)
        self.device = self.runner.provider

    def _embed_onnx(self, bgr: list[np.ndarray]) -> np.ndarray:
        if self.normalize:
            bgr = [normalize_photometric(b) for b in bgr]
        x = _preprocess_dinov3(bgr)
        if self.runner.fixed_shapes:  # DirectML: Einzelbilder mit fester Form statt dynamischer Stapel (schneller)
            f = np.concatenate([self.runner.run(x[i:i + 1])[0] for i in range(len(x))]).astype(np.float32)
        else:
            f = self.runner.run(x)[0].astype(np.float32)
        return f / np.maximum(np.linalg.norm(f, axis=1, keepdims=True), 1e-12)

    # ------------------------------------------------------------ PyTorch
    def _init_torch(self, model: str, device: str | None) -> None:
        import torch

        self.torch = torch
        spec = MODELS.get(model, model)
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        os.environ.setdefault("HF_HOME", os.path.join(MODELS_DIR, "hf"))
        if spec.startswith("hub:"):
            torch.hub.set_dir(os.path.join(MODELS_DIR, "torchhub"))
            self.model = torch.hub.load("facebookresearch/dinov2", spec[4:], trust_repo=True)
            self.processor = None
        elif spec.startswith("hf:"):
            from transformers import AutoModel, AutoProcessor

            self.processor = AutoProcessor.from_pretrained(spec[3:])
            self.model = AutoModel.from_pretrained(spec[3:])
        else:
            raise ValueError(f"unbekanntes Embedding-Modell {model!r}")
        self.model = self.model.to(self.device).eval()
        if self.device == "cuda":
            self.model = self.model.half()

    @staticmethod
    def default_index_name(model: str = DEFAULT_MODEL, normalize: bool = True) -> str:
        return model.replace(":", "_").replace("/", "_") + ("-norm" if normalize else "")

    @property
    def index_name(self) -> str:
        return self.name.replace(":", "_").replace("/", "_") + ("-norm" if self.normalize else "")

    def _forward(self, bgr: list[np.ndarray]):
        torch = self.torch
        if self.normalize:
            bgr = [normalize_photometric(b) for b in bgr]
        if self.processor is None:
            x = torch.from_numpy(_preprocess_224(bgr)).to(self.device)
            if self.device == "cuda":
                x = x.half()
            return self.model(x)
        from PIL import Image

        imgs = [Image.fromarray(cv2.cvtColor(b, cv2.COLOR_BGR2RGB)) for b in bgr]
        inputs = self.processor(images=imgs, return_tensors="pt").to(self.device)
        if self.device == "cuda":
            inputs["pixel_values"] = inputs["pixel_values"].half()
        return self.model(**inputs).last_hidden_state[:, 0]

    def embed(self, bgr_cards: list[np.ndarray], batch: int = 64) -> np.ndarray:
        """L2-normalisierte Embeddings (N, D) float32."""
        if self.backend == "onnx":
            chunks = [self._embed_onnx(bgr_cards[i:i + batch]) for i in range(0, len(bgr_cards), batch)]
            return np.concatenate(chunks) if chunks else np.zeros((0, 1), np.float32)
        torch = self.torch
        chunks = []
        with torch.inference_mode():
            for i in range(0, len(bgr_cards), batch):
                f = self._forward(bgr_cards[i:i + batch]).float()
                chunks.append(torch.nn.functional.normalize(f, dim=1).cpu().numpy())
        return np.concatenate(chunks) if chunks else np.zeros((0, 1), np.float32)
