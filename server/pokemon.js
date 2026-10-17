// Pokémon TCG über TCGdex (Cardmarket-Preise inkl. 1/7/30-Tage-Durchschnitt).
import { NameIndex, normalize, semiGlobalDistance } from './fuzzy.js';
import { cachedJson, getJson, memo } from './http.js';
import { recordPrice, getHistory, markWatched, today } from './db.js';

const API = 'https://api.tcgdex.net/v2';
export const LANGS = ['en', 'de'];
const cache = memo(30 * 60 * 1000);

let index = null;
const cardsByLangName = new Map(); // "de|Mew-ex" -> [brief cards]
const setsByLang = new Map(); // lang -> Map(setId -> {order, official, name})

export async function loadNames() {
  const entries = [];
  for (const lang of LANGS) {
    const [cards, sets] = await Promise.all([
      cachedJson(`pkm-cards-${lang}.json`, `${API}/${lang}/cards`, 3 * 864e5),
      cachedJson(`pkm-sets-${lang}.json`, `${API}/${lang}/sets`, 3 * 864e5),
    ]);
    setsByLang.set(lang, new Map(sets.map((s, i) => [s.id, { order: i, official: s.cardCount?.official, name: s.name }])));
    const setInfo = setsByLang.get(lang);
    for (const c of cards) {
      if (c.image) imageById.set(`${lang}|${c.id}`, c.image);
      const official = setInfo.get(setIdOf(c.id))?.official;
      if (official) {
        const nk = `${normNum(c.localId)}/${official}`;
        let list = cardsByNumber.get(nk);
        if (!list) cardsByNumber.set(nk, (list = []));
        list.push({ ...c, lang, norm: normalize(c.name) });
      }
      const key = `${lang}|${c.name}`;
      let list = cardsByLangName.get(key);
      if (!list) {
        cardsByLangName.set(key, (list = []));
        entries.push({ name: c.name, key });
      }
      list.push(c);
    }
  }
  index = new NameIndex(entries);
  return index.size;
}

export function matchName(text) {
  return index ? index.search(text, 3) : [];
}

/**
 * Über "Nummer/Setgröße" aus der Fußzeile suchen und den Namen nur noch grob abgleichen.
 * Robuster als die reine Namenssuche, weil Zusätze wie "ex", "V" oder "VMAX" als Logo gedruckt sind
 * und von der Texterkennung meist fehlen ("Mew" statt "Mew ex").
 */
export function matchByNumber(text, number, total) {
  const list = cardsByNumber.get(`${normNum(number)}/${Number(total)}`) || [];
  const ocr = normalize(text);
  const tokens = ocr.split(' ').filter((t) => t.length >= 3);
  let best = null;
  for (const c of list) {
    const whole = 1 - semiGlobalDistance(c.norm, ocr) / c.norm.length;
    const part = Math.max(0, ...tokens.map((t) => 1 - semiGlobalDistance(t, c.norm) / t.length));
    const score = Math.max(whole, part);
    if (!best || score > best.score) best = { id: c.id, lang: c.lang, name: c.name, score };
  }
  return best;
}

/**
 * Fallback, wenn die Fußzeile nicht sauber lesbar ist (z. B. "/" als Ziffer gelesen):
 * Für alle Drucke mit diesem Namen (inkl. Zusätzen wie "-ex") prüfen, ob "Nummer<1 Zeichen>Setgröße"
 * im Ziffernstrom der OCR vorkommt.
 */
export function matchByDigits(key, bottomText) {
  const [lang, baseName] = key.split('|');
  const digits = bottomText.replace(/[^0-9]/g, '');
  if (digits.length < 4) return null;
  const base = normalize(baseName).replace(/ /g, '');
  const sets = setsByLang.get(lang);
  const hits = [];
  for (const [k, list] of cardsByLangName) {
    if (!k.startsWith(`${lang}|`) || !normalize(k.slice(lang.length + 1)).replace(/ /g, '').startsWith(base)) continue;
    for (const c of list) {
      const official = sets.get(setIdOf(c.id))?.official;
      const local = String(c.localId).replace(/\D/g, '');
      if (!official || !local || local !== String(c.localId)) continue;
      const pattern = new RegExp(`0*${Number(local)}\\d?0*${official}`);
      if (pattern.test(digits)) hits.push({ c, weight: local.replace(/^0+/, '').length + String(official).length });
    }
  }
  // Längster Treffer gewinnt ("199·165" schlägt zufälliges "1·99"); bei Gleichstand keine Aussage
  hits.sort((a, b) => b.weight - a.weight);
  if (!hits.length || (hits[1] && hits[1].weight === hits[0].weight)) return null;
  return { id: hits[0].c.id, lang };
}

// TCG Pocket (A1, A2b, P-A …) ist ein reines Digitalspiel und hat keine Cardmarket-Preise.
const isPocket = (setId) => /^(A\d|P-A)/.test(setId);

/**
 * Alle Drucke zu einem Namen inklusive Varianten mit Zusatz ("Glurak" -> "Glurak-ex", "Glurak V" …),
 * jeweils mit kleinem Bild. Fehlt das Bild in der Sprache, dient das englische (gleiches Artwork) als Ersatz.
 */
export function listPrints(key) {
  const [lang, name] = key.split('|');
  const base = baseName(name);
  const out = [];
  for (const [k, list] of cardsByLangName) {
    if (!k.startsWith(`${lang}|`)) continue;
    const n = normalize(k.slice(lang.length + 1));
    if (n !== base && !n.startsWith(`${base} `)) continue;
    for (const c of list) {
      const setId = setIdOf(c.id);
      if (isPocket(setId)) continue;
      const image = c.image || imageById.get(`en|${c.id}`);
      if (!image) continue;
      const official = setsByLang.get(lang).get(setId)?.official;
      out.push({ id: c.id, lang, name: c.name, set: setId, number: official ? `${c.localId}/${official}` : c.localId, image: `${image}/low.webp` });
    }
  }
  return out;
}

// Zusätze hinter dem Pokémon-Namen. Die Namenszeile wird oft unvollständig oder mit Fremdtext gelesen
// ("Glurak Glutexo" -> "Glurak G"), deshalb sucht der Bildabgleich über alle Varianten des Grundnamens.
const SUFFIXES = new Set(['ex', 'gx', 'v', 'vmax', 'vstar', 'vunion', 'g', 'lv', 'x', 'break', 'legend', 'prime', 'star', 'c', 'fb', 'gl', '4']);
export function baseName(name) {
  const tokens = normalize(name).split(' ');
  while (tokens.length > 1 && SUFFIXES.has(tokens.at(-1))) tokens.pop();
  return tokens.join(' ');
}

const imageById = new Map(); // "en|sv03.5-199" -> Bild-URL ohne Endung
const setIdOf = (cardId) => cardId.slice(0, cardId.lastIndexOf('-'));
const normNum = (n) => String(n).replace(/^0+(?=\w)/, '').toUpperCase();
const cardsByNumber = new Map(); // "151/165" -> [brief cards aller Sprachen]

export async function lookup({ key, number, total }) {
  return cache(`${key}|${number}|${total}`, async () => {
    const [lang] = key.split('|');
    const sets = setsByLang.get(lang);
    const all = cardsByLangName.get(key) || [];
    let pool = all;
    if (number) {
      const hit = pool.filter((c) => normNum(c.localId) === normNum(number));
      if (hit.length) pool = hit;
    }
    if (total) {
      const hit = pool.filter((c) => String(sets.get(setIdOf(c.id))?.official) === String(Number(total)));
      if (hit.length) pool = hit;
    }
    // Neuestes Set zuerst
    pool = [...pool].sort((a, b) => (sets.get(setIdOf(b.id))?.order ?? 0) - (sets.get(setIdOf(a.id))?.order ?? 0));
    if (!pool.length) throw new Error(`Keine Karte für ${key}`);
    return byId(pool[0].id, lang, pool.length === 1 || all.length === 1);
  });
}

export async function byId(id, lang = 'en', printCertain = true) {
  const c = await getJson(`${API}/${lang}/cards/${encodeURIComponent(id)}`);
  const cm = c.pricing?.cardmarket ?? {};
  const pick = (k) => (cm[k] > 0 ? cm[k] : cm[`${k}-holo`] > 0 ? cm[`${k}-holo`] : null);
  const prices = {
    currency: 'EUR',
    trend: pick('trend'),
    low: pick('low'),
    avg: pick('avg'),
    avg1: pick('avg1'),
    avg7: pick('avg7'),
    avg30: pick('avg30'),
    // Reverse-/Holo-Variante, falls separat gelistet
    foilTrend: cm.trend > 0 && cm['trend-holo'] > 0 ? cm['trend-holo'] : null,
  };
  const key = `pkm:${c.id}`;
  recordPrice(key, today(), prices.trend, false, 'tcgdex');
  markWatched(key, 'pokemon', `${lang}|${c.id}`);

  return {
    game: 'pokemon',
    id: c.id,
    lang,
    name: c.name,
    setName: c.set?.name,
    setCode: c.set?.id,
    number: c.set?.cardCount?.official ? `${c.localId}/${c.set.cardCount.official}` : c.localId,
    rarity: c.rarity,
    image: c.image ? `${c.image}/high.webp` : null,
    cardmarketUrl: cm.idProduct ? `https://www.cardmarket.com/de/Pokemon/Products?idProduct=${cm.idProduct}` : null,
    printCertain,
    prices,
    history: getHistory([key]),
    historyFoil: false,
  };
}
