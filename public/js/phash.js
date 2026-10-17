// Perceptual Hash (dHash) für den Bildabgleich entzerrter Karten mit den Katalogbildern.
// Die reinen Funktionen (ohne DOM) werden auch serverseitig in den Tests genutzt.

/** Version des Verfahrens – ändert sich die Berechnung, werden alte Hashes im Cache ignoriert. */
export const HASH_ALGO = 'dhash-v1';

// Bereiche relativ zur Karte (x, y, Breite, Höhe). Das Artwork trägt den Großteil, die ganze Karte
// (Rahmen, Textbox, Vollbild-Gestaltung) unterscheidet Drucke mit gleichem Bild.
const REGIONS = [
  { rect: [0.1, 0.12, 0.8, 0.36], size: 16 },
  { rect: [0.04, 0.03, 0.92, 0.94], size: 8 },
];

/**
 * dHash einer Graustufen-Matrix (Zeilen zuerst) der Größe (size + 1) × (size + 1):
 * je size × size Bits für horizontale und vertikale Helligkeitsänderung.
 */
export function dhashBits(gray, size) {
  const n = size + 1;
  const bits = [];
  for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) bits.push(gray[y * n + x] < gray[y * n + x + 1] ? 1 : 0);
  for (let y = 0; y < size; y++) for (let x = 0; x < size; x++) bits.push(gray[y * n + x] < gray[(y + 1) * n + x] ? 1 : 0);
  return bits;
}

export function bitsToHex(bits) {
  let hex = '';
  for (let i = 0; i < bits.length; i += 4) hex += ((bits[i] << 3) | (bits[i + 1] << 2) | (bits[i + 2] << 1) | (bits[i + 3] | 0)).toString(16);
  return hex;
}

const POP = Array.from({ length: 16 }, (_, i) => (i & 1) + ((i >> 1) & 1) + ((i >> 2) & 1) + ((i >> 3) & 1));

/** Anteil unterschiedlicher Bits (0 = identisch, ~0,5 = unabhängige Bilder). */
export function hashDistance(a, b) {
  if (!a || !b || a.length !== b.length) return 1;
  let d = 0;
  for (let i = 0; i < a.length; i++) d += POP[parseInt(a[i], 16) ^ parseInt(b[i], 16)];
  return d / (a.length * 4);
}

/**
 * Bester Kandidat, wenn er deutlich unter der Schwelle liegt und sich klar vom nächsten Bild abhebt.
 * Kandidaten mit (nahezu) gleichem Bild – Nachdrucke mit identischem Artwork – sind nicht unterscheidbar.
 */
export function bestMatch(target, candidates, { maxDistance = 0.3, minGap = 0.05 } = {}) {
  const ranked = candidates
    .filter((c) => c.hash)
    .map((c) => ({ ...c, distance: hashDistance(target, c.hash) }))
    .sort((a, b) => a.distance - b.distance);
  const [best, second] = ranked;
  if (!best || best.distance > maxDistance) return { match: null, ranked };
  if (second && second.distance - best.distance < minGap) return { match: null, ranked, ambiguous: true };
  return { match: best, ranked };
}

/** Hash eines Kartenbildes (Canvas, Bild) – beliebige Größe, wird auf das Kartenformat gestreckt. */
export function hashImage(source, width = source.naturalWidth || source.width, height = source.naturalHeight || source.height) {
  const bits = [];
  for (const { rect, size } of REGIONS) {
    const [rx, ry, rw, rh] = rect;
    bits.push(...dhashBits(sampleGray(source, rx * width, ry * height, rw * width, rh * height, size + 1), size));
  }
  return bitsToHex(bits);
}

let scratch = null;
/** Bereich auf n × n Pixel verkleinern (über eine Zwischenstufe, damit nicht nur einzelne Pixel zählen). */
function sampleGray(source, sx, sy, sw, sh, n) {
  const step = 4;
  const m = n * step;
  if (!scratch) scratch = document.createElement('canvas');
  scratch.width = m;
  scratch.height = m;
  const ctx = scratch.getContext('2d', { willReadFrequently: true });
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = 'high';
  ctx.drawImage(source, sx, sy, sw, sh, 0, 0, m, m);
  const px = ctx.getImageData(0, 0, m, m).data;
  const gray = new Float32Array(n * n);
  for (let y = 0; y < m; y++)
    for (let x = 0; x < m; x++) {
      const i = (y * m + x) * 4;
      gray[Math.floor(y / step) * n + Math.floor(x / step)] += 0.299 * px[i] + 0.587 * px[i + 1] + 0.114 * px[i + 2];
    }
  return gray;
}
