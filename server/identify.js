import * as mtg from './mtg.js';
import * as pokemon from './pokemon.js';
import { getImageHash, saveImageHash } from './db.js';

const MTG_LANGS = 'EN|DE|FR|IT|ES|JA|JP|PT|RU|KO|ZH|PH';

/** Liest Set-Code und Sammlernummer aus dem OCR-Text der unteren Kartenzeile. */
export function parseBottom(text = '') {
  const t = text.replace(/[|]/g, '/').replace(/\s+/g, ' ');
  const out = { pokemon: {}, mtg: {} };

  // Pokémon: "151/165", "TG05/TG30", "045/198"
  const pk = t.match(/\b([A-Z]{0,3}\d{1,3})\s?\/\s?([A-Z]{0,3}\d{2,3})\b/);
  if (pk) out.pokemon = { number: pk[1], total: /^\d+$/.test(pk[2]) ? pk[2] : null };

  // Magic (ab 2015/2023): "0123 R" / "123/280 R" und darunter "DMU • EN"
  const set = t.match(new RegExp(`\\b([A-Z0-9]{3,5})[\\s•·*.+\\-«»<>]{1,4}(?:${MTG_LANGS})\\b`));
  if (set && !/^\d+$/.test(set[1])) out.mtg.setCode = set[1];
  // "0123 R", "123/280 R" oder (Fußzeile ab 2024) "U 0163"
  const nr =
    t.match(/\b(\d{1,4})(?:\s?\/\s?\d{1,4})?\s?[CURMSLTP]\b/) ||
    t.match(/\b[CURMSLTP]\s(\d{3,4})\b/) ||
    t.match(/\b(\d{1,4})\s?\/\s?\d{1,4}\b/);
  if (nr) out.mtg.number = String(Number(nr[1]));
  return out;
}

const MIN_SCORE = 0.72;

function acceptable(m, confidence) {
  const len = m.name.replace(/[^\p{L}\p{N}]/gu, '').length;
  // Unsichere OCR-Lesung (Zeichensalat) -> nur sehr gute Namenstreffer gelten lassen
  const min = confidence != null && confidence < 55 ? 0.88 : MIN_SCORE;
  return m.score >= (len <= 4 ? 0.99 : min);
}

export async function identify({ nameText = '', nameTexts = [], bottomText = '', game = 'auto', nameConfidence = null }) {
  const meta = parseBottom(bottomText);
  // Mehrere Lesungen der Namenszeile (verschiedene Bänder): die Kartendatenbank entscheidet, welche passt.
  const texts = [...new Set([nameText, ...(Array.isArray(nameTexts) ? nameTexts : [])])]
    .filter((t) => typeof t === 'string' && t.trim())
    .slice(0, 8);
  const byKey = new Map();
  const add = (m, g) => {
    const id = `${g}|${m.key}`;
    if (!byKey.has(id) || byKey.get(id).score < m.score) byKey.set(id, { ...m, game: g });
  };
  for (const t of texts) {
    if (game !== 'pokemon') for (const m of mtg.matchName(t)) add(m, 'mtg');
    if (game !== 'mtg') for (const m of pokemon.matchName(t)) add(m, 'pokemon');
  }
  const candidates = [...byKey.values()];

  // Hinweise aus der Fußzeile geben einen kleinen Bonus für das passende Spiel.
  for (const c of candidates) {
    c.rank = c.score;
    if (c.game === 'pokemon' && meta.pokemon.total) c.rank += 0.04;
    if (c.game === 'mtg' && meta.mtg.setCode) c.rank += 0.04;
  }
  candidates.sort((a, b) => b.rank - a.rank);

  // Pokémon mit lesbarer Nummer: direkte Zuordnung schlägt die Namenssuche.
  if (game !== 'mtg' && meta.pokemon.number && meta.pokemon.total) {
    const hit = texts
      .map((t) => pokemon.matchByNumber(t, meta.pokemon.number, meta.pokemon.total))
      .reduce((a, b) => (b && (!a || b.score > a.score) ? b : a), null);
    const rival = candidates.find((c) => c.game === 'mtg');
    // Nummer + Setgröße sind schon ein starkes Indiz – der Name muss nur plausibel sein.
    if (hit && hit.score >= 0.6 && (!rival || hit.score + 0.25 >= rival.score)) {
      const card = await pokemon.byId(hit.id, hit.lang, true);
      return { match: { score: hit.score, game: 'pokemon', via: 'number' }, card, meta };
    }
  }

  const best = candidates.find((c) => acceptable(c, nameConfidence));
  if (!best) return { match: null, candidates: candidates.slice(0, 3), meta };

  if (best.game === 'pokemon' && !meta.pokemon.total) {
    const hit = pokemon.matchByDigits(best.key, bottomText);
    if (hit) {
      const card = await pokemon.byId(hit.id, hit.lang, true);
      return { match: { score: best.score, game: 'pokemon', via: 'number' }, card, meta };
    }
  }

  const card =
    best.game === 'mtg'
      ? await mtg.lookup({ name: best.key, ...meta.mtg })
      : await pokemon.lookup({ key: best.key, ...meta.pokemon });
  return { match: { score: best.score, game: best.game, key: best.key }, card, meta };
}

export async function search(q, game = 'auto') {
  const out = [];
  if (game !== 'pokemon') out.push(...mtg.matchName(q).map((m) => ({ game: 'mtg', key: m.key, name: m.name, score: m.score })));
  if (game !== 'mtg')
    out.push(
      ...pokemon.matchName(q).map((m) => ({ game: 'pokemon', key: m.key, name: m.name, lang: m.key.split('|')[0], score: m.score }))
    );
  return out.sort((a, b) => b.score - a.score).slice(0, 8);
}

export async function resolve({ game, key, setCode, number, total, id, lang }) {
  // Exakter Druck (z. B. aus dem Bildabgleich)
  if (id) return game === 'mtg' ? mtg.byId(id) : pokemon.byId(id, lang || 'en', true);
  return game === 'mtg' ? mtg.lookup({ name: key, setCode, number }) : pokemon.lookup({ key, number, total });
}

const MAX_PRINTS = 150;
const IMAGE_URL = /^https:\/\/(cards\.scryfall\.io|assets\.tcgdex\.net)\//;
const HASH = /^[0-9a-f]{16,1024}$/;
const ALGO = /^[\w.-]{1,32}$/;

/** Kandidaten für den Bildabgleich: alle Drucke eines Namens samt bereits bekannter Bild-Hashes. */
export async function listPrints({ game, key, algo }) {
  const prints = game === 'mtg' ? await mtg.listPrints(key) : pokemon.listPrints(key);
  const known = ALGO.test(algo || '');
  return prints.slice(0, MAX_PRINTS).map((p) => ({ ...p, hash: known ? getImageHash(`${algo}|${p.image}`) : null }));
}

/** Im Browser berechnete Hashes der Katalogbilder speichern ({ algo, hashes: { bildUrl: hex } }). */
export function saveHashes({ algo, hashes = {} }) {
  if (!ALGO.test(algo || '')) throw new Error('algo fehlt');
  let saved = 0;
  for (const [image, hash] of Object.entries(hashes)) {
    if (typeof hash !== 'string' || !HASH.test(hash) || !IMAGE_URL.test(image)) continue;
    saveImageHash(`${algo}|${image}`, hash);
    saved++;
  }
  return { saved };
}
