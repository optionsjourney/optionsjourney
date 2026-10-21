import { test } from 'node:test';
import assert from 'node:assert/strict';
import { filterLine } from '../../../../src/optionsjourney/web/static/js/filters_now.js';

test('a passing filter shows a tick and its time', () => {
  const line = filterLine({ label: 'VIX level', ok: true, detail: 'VIX 18.5', as_of: '09:47:10', stale: false });
  assert.deepEqual(line, { text: '✓ VIX 18.5 (as of 09:47:10 ET)', cls: 'positive' });
});

test('a miss says it would not trade and a stale print says so', () => {
  const line = filterLine({ label: 'VIX level', ok: false, detail: 'VIX 31; above max 30', as_of: '16:15:00', stale: true });
  assert.equal(line.cls, 'negative');
  assert.equal(line.text, '✗ Would not trade: VIX 31; above max 30 (as of 16:15:00 ET, stale: a live entry would skip)');
});

test('an unreadable filter names itself', () => {
  assert.deepEqual(filterLine({ label: 'VIX9D/VIX ratio', error: 'no VIX9D price' }),
    { text: 'VIX9D/VIX ratio: no VIX9D price', cls: 'negative' });
});
