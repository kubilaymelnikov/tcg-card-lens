// Magic: The Gathering über Scryfall (aktueller Cardmarket-Preis) + MTGJSON (90-Tage-Verlauf).
import { NameIndex } from './fuzzy.js';
import { cachedJson, getJson, rateLimited, memo, HttpError } from './http.js';
import { db, recordPrice, getHistory, markWatched, getMeta, today } from './db.js';

const API = 'https://api.scryfall.com';
const scryfall = rateLimited(110);
const cache = memo(30 * 60 * 1000);

let index = null;

export async function loadNames() {
  const names = await cachedJson('mtg-names.json', `${API}/catalog/card-names`, 7 * 864e5, (r) => r.data);
  const entries = [];
  for (const full of names) {
    entries.push({ name: full, key: full });
    // Doppelseitige / Split-Karten: auf der Karte steht nur der Name einer Seite.
    if (full.includes(' // ')) for (const face of full.split(' // ')) entries.push({ name: face, key: full });
  }
  index = new NameIndex(entries);
  return index.size;
}

export function matchName(text) {
  return index ? index.search(text, 3) : [];
}

const num = (v) => (v == null || v === '' ? null : Number(v));

function pickPrint(prints, { setCode, number }) {
  const byNumber = number ? prints.filter((c) => c.collector_number === number) : [];
  const bySet = setCode ? prints.filter((c) => c.set.toUpperCase() === setCode) : [];
  const both = byNumber.filter((c) => bySet.includes(c));
  if (both.length) return { card: both[0], certain: true };
  if (bySet.length === 1) return { card: bySet[0], certain: true };
  if (byNumber.length === 1) return { card: byNumber[0], certain: !!setCode || prints.length === 1 };
  const pool = bySet.length ? bySet : byNumber.length ? byNumber : prints;
  // Ohne eindeutigen Druck: neueste "normale" Version mit Cardmarket-Preis.
  const normal = pool.find((c) => c.prices?.eur && !c.promo && !c.variation && !c.digital);
  return { card: normal || pool.find((c) => c.prices?.eur) || pool[0], certain: pool.length === 1 };
}

async function fetchPrints(name) {
  const q = encodeURIComponent(`!"${name}" game:paper`);
  const res = await scryfall(() => getJson(`${API}/cards/search?q=${q}&unique=prints&order=released&dir=desc`));
  return res.data;
}

export async function lookup({ name, setCode, number }) {
  return cache(`${name}|${setCode}|${number}`, async () => {
    if (setCode && number) {
      try {
        const c = await scryfall(() => getJson(`${API}/cards/${setCode.toLowerCase()}/${encodeURIComponent(number)}`));
        if (c.name === name || c.card_faces?.some((f) => f.name === name)) return toCard(c, true);
      } catch (err) {
        if (!(err instanceof HttpError)) throw err;
      }
    }
    const prints = await fetchPrints(name);
    const { card, certain } = pickPrint(prints, { setCode, number });
    return toCard(card, certain);
  });
}

/** Alle Papier-Drucke eines Namens mit kleinem Bild – Grundlage für den Bildabgleich. */
export async function listPrints(name) {
  return cache(`prints|${name}`, async () =>
    (await fetchPrints(name))
      .map((c) => ({
        id: c.id,
        name: c.name,
        set: c.set.toUpperCase(),
        number: c.collector_number,
        image: c.image_status === 'missing' ? null : (c.image_uris?.small ?? c.card_faces?.[0]?.image_uris?.small ?? null),
      }))
      .filter((p) => p.image)
  );
}

export async function byId(id) {
  return toCard(await scryfall(() => getJson(`${API}/cards/${id}`)), true);
}

export async function searchManual(q) {
  return matchName(q).map((m) => ({ game: 'mtg', name: m.key, score: m.score }));
}

async function toCard(c, printCertain) {
  const prices = {
    currency: 'EUR',
    trend: num(c.prices?.eur),
    foilTrend: num(c.prices?.eur_foil),
  };
  const key = `mtg:${c.id}`;
  recordPrice(key, today(), prices.trend, false, 'scryfall');
  recordPrice(key, today(), prices.foilTrend, true, 'scryfall');
  markWatched(key, 'mtg', c.id);

  const keys = [key];
  // Set-Datei wird beim ersten Mal geladen; länger als 10 s soll die Anzeige nicht warten.
  const uuid = await Promise.race([
    mtgjsonUuid(c).catch((err) => console.warn(`[mtgjson] ${err.message}`)),
    new Promise((r) => setTimeout(r, 10000)),
  ]);
  if (uuid) keys.unshift(`mtgjson:${uuid}`);
  const foilOnly = prices.trend == null && prices.foilTrend != null;

  return {
    game: 'mtg',
    id: c.id,
    name: c.name,
    setName: c.set_name,
    setCode: c.set.toUpperCase(),
    number: c.collector_number,
    rarity: c.rarity,
    image: c.image_uris?.normal ?? c.card_faces?.[0]?.image_uris?.normal ?? null,
    cardmarketUrl: c.purchase_uris?.cardmarket ?? null,
    printCertain,
    prices,
    history: getHistory(keys, foilOnly),
    historyFoil: foilOnly,
  };
}

// MTGJSON führt Preise unter eigenen UUIDs. Die Zuordnung holen wir pro Set einmalig aus der Set-Datei.
async function mtgjsonUuid(c) {
  if (!getMeta('mtgjson_imported_at')) return null; // ohne Import gibt es keine Historie
  const lookupUuid = () => db.prepare(`SELECT uuid FROM mtg_ids WHERE scryfall_id = ?`).get(c.id)?.uuid;
  let uuid = lookupUuid();
  if (uuid) return uuid;
  const set = c.set.toUpperCase();
  if (db.prepare(`SELECT 1 FROM mtg_sets_loaded WHERE set_code = ?`).get(set)) return null;
  await loadSetIds(set);
  return lookupUuid() ?? null;
}

const setLoads = memo(24 * 3600 * 1000);
const loadSetIds = (set) => setLoads(set, () => loadSetIdsImpl(set));

async function loadSetIdsImpl(set) {
  let data;
  try {
    data = (await getJson(`https://mtgjson.com/api/v5/${set}.json`, { timeoutMs: 120000 })).data;
  } catch (err) {
    if (err instanceof HttpError && err.status === 404) data = { cards: [] };
    else throw err;
  }
  const insert = db.prepare(`INSERT OR REPLACE INTO mtg_ids (scryfall_id, uuid) VALUES (?, ?)`);
  db.exec('BEGIN');
  try {
    for (const card of [...(data.cards || []), ...(data.tokens || [])]) {
      if (card.identifiers?.scryfallId) insert.run(card.identifiers.scryfallId, card.uuid);
    }
    db.prepare(`INSERT OR IGNORE INTO mtg_sets_loaded (set_code) VALUES (?)`).run(set);
    db.exec('COMMIT');
  } catch (err) {
    db.exec('ROLLBACK');
    throw err;
  }
}
