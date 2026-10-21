import { test } from 'node:test';
import assert from 'node:assert/strict';
import { opOf, placeFrom } from '../../../../src/optionsjourney/web/static/js/keep_place.js';

const form = (hidden) => ({ querySelector: () => (hidden ? { value: hidden } : null) });

test('an edit button keeps the place; Save does not', () => {
  assert.equal(opOf(form(), { name: 'op', value: 'add-group' }), 'add-group');
  assert.equal(opOf(form(), { name: '', value: '' }), '');
  assert.equal(opOf(form(), null), '');
  assert.equal(opOf(form('use-groups'), null), 'use-groups');  // the leg groups box
});

test('a stored place is used only while fresh', () => {
  const at = 1_000_000;
  assert.equal(placeFrom(JSON.stringify({ y: 1840, at }), at + 2000), 1840);
  assert.equal(placeFrom(JSON.stringify({ y: 1840, at }), at + 60000), null);
  assert.equal(placeFrom(null, at), null);
  assert.equal(placeFrom('nonsense', at), null);
});
