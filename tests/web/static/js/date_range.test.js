import { test } from 'node:test';
import assert from 'node:assert/strict';
import { monthGrid, pick } from '../../../../src/optionsjourney/web/static/js/date_range.js';

test('a month shows six weeks from the Sunday before its first day', () => {
  const days = monthGrid(2026, 9); // October 2026 starts on a Thursday
  assert.equal(days.length, 42);
  assert.equal(days[0], '2026-09-27');
  assert.equal(days[4], '2026-10-01');
  assert.equal(days[41], '2026-11-07');
});

test('the first click starts a range, the second ends it, the next starts again', () => {
  assert.deepEqual(pick('', '', '2026-10-05'), ['2026-10-05', '']);
  assert.deepEqual(pick('2026-10-05', '', '2026-10-09'), ['2026-10-05', '2026-10-09']);
  assert.deepEqual(pick('2026-10-05', '', '2026-10-01'), ['2026-10-01', '']); // earlier: a new start
  assert.deepEqual(pick('2026-10-05', '2026-10-09', '2026-10-20'), ['2026-10-20', '']);
});
