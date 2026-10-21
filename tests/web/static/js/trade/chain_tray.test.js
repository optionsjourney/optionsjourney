import test from 'node:test';
import assert from 'node:assert/strict';
import * as T from '../../../../../src/optionsjourney/web/static/js/trade/chain_tray.js';

const cell = (over = {}) => ({ expiration: '2026-10-16', strike: 40, type: 'put', side: 'sell',
  bid: 1.2, ask: 1.3, stream_symbol: '.HOOD261016P40', ...over });

test('toggleCell adds a leg at qty 1, and the same cell again removes it', () => {
  let t = T.toggleCell(T.emptyTray(), cell());
  assert.equal(t.legs.length, 1);
  assert.equal(t.legs[0].qty, 1);
  assert.equal(t.legs[0].side, 'sell');
  t = T.toggleCell(t, cell());
  assert.equal(t.legs.length, 0);
});

test('toggleCell flips side on the other cell of the same contract', () => {
  let t = T.toggleCell(T.emptyTray(), cell());
  t = T.setQty(t, T.legKey(t.legs[0]), 3);
  t = T.toggleCell(t, cell({ side: 'buy' }));
  assert.equal(t.legs.length, 1);
  assert.equal(t.legs[0].side, 'buy');
  assert.equal(t.legs[0].qty, 3);
});

test('legs from different expirations are separate', () => {
  let t = T.toggleCell(T.emptyTray(), cell());
  t = T.toggleCell(t, cell({ expiration: '2026-11-20', side: 'buy' }));
  assert.equal(t.legs.length, 2);
});

test('toggleCell refuses a 13th leg by returning the same tray', () => {
  let t = T.emptyTray();
  for (let k = 1; k <= 12; k++) t = T.toggleCell(t, cell({ strike: k }));
  const again = T.toggleCell(t, cell({ strike: 99 }));
  assert.equal(again, t);
});

test('setQty clamps to 1..1000 integers; removeLeg drops it', () => {
  let t = T.toggleCell(T.emptyTray(), cell());
  const k = T.legKey(t.legs[0]);
  assert.equal(T.setQty(t, k, 0).legs[0].qty, 1);
  assert.equal(T.setQty(t, k, 2.7).legs[0].qty, 3);
  assert.equal(T.setQty(t, k, 5000).legs[0].qty, 1000);
  assert.equal(T.removeLeg(t, k).legs.length, 0);
});

test('netMid is signed credit-positive and scales by qty', () => {
  let t = T.toggleCell(T.emptyTray(), cell({ strike: 40, bid: 1.4, ask: 1.6 }));       // sell 1.50
  t = T.toggleCell(t, cell({ strike: 35, side: 'buy', bid: 0.6, ask: 0.8,
    stream_symbol: '.HOOD261016P35' }));                                                 // buy 0.70
  t = T.setQty(t, T.legKey(t.legs[0]), 2);
  t = T.setQty(t, T.legKey(t.legs[1]), 2);
  assert.equal(Math.round(T.netMid(t) * 100) / 100, 1.6);
});

test('legMid falls back to the ask when there is no bid', () => {
  assert.equal(T.legMid({ bid: 0, ask: 0.1 }), 0.05);
  assert.equal(T.legMid({ bid: null, ask: 0.1 }), 0.1);
});

test('withQuotes refreshes bid/ask from the stream by symbol', () => {
  const t = T.toggleCell(T.emptyTray(), cell());
  const q = new Map([['.HOOD261016P40', { bid: 1.5, ask: 1.6 }]]);
  const u = T.withQuotes(t, q);
  assert.deepEqual([u.legs[0].bid, u.legs[0].ask], [1.5, 1.6]);
  assert.equal(t.legs[0].bid, 1.2, 'original untouched');
});

test('bpRequest and previewTrade shape the server bodies', () => {
  let t = T.toggleCell(T.emptyTray(), cell());
  t = T.setQty(t, T.legKey(t.legs[0]), 2);
  assert.deepEqual(T.bpRequest(t, 7, 'HOOD'), { account_id: 7, ticker: 'HOOD', legs: [
    { expiration: '2026-10-16', strike: 40, type: 'put', side: 'sell', qty: 2, mid: 1.25 }] });
  assert.deepEqual(T.previewTrade(t, 'HOOD'), { ticker: 'HOOD', trades: [{ label: '', legs: [
    { action: 'sell', option_type: 'PUT', strike: 40, expiration: '2026-10-16', quantity: 2, mid: 1.25 }] }] });
});

test('spreadInfo and spreadTone', () => {
  assert.deepEqual(T.spreadInfo(1.0, 1.1), { abs: 0.1, pct: (1.1 - 1.0) / 1.05 });
  assert.equal(T.spreadInfo(0, 0), null);
  assert.equal(T.spreadTone(0.05), 'ok');
  assert.equal(T.spreadTone(0.15), 'wide');
  assert.equal(T.spreadTone(0.30), 'very-wide');
});

const rows = [30, 35, 40, 45, 50].map(k => ({ strike: k,
  call: { stream_symbol: `C${k}` }, put: { stream_symbol: `P${k}` } }));

test('visibleRows centres on the strike nearest spot', () => {
  assert.deepEqual(T.visibleRows(rows, 44, false, 1).map(r => r.strike), [40, 45, 50]);
  assert.equal(T.visibleRows(rows, 44, true, 1).length, 5);
  assert.deepEqual(T.visibleRows(rows, null, false, 1).map(r => r.strike), [35, 40, 45]);
});

test('streamWindow takes nearest-to-spot first and caps', () => {
  assert.deepEqual(T.streamWindow(rows, 44, 4), ['P45', 'C45', 'P40', 'C40']);
});

test('bpGate ignores a stale response', () => {
  const g = T.bpGate();
  const first = g.next();
  const second = g.next();
  assert.equal(g.isCurrent(first), false);
  assert.equal(g.isCurrent(second), true);
});

test('bpView covers uses/frees, Schwab, rejection and HTTP error', () => {
  assert.deepEqual(T.bpView({ status: 'success', available: true, ok: true, errors: [],
    bp_change: -3877, new_buying_power: 20123, fees: 1.14, net_price: 1.23 }),
    { tone: 'ok', lines: ['Uses $3,877.00 BP', 'BP after $20,123.00', 'Net credit $1.23', 'Fees $1.14'] });
  assert.deepEqual(T.bpView({ status: 'success', available: true, ok: true, errors: [],
    bp_change: 250, new_buying_power: null, fees: null, net_price: -1.33 }),
    { tone: 'ok', lines: ['Frees $250.00 BP', 'Net debit $1.33'] });
  assert.deepEqual(T.bpView({ status: 'success', available: false, message: 'BP not available — check TOS' }),
    { tone: 'muted', lines: ['BP not available — check TOS'] });
  assert.deepEqual(T.bpView({ status: 'success', available: true, ok: false, errors: ['a', 'b'] }),
    { tone: 'error', lines: ['a', 'b'] });
  assert.deepEqual(T.bpView({ status: 'error', message: 'Unknown account' }),
    { tone: 'error', lines: ['Unknown account'] });
  assert.deepEqual(T.bpView(null), { tone: 'error', lines: ["Couldn't preview this trade"] });
});

test('bpRequest is null for an empty tray, so a debounced call that outlives Send posts nothing', () => {
  assert.equal(T.bpRequest(T.emptyTray(), 7, 'HOOD'), null);
});

test('toggleCell carries the chain delta onto the leg (the stats strip sums it)', () => {
  const t = T.toggleCell(T.emptyTray(), cell({ delta: -0.188 }));
  assert.equal(t.legs[0].delta, -0.188);
});

// Tracker2's ac941c41 (#211)
test('netPrices: a 3x put roll prices one unit, credit positive, Nat/Opp from bid/ask', () => {
  const tray = { legs: [
    { side: 'buy', qty: 3, bid: 14.65, ask: 15.25 },    // BTC 75P Nov 6
    { side: 'sell', qty: 3, bid: 15.75, ask: 16.10 },   // STO 72.5P Dec 18
  ] };
  assert.deepEqual(T.netPrices(tray), { mid: 0.98, nat: 0.5, opp: 1.45, unit: 3 });
  const debit = T.netPrices({ legs: [{ side: 'buy', qty: 2, bid: 1.2, ask: 1.4 }, { side: 'sell', qty: 1, bid: 0.5, ask: 0.6 }] });
  assert.deepEqual(debit, { mid: -2.05, nat: -2.3, opp: -1.8, unit: 1 });
  assert.equal(T.netPrices(T.emptyTray()), null);
});
