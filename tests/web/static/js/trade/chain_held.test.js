// Tracker2's tests/js_analyzer/chain_held.test.js, less its futures stream-symbol case (#210).
import test from 'node:test';
import assert from 'node:assert/strict';
import { ACCOUNT_COLORS, accountColors, heldAt, heldExpirations, heldAccounts, fmtQty }
  from '../../../../../src/optionsjourney/web/static/js/trade/chain_held.js';

const EXP = '2026-11-06';
const H = [
  { account_id: 3, expiration: EXP, strike: 75, type: 'put', qty: -5 },
  { account_id: 1, expiration: EXP, strike: 75, type: 'put', qty: 2 },
  { account_id: 1, expiration: '2026-12-18', strike: 90, type: 'call', qty: -1 },
  { account_id: 7, expiration: '2026-11-24', strike: 3840, type: 'put', qty: -1 },
];
const ACCTS = [{ id: 3, label: 'Alpha' }, { id: 1, label: 'Beta' }];

test('colors follow account id, unlisted holders included', () => {
  const c = accountColors(ACCTS, H);
  assert.deepEqual([...c.keys()], [1, 3, 7]);
  assert.equal(c.get(1), ACCOUNT_COLORS[0]);
  assert.equal(c.get(7), ACCOUNT_COLORS[2]);
});

test('heldAt: every account at a strike, or only the filtered one', () => {
  const cell = { expiration: EXP, strike: 75, type: 'put' };
  assert.deepEqual(heldAt(H, cell).map(h => h.account_id), [3, 1]);
  assert.deepEqual(heldAt(H, cell, 3).map(h => h.qty), [-5]);
  assert.deepEqual(heldAt(H, { ...cell, type: 'call' }), []);
  assert.deepEqual(heldAt(H, { ...cell, expiration: '2026-11-13' }), []);
  assert.deepEqual(heldAt(null, cell), []);
});

test('heldExpirations and heldAccounts honor the filter', () => {
  assert.deepEqual([...heldExpirations(H, 1).keys()].sort(), [EXP, '2026-12-18']);
  assert.deepEqual(heldExpirations(H).get(EXP), [1, 3]);
  const accts = heldAccounts(H, ACCTS, accountColors(ACCTS, H));
  assert.deepEqual(accts.map(a => a.label), ['Beta', 'Alpha', 'Account 7']);
});

test('fmtQty signs longs', () => {
  assert.equal(fmtQty(2), '+2');
  assert.equal(fmtQty(-5), '-5');
});
