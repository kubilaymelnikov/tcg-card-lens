"""OCR der Kartenfußzeile (Sammlernummer, Set-Code) mit RapidOCR (PP-OCR-Modelle, ONNX Runtime, Apache-2.0)."""
from __future__ import annotations

import cv2
import numpy as np

from ..geometry import RECT_H, RECT_W

# Bereiche der entzerrten Karte (mit 3 % Rand, siehe identifier.rectify(margin=0.03)) als Anteile der Kartenfläche
FOOTER_REGIONS = {
    "left": (0.0, 0.88, 0.62, 0.12),  # Magic ab 2015, Pokémon ab Schwert & Schild
    "right": (0.45, 0.88, 0.55, 0.12),  # ältere Pokémon-Karten
}
# Yu-Gi-Oh: Set-Code ("LOB-DE001") rechts direkt unter dem Artwork (Monster, Zauber, Fallen, Link links neben dem
# Pfeil), bei Pendelmonstern links unter dem Textkasten. Ausschnitte großzügig wegen Toleranz der Ecken.
YGO_CODE_REGIONS = {
    "ygo_art": (0.45, 0.70, 0.53, 0.07),
    "ygo_pendel": (0.02, 0.925, 0.50, 0.06),
}
MARGIN = 0.03


class FooterOcr:
    def __init__(self, use_cuda: bool = True):
        from rapidocr import RapidOCR

        params = {"Global.log_level": "error"}
        if use_cuda:
            params["EngineConfig.onnxruntime.use_cuda"] = True
        try:
            self.engine = RapidOCR(params=params)
        except Exception:
            self.engine = RapidOCR(params={"Global.log_level": "error"})

    def _crop(self, card: np.ndarray, region: str) -> np.ndarray:
        footer = region in FOOTER_REGIONS
        x, y, w, h = FOOTER_REGIONS[region] if footer else YGO_CODE_REGIONS[region]
        mx, my = RECT_W * MARGIN, RECT_H * MARGIN
        x0, y0 = int(mx + x * RECT_W), int(my + y * RECT_H)
        x1, y1 = int(mx + (x + w) * RECT_W), int(my + (y + h) * RECT_H + (my if footer else 0))  # Fußzeile: bis in den Rand
        crop = card[y0:min(y1, card.shape[0]), x0:min(x1, card.shape[1])]
        # Kleine Schrift: hochskalieren und Kontrast spreizen
        crop = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        lab = cv2.cvtColor(crop, cv2.COLOR_BGR2LAB)
        lab[..., 0] = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(lab[..., 0])
        return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)

    def read_footer(self, card_with_margin: np.ndarray) -> str:
        return self._read(card_with_margin, FOOTER_REGIONS)

    def read_set_code(self, card_with_margin: np.ndarray) -> str:
        """Yu-Gi-Oh: Text der Set-Code-Bereiche (entzerrte Karte mit 3 % Rand). Der Pendel-Bereich wird nur gelesen,
        wenn unter dem Artwork kein Set-Code steht (spart einen OCR-Lauf)."""
        from .footer import parse_ygo_codes

        texts = []
        for region in YGO_CODE_REGIONS:
            text = self._read(card_with_margin, {region: None})
            texts.append(text)
            if parse_ygo_codes(text):
                break
        return "\n".join(t for t in texts if t)

    def _read(self, card_with_margin: np.ndarray, regions: dict) -> str:
        texts = []
        for region in regions:
            res = self.engine(self._crop(card_with_margin, region))
            if res is not None and getattr(res, "txts", None):
                texts.append(" ".join(res.txts))
        return "\n".join(texts)
