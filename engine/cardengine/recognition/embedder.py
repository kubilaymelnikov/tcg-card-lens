"""Bild-Embeddings für entzerrte Karten. Austauschbare Modelle:

- `occ-dinov3s` (Standard): matteot11/collector-mtg-pkm-embedder-dinov3-small – DINOv3-S, auf Magic- und
  Pokémon-Karten kontrastiv nachtrainiert (Open Card Collector, MIT-Code; Gewichte unter DINOv3-Lizenz).
- `dinov2s`: DINOv2 ViT-S/14 ohne Kartentraining (Apache-2.0) – nur Basislinie.
- `hf:<model-id>`: beliebiges Hugging-Face-Bildmodell (CLS-Token).
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


class Embedder:
    def __init__(self, model: str = DEFAULT_MODEL, device: str | None = None, normalize: bool = True):
        import torch

        self.normalize = normalize
        self.torch = torch
        self.name = model
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
        torch = self.torch
        chunks = []
        with torch.inference_mode():
            for i in range(0, len(bgr_cards), batch):
                f = self._forward(bgr_cards[i:i + batch]).float()
                chunks.append(torch.nn.functional.normalize(f, dim=1).cpu().numpy())
        return np.concatenate(chunks) if chunks else np.zeros((0, 1), np.float32)
