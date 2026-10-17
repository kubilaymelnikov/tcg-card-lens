import { DatabaseSync } from 'node:sqlite';
import { mkdirSync } from 'node:fs';
import { dirname } from 'node:path';
import { DATA_DIR } from './paths.js';

const file = `${DATA_DIR}/prices.sqlite`;
mkdirSync(dirname(file), { recursive: true });

export const db = new DatabaseSync(file);
db.exec(`
  PRAGMA journal_mode = WAL;
  PRAGMA busy_timeout = 5000;
  -- Selbst aufgezeichnete Preispunkte je Karte und Tag. key = "mtg:<scryfallId>" oder "pkm:<tcgdexId>"
  CREATE TABLE IF NOT EXISTS price_history (
    key    TEXT NOT NULL,
    date   TEXT NOT NULL,
    price  REAL NOT NULL,
    foil   INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL,
    PRIMARY KEY (key, date, foil)
  );
  -- MTGJSON-Verlauf kompakt: eine Zeile je Karte, Preise in Cent ab start_day (Tage seit 1970), null = keine Angabe
  CREATE TABLE IF NOT EXISTS mtgjson_series (
    uuid      TEXT NOT NULL,
    foil      INTEGER NOT NULL,
    start_day INTEGER NOT NULL,
    cents     TEXT NOT NULL,
    PRIMARY KEY (uuid, foil)
  ) WITHOUT ROWID;
  -- Zuordnung Scryfall-ID -> MTGJSON-UUID (für die 90-Tage-Historie)
  CREATE TABLE IF NOT EXISTS mtg_ids (
    scryfall_id TEXT PRIMARY KEY,
    uuid        TEXT NOT NULL
  );
  CREATE TABLE IF NOT EXISTS mtg_sets_loaded (
    set_code TEXT PRIMARY KEY
  );
  -- Karten, die schon einmal erkannt wurden: deren Preis wird täglich mitgeschrieben
  CREATE TABLE IF NOT EXISTS watched (
    key       TEXT PRIMARY KEY,
    game      TEXT NOT NULL,
    ref       TEXT NOT NULL,
    last_seen TEXT NOT NULL
  );
  -- Perceptual Hashes der Katalogbilder (Bildabgleich des Drucks). ref = "<algo>|<bild-url>"
  CREATE TABLE IF NOT EXISTS image_hashes (
    ref  TEXT PRIMARY KEY,
    hash TEXT NOT NULL
  ) WITHOUT ROWID;
  CREATE TABLE IF NOT EXISTS meta (
    k TEXT PRIMARY KEY,
    v TEXT
  );
`);

const upsertPrice = db.prepare(
  `INSERT INTO price_history (key, date, price, foil, source) VALUES (?, ?, ?, ?, ?)
   ON CONFLICT(key, date, foil) DO UPDATE SET price = excluded.price, source = excluded.source`
);

export function recordPrice(key, date, price, foil, source) {
  if (price == null || !Number.isFinite(price) || price <= 0) return;
  try {
    upsertPrice.run(key, date, price, foil ? 1 : 0, source);
  } catch (err) {
    // z. B. während eines laufenden MTGJSON-Imports: Schnappschuss auslassen, Anfrage nicht scheitern lassen
    console.warn(`[db] Preis für ${key} nicht gespeichert: ${err.message}`);
  }
}

const selectHistory = db.prepare(
  `SELECT date, price, source FROM price_history WHERE key = ? AND foil = ? AND date >= ? ORDER BY date`
);

const dayOf = (date) => Math.round(Date.parse(date) / 864e5);
const dateOf = (day) => new Date(day * 864e5).toISOString().slice(0, 10);

const selectSeries = db.prepare(`SELECT start_day, cents FROM mtgjson_series WHERE uuid = ? AND foil = ?`);
const upsertSeries = db.prepare(
  `INSERT INTO mtgjson_series (uuid, foil, start_day, cents) VALUES (?, ?, ?, ?)
   ON CONFLICT(uuid, foil) DO UPDATE SET start_day = excluded.start_day, cents = excluded.cents`
);

function readSeries(uuid, foil) {
  const row = selectSeries.get(uuid, foil ? 1 : 0);
  const out = new Map();
  if (row) JSON.parse(row.cents).forEach((c, i) => c != null && out.set(row.start_day + i, c));
  return out;
}

/** MTGJSON-Preise ({ "2026-08-01": 1.23, … }) mit bereits gespeichertem Verlauf zusammenführen. */
export function mergeSeries(uuid, foil, pricesByDate) {
  const days = readSeries(uuid, foil);
  for (const [date, price] of Object.entries(pricesByDate)) {
    if (price > 0) days.set(dayOf(date), Math.round(price * 100));
  }
  if (!days.size) return;
  const keys = [...days.keys()];
  const start = Math.min(...keys);
  const end = Math.max(...keys);
  const cents = Array.from({ length: end - start + 1 }, (_, i) => days.get(start + i) ?? null);
  upsertSeries.run(uuid, foil ? 1 : 0, start, JSON.stringify(cents));
}

/** Verlauf über mehrere Schlüssel zusammenführen (erster Schlüssel gewinnt je Tag). */
export function getHistory(keys, foil = false, sinceDays = 180) {
  const since = new Date(Date.now() - sinceDays * 864e5).toISOString().slice(0, 10);
  const byDate = new Map();
  const add = (date, price, source) => {
    if (date >= since && !byDate.has(date)) byDate.set(date, { date, price, source });
  };
  for (const key of keys) {
    if (key.startsWith('mtgjson:')) {
      for (const [day, cents] of readSeries(key.slice(8), foil)) add(dateOf(day), cents / 100, 'mtgjson');
    } else {
      for (const row of selectHistory.all(key, foil ? 1 : 0, since)) add(row.date, row.price, row.source);
    }
  }
  return [...byDate.values()].sort((a, b) => a.date.localeCompare(b.date));
}

const upsertWatched = db.prepare(
  `INSERT INTO watched (key, game, ref, last_seen) VALUES (?, ?, ?, ?)
   ON CONFLICT(key) DO UPDATE SET last_seen = excluded.last_seen`
);
export function markWatched(key, game, ref) {
  try {
    upsertWatched.run(key, game, ref, today());
  } catch (err) {
    console.warn(`[db] ${key} nicht vorgemerkt: ${err.message}`);
  }
}
export function listWatched(maxAgeDays = 180) {
  const since = new Date(Date.now() - maxAgeDays * 864e5).toISOString().slice(0, 10);
  return db.prepare(`SELECT key, game, ref FROM watched WHERE last_seen >= ?`).all(since);
}

const selectHash = db.prepare(`SELECT hash FROM image_hashes WHERE ref = ?`);
const upsertHash = db.prepare(
  `INSERT INTO image_hashes (ref, hash) VALUES (?, ?) ON CONFLICT(ref) DO UPDATE SET hash = excluded.hash`
);
export function getImageHash(ref) {
  return selectHash.get(ref)?.hash ?? null;
}
export function saveImageHash(ref, hash) {
  try {
    upsertHash.run(ref, hash);
  } catch (err) {
    console.warn(`[db] Bild-Hash für ${ref} nicht gespeichert: ${err.message}`);
  }
}

export function getMeta(k) {
  return db.prepare(`SELECT v FROM meta WHERE k = ?`).get(k)?.v ?? null;
}
export function setMeta(k, v) {
  db.prepare(`INSERT INTO meta (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v`).run(k, String(v));
}

export function today() {
  return new Date().toISOString().slice(0, 10);
}
