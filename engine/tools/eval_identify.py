"""Identifikation messen: Katalogkarten werden wie im Kamerabild verfremdet (Perspektive, ungenaue Ecken,
Unschärfe, Rauschen, Glanz, dunkles Licht), dann entzerrt und gesucht.

    python tools/eval_identify.py --game pokemon --n 500
    python tools/eval_identify.py --game pokemon --n 500 --backend onnx --no-build   # Backends vergleichen, nur lesen
    python tools/eval_identify.py --frames data/labeled   # echte Kamerabilder mit Label-Datei (Phase 1b)

Hinweis: Synthetische Abfragen stammen aus denselben Referenzbildern wie der Index → Werte sind optimistisch.
Aussagekräftig sind sie vor allem für den *Vergleich* von Verfahren und für Edition/Reprint-Verwechslungen.
"""
from __future__ import annotations

import argparse
import os
import random
import sys
import time

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cardengine.card_database.catalog import Catalog  # noqa: E402
from cardengine.geometry import rectify  # noqa: E402
from cardengine.recognition.embedder import Embedder  # noqa: E402
from cardengine.recognition.index import EmbeddingIndex, available_games, build_index, migrate_legacy  # noqa: E402


def camera_view(card: np.ndarray, rng: random.Random, card_px: int, ratio: float = 63 / 88) -> tuple[np.ndarray, np.ndarray]:
    """Karte in eine 1280×720-Szene setzen; liefert Szene und (leicht verrauschte) Ecken wie vom Detektor.
    `ratio` = Breite/Höhe der Karte (Yu-Gi-Oh: 59/86)."""
    W, H = 1280, 720
    bg = np.full((H, W, 3), [rng.randint(20, 200) for _ in range(3)], np.uint8)
    bg = np.clip(bg + np.random.normal(0, 8, bg.shape), 0, 255).astype(np.uint8)
    ch = card_px
    cw = ch * ratio
    cx, cy = W / 2 + rng.uniform(-200, 200), H / 2 + rng.uniform(-100, 100)
    a = rng.uniform(-0.6, 0.6)
    pts = np.float32([[-cw / 2, -ch / 2], [cw / 2, -ch / 2], [cw / 2, ch / 2], [-cw / 2, ch / 2]])
    pts = pts @ np.float32([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]]).T
    pts += np.random.uniform(-0.12, 0.12, pts.shape).astype(np.float32) * cw
    pts += np.float32([cx, cy])
    src = np.float32([[0, 0], [card.shape[1], 0], [card.shape[1], card.shape[0]], [0, card.shape[0]]])
    m = cv2.getPerspectiveTransform(src, pts)
    warped = cv2.warpPerspective(card, m, (W, H))
    mask = cv2.warpPerspective(np.full(card.shape[:2], 255, np.uint8), m, (W, H)) > 0
    bg[mask] = warped[mask]
    if rng.random() < 0.4:  # Glanz
        g = np.zeros((H, W), np.float32)
        c = pts.mean(axis=0) + np.random.uniform(-0.3, 0.3, 2) * cw
        cv2.circle(g, (int(c[0]), int(c[1])), int(cw * rng.uniform(0.1, 0.3)), 1.0, -1)
        g = cv2.GaussianBlur(g, (0, 0), cw * 0.1) * mask * rng.uniform(0.3, 0.8)
        bg = np.clip(bg + g[..., None] * 255, 0, 255).astype(np.uint8)
    f = bg.astype(np.float32) * rng.uniform(0.4, 1.1) + rng.uniform(-20, 10)
    f += np.random.normal(0, rng.uniform(2, 8), f.shape)
    bg = np.clip(f, 0, 255).astype(np.uint8)
    if rng.random() < 0.5:
        bg = cv2.GaussianBlur(bg, (0, 0), rng.uniform(0.5, 1.8))
    _, enc = cv2.imencode(".jpg", bg, [cv2.IMWRITE_JPEG_QUALITY, rng.randint(60, 90)])
    bg = cv2.imdecode(enc, cv2.IMREAD_COLOR)
    detected = pts + np.random.normal(0, card_px * 0.012, pts.shape).astype(np.float32)  # Detektor-Ungenauigkeit
    return bg, detected


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default="pokemon")
    ap.add_argument("--n", type=int, default=500)
    ap.add_argument("--card-px", type=int, default=300, help="Kartenhöhe im Kamerabild")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--model", default="occ-dinov3s")
    ap.add_argument("--pipeline", action="store_true", help="komplette Identifikation inkl. OCR und Unsicherheitslogik")
    ap.add_argument("--backend", choices=["auto", "onnx", "torch"], help="Embedder über ONNX Runtime oder PyTorch")
    ap.add_argument("--no-build", action="store_true",
                    help="Index nie neu aufbauen (nur lesen); Karten ohne Vektor werden übersprungen")
    args = ap.parse_args()
    rng = random.Random(args.seed)
    np.random.seed(args.seed)

    emb = Embedder(args.model, backend=args.backend)
    print(f"Embedder: {emb.backend} ({emb.device})", flush=True)
    if not args.no_build:
        migrate_legacy(emb.index_name)
    index = EmbeddingIndex.load(emb.index_name) if args.no_build or available_games(emb.index_name) else build_index(emb)
    missing = [r for r in Catalog().all(args.game) if os.path.exists(r.image_path) and index.vector(f"{r.game}|{r.id}") is None]
    if missing and not args.no_build:
        index = build_index(emb)
    cat = Catalog()
    recs = [r for r in cat.all(args.game) if os.path.exists(r.image_path)
            and (not args.no_build or index.vector(f"{r.game}|{r.id}") is not None)]
    sample = rng.sample(recs, min(args.n, len(recs)))
    by_key = {f"{r.game}|{r.id}": r for r in cat.all(args.game)}

    if args.pipeline:
        return eval_pipeline(args, emb, index, cat, sample, rng)

    exact = name_ok = 0
    sims_ok, sims_bad, times = [], [], []
    confusions = []
    for i, rec in enumerate(sample):
        card = cv2.imread(rec.image_path)
        scene, corners = camera_view(card, rng, args.card_px)
        t0 = time.perf_counter()
        views = [rectify(scene, corners, rotate180=r) for r in (False, True)]
        e = emb.embed(views)
        hits = [index.search(v, k=5, game=args.game) for v in e]
        top = max(hits, key=lambda h: h[0][1])
        times.append((time.perf_counter() - t0) * 1000)
        key, sim = top[0]
        got = by_key[key]
        if got.id == rec.id:
            exact += 1
            sims_ok.append(sim)
        else:
            sims_bad.append(sim)
            confusions.append((rec, got, sim))
        if got.name == rec.name:
            name_ok += 1
        if (i + 1) % 100 == 0:
            print(f"{i + 1}/{len(sample)}  exakt {exact / (i + 1):.1%}  Name {name_ok / (i + 1):.1%}", flush=True)

    n = len(sample)
    print(f"\n=== {args.game}, {n} Abfragen, Karte {args.card_px} px hoch, Index {len(index.keys)} Karten ===")
    print(f"exakter Druck: {exact / n:.1%}   richtiger Name: {name_ok / n:.1%}")
    print(f"Zeit je Identifikation (2 Lagen, inkl. Suche): Median {np.median(times):.1f} ms")
    if sims_ok:
        print(f"Ähnlichkeit richtig: Median {np.median(sims_ok):.3f}, 5%-Quantil {np.quantile(sims_ok, 0.05):.3f}")
    if sims_bad:
        print(f"Ähnlichkeit falsch:  Median {np.median(sims_bad):.3f}, 95%-Quantil {np.quantile(sims_bad, 0.95):.3f}")
    same_name = sum(1 for a, b, _ in confusions if a.name == b.name)
    print(f"Verwechslungen: {len(confusions)}, davon gleicher Name (Reprint/Variante): {same_name}")
    for a, b, s in confusions[:12]:
        print(f"  {a.name} [{a.set_code} {a.printed_number}] -> {b.name} [{b.set_code} {b.printed_number}] ({s:.3f})")


def eval_pipeline(args, emb, index, cat, sample, rng):
    """Kernfrage: Wie oft meldet das System 'sicher' und liegt falsch (→ falscher Preis)?"""
    from cardengine.recognition.identifier import CardIdentifier
    from cardengine.recognition.ocr import FooterOcr

    ident = CardIdentifier(index, cat, emb, FooterOcr())
    stats = {"sicher+richtig": 0, "sicher+FALSCH": 0, "unsicher (Name richtig)": 0, "unsicher (Name falsch)": 0, "keine Karte": 0}
    methods, times, wrong = {}, [], []
    for i, rec in enumerate(sample):
        card = cv2.imread(rec.image_path)
        scene, corners = camera_view(card, rng, args.card_px)
        t0 = time.perf_counter()
        res = ident.identify(scene, corners, game=args.game)
        times.append((time.perf_counter() - t0) * 1000)
        if res is None:
            stats["keine Karte"] += 1
        elif res.certain:
            ok = res.card_id == rec.id
            stats["sicher+richtig" if ok else "sicher+FALSCH"] += 1
            methods[res.method] = methods.get(res.method, 0) + 1
            if not ok:
                wrong.append((rec, res))
        else:
            stats["unsicher (Name richtig)" if res.name == rec.name else "unsicher (Name falsch)"] += 1
        if (i + 1) % 100 == 0:
            print(f"{i + 1}/{len(sample)} {stats}", flush=True)
    n = len(sample)
    print(f"\n=== Pipeline {args.model}: {args.game}, {n} Karten, {args.card_px} px ===")
    for k, v in stats.items():
        print(f"  {k:26} {v:4d}  ({v / n:.1%})")
    print(f"  Verfahren der sicheren Treffer: {methods}")
    print(f"  Zeit je Identifikation: Median {np.median(times):.0f} ms, p95 {np.quantile(times, 0.95):.0f} ms")
    for rec, res in wrong[:10]:
        print(f"  FALSCH sicher: {rec.name} [{rec.set_code} {rec.printed_number}] -> {res.set_code} {res.number} ({res.method}, {res.similarity:.2f}) OCR='{res.footer_text[:40]}'")


if __name__ == "__main__":
    main()
