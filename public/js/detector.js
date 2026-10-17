// Findet Karten (Rechtecke im Seitenverhältnis 63 × 88 mm) per Kantenerkennung und entzerrt sie.

export async function loadOpenCv() {
  if (!window.cv) {
    await new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = '/vendor/opencv/opencv.js';
      s.async = true;
      s.onload = resolve;
      s.onerror = () => reject(new Error('OpenCV konnte nicht geladen werden'));
      document.head.appendChild(s);
    });
  }
  let cv = window.cv;
  if (cv instanceof Promise) cv = await cv;
  if (!cv.Mat) {
    // Der Hook kann schon gefeuert haben, bevor wir ihn setzen -> zusätzlich pollen.
    await new Promise((resolve) => {
      cv.onRuntimeInitialized = resolve;
      const poll = setInterval(() => window.cv?.Mat && (clearInterval(poll), resolve()), 50);
    });
    cv = window.cv.Mat ? window.cv : cv;
  }
  // Das Emscripten-Modul hat eine eigene then()-Methode, die sich selbst zurückgibt. Aus einer
  // async-Funktion zurückgegeben würde es endlos aufgelöst und die Seite einfrieren -> verpacken.
  return { cv };
}

export const CARD_W = 630;
export const CARD_H = 880;
// Rand um die entzerrte Karte: Der erkannte Umriss liegt oft etwas innerhalb der echten Kante
// (silberne Ränder, Hüllen, Schatten) – ohne Rand würde die Namenszeile oben abgeschnitten.
const MARGIN = 0.04;
const MX = Math.round(CARD_W * MARGIN);
const MY = Math.round(CARD_H * MARGIN);

/** Nur die Kartenfläche (ohne Rand) einer entzerrten Karte als 630 × 880-Canvas. */
export function cardOnly(warped) {
  const r = warped.card;
  if (!r) return warped;
  const c = document.createElement('canvas');
  c.width = CARD_W;
  c.height = CARD_H;
  c.getContext('2d').drawImage(warped, r.x, r.y, r.w, r.h, 0, 0, CARD_W, CARD_H);
  return c;
}
const CARD_RATIO = 63 / 88;

const dist = (a, b) => Math.hypot(a[0] - b[0], a[1] - b[1]);

function polygonArea(p) {
  let s = 0;
  for (let i = 0; i < p.length; i++) {
    const [x1, y1] = p[i];
    const [x2, y2] = p[(i + 1) % p.length];
    s += x1 * y2 - x2 * y1;
  }
  return Math.abs(s) / 2;
}

export function pointInPolygon([x, y], poly) {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i];
    const [xj, yj] = poly[j];
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

export const centerOf = (q) => [(q[0][0] + q[1][0] + q[2][0] + q[3][0]) / 4, (q[0][1] + q[1][1] + q[2][1] + q[3][1]) / 4];

/** Ecken im Uhrzeigersinn ordnen, beginnend so, dass die erste Kante eine kurze Kante ist (Hochformat). */
function orderQuad(pts) {
  const [cx, cy] = centerOf(pts);
  const sorted = [...pts].sort((a, b) => Math.atan2(a[1] - cy, a[0] - cx) - Math.atan2(b[1] - cy, b[0] - cx));
  // Start bei der Ecke oben links (kleinste x+y)
  let start = 0;
  sorted.forEach((p, i) => {
    if (p[0] + p[1] < sorted[start][0] + sorted[start][1]) start = i;
  });
  let q = [...sorted.slice(start), ...sorted.slice(0, start)];
  if (dist(q[0], q[1]) > dist(q[1], q[2])) q = [q[1], q[2], q[3], q[0]]; // Querformat -> drehen
  return q;
}

export class CardDetector {
  constructor(cv, { maxSide = 960, canny = [30, 100] } = {}) {
    this.cv = cv;
    this.maxSide = maxSide;
    this.canny = canny;
    this.small = document.createElement('canvas');
    this.small.getContext('2d', { willReadFrequently: true });
    this.full = document.createElement('canvas');
    this.full.getContext('2d', { willReadFrequently: true });
  }

  /** @returns {Array<{quad: number[][], area: number}>} Koordinaten im Quellbild */
  detect(source, srcW, srcH) {
    const cv = this.cv;
    const scale = Math.min(1, this.maxSide / Math.max(srcW, srcH));
    const w = Math.round(srcW * scale);
    const h = Math.round(srcH * scale);
    this.small.width = w;
    this.small.height = h;
    this.small.getContext('2d').drawImage(source, 0, 0, w, h);

    const mats = [];
    const keep = (m) => (mats.push(m), m);
    const found = [];
    try {
      const src = keep(cv.imread(this.small));
      const gray = keep(new cv.Mat());
      cv.cvtColor(src, gray, cv.COLOR_RGBA2GRAY);
      cv.GaussianBlur(gray, gray, new cv.Size(5, 5), 0);
      const edges = keep(new cv.Mat());
      cv.Canny(gray, edges, this.canny[0], this.canny[1]);
      const kernel = keep(cv.getStructuringElement(cv.MORPH_RECT, new cv.Size(3, 3)));
      cv.dilate(edges, edges, kernel, new cv.Point(-1, -1), 2);
      this.collect(edges, w, h, scale, found);

      // Zweiter Versuch, wenn die Kantensuche nichts findet (weiche Kanten, Unschärfe, Hand am Rand):
      // Karte als helle bzw. dunkle Fläche gegen den Hintergrund trennen (Otsu, beide Polaritäten).
      if (!found.length) {
        const bin = keep(new cv.Mat());
        cv.threshold(gray, bin, 0, 255, cv.THRESH_BINARY + cv.THRESH_OTSU);
        cv.morphologyEx(bin, bin, cv.MORPH_OPEN, kernel);
        this.collect(bin, w, h, scale, found);
        cv.bitwise_not(bin, bin);
        this.collect(bin, w, h, scale, found);
      }
    } finally {
      mats.forEach((m) => m.delete());
    }

    // Doppelte Umrisse und innere Rahmen (Bildfenster, Textbox) verwerfen: größte Kontur gewinnt.
    found.sort((a, b) => b.area - a.area);
    const result = [];
    for (const f of found) {
      if (!result.some((r) => pointInPolygon(centerOf(f.quad), r.quad))) result.push(f);
    }
    return result;
  }

  /** Konturen eines Binärbilds prüfen und kartenförmige Vierecke sammeln. */
  collect(binary, w, h, scale, found) {
    const cv = this.cv;
    const contours = new cv.MatVector();
    const hierarchy = new cv.Mat();
    try {
      cv.findContours(binary, contours, hierarchy, cv.RETR_LIST, cv.CHAIN_APPROX_SIMPLE);
      const minArea = w * h * 0.003;
      const maxArea = w * h * 0.9;
      for (let i = 0; i < contours.size(); i++) {
        const cnt = contours.get(i);
        const hull = new cv.Mat();
        const approx = new cv.Mat();
        try {
          cv.convexHull(cnt, hull, false, true);
          const area = cv.contourArea(hull);
          if (area < minArea || area > maxArea) continue;
          cv.approxPolyDP(hull, approx, 0.02 * cv.arcLength(hull, true), true);
          let pts;
          if (approx.rows === 4) {
            const d = approx.data32S;
            pts = [[d[0], d[1]], [d[2], d[3]], [d[4], d[5]], [d[6], d[7]]];
          } else {
            // Abgerundete Ecken / Finger am Rand: kleinstes umschließendes Rechteck
            const rect = cv.minAreaRect(hull);
            pts = cv.RotatedRect.points(rect).map((p) => [p.x, p.y]);
          }
          const q = orderQuad(pts);
          const short = (dist(q[0], q[1]) + dist(q[2], q[3])) / 2;
          const long = (dist(q[1], q[2]) + dist(q[3], q[0])) / 2;
          const ratio = short / long;
          if (ratio < CARD_RATIO * 0.86 || ratio > CARD_RATIO * 1.14) continue;
          const quadArea = polygonArea(q);
          if (area / quadArea < 0.85) continue; // Kontur füllt das Rechteck nicht -> kein Kartenumriss
          found.push({ quad: q.map(([x, y]) => [x / scale, y / scale]), area: quadArea / scale / scale });
        } finally {
          cnt.delete();
          hull.delete();
          approx.delete();
        }
      }
    } finally {
      contours.delete();
      hierarchy.delete();
    }
  }

  /** Entzerrt eine Karte aus der vollen Auflösung: 630 × 880 plus Rand, Kartenfläche steht in `canvas.card`. */
  warp(source, srcW, srcH, quad, rotate180 = false) {
    const cv = this.cv;
    this.full.width = srcW;
    this.full.height = srcH;
    this.full.getContext('2d').drawImage(source, 0, 0, srcW, srcH);
    const q = rotate180 ? [quad[2], quad[3], quad[0], quad[1]] : quad;
    const src = cv.imread(this.full);
    const dst = new cv.Mat();
    const from = cv.matFromArray(4, 1, cv.CV_32FC2, q.flat());
    const to = cv.matFromArray(4, 1, cv.CV_32FC2, [MX, MY, MX + CARD_W, MY, MX + CARD_W, MY + CARD_H, MX, MY + CARD_H]);
    const m = cv.getPerspectiveTransform(from, to);
    try {
      cv.warpPerspective(src, dst, m, new cv.Size(CARD_W + 2 * MX, CARD_H + 2 * MY), cv.INTER_CUBIC, cv.BORDER_REPLICATE, new cv.Scalar());
      const out = document.createElement('canvas');
      cv.imshow(out, dst);
      out.card = { x: MX, y: MY, w: CARD_W, h: CARD_H };
      return out;
    } finally {
      [src, dst, from, to, m].forEach((x) => x.delete());
    }
  }
}
