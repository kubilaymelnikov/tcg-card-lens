import { test } from 'node:test';
import assert from 'node:assert/strict';
import { dhashBits, bitsToHex, hashDistance, bestMatch } from '../public/js/phash.js';

test('dHash: Helligkeitsverlauf ergibt die erwarteten Bits', () => {
  // 3 × 3 Pixel, nach rechts heller, nach unten dunkler
  const gray = [10, 20, 30, 5, 15, 25, 0, 10, 20];
  assert.deepEqual(dhashBits(gray, 2), [1, 1, 1, 1, 0, 0, 0, 0]);
  assert.equal(bitsToHex([1, 1, 1, 1, 0, 0, 0, 1]), 'f1');
});

test('Hash-Abstand als Anteil unterschiedlicher Bits', () => {
  assert.equal(hashDistance('ff00', 'ff00'), 0);
  assert.equal(hashDistance('ff00', '00ff'), 1);
  assert.equal(hashDistance('f0f0', 'f0f1'), 1 / 16);
  assert.equal(hashDistance('f0', 'f0f0'), 1, 'unterschiedliche Länge = kein Vergleich');
});

test('Bester Druck nur mit klarem Abstand unter der Schwelle', () => {
  const target = 'ffffffffffffffff';
  const prints = [
    { id: 'a', hash: '0000000000000000' },
    { id: 'b', hash: 'fffffffffffffff0' }, // 4 von 64 Bits anders
    { id: 'c', hash: 'ff00ff00ff00ff00' },
    { id: 'd', hash: null },
  ];
  assert.equal(bestMatch(target, prints).match.id, 'b');
  // zu weit weg
  assert.equal(bestMatch(target, [prints[0], prints[2]]).match, null);
  // zwei fast gleiche Bilder (Nachdruck mit gleichem Artwork) -> keine Aussage
  const twin = bestMatch(target, [prints[1], { id: 'e', hash: 'ffffffffffffff0f' }]);
  assert.equal(twin.match, null);
  assert.equal(twin.ambiguous, true);
});

test('Pokémon-Grundname ohne Zusätze für die Kandidatensuche', async () => {
  const { baseName } = await import('./pokemon.js');
  assert.equal(baseName('Glurak G'), 'glurak');
  assert.equal(baseName('Glurak-ex'), 'glurak');
  assert.equal(baseName('Glurak VSTAR'), 'glurak');
  assert.equal(baseName('Mew ex'), 'mew');
  assert.equal(baseName('Mr. Mime'), 'mr mime');
  assert.equal(baseName('V'), 'v');
});
