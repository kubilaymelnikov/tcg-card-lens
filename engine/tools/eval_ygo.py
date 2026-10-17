"""Yu-Gi-Oh-Identifikation messen.

    python tools/eval_ygo.py --snapshots data/ygo-test        # echte Kamerabilder (Ecken aus den Snapshot-.json)
    python tools/eval_ygo.py --n 200                         # synthetisch: Referenzbilder verzerrt/verrauscht
    python tools/eval_ygo.py --n 200 --set-code              # zusätzlich Set-Code aufs Bild gedruckt (OCR-Pfad)

Synthetisch: Die YGOPRODeck-Bilder sind Nachbauten ohne Set-Code. Ohne `--set-code` kann nur der Name stimmen;
ein Druck gilt dann nur bei Karten mit genau einem TCG-Druck als sicher. Mit `--set-code` wird der Code eines
zufälligen Drucks der Karte (zufällig EN/DE/G-Variante) an die Stelle unter dem Artwork geschrieben – die Wahrheit
ist dann dieser Druck. Kernzahl: „sicher, aber falsch“ (falscher Preis) muss ~0 sein.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import random
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cardengine.card_database.catalog import Catalog  # noqa: E402
from cardengine.geometry import order_corners  # noqa: E402
from cardengine.recognition.embedder import Embedder  # noqa: E402
from cardengine.recognition.footer import ygo_code_key, ygo_match_codes  # noqa: E402
from cardengine.recognition.identifier import CardIdentifier  # noqa: E402
from cardengine.recognition.index import EmbeddingIndex, index_path  # noqa: E402
from cardengine.recognition.ocr import FooterOcr  # noqa: E402
from eval_identify import camera_view  # noqa: E402

YGO_RATIO = 59 / 86


def load_identifier(model: str) -> CardIdentifier:
    emb = Embedder(model)
    if not os.path.exists(index_path(emb.index_name)):
        sys.exit("kein Index – erst build_index(embedder, games=('ygo',)) ausführen")
    return CardIdentifier(EmbeddingIndex.load(emb.index_name), Catalog(), emb, FooterOcr())


def describe(ident, res) -> str:
    if res is None:
        return "keine Karte"
    top = []
    for k, s in res.alternatives[:5]:
        r = ident._record(k)
        top.append(f"{r.name if r else k} ({s:.3f})")
    state = "SICHER" if res.certain else "unsicher"
    return (f"{state}: {res.name} | Druck {res.number or '-'} ({res.method}, {res.similarity:.3f})"
            f"{' – ' + res.reason if res.reason else ''}\n      OCR: {res.footer_text!r}\n"
            f"      Kandidaten: {len(res.candidates)}  Top-5: {', '.join(top)}  ({res.ms:.0f} ms)")


def eval_snapshots(ident: CardIdentifier, folder: str) -> None:
    for path in sorted(glob.glob(os.path.join(folder, "*.json"))):
        meta = json.load(open(path, encoding="utf-8"))
        frame = cv2.imread(path[:-5] + ".jpg")
        print(f"\n=== {os.path.basename(path)} ({frame.shape[1]}×{frame.shape[0]})")
        quads = [("Track", t["corners"]) for t in meta.get("tracks", [])]
        quads += [("Detektion", d["corners"]) for d in meta.get("detections", [])]
        for label, corners in quads:
            q = order_corners(np.array(corners, np.float32))
            w = (np.linalg.norm(q[0] - q[1]) + np.linalg.norm(q[2] - q[3])) / 2
            h = (np.linalg.norm(q[1] - q[2]) + np.linalg.norm(q[3] - q[0])) / 2
            res = ident.identify(frame, q, game="ygo")
            print(f"  {label} ({w:.0f}×{h:.0f} px, Seitenverhältnis {w / h:.3f}): {describe(ident, res)}")


def put_set_code(card: np.ndarray, code: str) -> np.ndarray:
    """Set-Code wie gedruckt rechts unter das Artwork schreiben (schwarz, ~1,7 % der Kartenhöhe)."""
    out = card.copy()
    h, w = out.shape[:2]
    scale = h * 0.017 / 22  # Hershey-Simplex: ~22 px Versalhöhe bei Skala 1
    (tw, _), _ = cv2.getTextSize(code, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)
    x, y = int(w * 0.925) - tw, int(h * 0.742)
    cv2.putText(out, code, (x, y), cv2.FONT_HERSHEY_SIMPLEX, scale, (20, 20, 20), max(1, round(scale * 1.6)), cv2.LINE_AA)
    return out


def regional(code: str, rng: random.Random) -> str:
    """EN-Code in eine zufällige gedruckte Variante umschreiben: EN / DE / alte deutsche „G“-Codes."""
    prefix, rest = code.split("-", 1)
    num = rest[2:] if rest.startswith("EN") else rest
    variant = rng.choice(["EN", "DE", "G"]) if rest.startswith("EN") else ""
    return f"{prefix}-{variant}{num}"


def eval_synthetic(ident: CardIdentifier, n: int, card_px: int, seed: int, set_code: bool) -> None:
    rng = random.Random(seed)
    np.random.seed(seed)
    cat = ident.catalog
    by_key = {k: ident._record(k) for k in ident.index.keys if k.startswith("ygo|")}
    sample = rng.sample(sorted(by_key), min(n, len(by_key)))
    stats = {"sicher+richtig": 0, "sicher+FALSCH": 0, "unsicher (Name richtig)": 0, "unsicher (Name falsch)": 0,
             "keine Karte": 0}
    top1 = 0
    times, wrong, methods, reasons = [], [], {}, {}
    ambiguous = code_read = 0  # Wahrheit nicht eindeutig (gleicher Code, mehrere Seltenheiten) / Code gelesen
    for i, key in enumerate(sample):
        rec = by_key[key]
        card = cv2.imread(rec.image_path)
        truth = None
        if set_code:
            prints = [r for r in cat.by_name("ygo", rec.name) if r.set_code and ygo_code_key(r.printed_number)]
            if prints:
                truth = rng.choice(prints)
                card = put_set_code(card, regional(truth.printed_number, rng))
        scene, corners = camera_view(card, rng, card_px, ratio=YGO_RATIO)
        t0 = time.perf_counter()
        res = ident.identify(scene, corners, game="ygo")
        times.append((time.perf_counter() - t0) * 1000)
        if res is None:
            stats["keine Karte"] += 1
            continue
        top1 += res.name == rec.name
        if truth is not None:
            same = [r for r in cat.by_name("ygo", rec.name) if ygo_code_key(r.printed_number) == ygo_code_key(truth.printed_number)]
            ambiguous += len(same) > 1
            code_read += ygo_code_key(truth.printed_number) in ygo_match_codes(res.footer_text, [truth.printed_number])
            if os.environ.get("EVAL_DEBUG"):
                print(f"    {truth.printed_number} turns={res.turns} OCR={res.footer_text!r}")
        if not res.certain:
            reason = res.reason.split(" (")[0].split(" mit ")[0]
            reason = "mehrere Seltenheiten" if "Seltenheiten" in reason else reason
            reasons[reason] = reasons.get(reason, 0) + 1
        if res.certain:
            if truth is not None:
                # Gleicher Schlüssel + gleiche Seltenheit wäre nicht unterscheidbar → zählt nur der Druck selbst
                ok = res.card_id == truth.id
            else:
                own = [r for r in cat.by_name("ygo", rec.name) if r.set_code]
                ok = res.name == rec.name and (len(own) == 1 and res.card_id == own[0].id)
            stats["sicher+richtig" if ok else "sicher+FALSCH"] += 1
            methods[res.method] = methods.get(res.method, 0) + 1
            if not ok:
                wrong.append((rec, truth, res))
        else:
            stats["unsicher (Name richtig)" if res.name == rec.name else "unsicher (Name falsch)"] += 1
        if (i + 1) % 50 == 0:
            print(f"{i + 1}/{len(sample)} Name top-1 {top1 / (i + 1):.1%} {stats}", flush=True)
    m = len(sample)
    print(f"\n=== Yu-Gi-Oh synthetisch: {m} Artworks, Karte {card_px} px hoch, Set-Code {'ja' if set_code else 'nein'} ===")
    print(f"  Name top-1: {top1 / m:.1%}")
    for k, v in stats.items():
        print(f"  {k:26} {v:4d}  ({v / m:.1%})")
    print(f"  Verfahren der sicheren Treffer: {methods}")
    print(f"  Gründe „unsicher“: {reasons}")
    if set_code:
        print(f"  Set-Code richtig gelesen: {code_read}/{m} ({code_read / m:.1%}); Druck prinzipiell nicht eindeutig "
              f"(gleicher Code in mehreren Seltenheiten): {ambiguous}/{m}")
    print(f"  Zeit je Identifikation: Median {np.median(times):.0f} ms, p95 {np.quantile(times, 0.95):.0f} ms")
    for rec, truth, res in wrong[:10]:
        print(f"  FALSCH sicher: {rec.name} [{truth.printed_number if truth else '-'}] -> {res.name} "
              f"[{res.number}] ({res.method}, {res.similarity:.2f}) OCR={res.footer_text!r}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshots", help="Ordner mit Snapshot-.jpg/.json")
    ap.add_argument("--n", type=int, default=0)
    ap.add_argument("--card-px", type=int, default=400)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--set-code", action="store_true")
    ap.add_argument("--model", default="occ-dinov3s")
    args = ap.parse_args()
    ident = load_identifier(args.model)
    if args.snapshots:
        eval_snapshots(ident, args.snapshots)
    if args.n:
        eval_synthetic(ident, args.n, args.card_px, args.seed, args.set_code)


if __name__ == "__main__":
    main()
