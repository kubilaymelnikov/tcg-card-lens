TCG Card Lens – Licences and notices
===================================

TCG Card Lens (OBS plugin and engine) is free software under the GNU Affero General Public License v3.0
or later.

Models used (downloaded during setup, not included in the installer)
- Card detector "collector-mtg-pkm-detector-yolo11n-obb" by Matteo Tomei (Open Card Collector),
  trained with Ultralytics YOLO11 – licence AGPL-3.0
- Card embedder "collector-mtg-pkm-embedder-dinov3-small" by Matteo Tomei, derived from Meta DINOv3 –
  DINOv3 licence (https://ai.meta.com/resources/models-and-libraries/dinov3-license/)
- This project additionally redistributes both models converted to ONNX as release files
  ("tcglens-detector-occ-yolo11n-obb-v1.onnx", "tcglens-embedder-occ-dinov3s-v1.onnx"; weights unchanged, only the
  format is converted). The same licences as for the originals apply:
  - Detector ONNX: AGPL-3.0 (like Ultralytics and the trained weights). Source of the weights:
    https://huggingface.co/matteot11/collector-mtg-pkm-detector-yolo11n-obb – the conversion is reproducible with
    engine/tools/export_onnx.py in this repository.
  - Embedder ONNX: Meta's DINOv3 licence (section 1.b.i): redistribution only under the terms of that licence,
    with a copy of the licence text (attached to the release as "LICENSE-DINOv3.txt" next to the ONNX file) and the
    prominently displayed notice "Built with DINOv3". The licence's use restrictions continue to apply (incl. no use
    for ITAR/export-controlled or military purposes). Built with DINOv3.
    Source: https://huggingface.co/matteot11/collector-mtg-pkm-embedder-dinov3-small
- Text recognition: PP-OCR models via RapidOCR – Apache-2.0
- Optional: RF-DETR-Seg card outlines by Ximilar (cardstream) – Apache-2.0

Adopted or adapted code
- obs-plugintemplate (OBS Project) – GPL-2.0-or-later
- Quad-from-mask logic after Ximilar cardstream (core/quad.py) – Apache-2.0
- nlohmann/json – MIT

Data
- Card data and card images: Scryfall (Magic), TCGdex (Pokémon, data under the MIT licence) and YGOPRODeck
  (Yu-Gi-Oh!). Images are stored only locally on your computer and are never redistributed.
  Magic: The Gathering © Wizards of the Coast; Pokémon © Nintendo / Creatures / GAME FREAK / The Pokémon Company;
  Yu-Gi-Oh! © Konami / Studio Dice.
- Data packs (GitHub releases): contain self-computed recognition features (embeddings, no images) and – for
  Pokémon only (TCGdex, MIT) – catalogue data. For Magic and Yu-Gi-Oh! every installation downloads the catalogue
  itself from Scryfall or YGOPRODeck; their data is not redistributed.
- Prices: public price guide files from Cardmarket (cardmarket.com), Magic price history: MTGJSON.
  Prices are guide values without warranty. Please respect Cardmarket's terms of use when showing prices
  publicly (e.g. on stream).

This project is not affiliated with Cardmarket, Wizards of the Coast, The Pokémon Company, Konami,
Scryfall, TCGdex, YGOPRODeck or MTGJSON.
