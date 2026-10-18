TCG Card Lens – Lizenzen und Hinweise
=====================================

TCG Card Lens (OBS-Plugin und Engine) ist freie Software unter der GNU Affero General Public License v3.0
oder später.

Verwendete Modelle (werden bei der Einrichtung heruntergeladen, nicht im Installer enthalten)
- Kartendetektor „collector-mtg-pkm-detector-yolo11n-obb“ von Matteo Tomei (Open Card Collector),
  trainiert mit Ultralytics YOLO11 – Lizenz AGPL-3.0
- Karten-Embedder „collector-mtg-pkm-embedder-dinov3-small“ von Matteo Tomei, abgeleitet von Meta DINOv3 –
  DINOv3-Lizenz (https://ai.meta.com/resources/models-and-libraries/dinov3-license/)
- Beide Modelle verbreitet dieses Projekt zusätzlich in das ONNX-Format umgewandelt als Release-Dateien
  („tcglens-detector-occ-yolo11n-obb-v1.onnx“, „tcglens-embedder-occ-dinov3s-v1.onnx“; Gewichte unverändert, nur das
  Format ist umgewandelt). Für diese Dateien gelten dieselben Lizenzen wie für die Originale:
  - Detektor-ONNX: AGPL-3.0 (wie Ultralytics und die trainierten Gewichte). Quelle der Gewichte:
    https://huggingface.co/matteot11/collector-mtg-pkm-detector-yolo11n-obb – die Umwandlung ist mit
    engine/tools/export_onnx.py in diesem Repository nachvollziehbar.
  - Embedder-ONNX: DINOv3-Lizenz von Meta (Abschnitt 1.b.i): Weitergabe nur unter den Bedingungen dieser Lizenz,
    mit einer Kopie des Lizenztexts (liegt am Release als „LICENSE-DINOv3.txt“ neben der ONNX-Datei) und dem
    sichtbaren Hinweis „Built with DINOv3“. Die Nutzungsbeschränkungen der Lizenz gelten weiter (u. a. keine
    Verwendung für ITAR-/exportkontrollierte oder militärische Zwecke). Built with DINOv3.
    Quelle: https://huggingface.co/matteot11/collector-mtg-pkm-embedder-dinov3-small
- Texterkennung: PP-OCR-Modelle über RapidOCR – Apache-2.0
- Optional: RF-DETR-Seg-Kartenumrisse von Ximilar (cardstream) – Apache-2.0

Übernommener bzw. adaptierter Code
- obs-plugintemplate (OBS Project) – GPL-2.0-or-later
- Viereck-aus-Maske-Logik nach Ximilar cardstream (core/quad.py) – Apache-2.0
- nlohmann/json – MIT

Daten
- Kartendaten und Kartenbilder: Scryfall (Magic), TCGdex (Pokémon, Daten unter MIT-Lizenz) und YGOPRODeck
  (Yu-Gi-Oh!). Die Bilder werden nur lokal auf deinem Rechner gespeichert und nicht weitergegeben.
  Magic: The Gathering © Wizards of the Coast; Pokémon © Nintendo / Creatures / GAME FREAK / The Pokémon Company;
  Yu-Gi-Oh! © Konami / Studio Dice.
- Datenpakete (GitHub-Releases): enthalten selbst berechnete Erkennungsmerkmale (Embeddings, keine Bilder) und –
  nur für Pokémon (TCGdex, MIT) – Katalogdaten. Für Magic und Yu-Gi-Oh! lädt jede Installation den Katalog selbst
  bei Scryfall bzw. YGOPRODeck; deren Daten werden nicht weiterverbreitet.
- Preise: öffentliche Preisdateien von Cardmarket (cardmarket.com), Preisverlauf Magic: MTGJSON.
  Preise sind Richtwerte ohne Gewähr. Bitte die Nutzungsbedingungen von Cardmarket beachten, wenn Preise
  öffentlich (z. B. im Stream) gezeigt werden.

Dieses Projekt steht in keiner Verbindung zu Cardmarket, Wizards of the Coast, The Pokémon Company, Konami,
Scryfall, TCGdex, YGOPRODeck oder MTGJSON.
