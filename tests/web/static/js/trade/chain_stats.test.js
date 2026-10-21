import test from 'node:test';
import assert from 'node:assert/strict';
import { trayStats } from '../../../../../src/optionsjourney/web/static/js/trade/chain_stats.js';
import { ncdf, expiryMs } from '../../../../../src/optionsjourney/web/static/js/modeler/pricing.js';

// Sun 2026-09-27 12:30 ET -- the SPCX screenshot's moment.
const NOW = Date.parse('2026-09-27T16:30:00Z');
const EXP = '2026-11-06';
const leg = (o = {}) => ({ expiration: EXP, strike: 130, type: 'put', side: 'sell', qty: 1,
  bid: 2.31, ask: 2.31, delta: -0.188, ...o });
const yearsTo = exp => (expiryMs(exp) - NOW) / (365 * 864e5);
// P(S_T < x) for a driftless lognormal -- written out here, independent of chain_stats.
const below = (x, S, sig, T) => ncdf((Math.log(x / S) + sig * sig * T / 2) / (sig * Math.sqrt(T)));
const near = (a, b, tol, msg) => assert.ok(Math.abs(a - b) <= tol, `${msg}: ${a} vs ${b}`);

test('empty tray has no stats', () => {
  assert.equal(trayStats([], { spot: 148.68, now: NOW }), null);
});

test('SPCX Nov 6 130P short put: delta, max profit/loss, POP, P50', () => {
  const s = trayStats([leg()], { spot: 148.68, now: NOW });
  near(s.delta, 18.8, 1e-9, 'delta');
  assert.equal(s.maxProfit, 231);
  assert.equal(s.maxLoss, -12769);
  assert.equal(s.note, null);
  // Vol is implied from the leg's own mid (2.31), so POP is the lognormal chance of
  // finishing above the 127.69 breakeven at that vol.
  near(s.pop, 1 - below(127.69, 148.68, s.sigma, yearsTo(EXP)), 1e-9, 'pop');
  assert.ok(s.sigma > 0.4 && s.sigma < 0.5, `sigma ${s.sigma}`);
  assert.ok(s.p50 > s.pop && s.p50 < 1, `p50 ${s.p50} vs pop ${s.pop}`);
});

test('P50 is deterministic (fixed seed) so it does not jitter between edits', () => {
  const a = trayStats([leg()], { spot: 148.68, now: NOW });
  const b = trayStats([leg()], { spot: 148.68, now: NOW });
  assert.equal(a.p50, b.p50);
});

test('put credit spread 2-lot: capped profit and loss scale by quantity', () => {
  const s = trayStats([
    leg({ strike: 110, qty: 2, bid: 1.40, ask: 1.60, delta: -0.10 }),        // sell @1.50
    leg({ strike: 100, side: 'buy', qty: 2, bid: 0.60, ask: 0.80, delta: -0.05 }), // buy @0.70
  ], { spot: 120, now: NOW, sigma: 0.5 });
  near(s.maxProfit, 160, 1e-9, 'max profit');          // 0.80 x 2 x 100
  near(s.maxLoss, -1840, 1e-9, 'max loss');            // (10 - 0.80) x 2 x 100
  near(s.delta, 10, 1e-9, 'delta');                    // (0.10 - 0.05) x 2 x 100
  near(s.pop, 1 - below(109.2, 120, 0.5, yearsTo(EXP)), 1e-9, 'pop');
});

test('iron condor POP is the chance of finishing between both breakevens', () => {
  const s = trayStats([
    leg({ strike: 90, side: 'buy', bid: 0.4, ask: 0.6 }),
    leg({ strike: 95, bid: 0.9, ask: 1.1 }),
    leg({ strike: 105, type: 'call', bid: 0.9, ask: 1.1, delta: 0.2 }),
    leg({ strike: 110, type: 'call', side: 'buy', bid: 0.4, ask: 0.6, delta: 0.1 }),
  ], { spot: 100, now: NOW, sigma: 0.3 });
  const credit = 1.0;                                   // 1.0 + 1.0 - 0.5 - 0.5
  near(s.maxProfit, 100, 1e-9, 'max profit');
  near(s.maxLoss, -400, 1e-9, 'max loss');
  const T = yearsTo(EXP);
  near(s.pop, below(105 + credit, 100, 0.3, T) - below(95 - credit, 100, 0.3, T), 1e-9, 'pop');
});

test('naked short call has unlimited max loss; long call unlimited profit and no P50', () => {
  const short = trayStats([leg({ type: 'call', strike: 160, delta: 0.2 })], { spot: 148.68, now: NOW });
  assert.equal(short.maxLoss, -Infinity);
  assert.ok(short.p50 > 0);
  const long = trayStats([leg({ type: 'call', strike: 160, side: 'buy', delta: 0.2 })], { spot: 148.68, now: NOW });
  assert.equal(long.maxProfit, Infinity);
  assert.equal(long.p50, null);
});

test('mixed expirations: only delta; the rest need one expiration', () => {
  const s = trayStats([leg(), leg({ expiration: '2026-12-18', side: 'buy', bid: 5.65, ask: 5.8, delta: -0.246 })],
    { spot: 148.68, now: NOW });
  near(s.delta, 18.8 - 24.6, 1e-9, 'delta');
  assert.equal(s.maxProfit, null);
  assert.equal(s.maxLoss, null);
  assert.equal(s.pop, null);
  assert.equal(s.p50, null);
  assert.equal(s.note, 'one expiration only');
});

test('delta is null when a leg has none', () => {
  assert.equal(trayStats([leg({ delta: null })], { spot: 148.68, now: NOW }).delta, null);
});

test('no spot or an expired date: payoff figures still show, probabilities do not', () => {
  const s = trayStats([leg()], { spot: null, now: NOW });
  assert.equal(s.maxProfit, 231);
  assert.equal(s.pop, null);
  assert.equal(s.p50, null);
});

import { stripItems } from '../../../../../src/optionsjourney/web/static/js/trade/chain_stats.js';
const byLabel = items => Object.fromEntries(items.map(i => [i.label, i]));

test('stripItems formats like the TastyTrade ticket, with BP Eff. from the preview', () => {
  const s = { delta: 18.2, pop: 0.817, p50: 0.898, maxProfit: 231, maxLoss: -12769, note: null };
  const it = byLabel(stripItems(s, { status: 'success', available: true, ok: true, bp_change: -824.68,
    new_buying_power: 116151.64, fees: 1.28 }));
  assert.deepEqual(Object.keys(it), ['Delta', 'POP', 'P50', 'Max Profit', 'Max Loss', 'BP Eff.', 'BP After', 'Fees']);
  assert.equal(it['BP After'].value, '116,151.64');
  assert.equal(it.Fees.value, '1.28');
  assert.equal(it.Delta.value, '18.20');
  assert.equal(it.POP.value, '82%');
  assert.equal(it.P50.value, '90%');
  assert.deepEqual([it['Max Profit'].value, it['Max Profit'].tone], ['231.00', 'pos']);
  assert.deepEqual([it['Max Loss'].value, it['Max Loss'].tone], ['-12,769.00', 'neg']);
  assert.equal(it['BP Eff.'].value, '824.68');
});

test('stripItems: unlimited, missing, loading, Schwab and multi-expiry', () => {
  const s = { delta: null, pop: null, p50: null, maxProfit: Infinity, maxLoss: -Infinity, note: null };
  let it = byLabel(stripItems(s, 'loading'));
  assert.equal(it['Max Profit'].value, 'Unlimited');
  assert.equal(it['Max Loss'].value, 'Unlimited');
  assert.equal(it.Delta.value, '—');
  assert.equal(it['BP Eff.'].value, '…');
  assert.equal(it['BP After'].value, '—');
  it = byLabel(stripItems(s, { status: 'success', available: false, message: 'x' }));
  assert.equal(it['BP Eff.'].value, 'n/a');
  it = byLabel(stripItems(s, { status: 'success', available: true, ok: false, errors: ['no'] }));
  assert.equal(it['BP Eff.'].value, '—');
  it = byLabel(stripItems({ ...s, maxProfit: null, maxLoss: null, note: 'one expiration only' }, null));
  assert.equal(it['Max Profit'].value, '—');
  assert.equal(it['Max Profit'].title, 'one expiration only');
});

test('futures: dollar stats scale by the contract multiplier (/ES ×50), POP does not', () => {
  const es = trayStats([leg()], { spot: 148.68, now: NOW, mult: 50 });
  const eq = trayStats([leg()], { spot: 148.68, now: NOW });
  near(es.delta, 9.4, 1e-9, 'delta');
  assert.equal(es.maxProfit, 115.5);
  assert.equal(es.maxLoss, -6384.5);
  near(es.pop, eq.pop, 1e-12, 'pop');
  near(es.p50, eq.p50, 1e-12, 'p50');
});
