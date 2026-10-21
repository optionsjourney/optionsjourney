import { test } from 'node:test';
import assert from 'node:assert/strict';
import { rowShows, choices, shortsOf } from '../../../../src/optionsjourney/web/static/js/watch_rows.js';

test("a leg's delta row shows while either level is filled in", () => {
  assert.equal(rowShows('', ''), false);
  assert.equal(rowShows(' ', undefined), false);
  assert.equal(rowShows('-40', ''), true);
  assert.equal(rowShows('', '65'), true);
});

test('a row may switch to its own leg or a leg with no row showing', () => {
  assert.deepEqual(choices([0, 1, 2, 3], 1, [1, 2]), [0, 1, 3]);
  assert.deepEqual(choices([0, 1], 0, [0, 1]), [0]);
});

test('the shorts are the rights the legs sell', () => {
  const shorts = shortsOf([{ side: 'sell', right: 'put' }, { side: 'buy', right: 'put' }, { side: 'buy', right: 'call' }]);
  assert.deepEqual([...shorts], ['put']);
  assert.equal(shortsOf([]).size, 0);
});
