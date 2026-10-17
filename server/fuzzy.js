// Unscharfe Namenssuche für OCR-Text.
// Trigramm-Index zur Kandidatenauswahl, danach semi-globale Edit-Distanz:
// der Kartenname darf irgendwo im OCR-Text stehen (z. B. "BASIC Mew ex HP180").

export function normalize(s) {
  return s
    .toLowerCase()
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
    .replace(/[^a-z0-9 ]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

function trigrams(s) {
  const t = ` ${s} `;
  const out = new Set();
  for (let i = 0; i < t.length - 2; i++) out.add(t.slice(i, i + 3));
  return out;
}

// Edit-Distanz von `needle`, wenn es an beliebiger Stelle in `hay` ausgerichtet wird
// (Präfix/Suffix von `hay` kosten nichts).
export function semiGlobalDistance(needle, hay) {
  const n = needle.length, m = hay.length;
  let prev = new Array(m + 1).fill(0);
  let cur = new Array(m + 1);
  for (let i = 1; i <= n; i++) {
    cur[0] = i;
    for (let j = 1; j <= m; j++) {
      const cost = needle[i - 1] === hay[j - 1] ? 0 : 1;
      cur[j] = Math.min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost);
    }
    [prev, cur] = [cur, prev];
  }
  return Math.min(...prev);
}

export class NameIndex {
  /** @param {Array<{name: string, key: string}>} entries */
  constructor(entries) {
    this.entries = [];
    this.grams = new Map();
    for (const e of entries) {
      const norm = normalize(e.name);
      if (norm.length < 3) continue;
      const idx = this.entries.push({ ...e, norm }) - 1;
      for (const g of trigrams(norm)) {
        let list = this.grams.get(g);
        if (!list) this.grams.set(g, (list = []));
        list.push(idx);
      }
    }
  }

  get size() {
    return this.entries.length;
  }

  /** Liefert die besten Treffer, sortiert nach Score (0..1). */
  search(text, limit = 5) {
    const q = normalize(text);
    if (q.length < 3) return [];
    const counts = new Map();
    for (const g of trigrams(q)) {
      const list = this.grams.get(g);
      if (!list || list.length > 4000) continue; // sehr häufige Trigramme ignorieren
      for (const idx of list) counts.set(idx, (counts.get(idx) || 0) + 1);
    }
    const candidates = [...counts.entries()]
      .sort((a, b) => b[1] - a[1])
      .slice(0, 200)
      .map(([idx]) => this.entries[idx]);

    const scored = candidates.map((e) => {
      const dist = semiGlobalDistance(e.norm, q);
      const score = 1 - dist / e.norm.length;
      // Längere Namen bevorzugen, damit "Mew ex" vor "Mew" landet.
      // Eigenständige Karte vor gleichnamiger Seite einer Doppelkarte.
      const exact = e.key === e.name || e.key.endsWith(`|${e.name}`) ? 0.001 : 0;
      return { ...e, score, rank: score + Math.min(e.norm.length, 30) * 0.002 + exact };
    });
    scored.sort((a, b) => b.rank - a.rank);
    const seen = new Set();
    return scored
      .filter((e) => !seen.has(e.key) && seen.add(e.key))
      .slice(0, limit)
      .map(({ name, key, score }) => ({ name, key, score }));
  }
}
