"""Gleichwertigkeit und Geschwindigkeit der Backends prüfen (PyTorch vs. ONNX, je ONNX-Provider).

Jeder Lauf schreibt seine Ergebnisse in eine .npz-Datei; `diff` vergleicht zwei Läufe. So lassen sich auch Läufe aus
verschiedenen Umgebungen vergleichen (z. B. PyTorch-venv gegen eine venv mit onnxruntime-directml).

    # Embedder: 600 Referenzbilder (je Spiel gleich viele, fester Zufallsstartwert)
    python tools/compare_backends.py embed --backend torch --out t.npz
    python tools/compare_backends.py embed --backend torch --device cpu --out t32.npz   # PyTorch fp32
    python tools/compare_backends.py embed --backend onnx --provider cuda --out o.npz
    python tools/compare_backends.py diff t.npz o.npz
    # Detektor (ohne Eckenverfeinerung) auf echten Bildern
    python tools/compare_backends.py detect --backend torch --out dt.npz
    python tools/compare_backends.py detect --backend onnx --provider dml --out do.npz
    python tools/compare_backends.py diff dt.npz do.npz

Liest nur (Referenzbilder unter DATA_DIR/refimg, Bilder unter data/snapshots, data/ygo-test, data/frames).
Pflicht-Toleranzen (Indexname occ-dinov3s-norm, veröffentlichte Datenpakete): Kosinus im Mittel ≥ 0,999, Minimum
≥ 0,995; Detektor: gleiche Karten mit IoU ≥ 0,95.
"""
from __future__ import annotations

import argparse
import glob
import os
import random
import sys
import time

import cv2
import numpy as np

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)
from cardengine import paths  # noqa: E402

GAMES = ("pokemon", "mtg", "ygo")
IMAGE_DIRS = ("snapshots", "ygo-test", "frames")


def ref_images(n: int, seed: int = 0) -> list[str]:
    out = []
    per_game = max(1, n // len(GAMES))
    for g in GAMES:
        files = sorted(glob.glob(os.path.join(paths.DATA_DIR, "refimg", g, "*")))
        out += random.Random(seed).sample(files, min(per_game, len(files)))
    return out


def real_images(limit: int = 0) -> list[str]:
    out = []
    for d in IMAGE_DIRS:
        for ext in ("jpg", "png"):
            out += glob.glob(os.path.join(paths.DATA_DIR, d, "**", f"*.{ext}"), recursive=True)
    out = sorted(out)
    return out[:limit] if limit else out


def _ms(times: list[float]) -> float:
    return float(np.median(times)) if times else float("nan")


def run_embed(args) -> dict:
    from cardengine.recognition.embedder import Embedder

    emb = Embedder(backend=args.backend, device=args.device, provider=args.provider)
    files = ref_images(args.n, args.seed)
    imgs = [cv2.imread(f) for f in files]
    keep = [i for i, im in enumerate(imgs) if im is not None]
    files = [files[i] for i in keep]
    imgs = [imgs[i] for i in keep]
    emb.embed(imgs[:2])  # Aufwärmen
    t_single = []
    for im in imgs[:args.timing]:  # Latenz: ein Bild (Identifikation bettet 2–4 Lagen je Karte ein)
        t0 = time.perf_counter()
        emb.embed([im])
        t_single.append((time.perf_counter() - t0) * 1000)
    t0 = time.perf_counter()
    vecs = emb.embed(imgs, batch=32)
    per_img_batch = (time.perf_counter() - t0) * 1000 / len(imgs)
    t_model = _model_only(emb, imgs[:args.timing])
    label = f"{emb.backend}/{getattr(emb, 'device', '')}"
    print(f"{label}: {len(imgs)} Bilder | 1 Bild: Median {_ms(t_single):.1f} ms (davon Modell {_ms(t_model):.1f} ms) | "
          f"Stapel 32: {per_img_batch:.1f} ms/Bild (inkl. Bildnormierung)")
    return {"kind": "embed", "label": label, "files": np.array([os.path.relpath(f, paths.DATA_DIR) for f in files]),
            "vecs": vecs.astype(np.float32), "ms_single": _ms(t_single), "ms_batch": per_img_batch,
            "ms_model": _ms(t_model)}


def _model_only(emb, imgs) -> list[float]:
    """Nur das Netz (ein Bild, vorbereitete Eingabe) – ohne Bildnormierung und Größenänderung."""
    from cardengine.recognition.embedder import _preprocess_dinov3

    x = _preprocess_dinov3(imgs[:1])
    times = []
    if emb.backend == "onnx":
        for _ in imgs:
            t0 = time.perf_counter()
            emb.runner.run(x)
            times.append((time.perf_counter() - t0) * 1000)
        return times
    torch = emb.torch
    xt = torch.from_numpy(x).to(emb.device)
    if emb.device == "cuda":
        xt = xt.half()
    with torch.inference_mode():
        for _ in imgs:
            t0 = time.perf_counter()
            emb.model(pixel_values=xt).last_hidden_state[:, 0].float().cpu()
            times.append((time.perf_counter() - t0) * 1000)
    return times


def run_detect(args) -> dict:
    from cardengine.backend import resolve_backend
    from cardengine.detection.yolo_obb import OnnxObbDetector, YoloObbDetector
    from cardengine.models_fetch import DETECTOR_PATH, onnx_path

    backend = resolve_backend(args.backend)
    if backend == "torch":
        det = YoloObbDetector(DETECTOR_PATH, imgsz=640, conf=args.conf)
        label = "torch"
    else:
        det = OnnxObbDetector(onnx_path("detector"), imgsz=640, conf=args.conf, provider=args.provider)
        label = f"onnx/{det.provider}"
    files = real_images(args.limit)
    corners, scores, owner, times = [], [], [], []
    warm = cv2.imread(files[0])
    det.detect(warm)
    for i, f in enumerate(files):
        img = cv2.imread(f)
        if img is None:
            continue
        t0 = time.perf_counter()
        dets = det.detect(img)
        times.append((time.perf_counter() - t0) * 1000)
        for d in dets:
            corners.append(d.corners)
            scores.append(d.score)
            owner.append(i)
    print(f"{label}: {len(files)} Bilder, {len(corners)} Karten (conf {args.conf}) | Median {_ms(times):.1f} ms/Bild "
          f"(inkl. Vor-/Nachverarbeitung)")
    return {"kind": "detect", "label": label, "files": np.array([os.path.relpath(f, paths.DATA_DIR) for f in files]),
            "corners": np.array(corners, np.float32).reshape(-1, 4, 2), "scores": np.array(scores, np.float32),
            "owner": np.array(owner, np.int64), "ms": _ms(times)}


def diff(a_path: str, b_path: str) -> None:
    a, b = np.load(a_path), np.load(b_path)
    print(f"A = {a['label']}   B = {b['label']}")
    if str(a["kind"]) == "embed":
        assert list(a["files"]) == list(b["files"]), "unterschiedliche Bildlisten"
        cos = (a["vecs"] * b["vecs"]).sum(1)
        games = np.array([f.replace("\\", "/").split("/")[1] for f in a["files"]])
        print(f"Kosinus über {len(cos)} Bilder: Mittel {cos.mean():.6f}, Minimum {cos.min():.6f}, "
              f"1%-Quantil {np.quantile(cos, 0.01):.6f}")
        for g in GAMES:
            m = games == g
            if m.any():
                print(f"  {g:8} n={m.sum():4d}  Mittel {cos[m].mean():.6f}  Minimum {cos[m].min():.6f}")
        ok = cos.mean() >= 0.999 and cos.min() >= 0.995
        print("ERGEBNIS:", "gleichwertig (Mittel ≥ 0,999, Minimum ≥ 0,995)" if ok else "NICHT gleichwertig")
        return
    files = list(a["files"])
    assert files == list(b["files"]), "unterschiedliche Bildlisten"
    ious, dcorner, dscore, unmatched_a, unmatched_b = [], [], [], 0, 0
    for i in range(len(files)):
        ia = list(np.where(a["owner"] == i)[0])
        ib = list(np.where(b["owner"] == i)[0])
        # global gierig: jeweils das Paar mit der höchsten IoU zuordnen
        pairs = sorted(((quad_iou(a["corners"][k], b["corners"][j]), k, j) for k in ia for j in ib
                        if _boxes_overlap(a["corners"][k], b["corners"][j])), reverse=True)
        used_a, used_b = set(), set()
        for v, k, j in pairs:
            if v < 0.5 or k in used_a or j in used_b:
                continue
            used_a.add(k)
            used_b.add(j)
            ious.append(v)
            dcorner.append(float(np.abs(a["corners"][k] - b["corners"][j]).max()))
            dscore.append(abs(float(a["scores"][k]) - float(b["scores"][j])))
        unmatched_a += len(ia) - len(used_a)
        unmatched_b += len(ib) - len(used_b)
    ious = np.array(ious)
    print(f"{len(files)} Bilder: A {len(a['scores'])} Karten, B {len(b['scores'])} Karten, zugeordnet {len(ious)}, "
          f"nur A {unmatched_a}, nur B {unmatched_b}")
    if len(ious):
        print(f"IoU: Mittel {ious.mean():.5f}, Minimum {ious.min():.5f}, < 0,95: {(ious < 0.95).sum()} | "
              f"Eckenabweichung: Median {np.median(dcorner):.3f} px, max {max(dcorner):.2f} px | "
              f"Score-Abweichung: max {max(dscore):.4f}")
    ok = len(ious) and ious.min() >= 0.95 and unmatched_a == 0 and unmatched_b == 0
    print("ERGEBNIS:", "gleiche Karten (IoU ≥ 0,95)" if ok else "Abweichungen – Details oben")


def _boxes_overlap(a: np.ndarray, b: np.ndarray) -> bool:
    return bool((a.min(0) < b.max(0)).all() and (b.min(0) < a.max(0)).all())


def quad_iou(a: np.ndarray, b: np.ndarray, scale: int = 2) -> float:
    """IoU zweier Vierecke über Rasterung (1/2 px). `geometry.iou_quads` (cv2.intersectConvexConvex) liefert für
    deckungsgleiche Vierecke teils 0 – für diesen Vergleich ungeeignet."""
    pts = np.concatenate([a, b]).astype(np.float64)
    lo = np.floor(pts.min(0)) - 2
    size = (np.ceil(pts.max(0)) - lo + 4).astype(int) * scale
    ma = np.zeros((size[1], size[0]), np.uint8)
    mb = np.zeros_like(ma)
    cv2.fillPoly(ma, [np.round((a - lo) * scale).astype(np.int32)], 1)
    cv2.fillPoly(mb, [np.round((b - lo) * scale).astype(np.int32)], 1)
    inter = int(np.count_nonzero(ma & mb))
    union = int(np.count_nonzero(ma | mb))
    return inter / union if union else 0.0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("embed", "detect"):
        p = sub.add_parser(name)
        p.add_argument("--backend", choices=["onnx", "torch"], required=True)
        p.add_argument("--provider", help="ONNX: cuda | dml | cpu (Standard: auto)")
        p.add_argument("--device", help="PyTorch-Embedder: cpu erzwingt fp32 (Standard: cuda mit fp16)")
        p.add_argument("--out", required=True)
        p.add_argument("--n", type=int, default=600)
        p.add_argument("--seed", type=int, default=0)
        p.add_argument("--timing", type=int, default=50, help="Embedder: so viele Einzelbild-Messungen")
        p.add_argument("--conf", type=float, default=0.2, help="Detektor-Schwelle (Engine: 0,2)")
        p.add_argument("--limit", type=int, default=0, help="Detektor: höchstens so viele Bilder")
    p = sub.add_parser("diff")
    p.add_argument("a")
    p.add_argument("b")
    args = ap.parse_args()
    if args.cmd == "diff":
        return diff(args.a, args.b)
    res = run_embed(args) if args.cmd == "embed" else run_detect(args)
    np.savez(args.out, **res)


if __name__ == "__main__":
    main()
