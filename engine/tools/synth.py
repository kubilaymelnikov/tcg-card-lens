"""Synthetischen Trainingsdatensatz für den Kartendetektor erzeugen (YOLO-OBB-Format).

    python tools/synth.py --count 20000 --out data/synth

Jedes Bild: Hintergrund (prozedural oder eigene Kamerabilder ohne Karten aus data/backgrounds), 1–9 Karten aus
den lokalen Referenzbildern mit zufälliger Perspektive/Rotation/Größe, Teilverdeckung (Hand), Glanz (Hülle/Foil),
Bewegungsunschärfe, Rauschen, Farbstich, JPEG-Artefakten. Störobjekte mit anderem Seitenverhältnis (Handy, Buch)
lehren das Modell, nicht jedes Rechteck als Karte zu melden.

Labels: `klasse x1 y1 x2 y2 x3 y3 x4 y4` (normiert), Klasse 0 = mtg, 1 = pokemon.
"""
from __future__ import annotations

import argparse
import glob
import os
import random
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from cardengine.paths import DATA_DIR  # noqa: E402

CLASSES = {"mtg": 0, "pokemon": 1}
CARD_RATIO = 63 / 88


def load_card_paths() -> dict[str, list[str]]:
    out = {}
    for game in CLASSES:
        out[game] = glob.glob(os.path.join(DATA_DIR, "refimg", game, "*.jpg"))
    return out


def procedural_background(w: int, h: int, rng: random.Random) -> np.ndarray:
    kind = rng.choice(["gradient", "noise", "wood", "fabric", "blocks"])
    base = np.array([rng.randint(0, 255) for _ in range(3)], np.float32)
    if kind == "gradient":
        other = np.array([rng.randint(0, 255) for _ in range(3)], np.float32)
        t = np.linspace(0, 1, w, dtype=np.float32)[None, :, None]
        img = base * (1 - t) + other * t
        img = np.repeat(img, h, axis=0)
    elif kind == "wood":
        y = np.arange(h, dtype=np.float32)[:, None]
        x = np.arange(w, dtype=np.float32)[None, :]
        rings = np.sin((y + 8 * np.sin(x / rng.uniform(40, 120))) / rng.uniform(3, 9)) * 0.5 + 0.5
        img = base[None, None, :] * (0.6 + 0.4 * rings[..., None])
    elif kind == "fabric":
        n = cv2.resize(np.random.rand(h // 4 + 1, w // 4 + 1).astype(np.float32), (w, h))
        img = base[None, None, :] * (0.7 + 0.3 * n[..., None])
    elif kind == "blocks":
        img = np.zeros((h, w, 3), np.float32) + base
        for _ in range(rng.randint(3, 12)):
            x0, y0 = rng.randint(0, w), rng.randint(0, h)
            c = [rng.randint(0, 255) for _ in range(3)]
            cv2.rectangle(img, (x0, y0), (x0 + rng.randint(50, w // 2), y0 + rng.randint(50, h // 2)), c, -1)
    else:
        img = np.zeros((h, w, 3), np.float32) + base
    noise = np.random.normal(0, rng.uniform(2, 15), (h, w, 3)).astype(np.float32)
    return np.clip(img + noise, 0, 255).astype(np.uint8)


def random_background(w, h, rng, real_bgs: list[str]) -> np.ndarray:
    if real_bgs and rng.random() < 0.6:
        img = cv2.imread(rng.choice(real_bgs))
        if img is not None:
            if rng.random() < 0.5:
                img = cv2.flip(img, 1)
            return cv2.resize(img, (w, h))
    return procedural_background(w, h, rng)


def card_quad(w, h, rng, size_frac: float) -> np.ndarray:
    """Zufälliges Viereck einer Karte: Größe, Rotation, Perspektive."""
    ch = h * size_frac
    cw = ch * CARD_RATIO
    cx, cy = rng.uniform(0.05, 0.95) * w, rng.uniform(0.05, 0.95) * h
    ang = rng.uniform(0, 2 * np.pi) if rng.random() < 0.6 else rng.gauss(0, 0.25)
    pts = np.array([[-cw / 2, -ch / 2], [cw / 2, -ch / 2], [cw / 2, ch / 2], [-cw / 2, ch / 2]], np.float32)
    rot = np.array([[np.cos(ang), -np.sin(ang)], [np.sin(ang), np.cos(ang)]], np.float32)
    pts = pts @ rot.T
    # Perspektive: Ecken unabhängig verschieben (bis ~18 % der Kartenbreite)
    pts += np.random.uniform(-0.18, 0.18, pts.shape).astype(np.float32) * cw * rng.uniform(0, 1)
    return pts + np.array([cx, cy], np.float32)


def paste_card(canvas: np.ndarray, card: np.ndarray, quad: np.ndarray, rng) -> None:
    h, w = canvas.shape[:2]
    ch, cw = card.shape[:2]
    # Abgerundete Ecken wie bei echten Karten
    mask = np.zeros((ch, cw), np.uint8)
    r = max(2, int(cw * 0.045))
    cv2.rectangle(mask, (r, 0), (cw - r, ch), 255, -1)
    cv2.rectangle(mask, (0, r), (cw, ch - r), 255, -1)
    for x, y in ((r, r), (cw - r, r), (r, ch - r), (cw - r, ch - r)):
        cv2.circle(mask, (x, y), r, 255, -1)
    src = np.float32([[0, 0], [cw, 0], [cw, ch], [0, ch]])
    m = cv2.getPerspectiveTransform(src, quad.astype(np.float32))
    warped = cv2.warpPerspective(card, m, (w, h), flags=cv2.INTER_LINEAR)
    wmask = cv2.warpPerspective(mask, m, (w, h), flags=cv2.INTER_LINEAR).astype(np.float32)[..., None] / 255
    if rng.random() < 0.5:  # Schlagschatten
        shadow = cv2.GaussianBlur(wmask, (0, 0), rng.uniform(4, 15))
        if shadow.ndim == 2:
            shadow = shadow[..., None]
        sh = np.roll(shadow, (rng.randint(2, 15), rng.randint(2, 15)), axis=(0, 1))
        canvas[:] = (canvas * (1 - 0.45 * sh)).astype(np.uint8)
    canvas[:] = (warped * wmask + canvas * (1 - wmask)).astype(np.uint8)
    if rng.random() < 0.35:  # Glanz (Hülle / Foil)
        glare = np.zeros((h, w), np.float32)
        c = quad.mean(axis=0) + np.random.uniform(-0.3, 0.3, 2) * np.ptp(quad, axis=0)
        cv2.ellipse(glare, (int(c[0]), int(c[1])), (int(rng.uniform(20, 120)), int(rng.uniform(10, 60))),
                    rng.uniform(0, 180), 0, 360, 1.0, -1)
        glare = cv2.GaussianBlur(glare, (0, 0), rng.uniform(8, 25)) * wmask[..., 0] * rng.uniform(0.3, 0.9)
        canvas[:] = np.clip(canvas + glare[..., None] * 255, 0, 255).astype(np.uint8)


def add_hand(canvas: np.ndarray, quad: np.ndarray, rng) -> None:
    """Hautfarbene Fläche, die eine Kartenkante teilweise verdeckt (Karte wird in der Hand gehalten)."""
    skin = np.array([rng.randint(60, 140), rng.randint(90, 170), rng.randint(140, 230)], np.float32)  # BGR
    edge = rng.randint(0, 3)
    p = quad[edge] + (quad[(edge + 1) % 4] - quad[edge]) * rng.uniform(0.2, 0.8)
    size = np.linalg.norm(quad[0] - quad[1]) * rng.uniform(0.15, 0.35)
    overlay = canvas.copy()
    cv2.ellipse(overlay, (int(p[0]), int(p[1])), (int(size), int(size * 1.8)), rng.uniform(0, 180), 0, 360, skin.tolist(), -1)
    canvas[:] = cv2.GaussianBlur(overlay, (5, 5), 0) if rng.random() < 0.5 else overlay


def add_distractor(canvas: np.ndarray, rng) -> None:
    h, w = canvas.shape[:2]
    ratio = rng.choice([0.46, 0.5, 1.0, 1.4, 0.3])  # Handy, Buch, Quadrat … (kein Kartenformat)
    hh = h * rng.uniform(0.15, 0.5)
    ww = hh * ratio
    c = (rng.uniform(0, w), rng.uniform(0, h))
    box = cv2.boxPoints(((c[0], c[1]), (ww, hh), rng.uniform(0, 180))).astype(np.int32)
    cv2.fillConvexPoly(canvas, box, [rng.randint(0, 255) for _ in range(3)])


def degrade(img: np.ndarray, rng) -> np.ndarray:
    if rng.random() < 0.4:  # Bewegungsunschärfe
        k = rng.randint(3, 15)
        kernel = np.zeros((k, k), np.float32)
        kernel[k // 2, :] = 1 / k
        m = cv2.getRotationMatrix2D((k / 2, k / 2), rng.uniform(0, 180), 1)
        kernel = cv2.warpAffine(kernel, m, (k, k))
        img = cv2.filter2D(img, -1, kernel / max(kernel.sum(), 1e-6))
    if rng.random() < 0.3:
        img = cv2.GaussianBlur(img, (0, 0), rng.uniform(0.5, 2.0))
    f = img.astype(np.float32)
    f = f * rng.uniform(0.45, 1.3) + rng.uniform(-30, 30)  # Helligkeit/Kontrast (dunkle Räume!)
    f *= np.array([rng.uniform(0.8, 1.2) for _ in range(3)], np.float32)  # Farbstich
    f += np.random.normal(0, rng.uniform(0, 10), f.shape)
    img = np.clip(f, 0, 255).astype(np.uint8)
    ok, enc = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, rng.randint(45, 95)])
    return cv2.imdecode(enc, cv2.IMREAD_COLOR)


def make_sample(cards: dict[str, list[str]], real_bgs: list[str], rng, size=(1280, 720)) -> tuple[np.ndarray, list[str]]:
    w, h = size
    canvas = random_background(w, h, rng, real_bgs)
    for _ in range(rng.randint(0, 2)):
        add_distractor(canvas, rng)
    table = rng.random() < 0.5
    n = rng.randint(3, 9) if table else rng.randint(1, 2)
    labels, placed = [], []
    for _ in range(n):
        game = rng.choice([g for g in cards if cards[g]])
        card = cv2.imread(rng.choice(cards[game]))
        if card is None:
            continue
        size_frac = rng.uniform(0.15, 0.35) if table else rng.uniform(0.25, 0.85)
        for _try in range(10):
            quad = card_quad(w, h, rng, size_frac)
            c = quad.mean(axis=0)
            # Nicht komplett übereinander, aber Überlappung zulassen
            if all(np.linalg.norm(c - q.mean(axis=0)) > 0.45 * np.linalg.norm(q[0] - q[2]) for q in placed):
                break
        paste_card(canvas, card, quad, rng)
        placed.append(quad)
        if rng.random() < (0.15 if table else 0.5):
            add_hand(canvas, quad, rng)
        labels.append((CLASSES[game], quad))
    img = degrade(canvas, rng)
    lines = []
    for cls, q in labels:
        qn = np.clip(q / np.array([w, h], np.float32), 0, 1)
        # Karten, die zu mehr als ~40 % außerhalb liegen, nicht labeln
        if cv2.contourArea(qn.astype(np.float32)) < 0.6 * cv2.contourArea((q / np.array([w, h], np.float32)).astype(np.float32)):
            continue
        lines.append(f"{cls} " + " ".join(f"{v:.6f}" for v in qn.ravel()))
    return img, lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=20000)
    ap.add_argument("--val", type=float, default=0.05)
    ap.add_argument("--out", default=os.path.join(DATA_DIR, "synth"))
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    np.random.seed(args.seed)
    cards = load_card_paths()
    print({g: len(v) for g, v in cards.items()})
    real_bgs = glob.glob(os.path.join(DATA_DIR, "backgrounds", "*.jpg"))
    for split in ("train", "val"):
        os.makedirs(os.path.join(args.out, "images", split), exist_ok=True)
        os.makedirs(os.path.join(args.out, "labels", split), exist_ok=True)
    for i in range(args.count):
        split = "val" if i < args.count * args.val else "train"
        img, lines = make_sample(cards, real_bgs, rng)
        name = f"s{i:06d}"
        cv2.imwrite(os.path.join(args.out, "images", split, name + ".jpg"), img, [cv2.IMWRITE_JPEG_QUALITY, 92])
        with open(os.path.join(args.out, "labels", split, name + ".txt"), "w") as f:
            f.write("\n".join(lines))
        if (i + 1) % 500 == 0:
            print(f"{i + 1}/{args.count}", flush=True)
    with open(os.path.join(args.out, "data.yaml"), "w", encoding="utf-8") as f:
        f.write(f"path: {os.path.abspath(args.out)}\ntrain: images/train\nval: images/val\nnames:\n  0: mtg\n  1: pokemon\n")


if __name__ == "__main__":
    main()
