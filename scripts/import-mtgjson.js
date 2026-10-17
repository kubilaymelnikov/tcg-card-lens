// Importiert die Cardmarket-Preise der letzten 90 Tage aus MTGJSON (AllPrices.json.gz, ~150 MB).
// Mehrfach ausführen ist gewollt: ältere Tage bleiben in der Datenbank, der Verlauf wird also länger als 90 Tage.
//
//   npm run import:mtgjson                 # lädt direkt von mtgjson.com
//   npm run import:mtgjson -- pfad/AllPrices.json.gz
import { createRequire } from 'node:module';
import { createReadStream } from 'node:fs';
import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import { createGunzip } from 'node:zlib';
import { db, setMeta, mergeSeries } from '../server/db.js';
import { USER_AGENT } from '../server/http.js';

const require = createRequire(import.meta.url);
const { parser } = require('stream-json');
const { pick } = require('stream-json/filters/Pick');
const { streamObject } = require('stream-json/streamers/StreamObject');

const URL = 'https://mtgjson.com/api/v5/AllPrices.json.gz';

async function source() {
  const local = process.argv[2];
  if (local) return createReadStream(local);
  console.log(`Lade ${URL} …`);
  const res = await fetch(URL, { headers: { 'User-Agent': USER_AGENT } });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return Readable.fromWeb(res.body);
}

let cards = 0;
let points = 0;
let inTx = false;
const started = Date.now();

const collect = new (require('node:stream').Writable)({
  objectMode: true,
  write({ key: uuid, value }, _enc, done) {
    const retail = value?.paper?.cardmarket?.retail;
    if (retail) {
      if (!inTx) {
        db.exec('BEGIN');
        inTx = true;
      }
      for (const [variant, foil] of [['normal', 0], ['foil', 1]]) {
        if (!retail[variant]) continue;
        mergeSeries(uuid, foil, retail[variant]);
        points += Object.keys(retail[variant]).length;
      }
      // Kurze Transaktionen, damit der laufende Server zwischendurch schreiben kann
      if (++cards % 500 === 0) {
        db.exec('COMMIT');
        inTx = false;
      }
      if (cards % 5000 === 0) process.stdout.write(`\r${cards} Karten, ${points} Preispunkte …`);
    }
    done();
  },
  final(done) {
    if (inTx) db.exec('COMMIT');
    done();
  },
});

await pipeline(await source(), createGunzip(), parser(), pick({ filter: 'data' }), streamObject(), collect);
setMeta('mtgjson_imported_at', new Date().toISOString());
db.exec('PRAGMA wal_checkpoint(TRUNCATE)');
console.log(`\nFertig: ${cards} Karten, ${points} Preispunkte in ${Math.round((Date.now() - started) / 1000)} s`);
