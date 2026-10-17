// Bildabgleich: findet den exakten Druck einer Karte, deren Name bekannt ist, deren Fußzeile aber
// unlesbar war. Vergleicht den Hash der entzerrten Karte mit den Hashes aller Drucke dieses Namens.
import { HASH_ALGO, hashImage, bestMatch } from './phash.js';
import { loadImage } from './overlay-utils.js';

const PARALLEL = 6;
const lists = new Map(); // "pokemon|de|Glurak" -> Promise<Array<{id, lang, image, hash}>>

async function hashMissing(prints) {
  const todo = prints.filter((p) => !p.hash);
  const fresh = {};
  let next = 0;
  const worker = async () => {
    while (next < todo.length) {
      const p = todo[next++];
      try {
        const img = await loadImage(`/api/image?url=${encodeURIComponent(p.image)}`);
        p.hash = fresh[p.image] = hashImage(img);
      } catch (err) {
        console.warn(`[printmatch] ${p.id}: ${err.message}`);
      }
    }
  };
  await Promise.all(Array.from({ length: PARALLEL }, worker));
  if (Object.keys(fresh).length) {
    // Serverseitig merken, damit die Bilder nur einmal geladen werden müssen
    await fetch('/api/hashes', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ algo: HASH_ALGO, hashes: fresh }),
    }).catch((err) => console.warn(`[printmatch] Hashes nicht gespeichert: ${err.message}`));
  }
  return prints;
}

/** Alle Drucke eines Namens samt Hash (lädt fehlende Bilder einmalig). */
export function printCandidates(game, key) {
  const id = `${game}|${key}`;
  if (!lists.has(id)) {
    const q = new URLSearchParams({ game, key, algo: HASH_ALGO });
    const p = fetch(`/api/prints?${q}`)
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`Drucke nicht ladbar (${r.status})`))))
      .then(hashMissing);
    p.catch(() => lists.delete(id));
    lists.set(id, p);
  }
  return lists.get(id);
}

/**
 * @param {HTMLCanvasElement} canvas entzerrte, aufrecht liegende Karte (630 × 880)
 * @returns {Promise<{id, lang, name, set, number, distance} | null>}
 */
export async function matchPrint(canvas, game, key) {
  const prints = await printCandidates(game, key);
  if (!prints.length) return null;
  const { match } = bestMatch(hashImage(canvas, canvas.width, canvas.height), prints);
  return match;
}
