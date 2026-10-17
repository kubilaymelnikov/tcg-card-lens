"""Trainingsdatensatz aus Bestätigungen exportieren (Stufe 2, siehe docs/DOCK.md).

Packt data/learned/dataset (labels.jsonl + entzerrte Bilder) in eine ZIP und gibt eine Statistik aus.

    python tools/export_training_set.py
    python tools/export_training_set.py --out D:/export/karten.zip
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import zipfile
from collections import Counter
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cardengine.learning import ROOT as LEARNED_ROOT  # noqa: E402
from cardengine.paths import DATA_DIR  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default=os.path.join(LEARNED_ROOT, "dataset"), help="Datensatz-Verzeichnis")
    ap.add_argument("--out", help="Ziel-ZIP (Standard: data/exports/<datum>.zip)")
    args = ap.parse_args()

    labels = os.path.join(args.dataset, "labels.jsonl")
    if not os.path.exists(labels):
        print(f"Kein Datensatz gefunden: {labels}")
        return 1
    out = args.out or os.path.join(DATA_DIR, "exports", f"{datetime.now():%Y-%m-%d}.zip")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)

    rows = []
    with open(labels, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    by_label, by_game, corrections = Counter(), Counter(), Counter()
    images = missing = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(labels, "labels.jsonl")
        for r in rows:
            by_label[r.get("label", "?")] += 1
            key = r.get("key")
            if key:
                by_game[key.split("|", 1)[0]] += 1
                proposed = (r.get("meta") or {}).get("proposed_key")
                if proposed and proposed != key:
                    corrections[key] += 1
            img = r.get("image")
            p = os.path.join(args.dataset, img) if img else None
            if p and os.path.exists(p):
                z.write(p, img.replace("\\", "/"), compress_type=zipfile.ZIP_STORED)  # JPEG: nicht nochmal packen
                images += 1
            else:
                missing += 1

    print(f"Export: {out}  ({os.path.getsize(out) / 1024 / 1024:.1f} MB)")
    print(f"Beispiele: {len(rows)}  Bilder: {images}  fehlend: {missing}")
    print("Je Label: " + (", ".join(f"{k} {v}" for k, v in by_label.most_common()) or "–"))
    print("Je Spiel: " + (", ".join(f"{k} {v}" for k, v in by_game.most_common()) or "–"))
    if corrections:
        print("Meiste Korrekturen (vorgeschlagen != bestätigt):")
        for key, n in corrections.most_common(10):
            print(f"  {n:4d}  {key}")
    else:
        print("Keine Korrekturen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
