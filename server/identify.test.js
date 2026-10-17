import { test } from 'node:test';
import assert from 'node:assert/strict';
import { parseBottom } from './identify.js';
import { NameIndex, semiGlobalDistance } from './fuzzy.js';

test('Pokémon-Nummer aus Fußzeile', () => {
  assert.deepEqual(parseBottom('G  151/165 ★').pokemon, { number: '151', total: '165' });
  assert.deepEqual(parseBottom('TG05/TG30').pokemon, { number: 'TG05', total: null });
});

test('Magic-Set und Nummer aus Fußzeile', () => {
  const r = parseBottom('0123 R\nDMU • EN  Illus. Someone');
  assert.equal(r.mtg.setCode, 'DMU');
  assert.equal(r.mtg.number, '123');
  // echte OCR-Ausgabe einer Foundations-Commander-Karte
  const f = parseBottom('U 0163 ™ & © 2026 Wizards of the Coast\nFDC « EN > CHRISTOPHER MOELLER');
  assert.equal(f.mtg.setCode, 'FDC');
  assert.equal(f.mtg.number, '163');
});

test('Digit-Fallback-Muster erkennt falsch gelesenen Schrägstrich', () => {
  // "199/165" wurde als "119971658" gelesen
  assert.match('119971658', new RegExp(`0*199\\d?0*165`));
  assert.doesNotMatch('119971658', new RegExp(`0*19\\d?0*30`));
});

test('Name im OCR-Text mit Tippfehlern finden', () => {
  const idx = new NameIndex(
    ['Mew', 'Mew ex', 'Mewtwo', 'Lightning Bolt', 'Lightning Helix'].map((name) => ({ name, key: name }))
  );
  assert.equal(idx.search('BASIC Mew ex HP180')[0].name, 'Mew ex');
  assert.equal(idx.search('Lightnina Bo1t')[0].name, 'Lightning Bolt');
  assert.equal(semiGlobalDistance('bolt', 'xx bolt yy'), 0);
});
