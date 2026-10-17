import { readFile, writeFile, stat, mkdir } from 'node:fs/promises';
import { DATA_DIR } from './paths.js';

export const USER_AGENT = 'TCGCardLens/0.1 (local hobby tool)';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/** Serialisiert Aufrufe mit Mindestabstand (Scryfall bittet um 50–100 ms zwischen Requests). */
export function rateLimited(minGapMs) {
  let last = 0;
  let chain = Promise.resolve();
  return (fn) => {
    const run = chain.then(async () => {
      const wait = last + minGapMs - Date.now();
      if (wait > 0) await sleep(wait);
      last = Date.now();
      return fn();
    });
    chain = run.catch(() => {});
    return run;
  };
}

export class HttpError extends Error {
  constructor(status, url) {
    super(`HTTP ${status} für ${url}`);
    this.status = status;
  }
}

export async function getJson(url, { timeoutMs = 20000 } = {}) {
  const res = await fetch(url, {
    headers: { 'User-Agent': USER_AGENT, Accept: 'application/json' },
    signal: AbortSignal.timeout(timeoutMs),
  });
  if (!res.ok) throw new HttpError(res.status, url);
  return res.json();
}

/** JSON aus data/<name> lesen, bei Alter > maxAgeMs neu laden. Fällt bei Netzfehlern auf die alte Datei zurück. */
export async function cachedJson(name, url, maxAgeMs, transform = (x) => x) {
  const file = `${DATA_DIR}/${name}`;
  let fresh = false;
  try {
    fresh = Date.now() - (await stat(file)).mtimeMs < maxAgeMs;
  } catch {}
  if (!fresh) {
    try {
      const data = transform(await getJson(url, { timeoutMs: 120000 }));
      await mkdir(DATA_DIR, { recursive: true });
      await writeFile(file, JSON.stringify(data));
      return data;
    } catch (err) {
      console.warn(`[cache] ${name} konnte nicht aktualisiert werden: ${err.message}`);
    }
  }
  return JSON.parse(await readFile(file, 'utf8'));
}

/** Einfacher TTL-Cache im Speicher, der auch laufende Promises teilt. */
export function memo(ttlMs) {
  const map = new Map();
  return (key, fn) => {
    const hit = map.get(key);
    if (hit && hit.until > Date.now()) return hit.value;
    const value = Promise.resolve().then(fn);
    map.set(key, { value, until: Date.now() + ttlMs });
    value.catch(() => map.delete(key));
    return value;
  };
}
