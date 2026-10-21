import { test } from 'node:test';
import assert from 'node:assert/strict';
import { MAX_GRAPHED, allOrNone, graphHref, onlyAccount, toggled, totals } from '../../../../src/optionsjourney/web/static/js/journal_grid.js';

// #215: the grid view's overview table. Each row is a trade; the ticked ones are graphed together.
const rows = [
  { key: 'a', account: 'Alpha ••01', premium: 200, checked: true },
  { key: 'b', account: 'Alpha ••01', premium: 300, checked: true },
  { key: 'c', account: 'Beta ••02', premium: 500, checked: false },
];

test('totals cover the ticked trades only, with a % of their premium', () => {
  const t = totals(rows, { a: 30, b: -10, c: 99 });
  assert.equal(t.pl, 20);
  assert.equal(t.premium, 500);
  assert.equal(t.percent, 4);
  assert.equal(t.selected, 2);
  assert.equal(t.count, 3);
  assert.equal(t.priced, 2);
});

test('each account has a total of its ticked trades; one with none ticked has none', () => {
  const t = totals(rows, { a: 30, b: -10, c: 99 });
  assert.deepEqual(t.accounts, [{ account: 'Alpha ••01', pl: 20 }, { account: 'Beta ••02', pl: null }]);
});

test('no prices yet: no total, and the count of priced trades says so', () => {
  const t = totals(rows, {});
  assert.equal(t.pl, null);
  assert.equal(t.percent, null);
  assert.equal(t.priced, 0);
});

test('the header box ticks all when none are ticked, and none otherwise', () => {
  assert.deepEqual(allOrNone(rows), []);
  assert.deepEqual(allOrNone(rows.map(r => ({ ...r, checked: false }))), ['a', 'b', 'c']);
});

test('an account total ticks just that account', () => {
  assert.deepEqual(onlyAccount(rows, 'Beta ••02'), ['c']);
});

test('a click on a row ticks or unticks it', () => {
  assert.deepEqual(toggled(rows, 'c'), ['a', 'b', 'c']);
  assert.deepEqual(toggled(rows, 'a'), ['b']);
});

test('at most MAX_GRAPHED trades are graphed at once', () => {
  const many = Array.from({ length: MAX_GRAPHED + 5 }, (_, i) => ({ key: `k${i}`, account: 'A', premium: 1, checked: false }));
  assert.equal(allOrNone(many).length, MAX_GRAPHED);
});

test('the graph is the risk graph of the ticked trades together, or none', () => {
  assert.equal(graphHref(['tastytrade:5WX01234:1#put', 'b']), '/risk-graph?trade=tastytrade%3A5WX01234%3A1%23put,b');
  assert.equal(graphHref([]), null);
});
