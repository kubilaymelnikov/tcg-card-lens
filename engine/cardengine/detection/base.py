"""Gemeinsame Schnittstelle aller Detektoren (klassisch, YOLO-OBB, später andere Modelle)."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

import numpy as np


@dataclass
class Detection:
    corners: np.ndarray  # (4, 2) float32, Bildkoordinaten, im Uhrzeigersinn, erste Kante kurz
    score: float
    game: str | None = None  # "mtg" | "pokemon" | None (Detektor kann es nicht unterscheiden)
    extra: dict = field(default_factory=dict)


class Detector(ABC):
    name: str = "base"

    @abstractmethod
    def detect(self, frame: np.ndarray) -> list[Detection]:
        """Karten in einem BGR-Frame finden."""
