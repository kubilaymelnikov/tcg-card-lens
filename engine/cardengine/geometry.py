"""Geometrie für Kartenvierecke: Ecken ordnen, entzerren, Kennzahlen."""
from __future__ import annotations

import cv2
import numpy as np

# Standard-Kartenformat 63 × 88 mm (Magic und Pokémon identisch)
CARD_RATIO = 63 / 88
RECT_W, RECT_H = 488, 680  # entzerrte Karte (entspricht Scryfall "normal")


def order_corners(pts: np.ndarray) -> np.ndarray:
    """Ecken im Uhrzeigersinn ab oben links ordnen; erste Kante ist eine kurze Kante (Hochformat).

    Welche der beiden kurzen Kanten "oben" ist, bleibt offen (180°-Mehrdeutigkeit) –
    das entscheidet erst die Identifikation.
    """
    pts = np.asarray(pts, dtype=np.float32).reshape(4, 2)
    c = pts.mean(axis=0)
    ang = np.arctan2(pts[:, 1] - c[1], pts[:, 0] - c[0])
    pts = pts[np.argsort(ang)]
    start = int(np.argmin(pts.sum(axis=1)))
    pts = np.roll(pts, -start, axis=0)
    if np.linalg.norm(pts[0] - pts[1]) > np.linalg.norm(pts[1] - pts[2]):
        pts = np.roll(pts, -1, axis=0)
    return pts


def side_lengths(q: np.ndarray) -> tuple[float, float]:
    """(mittlere kurze Seite, mittlere lange Seite)."""
    short = (np.linalg.norm(q[0] - q[1]) + np.linalg.norm(q[2] - q[3])) / 2
    long = (np.linalg.norm(q[1] - q[2]) + np.linalg.norm(q[3] - q[0])) / 2
    return float(short), float(long)


def quad_area(q: np.ndarray) -> float:
    return float(cv2.contourArea(np.asarray(q, dtype=np.float32)))


def rectify(frame: np.ndarray, quad: np.ndarray, rotate180: bool = False, margin: float = 0.0) -> np.ndarray:
    """Karte perspektivisch entzerren (RECT_W × RECT_H, optional mit Rand in Kartenanteilen)."""
    q = np.asarray(quad, dtype=np.float32)
    if rotate180:
        q = np.roll(q, 2, axis=0)
    mx, my = RECT_W * margin, RECT_H * margin
    dst = np.float32([[mx, my], [mx + RECT_W, my], [mx + RECT_W, my + RECT_H], [mx, my + RECT_H]])
    m = cv2.getPerspectiveTransform(q, dst)
    size = (int(round(RECT_W + 2 * mx)), int(round(RECT_H + 2 * my)))
    return cv2.warpPerspective(frame, m, size, flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def refine_quad_edges(gray: np.ndarray, quad: np.ndarray, search: float = 0.04, samples: int = 24) -> np.ndarray | None:
    """Ecken subpixelgenau an die echten Kartenkanten anpassen (perspektivische Feinjustierung).

    Für jede Seite werden entlang der Normalen die Punkte mit dem stärksten Helligkeitsgradienten gesucht
    (±`search` × kurze Kartenseite), eine Gerade robust hineingelegt (Huber) und die Ecken als Schnittpunkte
    benachbarter Geraden berechnet. Liefert None, wenn das Ergebnis unplausibel ist (dann alte Ecken behalten).
    """
    q = np.asarray(quad, np.float32)
    short, _ = side_lengths(q)
    r = max(3.0, short * search)
    g = cv2.GaussianBlur(gray, (5, 5), 0).astype(np.float32)
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.magnitude(gx, gy)
    h, w = gray.shape[:2]
    lines = []
    for i in range(4):
        p0, p1 = q[i], q[(i + 1) % 4]
        d = p1 - p0
        length = float(np.linalg.norm(d))
        if length < 10:
            return None
        d /= length
        n = np.array([-d[1], d[0]], np.float32)
        if np.dot(n, (p0 + p1) / 2 - q.mean(axis=0)) < 0:
            n = -n  # Normale zeigt nach außen
        # Profil von außen (+r) nach innen (-r): die äußere Kartenkante ist die *erste* deutliche Kante.
        # Die stärkste Kante wäre oft die innere Rahmenkante (dunkler Kartenrand → helles Bildfeld).
        offs = np.linspace(r, -r, int(2 * r) + 1).astype(np.float32)
        pts = []
        for t in np.linspace(0.12, 0.88, samples):
            c = p0 + d * (t * length)
            prof_pts = c[None, :] + offs[:, None] * n[None, :]
            xs = np.clip(prof_pts[:, 0], 0, w - 1).astype(np.float32)
            ys = np.clip(prof_pts[:, 1], 0, h - 1).astype(np.float32)
            prof = cv2.remap(mag, xs.reshape(1, -1), ys.reshape(1, -1), cv2.INTER_LINEAR).ravel()
            peak = float(prof.max())
            if peak < 8.0:  # keine klare Kante (Finger, Glanz)
                continue
            k = next((j for j in range(1, len(prof) - 1)
                      if prof[j] >= 0.25 * peak and prof[j] >= prof[j - 1] and prof[j] >= prof[j + 1]),
                     int(np.argmax(prof)))
            # Subpixel: Parabel durch das Maximum und seine Nachbarn
            delta = 0.0
            if 0 < k < len(prof) - 1:
                a, b, cc = prof[k - 1], prof[k], prof[k + 1]
                den = a - 2 * b + cc
                if abs(den) > 1e-6:
                    delta = float(np.clip(0.5 * (a - cc) / den, -0.5, 0.5))
            pts.append(c + n * (offs[k] + delta * (offs[1] - offs[0])))
        if len(pts) < samples // 3:
            return None
        vx, vy, x0, y0 = cv2.fitLine(np.array(pts, np.float32), cv2.DIST_HUBER, 0, 0.01, 0.01).ravel()
        lines.append((np.array([x0, y0]), np.array([vx, vy])))
    out = []
    for i in range(4):
        (pa, da), (pb, db) = lines[i - 1], lines[i]
        m = np.array([[da[0], -db[0]], [da[1], -db[1]]])
        if abs(np.linalg.det(m)) < 1e-6:
            return None
        s = np.linalg.solve(m, pb - pa)
        out.append(pa + da * s[0])
    out = np.array(out, np.float32)
    # Ecke i ist der Schnitt von Seite i-1 und Seite i → entspricht q[i]
    if np.linalg.norm(out - q, axis=1).max() > short * 0.05:
        return None
    return out


def iou_quads(a: np.ndarray, b: np.ndarray) -> float:
    """IoU zweier konvexer Vierecke."""
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    inter, _ = cv2.intersectConvexConvex(a, b)
    union = quad_area(a) + quad_area(b) - inter
    return float(inter / union) if union > 0 else 0.0
