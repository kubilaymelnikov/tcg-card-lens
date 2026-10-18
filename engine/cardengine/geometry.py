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


def rectify(frame: np.ndarray, quad: np.ndarray, rotate180: bool = False, margin: float = 0.0, turns: int = 0) -> np.ndarray:
    """Karte perspektivisch entzerren (RECT_W × RECT_H, optional mit Rand in Kartenanteilen).

    `turns` dreht die Eckenzuordnung in Vierteldrehungen (1 = 90°); `rotate180` entspricht `turns=2`.
    """
    q = np.asarray(quad, dtype=np.float32)
    k = (int(turns) + (2 if rotate180 else 0)) % 4
    if k:
        q = np.roll(q, k, axis=0)
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


def _signed_area(p: np.ndarray) -> float:
    x, y = p[:, 0], p[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def _convex_ccw(q: np.ndarray) -> np.ndarray:
    """Konvexe Hülle, gegen den Uhrzeigersinn (mathematisch) – auch bei leicht verzerrten/vertauschten Ecken."""
    hull = cv2.convexHull(np.asarray(q, np.float32).reshape(-1, 1, 2)).reshape(-1, 2).astype(np.float64)
    return hull[::-1] if _signed_area(hull) < 0 else hull


def _clip(subject: np.ndarray, clip: np.ndarray) -> np.ndarray:
    """Sutherland-Hodgman: Schnitt des Polygons `subject` mit dem konvexen Polygon `clip` (beide gegen den
    Uhrzeigersinn)."""
    out = subject
    n = len(clip)
    for i in range(n):
        if len(out) == 0:
            break
        a, b = clip[i], clip[(i + 1) % n]
        e = b - a
        # > 0: links der Kante = innen (gegen den Uhrzeigersinn)
        side = e[0] * (out[:, 1] - a[1]) - e[1] * (out[:, 0] - a[0])
        res = []
        m = len(out)
        for j in range(m):
            p, q = out[j], out[(j + 1) % m]
            sp, sq = side[j], side[(j + 1) % m]
            if sp >= 0:
                res.append(p)
            if (sp >= 0) != (sq >= 0):
                t = sp / (sp - sq)
                res.append(p + t * (q - p))
        out = np.array(res) if res else np.zeros((0, 2))
    return out


def iou_quads(a: np.ndarray, b: np.ndarray) -> float:
    """IoU zweier (konvexer) Vierecke.

    Eigener Polygon-Schnitt statt cv2.intersectConvexConvex: Das liefert bei fast deckungsgleichen Vierecken
    (Abweichungen im Bereich 1e-4 px – im Tracking alltäglich) gelegentlich eine viel zu kleine Schnittfläche.
    """
    a = np.asarray(a, np.float32)
    b = np.asarray(b, np.float32)
    # Schnelltest: umschließende Rechtecke ohne Überlappung → 0 (der häufigste Fall bei vielen Karten)
    if (a[:, 0].max() <= b[:, 0].min() or b[:, 0].max() <= a[:, 0].min()
            or a[:, 1].max() <= b[:, 1].min() or b[:, 1].max() <= a[:, 1].min()):
        return 0.0
    pa, pb = _convex_ccw(a), _convex_ccw(b)
    area_a, area_b = _signed_area(pa), _signed_area(pb)
    if area_a <= 0 or area_b <= 0:
        return 0.0
    inter_poly = _clip(pa, pb)
    inter = _signed_area(inter_poly) if len(inter_poly) >= 3 else 0.0
    inter = min(max(inter, 0.0), area_a, area_b)
    union = area_a + area_b - inter
    return float(inter / union) if union > 0 else 0.0
