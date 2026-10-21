import { test } from 'node:test';
import assert from 'node:assert/strict';
import { renamed, MOST } from '../../../../src/optionsjourney/web/static/js/profit_rows.js';

test('a copied row takes the next number, in its own group', () => {
  assert.equal(renamed('pa-1-0-at', 3), 'pa-1-3-at');
  assert.equal(renamed('pa-0-2-stop-unit', 3), 'pa-0-3-stop-unit');
  assert.equal(MOST, 10);  // bot_store._profit_count reads at most 10
});
