// Liest Kartenname (oben) und Fußzeile (Set/Nummer) einer entzerrten Karte per Tesseract.

const REGIONS = {
  // Anteile der Kartenfläche: [x, y, w, h]
  // Namenszeile als drei schmale, überlappende Bänder: Ihre Lage schwankt je nach Kartenlayout und
  // je nachdem, wie knapp der erkannte Umriss ist (das obere Band reicht in den Entzerrungsrand).
  // Ein hohes Band mit Bildrahmen/HP-Symbol darin liest Tesseract deutlich schlechter.
  nameTop: [0.05, -0.015, 0.75, 0.07],
  nameMid: [0.05, 0.01, 0.75, 0.07],
  nameLow: [0.05, 0.035, 0.75, 0.07],
  // Sammlernummer: unten links (Magic ab 2015, Pokémon ab Schwert & Schild) …
  bottomLeft: [0.02, 0.905, 0.5, 0.09],
  // … oder unten rechts (ältere Pokémon-Karten)
  bottomRight: [0.55, 0.905, 0.43, 0.09],
};
const HAS_NUMBER = /\d{1,3}\s?\/\s?[A-Z]{0,3}\d{2,3}|\b[A-Z0-9]{3,5}[\s•·*.+\-«»<>]{1,4}[A-Z]{2}\b/;
const PAD = 16; // weißer Rand: ohne ihn liefert Tesseract bei dunklen Kartenrahmen oft gar nichts

/** Schwellwert nach Otsu aus einem Graustufen-Histogramm. */
function otsu(gray) {
  const hist = new Array(256).fill(0);
  for (const v of gray) hist[v | 0]++;
  let sumAll = 0;
  for (let i = 0; i < 256; i++) sumAll += i * hist[i];
  let wB = 0, sumB = 0, best = 0, threshold = 128;
  for (let t = 0; t < 256; t++) {
    wB += hist[t];
    if (!wB) continue;
    const wF = gray.length - wB;
    if (!wF) break;
    sumB += t * hist[t];
    const between = wB * wF * (sumB / wB - (sumAll - sumB) / wF) ** 2;
    if (between > best) (best = between), (threshold = t);
  }
  return threshold;
}

/** Helligkeitswerte an zwei Perzentilen – für eine robuste Kontrastspreizung. */
function percentiles(gray, pLo, pHi) {
  const hist = new Array(256).fill(0);
  for (const v of gray) hist[v | 0]++;
  const find = (p) => {
    let acc = 0;
    for (let i = 0; i < 256; i++) if ((acc += hist[i]) >= p * gray.length) return i;
    return 255;
  };
  return [find(pLo), find(pHi)];
}

export class CardReader {
  async init() {
    if (!window.Tesseract) {
      await new Promise((resolve, reject) => {
        const s = document.createElement('script');
        s.src = '/vendor/tesseract/tesseract.min.js';
        s.onload = resolve;
        s.onerror = () => reject(new Error('Tesseract konnte nicht geladen werden'));
        document.head.appendChild(s);
      });
    }
    this.worker = await window.Tesseract.createWorker(['eng', 'deu'], 1, {
      workerPath: '/vendor/tesseract/worker.min.js',
      corePath: '/vendor/tesseract-core/',
      langPath: '/vendor/tessdata',
    });
  }

  /** @param {'gray'|'otsu'} mode */
  crop(card, region, { mode = 'gray', invert = false, scale = 2 } = {}) {
    const [rx, ry, rw, rh] = REGIONS[region];
    const r = card.card ?? { x: 0, y: 0, w: card.width, h: card.height }; // Kartenfläche ohne Entzerrungsrand
    const sx = r.x + rx * r.w, sy = r.y + ry * r.h, sw = rw * r.w, sh = rh * r.h;
    const w = Math.round(sw * scale);
    const h = Math.round(sh * scale);
    const c = document.createElement('canvas');
    c.width = w + PAD * 2;
    c.height = h + PAD * 2;
    const ctx = c.getContext('2d', { willReadFrequently: true });
    ctx.drawImage(card, sx, sy, sw, sh, PAD, PAD, w, h);
    const img = ctx.getImageData(PAD, PAD, w, h);
    const d = img.data;
    const gray = new Float32Array(d.length / 4);
    for (let i = 0; i < gray.length; i++) gray[i] = 0.299 * d[i * 4] + 0.587 * d[i * 4 + 1] + 0.114 * d[i * 4 + 2];
    const th = mode === 'otsu' ? otsu(gray) : 0;
    const [lo, hi] = percentiles(gray, 0.01, 0.99);
    const span = Math.max(1, hi - lo);
    for (let i = 0; i < gray.length; i++) {
      let v = mode === 'otsu' ? (gray[i] > th ? 255 : 0) : Math.min(255, Math.max(0, ((gray[i] - lo) / span) * 255));
      if (invert) v = 255 - v;
      d[i * 4] = d[i * 4 + 1] = d[i * 4 + 2] = v;
    }
    ctx.fillStyle = '#fff';
    ctx.fillRect(0, 0, c.width, c.height);
    ctx.putImageData(img, PAD, PAD);
    return c;
  }

  async ocr(canvas, psm) {
    await this.worker.setParameters({ tessedit_pageseg_mode: String(psm) });
    const { data } = await this.worker.recognize(canvas);
    return { text: data.text.trim(), confidence: data.text.trim() ? data.confidence : 0 };
  }

  /** @returns {{nameText: string, nameTexts: string[], bottomText: string, nameConfidence: number}} */
  async read(card, { invert = false } = {}) {
    const name = await this.readName(card, { invert });
    const bottomText = await this.readBottom(card, { invert });
    return { nameText: name.text, nameTexts: name.texts, bottomText, nameConfidence: name.confidence };
  }

  /**
   * Alle Namensbänder lesen. Welche Lesung stimmt, entscheidet der Server anhand der Kartendatenbank
   * (`texts`); `text`/`confidence` ist die laut Tesseract sicherste Lesung (für die Ausrichtungswahl).
   */
  async readName(card, { invert = false } = {}) {
    let best = { text: '', confidence: 0 };
    const texts = [];
    for (const band of ['nameTop', 'nameMid', 'nameLow']) {
      const r = await this.ocr(this.crop(card, band, { invert, scale: 2.5 }), 7);
      if (r.text) texts.push(r.text);
      if (r.confidence > best.confidence) best = r;
      if (r.confidence >= 85 && r.text.replace(/W/g, '').length >= 4) break;
    }
    return { ...best, texts };
  }

  async readBottom(card, { invert = false } = {}) {
    const tries = [
      ['bottomLeft', 'gray'],
      ['bottomLeft', 'otsu'],
      ['bottomRight', 'gray'],
    ];
    const texts = [];
    for (const [region, mode] of tries) {
      const { text } = await this.ocr(this.crop(card, region, { mode, invert, scale: 3 }), 6);
      texts.push(text);
      if (HAS_NUMBER.test(text)) break;
    }
    return texts.join('\n');
  }
}
