import { test } from 'node:test';
import assert from 'node:assert/strict';
import { plan } from '../../../../src/optionsjourney/web/static/js/stock_value.js';

test('nothing held and no trades: the whole card is hidden, no text', () => {
  assert.deepEqual(plan({ points: [], note: '' }), { hide: true, draw: false, note: '' });
});

test('no points with a note says the note', () => {
  assert.deepEqual(plan({ points: [], note: 'Prices aren\'t available right now.' }),
    { hide: false, draw: false, note: 'Prices aren\'t available right now.' });
});

test('points are drawn, with any note under them', () => {
  const points = [{ day: '2026-10-22', value: 1000 }];
  assert.deepEqual(plan({ points, note: '' }), { hide: false, draw: true, note: '' });
  assert.deepEqual(plan({ points, note: 'x' }), { hide: false, draw: true, note: 'x' });
});
