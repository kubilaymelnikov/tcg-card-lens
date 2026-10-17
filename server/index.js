import { createServer } from 'node:http';
import { createServer as createHttpsServer } from 'node:https';
import { ensureCert, lanAddresses } from './tls.js';
import { createReadStream } from 'node:fs';
import { stat, mkdir, writeFile } from 'node:fs/promises';
import { extname, join, normalize, sep } from 'node:path';
import { PUBLIC_DIR, MODULES_DIR, DATA_DIR } from './paths.js';
import * as mtg from './mtg.js';
import * as pokemon from './pokemon.js';
import { identify, search, resolve, listPrints, saveHashes } from './identify.js';
import { getMeta, setMeta, listWatched, today } from './db.js';
import { USER_AGENT } from './http.js';

const PORT = Number(process.env.PORT || 4711);

// Statische Dateien: eigene Seiten plus die Bibliotheken aus node_modules (läuft so auch offline).
const MOUNTS = [
  ['/vendor/opencv/', join(MODULES_DIR, '@techstark/opencv-js/dist')],
  ['/vendor/tesseract/', join(MODULES_DIR, 'tesseract.js/dist')],
  ['/vendor/tesseract-core/', join(MODULES_DIR, 'tesseract.js-core')],
  ['/vendor/tessdata/', join(MODULES_DIR, '@tesseract.js-data/eng/4.0.0_best_int')],
  ['/vendor/tessdata/', join(MODULES_DIR, '@tesseract.js-data/deu/4.0.0_best_int')],
  ['/', PUBLIC_DIR],
];
const TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json',
  '.wasm': 'application/wasm',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
};

async function serveStatic(req, res, pathname) {
  for (const [prefix, dir] of MOUNTS) {
    if (!pathname.startsWith(prefix)) continue;
    let rel = decodeURIComponent(pathname.slice(prefix.length)) || 'index.html';
    const file = normalize(join(dir, rel));
    if (!file.startsWith(dir + sep) && file !== dir) break;
    try {
      const s = await stat(file);
      if (!s.isFile()) continue;
      res.writeHead(200, {
        'Content-Type': TYPES[extname(file)] || 'application/octet-stream',
        'Content-Length': s.size,
        'Cache-Control': prefix.startsWith('/vendor') ? 'max-age=604800' : 'no-cache',
      });
      createReadStream(file).pipe(res);
      return true;
    } catch {}
  }
  return false;
}

function sendJson(res, status, body) {
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' });
  res.end(JSON.stringify(body));
}

async function readBody(req, limit = 64 * 1024) {
  let raw = '';
  for await (const chunk of req) {
    raw += chunk;
    if (raw.length > limit) throw new Error('Body zu groß');
  }
  return raw ? JSON.parse(raw) : {};
}

// Diagnose: aktuelles Kamerabild als JPEG unter data/snapshots/ ablegen.
async function saveSnapshot(req) {
  const { image, info } = await readBody(req, 20 * 1024 * 1024);
  const m = /^data:image\/jpeg;base64,([A-Za-z0-9+/=]+)$/.exec(image || '');
  if (!m) throw new Error('Kein JPEG');
  const dir = join(DATA_DIR, 'snapshots');
  await mkdir(dir, { recursive: true });
  const name = `snap-${new Date().toISOString().replace(/[:.]/g, '-')}`;
  await writeFile(join(dir, `${name}.jpg`), Buffer.from(m[1], 'base64'));
  await writeFile(join(dir, `${name}.json`), JSON.stringify(info ?? {}, null, 2));
  return { file: `data/snapshots/${name}.jpg` };
}

let ready = false;
const api = {
  'GET /api/status': async () => ({
    ready,
    mtgjsonImportedAt: getMeta('mtgjson_imported_at'),
    watched: listWatched().length,
  }),
  'POST /api/identify': async (req) => identify(await readBody(req)),
  'POST /api/resolve': async (req) => resolve(await readBody(req)),
  'GET /api/prints': async (req, url) =>
    listPrints({ game: url.searchParams.get('game'), key: url.searchParams.get('key') || '', algo: url.searchParams.get('algo') }),
  'POST /api/hashes': async (req) => saveHashes(await readBody(req)),
  'POST /api/snapshot': saveSnapshot,
  'GET /api/search': async (req, url) => search(url.searchParams.get('q') || '', url.searchParams.get('game') || 'auto'),
};

// Kartenbilder durchreichen, damit sie im Canvas ohne CORS-Sperre nutzbar sind (Testseite).
const IMAGE_HOSTS = new Set(['cards.scryfall.io', 'assets.tcgdex.net']);
async function proxyImage(res, target) {
  const u = new URL(target);
  if (u.protocol !== 'https:' || !IMAGE_HOSTS.has(u.hostname)) return sendJson(res, 400, { error: 'Host nicht erlaubt' });
  const r = await fetch(u, { headers: { 'User-Agent': USER_AGENT, Accept: 'image/*' }, signal: AbortSignal.timeout(20000) });
  if (!r.ok) return sendJson(res, r.status, { error: `Bild nicht ladbar (${r.status})` });
  res.writeHead(200, { 'Content-Type': r.headers.get('content-type') || 'image/jpeg', 'Cache-Control': 'max-age=86400' });
  res.end(Buffer.from(await r.arrayBuffer()));
}

async function handle(req, res) {
  const url = new URL(req.url, `http://${req.headers.host}`);
  if (url.pathname === '/api/image') {
    return proxyImage(res, url.searchParams.get('url') || '').catch((err) => sendJson(res, 500, { error: err.message }));
  }
  const handler = api[`${req.method} ${url.pathname}`];
  try {
    if (handler) {
      if (!ready && url.pathname !== '/api/status') return sendJson(res, 503, { error: 'Kartenkataloge werden noch geladen' });
      return sendJson(res, 200, await handler(req, url));
    }
    if (req.method === 'GET' && (await serveStatic(req, res, url.pathname))) return;
    sendJson(res, 404, { error: 'Nicht gefunden' });
  } catch (err) {
    console.error(`[api] ${req.method} ${url.pathname}:`, err.message);
    sendJson(res, 500, { error: err.message });
  }
}

// Standard: nur dieser Rechner. Mit --lan (npm run start:lan) auch im Heimnetz erreichbar,
// zusätzlich per HTTPS, weil Handy-Browser die Kamera nur über HTTPS freigeben.
const LAN = process.env.LAN === '1' || process.argv.includes('--lan');
const HTTPS_PORT = Number(process.env.HTTPS_PORT || PORT + 1);

// Aufrufe aus dem Netz per HTTP auf HTTPS umleiten – sonst bietet der Browser gar keinen Kamerazugriff an.
function handleHttp(req, res) {
  const host = (req.headers.host || '').replace(/:\d+$/, '');
  const local = ['localhost', '127.0.0.1', '[::1]'].includes(host);
  if (LAN && !local && req.method === 'GET' && !req.url.startsWith('/api/')) {
    res.writeHead(302, { Location: `https://${host}:${HTTPS_PORT}${req.url}` });
    return res.end();
  }
  return handle(req, res);
}

createServer(handleHttp).listen(PORT, LAN ? '0.0.0.0' : '127.0.0.1', () => {
  console.log(`TCG Card Lens läuft auf http://localhost:${PORT}`);
  console.log(`  Kamera-Ansicht:  http://localhost:${PORT}/`);
  console.log(`  OBS-Overlay:     http://localhost:${PORT}/overlay.html?source=<Quellenname>`);
});

if (LAN) {
  try {
    createHttpsServer(ensureCert(), handle).listen(HTTPS_PORT, '0.0.0.0', () => {
      console.log('Im Heimnetz (z. B. vom Handy) – Zertifikatswarnung einmalig bestätigen:');
      for (const ip of lanAddresses()) console.log(`  https://${ip}:${HTTPS_PORT}/`);
    });
  } catch (err) {
    console.error(`HTTPS für das Heimnetz nicht verfügbar: ${err.message}`);
  }
}

const [mtgCount, pkmCount] = await Promise.all([mtg.loadNames(), pokemon.loadNames()]);
ready = true;
console.log(`Kataloge geladen: ${mtgCount} Magic-Namen, ${pkmCount} Pokémon-Namen`);
if (!getMeta('mtgjson_imported_at')) {
  console.log('Hinweis: Für den Magic-Preisverlauf einmal "npm run import:mtgjson" ausführen.');
}

// Täglicher Preis-Schnappschuss aller schon einmal erkannten Karten -> der Verlauf wächst mit der Zeit.
async function snapshotWatched() {
  if (getMeta('snapshot_date') === today()) return;
  const watched = listWatched();
  let ok = 0;
  for (const w of watched) {
    try {
      if (w.game === 'mtg') await mtg.byId(w.ref);
      else {
        const [lang, id] = w.ref.split('|');
        await pokemon.byId(id, lang);
      }
      ok++;
    } catch (err) {
      console.warn(`[snapshot] ${w.key}: ${err.message}`);
    }
  }
  setMeta('snapshot_date', today());
  if (watched.length) console.log(`[snapshot] ${ok}/${watched.length} Preise gespeichert`);
}
const runSnapshot = () => snapshotWatched().catch((err) => console.warn(`[snapshot] ${err.message}`));
runSnapshot();
setInterval(runSnapshot, 3 * 3600 * 1000).unref();
