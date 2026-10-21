import { test } from 'node:test';
import assert from 'node:assert/strict';
import { placeFor } from '../../../../src/optionsjourney/web/static/js/new_tag.js';

test('a new tag goes in name order, ignoring case', () => {
  assert.equal(placeFor(['Calendar', 'MEIC', 'Wheel'], 'iron fly'), 1);
  assert.equal(placeFor(['Calendar', 'MEIC', 'Wheel'], 'Zebra'), 3);
  assert.equal(placeFor(['Calendar', 'MEIC'], 'butterfly'), 0);
});

test('the first tag goes first', () => {
  assert.equal(placeFor([], 'MEIC'), 0);
});
