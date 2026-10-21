import test from 'node:test';
import assert from 'node:assert/strict';
import * as C from '../../../../../src/optionsjourney/web/static/js/trade/ticket_core.js';

const leg = (over) => ({ expiration: '2026-10-16', strike: 7700, type: 'put', side: 'sell', qty: 2,
  bid: 3.0, ask: 3.2, stream_symbol: '.SPXW261016P7700', delta: -0.2, ...over });
const condor = { legs: [
  leg({}),
  leg({ strike: 7650, side: 'buy', bid: 1.9, ask: 2.1, stream_symbol: '.SPXW261016P7650' }),
] };

test('units is the gcd of the quantities', () => {
  assert.equal(C.units(condor), 2);
  assert.equal(C.units({ legs: [leg({ qty: 2 }), leg({ qty: 3, strike: 1 })] }), 1);
  assert.equal(C.units({ legs: [] }), 0);
});

test('reviewRequest: ratios, quantity and a signed price', () => {
  const body = C.reviewRequest(condor, { accountId: 7, price: 1.1, effect: 'credit', tif: 'day' });
  assert.deepEqual(body, {
    account_id: 7, quantity: 2, price: 1.1, tif: 'day',
    legs: [
      { stream_symbol: '.SPXW261016P7700', side: 'sell', ratio: 1 },
      { stream_symbol: '.SPXW261016P7650', side: 'buy', ratio: 1 },
    ],
  });
  assert.equal(C.reviewRequest(condor, { accountId: 7, price: 0.4, effect: 'debit', tif: 'gtc' }).price, -0.4);
});

test('reviewRequest refuses a missing or zero price, no legs and no account', () => {
  assert.ok(C.reviewRequest(condor, { accountId: 7, price: null, effect: 'credit', tif: 'day' }).error);
  assert.ok(C.reviewRequest(condor, { accountId: 7, price: 0, effect: 'credit', tif: 'day' }).error);
  assert.ok(C.reviewRequest({ legs: [] }, { accountId: 7, price: 1, effect: 'credit', tif: 'day' }).error);
  assert.ok(C.reviewRequest(condor, { accountId: null, price: 1, effect: 'credit', tif: 'day' }).error);
});

test('graphLegs: signed entries at mid, and the limit as the cost basis', () => {
  const { legs, realizedPl } = C.graphLegs(condor, { limit: 1.2, effect: 'credit', mult: 100 });
  assert.deepEqual(legs[0], { leg_type: 'PUT', side: 'SHORT', strike: 7700, expiration_date: '2026-10-16',
    entry_price: 3.1, quantity: 2, multiplier: 100, live_price: 3.1, stream_symbol: '.SPXW261016P7700' });
  assert.equal(legs[1].side, 'LONG');
  assert.equal(legs[1].entry_price, -2.0);
  // mids: 2 x 3.10 credit - 2 x 2.00 debit = 2.20 net for 2 units (1.10 each); a 1.20 limit is
  // 0.10 better per unit: +$20 for 2 units of 100
  assert.ok(Math.abs(realizedPl - 20) < 1e-9);
  const debit = C.graphLegs(condor, { limit: 1.0, effect: 'debit', mult: 100 });
  // paying 1.00 a unit for something whose mids take in 1.10: (-1.00 - 1.10) x 2 x 100
  assert.ok(Math.abs(debit.realizedPl - (-420)) < 1e-9);
});

test('alertContracts: short legs negative', () => {
  assert.deepEqual(C.alertContracts(condor), [
    { expiry: '2026-10-16', strike: 7700, right: 'put', qty: -2 },
    { expiry: '2026-10-16', strike: 7650, right: 'put', qty: 2 },
  ]);
});

test('graphLegs: no realized P/L without a usable limit (empty, null, zero)', () => {
  for (const limit of ['', null, 0]) {
    const { realizedPl } = C.graphLegs(condor, { limit, effect: 'credit', mult: 100 });
    assert.equal(realizedPl, 0, `limit ${JSON.stringify(limit)}`);
  }
});

test('graphLegs: realizedPl reconciles with the rounded entry prices on a half-cent mid', () => {
  // mid 3.125 rounds to 3.13 in entry_price; the cost basis must use that same 3.13
  const tray = { legs: [leg({ bid: 3.0, ask: 3.25, qty: 1 })] };
  const { legs, realizedPl } = C.graphLegs(tray, { limit: 1.0, effect: 'credit', mult: 100 });
  const basis = legs.reduce((s, l) => s + l.entry_price * l.quantity * l.multiplier, 0);
  assert.ok(Math.abs(basis + realizedPl - 1.0 * 1 * 100) < 1e-9);
});

test('defaultTicket: the limit and effect from the mids, the same number as Tracker2', async () => {
  const { defaultLimit, defaultEffect } = await import('../../../../../src/optionsjourney/web/static/js/trade/trade_ticket.js');
  const t = C.defaultTicket(condor);
  assert.equal(t.limit, defaultLimit(condor));
  assert.equal(t.effect, defaultEffect(condor));
  const debit = { legs: [leg({ side: 'buy', qty: 1 })] };
  assert.deepEqual(C.defaultTicket(debit), { limit: 3.1, effect: 'debit' });
});

test('defaultTicket: a leg with no ask leaves the limit empty, never a believable wrong one', () => {
  const bought = leg({ side: 'buy', qty: 1, bid: 4.9, ask: 5.1 });
  const unquoted = leg({ side: 'sell', qty: 1, strike: 1, bid: null, ask: null });
  assert.equal(C.defaultTicket({ legs: [bought, unquoted] }).limit, null);
  assert.equal(C.defaultTicket({ legs: [bought, leg({ side: 'sell', qty: 1, ask: 0, bid: 0 })] }).limit, null);
  assert.deepEqual(C.defaultTicket({ legs: [] }), { limit: null, effect: 'credit' });
  assert.equal(C.graphLegs({ legs: [bought, unquoted] }, { limit: null, effect: 'debit', mult: 100 }).realizedPl, 0);
});

test('confirmLabel says exactly the reviewed summary', () => {
  assert.equal(C.confirmLabel('Sell 1 SPXW 5800P · $1.10 credit · Day'), 'Send: Sell 1 SPXW 5800P · $1.10 credit · Day');
});

test('confirmParts: one line per leg, then the terms', () => {
  assert.deepEqual(C.confirmParts('Sell 1 SPXW 19 Oct 7600P / Buy 1 SPXW 19 Oct 7595P · $5.00 credit · Day'),
    { legs: ['Sell 1 SPXW 19 Oct 7600P', 'Buy 1 SPXW 19 Oct 7595P'], terms: '$5.00 credit · Day' });
  assert.deepEqual(C.confirmParts('Sell 1 SPXW 5800P'), { legs: ['Sell 1 SPXW 5800P'], terms: '' });
});

test('workingRows: the legs as text, a partly filled leg, and read-only rows that are not yours', () => {
  const rows = C.workingRows([
    { order_id: '11', status: 'live', yours: true, price: 1.1, entered_at: null, legs: [
      { symbol: 'SPXW261013P5800', side: 'sell', opening: true, quantity: 2, remaining: 2 },
      { symbol: 'SPXW261013P5750', side: 'buy', opening: true, quantity: 2, remaining: 1 },
    ] },
    { order_id: '12', status: 'received', yours: false, price: null, entered_at: null, legs: [
      { symbol: 'SPY', side: 'buy', opening: false, quantity: 5, remaining: 5 },
    ] },
    { order_id: '13', status: 'live', yours: true, price: -0.4, entered_at: null, legs: [
      { symbol: 'QQQ261016C500', side: 'buy', opening: true, quantity: 1, remaining: 1 },
    ] },
  ]);
  assert.deepEqual(rows[0], { id: '11', lines: ['Sell 2 SPXW 13 Oct 5800P', 'Buy 1 of 2 SPXW 13 Oct 5750P'],
    status: 'live', yours: true, price: 1.1, effect: 'credit', canReplace: false });
  assert.deepEqual(rows[1], { id: '12', lines: ['Buy 5 SPY'], status: 'received', yours: false, price: null, effect: null,
    canReplace: true });
  assert.equal(rows[2].price, 0.4);
  assert.equal(rows[2].effect, 'debit');
  assert.equal(rows[2].canReplace, true);  // nothing filled yet; a partly filled one can only be cancelled
});

test('readableSymbol: OCC symbols with or without the padded strike, others as they are', () => {
  assert.equal(C.readableSymbol('SPXW  261023C07645000'), 'SPXW 23 Oct 7645C');
  assert.equal(C.readableSymbol('SPXW261013P5800'), 'SPXW 13 Oct 5800P');
  assert.equal(C.readableSymbol('SPY   261121P00512500'), 'SPY 21 Nov 512.5P');
  assert.equal(C.readableSymbol('SPY'), 'SPY');
});

test('replacePrice keeps the logged credit or debit sign', () => {
  assert.equal(C.replacePrice({ effect: 'credit' }, '1.15'), 1.15);
  assert.equal(C.replacePrice({ effect: 'debit' }, '0.45'), -0.45);
  assert.equal(C.replacePrice({ effect: 'debit' }, ''), null);
  assert.equal(C.replacePrice({ effect: 'credit' }, '0'), null);
  assert.equal(C.replacePrice({ effect: null }, '1'), null);
});

// Bots slice 15: Manage with a bot.
test('newToken: 12 hex characters from 6 random bytes', () => {
  assert.equal(C.newToken(new Uint8Array([0, 1, 171, 205, 239, 255])), '0001abcdefff');
  assert.match(C.newToken(new Uint8Array([9, 9, 9, 9, 9, 9, 9, 9])), /^[0-9a-f]{12}$/);
});

test('botRequest: the ticket, the bot and the token; no price, no time in force', () => {
  const body = C.botRequest(condor, { accountId: 7, botId: 3, token: '0123456789ab', code: 'c0de' });
  assert.deepEqual(body, {
    account_id: 7, bot_id: 3, token: '0123456789ab', code: 'c0de', quantity: 2,
    legs: [
      { stream_symbol: '.SPXW261016P7700', side: 'sell', ratio: 1 },
      { stream_symbol: '.SPXW261016P7650', side: 'buy', ratio: 1 },
    ],
  });
  assert.equal(C.botRequest(condor, { accountId: 7, botId: null, token: 'x' }).error, 'Pick a bot.');
  assert.equal(C.botRequest({ legs: [] }, { accountId: 7, botId: 3, token: 'x' }).error, 'Pick a bid (sell) or ask (buy) first.');
  assert.equal(C.botRequest(condor, { accountId: null, botId: 3, token: 'x' }).error, 'Pick an account.');
});

test('botLists: the bots that can take it, and the rest with why', () => {
  const lists = C.botLists([{ id: 1, problem: null }, { id: 2, problem: 'preview mode' }]);
  assert.deepEqual(lists.offered.map(b => b.id), [1]);
  assert.deepEqual(lists.refused.map(b => b.id), [2]);
  assert.deepEqual(C.botLists(null), { offered: [], refused: [] });
});

test('botSendLabel names the bot', () => {
  assert.equal(C.botSendLabel('IC'), "Send as IC's entry");
});

test('pickerRequest: the legs and size on the account', () => {
  assert.deepEqual(C.pickerRequest(condor, 7), {
    account_id: 7, quantity: 2,
    legs: [
      { stream_symbol: '.SPXW261016P7700', side: 'sell', ratio: 1 },
      { stream_symbol: '.SPXW261016P7650', side: 'buy', ratio: 1 },
    ],
  });
});

// Bots slice 17: a futures ticket names its root, so the server can find its series.
const esPut = { legs: [leg({ strike: 7000, qty: 1, stream_symbol: './EW3X26P7000:XCME' })] };

test('a futures ticket carries its ticker in every request', () => {
  assert.equal(C.reviewRequest(esPut, { accountId: 7, price: 12.5, effect: 'credit', tif: 'gtc', ticker: '/ES' }).ticker, '/ES');
  assert.equal(C.pickerRequest(esPut, 7, '/ES').ticker, '/ES');
  assert.equal(C.botRequest(esPut, { accountId: 7, botId: 3, token: 'x', code: 'c', ticker: '/ES' }).ticker, '/ES');
  assert.equal(C.pickerRequest(condor, 7).ticker, undefined);  // as before for the rest
});

test('a futures bot rests the reviewed limit: its send says so', () => {
  assert.equal(C.botSendLabel('ES put', true), "Rest the limit as ES put's entry");
  assert.match(C.futuresBotNote('12.50 credit · GTC'), /Futures: profit target only, no stop/);
  assert.match(C.futuresBotNote('12.50 credit · GTC'), /rests your limit \(12\.50 credit · GTC\)/);
});

test('costRows: the trade, commission, fees, net, then buying power', () => {
  assert.deepEqual(C.costRows({ value: 300, commission: 1, fees: 1.3, bp_change: -202.3, bp_after: 9797.7 }), [
    ['Trade', '$300.00 credit', ''], ['Commission', '$1.00', ''], ['Fees', '$1.30', ''],
    ['Net', '$297.70 credit', 'strong'], ['Buying power effect', '−$202.30', 'strong'],
    ['Buying power after', '$9,797.70', ''],
  ]);
  const schwab = C.costRows({ value: -120, commission: 1, fees: 1.14, bp_change: null, bp_after: -50 });
  assert.deepEqual(schwab.map(r => r[0]), ['Trade', 'Commission', 'Fees', 'Net', 'Buying power after']);
  assert.equal(schwab[3][1], '$122.14 debit');
  assert.deepEqual(schwab[4], ['Buying power after', '−$50.00', 'bad']);
  assert.deepEqual(C.costRows(null), []);
});

test('reviewBp: a review\'s costs as the strip\'s BP Eff., BP After and Fees (#211)', () => {
  assert.deepEqual(C.reviewBp({ value: 300, commission: 1, fees: 1.3, bp_change: -202.3, bp_after: 9797.7 }),
    { status: 'success', available: true, ok: true, bp_change: -202.3, new_buying_power: 9797.7, fees: 2.3 });
  // Schwab: no buying power change; fees alone
  assert.deepEqual(C.reviewBp({ value: -120, commission: null, fees: 1.14, bp_change: null, bp_after: -50 }),
    { status: 'success', available: true, ok: true, bp_change: null, new_buying_power: -50, fees: 1.14 });
  assert.equal(C.reviewBp({ value: 1, commission: null, fees: null, bp_change: null, bp_after: null }).fees, null);
  assert.equal(C.reviewBp(null), null);
});
