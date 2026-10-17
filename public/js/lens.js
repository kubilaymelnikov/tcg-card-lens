// Verbindet Erkennung, Tracking über mehrere Frames und die Identifikation per Server.
import { CardDetector, centerOf, cardOnly } from './detector.js';
import { matchPrint } from './printmatch.js';

const LOST_AFTER_MS = 1500; // Track verschwindet erst nach dieser Zeit ohne Detektion
const REMEMBER_MS = 5000; // Karte an gleicher Stelle wiedererkennen, ohne neu zu lesen
const STABLE_HITS = 3; // so oft gesehen, bevor OCR startet
const RETRY_MS = 2500;
const MAX_ATTEMPTS = 6;
const MAX_REFINES = 5;
const REFINE_MS = 1200;

const simple = (s) => s.toLowerCase().replace(/[^\p{L}\p{N}]/gu, '');
/** Nachlesen darf präzisieren ("Mew" -> "Mew ex" #151/165), aber keine ganz andere Karte einsetzen. */
function refines(old, res) {
  const card = res.card;
  if (!card.printCertain || card.game !== old.game) return false;
  // Der Bildabgleich vergleicht mit allen Varianten des Grundnamens – ein Treffer ist stärker als die Namenszeile.
  if (res.match?.via === 'image') return true;
  return res.match?.via === 'number' ? simple(card.name).includes(simple(old.name)) || simple(old.name).includes(simple(card.name))
    : simple(card.name) === simple(old.name);
}

const diag = (q) => Math.hypot(q[2][0] - q[0][0], q[2][1] - q[0][1]);

export class CardLens extends EventTarget {
  constructor(cv, reader, { game = 'auto' } = {}) {
    super();
    this.detector = new CardDetector(cv);
    this.reader = reader;
    this.game = game;
    this.tracks = [];
    this.graveyard = [];
    this.nextId = 1;
    this.busy = false;
    this.lastDebug = null;
  }

  /** Einen Frame verarbeiten. `source` ist Video, Bild oder Canvas in voller Auflösung. */
  process(source, width, height, now = performance.now()) {
    const detections = this.detector.detect(source, width, height);
    this.updateTracks(detections, now);
    const next =
      this.tracks.find(
        (t) => t.hits >= STABLE_HITS && (t.state === 'new' || (t.state === 'failed' && t.attempts < MAX_ATTEMPTS && now > t.retryAt))
      ) ||
      // Erkannt, aber Druck nur geschätzt: in späteren Frames nachlesen
      this.tracks.find((t) => t.state === 'done' && t.card && !t.card.printCertain && !t.manual && (t.refines || 0) < MAX_REFINES && now > (t.refineAt || 0));
    if (next && !this.busy) {
      // Entzerren muss jetzt passieren, solange `source` noch diesen Frame zeigt.
      const upright = this.detector.warp(source, width, height, next.quad);
      this.identify(next, upright, () => this.detector.warp(source, width, height, next.quad, true));
    }
    return { detections, tracks: this.tracks, width, height };
  }

  updateTracks(detections, now) {
    const free = new Set(this.tracks);
    for (const det of detections) {
      const c = centerOf(det.quad);
      let best = null;
      let bestD = Infinity;
      for (const t of free) {
        const d = Math.hypot(c[0] - t.center[0], c[1] - t.center[1]);
        if (d < diag(t.quad) * 0.45 && d < bestD) (best = t), (bestD = d);
      }
      if (best) {
        free.delete(best);
        best.quad = det.quad.map((p, i) => [best.quad[i][0] * 0.35 + p[0] * 0.65, best.quad[i][1] * 0.35 + p[1] * 0.65]);
        best.center = centerOf(best.quad);
        best.lastSeen = now;
        best.hits++;
      } else {
        const t = { id: this.nextId++, quad: det.quad, center: c, firstSeen: now, lastSeen: now, hits: 1, state: 'new', attempts: 0, card: null };
        const ghost = this.graveyard.find((g) => Math.hypot(c[0] - g.center[0], c[1] - g.center[1]) < diag(g.quad) * 0.5);
        if (ghost) {
          Object.assign(t, { state: 'done', card: ghost.card, identifiedAt: now - 1000, hits: STABLE_HITS });
          this.graveyard.splice(this.graveyard.indexOf(ghost), 1);
        }
        this.tracks.push(t);
      }
    }
    for (const t of this.tracks) {
      if (now - t.lastSeen > LOST_AFTER_MS && t.state === 'done') this.graveyard.push({ ...t, diedAt: now });
    }
    this.tracks = this.tracks.filter((t) => now - t.lastSeen <= LOST_AFTER_MS);
    this.graveyard = this.graveyard.filter((g) => now - g.diedAt < REMEMBER_MS);
  }

  async identify(track, upright, makeRotated) {
    this.busy = true;
    const refining = track.state === 'done';
    if (refining) {
      track.refines = (track.refines || 0) + 1;
      track.refineAt = performance.now() + REFINE_MS;
    } else {
      track.state = 'reading';
      track.attempts++;
    }
    try {
      // Ausrichtung bestimmen: Name in beiden Lagen lesen, die sicherere Lesung zuerst probieren.
      // Danach invertiert (helle Schrift auf Vollbild-Karten).
      const rotated = makeRotated();
      const [a, b] = await Promise.all([this.reader.readName(upright), this.reader.readName(rotated)]);
      const [first, second] = a.confidence >= b.confidence ? [upright, rotated] : [rotated, upright];
      const variants = [
        () => [first, {}],
        () => [first, { invert: true }],
        () => [second, {}],
        () => [second, { invert: true }],
      ];
      const ask = (text) =>
        fetch('/api/identify', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ...text, game: this.game }),
        }).then((r) => r.json());
      for (const variant of variants) {
        const [card, opts] = variant();
        const text = await this.reader.read(card, opts);
        this.lastDebug = { card, text, trackId: track.id };
        this.dispatchEvent(new CustomEvent('debug', { detail: this.lastDebug }));
        if (!(text.nameTexts || [text.nameText]).some((t) => t.replace(/\W/g, '').length >= 3)) continue;
        let res = await ask(text);
        if (res.card && !res.card.printCertain) {
          // Name erkannt, Druck unklar: Fußzeile invertiert lesen (helle Schrift auf Vollbild-Karten)
          const bottomText = await this.reader.readBottom(card, { invert: !opts.invert });
          const retry = await ask({ ...text, bottomText });
          if (retry.card?.printCertain) res = retry;
        }
        if (res.card && !res.card.printCertain) res = (await this.matchByImage(card, res)) || res;
        if (res.card && (!refining || refines(track.card, res))) {
          const changed = track.card?.id !== res.card.id;
          track.card = res.card;
          track.state = 'done';
          if (changed) {
            track.identifiedAt = performance.now();
            this.dispatchEvent(new CustomEvent('card', { detail: { track, result: res } }));
          }
          return;
        }
      }
      if (!refining) {
        track.state = 'failed';
        track.retryAt = performance.now() + RETRY_MS;
      }
    } catch (err) {
      console.error(err);
      if (!refining) {
        track.state = 'failed';
        track.retryAt = performance.now() + RETRY_MS;
      }
    } finally {
      this.busy = false;
    }
  }

  /** Fußzeile unlesbar: Druck über den Bildvergleich mit allen Drucken des erkannten Namens bestimmen. */
  async matchByImage(canvas, res) {
    const { game } = res.card;
    const key = res.match?.key ?? (game === 'pokemon' ? `${res.card.lang}|${res.card.name}` : res.card.name);
    try {
      const hit = await matchPrint(cardOnly(canvas), game, key);
      if (!hit) return null;
      const match = { ...res.match, via: 'image', distance: hit.distance };
      if (hit.id === res.card.id) return { ...res, match, card: { ...res.card, printCertain: true } };
      const card = await fetch('/api/resolve', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ game, id: hit.id, lang: hit.lang }),
      }).then((r) => (r.ok ? r.json() : null));
      return card?.id ? { ...res, match, card } : null;
    } catch (err) {
      console.warn('[printmatch]', err.message);
      return null;
    }
  }

  /** Manuelle Korrektur: einer Karte im Bild eine Karte aus der Suche zuweisen. */
  assign(trackId, card) {
    const t = this.tracks.find((x) => x.id === trackId);
    if (t) Object.assign(t, { card, state: 'done', manual: true, identifiedAt: performance.now() });
  }
}
