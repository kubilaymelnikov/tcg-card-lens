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
# Yu-Gi-Oh: Set-Code ("LOB-DE001") rechts direkt unter dem Artwork (Monster, Zauber, Fallen; Link links neben dem
# Pfeil), bei Pendelmonstern links unter dem Textkasten. Die Zeile ist sehr klein (~1,7 % der Kartenhöhe): Der
# Textdetektor findet sie im Kamerabild kaum (synthetisch 2/30), die reine Zeilenerkennung auf festen Streifen dagegen
# meist (19/27). Daher mehrere schmale Streifen (x0, x1, y-Mitte, Höhe) gegen Eckenungenauigkeit, nur Erkennung.
YGO_CODE_LINES = {
    "ygo_art": [(0.50, 0.97, y, 0.032) for y in (0.716, 0.726, 0.736, 0.746)],
    "ygo_pendel": [(0.03, 0.47, y, 0.032) for y in (0.950, 0.962)],
}
MARGIN = 0.03


class FooterOcr:
    def __init__(self, use_gpu: bool = True):
        """`use_gpu`: CUDA bzw. DirectML, je nach installiertem onnxruntime-Paket (cardengine.backend.ocr_flags)."""
        from rapidocr import RapidOCR

        from ..backend import ocr_flags

        params = {"Global.log_level": "error"}
        if use_gpu:
            params.update(ocr_flags())
        try:
            self.engine = RapidOCR(params=params)
        except Exception:
            self.engine = RapidOCR(params={"Global.log_level": "error"})

    def _crop(self, card: np.ndarray, region: str) -> np.ndarray:
        x, y, w, h = FOOTER_REGIONS[region]
        mx, my = RECT_W * MARGIN, RECT_H * MARGIN
        x0, y0 = int(mx + x * RECT_W), int(my + y * RECT_H)
        x1, y1 = int(mx + (x + w) * RECT_W), int(my + (y + h) * RECT_H + my)  # bis in den unteren Rand
        crop = card[y0:min(y1, card.shape[0]), x0:min(x1, card.shape[1])]
        # Kleine Schrift: hochskalieren und Kontrast spreizen
        crop = cv2.resize(crop, None, fx=3, fy=3, interpolation=cv2.INTER_CUBIC)
        lab = cv2.cvtColor(crop, cv2.COLOR_BGR2LAB)
        lab[..., 0] = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(lab[..., 0])
        return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)

    def read_footer(self, card_with_margin: np.ndarray) -> str:
        return self._read(card_with_margin, FOOTER_REGIONS)

    def read_set_code(self, card_with_margin: np.ndarray, stop=None) -> str:
        """Yu-Gi-Oh: Text der Set-Code-Streifen (entzerrte Karte mit 3 % Rand), eine Zeile je Streifen.
        `stop(text) -> bool` beendet das Lesen vorzeitig (z. B. sobald ein passender Code gefunden ist); der
        Pendel-Bereich wird nur gelesen, wenn unter dem Artwork nichts Passendes stand."""
        from .footer import parse_ygo_codes

        stop = stop or (lambda t: bool(parse_ygo_codes(t)))
        mx, my = RECT_W * MARGIN, RECT_H * MARGIN
        texts: list[str] = []
        for lines in YGO_CODE_LINES.values():
            for x0, x1, yc, h in lines:
                ya, yb = int(my + (yc - h / 2) * RECT_H), int(my + (yc + h / 2) * RECT_H)
                strip = card_with_margin[max(0, ya):yb, int(mx + x0 * RECT_W):int(mx + x1 * RECT_W)]
                if strip.size == 0:
                    continue
                res = self.engine(strip, use_det=False, use_cls=False, use_rec=True)
                text = " ".join(res.txts) if res is not None and getattr(res, "txts", None) else ""
                if text:
                    texts.append(text)
            if any(stop(t) for t in texts):
                break
        return "\n".join(texts)

    def _read(self, card_with_margin: np.ndarray, regions: dict) -> str:
        texts = []
        for region in regions:
            res = self.engine(self._crop(card_with_margin, region))
            if res is not None and getattr(res, "txts", None):
                texts.append(" ".join(res.txts))
        return "\n".join(texts)
