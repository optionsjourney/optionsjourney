import test from 'node:test';
import assert from 'node:assert/strict';
import * as K from '../../../../../src/optionsjourney/web/static/js/trade/trade_ticket.js';

const put = { expiration: '2026-10-16', strike: 220, type: 'put', side: 'sell', qty: 2, bid: 1.35, ask: 1.45 };
const call = { expiration: '2026-10-16', strike: 240, type: 'call', side: 'sell', qty: 2, bid: 0.80, ask: 0.86 };

test('newToken is 12 lowercase hex characters', () => {
  const t = K.newToken(a => a.fill(171));
  assert.match(t, /^[0-9a-f]{12}$/);
});

test('defaultLimit is the per-unit net mid', () => {
  assert.equal(K.defaultLimit({ legs: [put, call] }), 2.23);
  assert.equal(K.defaultLimit({ legs: [put] }), 1.4);
});

test('tradeRequest carries the token, entry and confirm only when live', () => {
  const body = K.tradeRequest({ tray: { legs: [put] }, accountId: 3, ticker: 'AAPL', botId: 7,
    mode: 'limit', price: 1.45, token: 'abcabcabcabc', live: true });
  assert.deepEqual(body.entry, { mode: 'limit', price: 1.45 });
  assert.equal(body.confirm, 'TRADE');
  assert.equal(body.token, 'abcabcabcabc');
  assert.deepEqual(body.legs[0], { expiration: '2026-10-16', strike: 220, type: 'put', side: 'sell', qty: 2, mid: 1.4 });
  const chase = K.tradeRequest({ tray: { legs: [put] }, accountId: 3, ticker: 'AAPL', botId: 7,
    mode: 'chase', price: 1.45, token: 'abcabcabcabc', live: false });
  assert.deepEqual(chase.entry, { mode: 'chase' });
  assert.equal('confirm' in chase, false);
});

test('sendLabel', () => {
  assert.equal(K.sendLabel({ mode: 'live' }), 'Send LIVE order');
  assert.equal(K.sendLabel({ mode: 'dry_run' }), 'Send dry run');
});

test('the demo page never says real (demo final review I-1); elsewhere the words are as before', () => {
  assert.equal(K.sendLabel({ mode: 'live' }, true), 'Send order (demo)');
  assert.equal(K.sendLabel({ mode: 'dry_run' }, true), 'Send dry run');
  assert.equal(K.sendWarning(true), "This sends an order to the demo's pretend broker. Nothing here is real money.");
  assert.equal(K.acceptedWords(true), "The demo's pretend broker accepted it.");
  assert.equal(K.sendWarning(), 'This sends a live order to your broker.');
  assert.equal(K.acceptedWords(false), 'Your broker accepted it.');
});

test('resultView covers working, filled, dry run, refused and errors', () => {
  assert.deepEqual(K.resultView({ status: 'success', result: { status: 'working', run_id: 5, price: '1.45' } }),
    { tone: 'ok', text: 'Working at 1.45 — the strategy protects it when it fills', canCancel: true, runId: 5 });
  assert.equal(K.resultView({ status: 'success', result: { status: 'filled', run_id: 5, fill_price: '1.43' } }).text,
    'Filled at 1.43 — exits placed by the strategy');
  assert.equal(K.resultView({ status: 'success', result: { status: 'dry_run', run_id: 5 } }).text,
    'Dry run recorded — nothing was sent');
  assert.equal(K.resultView({ status: 'success', result: { status: 'missed', run_id: 5, errors: [] } }).text,
    'Not filled — the chase ran out');
  assert.equal(K.resultView({ status: 'success', result: { status: 'skipped', detail: 'market closed' } }).text,
    'Not sent — market closed');
  assert.equal(K.resultView({ status: 'error', message: 'nope' }).tone, 'error');
  assert.equal(K.resultView(null).text, 'Order state unknown — check the broker');
  assert.equal(K.resultView({ status: 'success', result: { status: 'cancelled', run_id: 5 } }).text,
    'Cancelled');
});

test('canResend: only after a 400 refusal or a non-duplicate skip', () => {
  assert.equal(K.canResend({ status: 'error', message: 'Limit price must be a positive number' }, 400), true);
  assert.equal(K.canResend({ status: 'success', result: { status: 'skipped', skip_reason: 'kill_switch' } }, 200), true);
  // Unknown outcomes: the order may be live.
  assert.equal(K.canResend(null, 0), false);
  assert.equal(K.canResend(null, 502), false);
  assert.equal(K.canResend({ status: 'error', message: "Couldn't send this trade — check the broker" }, 500), false);
  assert.equal(K.canResend({ status: 'success', result: { status: 'skipped', skip_reason: 'duplicate', run_id: 5 } }, 200), false);
  for (const s of ['working', 'filled', 'partial', 'dry_run', 'error', 'running', 'missed', 'cancelled'])
    assert.equal(K.canResend({ status: 'success', result: { status: s, run_id: 5 } }, 200), false, s);
});

test('resultView: no answer or a 5xx is an unknown order state', () => {
  assert.equal(K.resultView(null, 0).text, 'Order state unknown — check the broker');
  assert.equal(K.resultView({ status: 'error', message: 'boom' }, 500).text, 'Order state unknown — check the broker');
  assert.equal(K.resultView({ status: 'error', message: 'nope' }, 400).text, 'nope');
});

test('withPlaceOnly always offers Place only after the matching strategies', () => {
  const put7 = { id: 7, name: 'Short Put', mode: 'live', exits: [], chase: null };
  const list = K.withPlaceOnly([put7]);
  assert.deepEqual(list.map(b => b.id), [7, K.PLACE_ONLY]);
  assert.equal(list[1].mode, 'live');
  assert.deepEqual(K.withPlaceOnly([]).map(b => b.id), [K.PLACE_ONLY]);
});

test('defaultBotId: the only match, else Place only when nothing matches, else no pick', () => {
  const a = { id: 7 }, b = { id: 8 }, p = { id: K.PLACE_ONLY };
  assert.equal(K.defaultBotId([a, p]), 7);
  assert.equal(K.defaultBotId([p]), K.PLACE_ONLY);
  assert.equal(K.defaultBotId([a, b, p]), null);
});

test('tradeRequest for Place only sends place_only instead of a bot id', () => {
  const body = K.tradeRequest({ tray: { legs: [put] }, accountId: 3, ticker: 'SPX', botId: K.PLACE_ONLY,
    mode: 'limit', price: 4.6, token: 'abcabcabcabc', live: true });
  assert.equal(body.place_only, true);
  assert.equal('bot_id' in body, false);
  assert.equal(body.confirm, 'TRADE');
});

test('resultView for Place only never promises exits', () => {
  const working = K.resultView({ status: 'success', result: { status: 'working', run_id: 5, price: '4.60' } }, 200, true);
  assert.equal(working.text, 'Working at 4.60 — no exits will be placed');
  assert.equal(working.canCancel, true);
  assert.equal(K.resultView({ status: 'success', result: { status: 'filled', fill_price: '4.55' } }, 200, true).text,
    'Filled at 4.55 — no exits placed');
  assert.equal(K.resultView({ status: 'success', result: { status: 'partial' } }, 200, true).text,
    'Partly filled — no exits placed');
});

test('tradeRequest sends a Limit effect; a Chase never does', () => {
  const lim = K.tradeRequest({ tray: { legs: [put] }, accountId: 3, ticker: 'SPX', botId: 7,
    mode: 'limit', price: 0.5, effect: 'debit', token: 'abc', live: false });
  assert.deepEqual(lim.entry, { mode: 'limit', price: 0.5, effect: 'debit' });
  const chase = K.tradeRequest({ tray: { legs: [put] }, accountId: 3, ticker: 'SPX', botId: 7,
    mode: 'chase', effect: 'debit', token: 'abc', live: false });
  assert.deepEqual(chase.entry, { mode: 'chase' });
});

test('defaultEffect follows the tray mid net', () => {
  assert.equal(K.defaultEffect({ legs: [put] }), 'credit');
  assert.equal(K.defaultEffect({ legs: [{ ...put, side: 'buy' }] }), 'debit');
});

test('tradeRequest sends the entry and profit-target time in force', () => {
  const b = K.tradeRequest({ tray: { legs: [put] }, accountId: 1, ticker: 'XSP', botId: 7, mode: 'limit',
    price: 0.1, effect: 'debit', token: 't', live: false, entryTif: 'GTC_OVERNIGHT', exitTif: 'GTC_OVERNIGHT' });
  assert.equal(b.entry.tif, 'GTC_OVERNIGHT');
  assert.equal(b.exit_tif, 'GTC_OVERNIGHT');
});

test('chase sends no entry tif; place-only sends no exit tif', () => {
  const chase = K.tradeRequest({ tray: { legs: [put] }, accountId: 1, ticker: 'XSP', botId: 7, mode: 'chase', token: 't', entryTif: 'GTC_OVERNIGHT', exitTif: 'GTC' });
  assert.equal(chase.entry.tif, undefined);
  const po = K.tradeRequest({ tray: { legs: [put] }, accountId: 1, ticker: 'XSP', botId: K.PLACE_ONLY, mode: 'limit', price: 1, effect: 'debit', token: 't', entryTif: 'GTC', exitTif: 'GTC_OVERNIGHT' });
  assert.equal(po.exit_tif, undefined);
});

test('overnightEligible is the Cboe index roots only', () => {
  for (const t of ['SPX', 'SPXW', 'XSP', 'VIX', 'RUT']) assert.ok(K.overnightEligible(t));
  for (const t of ['SPY', 'QQQ', 'AAPL', '/ES']) assert.ok(!K.overnightEligible(t));
});

test('overnightOffered hides GTC Overnight on Schwab, keeps it for TT or unknown', () => {
  assert.ok(K.overnightOffered('XSP', 'TASTYTRADE'));
  assert.ok(K.overnightOffered('SPX', null));          // unknown: server falls back safely
  assert.ok(K.overnightOffered('SPX', undefined));
  assert.ok(!K.overnightOffered('SPX', 'SCHWAB'));
  assert.ok(!K.overnightOffered('SPY', 'TASTYTRADE'));
});

test('defaultExitTif follows the strategy, GTC otherwise', () => {
  assert.equal(K.defaultExitTif({ id: 1, exit_time_in_force: 'GTC_OVERNIGHT' }), 'GTC_OVERNIGHT');
  assert.equal(K.defaultExitTif({ id: 1, exit_time_in_force: 'GTC' }), 'GTC');
  assert.equal(K.defaultExitTif({ id: 1 }), 'GTC');
  assert.equal(K.defaultExitTif(null), 'GTC');
});

const ES = [{ threshold: '5', value: '0.05' }, { threshold: null, value: '0.25' }];
test('tickAt: tick by price tier', () => {
  assert.equal(K.tickAt(ES, 12.3), 0.25);
  assert.equal(K.tickAt(ES, 3.1), 0.05);
  assert.equal(K.tickAt([], 3), 0.01);
});
test('snapPrice: credit snaps down, debit up', () => {
  assert.equal(K.snapPrice(12.3, ES, 'credit'), 12.25);
  assert.equal(K.snapPrice(12.3, ES, 'debit'), 12.5);
  assert.equal(K.snapPrice(12.25, ES, 'credit'), 12.25);
});
test('tradeBotsRequest carries the ticker only when given', () => {
  assert.equal(K.tradeBotsRequest({ legs: [] }, 3, '/ES').ticker, '/ES');
  assert.equal('ticker' in K.tradeBotsRequest({ legs: [] }, 3), false);
});

test('tradeRequest carries idea_id only when given an integer', () => {
  const base = { tray: { legs: [] }, accountId: 1, ticker: 'SPX', botId: K.PLACE_ONLY, mode: 'limit', price: 1, effect: 'credit', token: 't' };
  assert.equal(K.tradeRequest({ ...base, ideaId: 7 }).idea_id, 7);
  assert.equal('idea_id' in K.tradeRequest(base), false);
  assert.equal('idea_id' in K.tradeRequest({ ...base, ideaId: null }), false);
});
