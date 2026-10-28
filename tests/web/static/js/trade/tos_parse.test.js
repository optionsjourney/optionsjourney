import test from 'node:test';
import assert from 'node:assert/strict';
import { parseTosOrder, tosRejectReason, firstTosOrder, tosTray, FUTURES } from '../../../../../src/optionsjourney/web/static/js/trade/tos_parse.js';

// Parser tests ported from Tracker2 (#312). Every order string is real ThinkorSwim output: the
// leg-direction convention is not stated in the string, so a test built from our own generator
// would confirm a wrong reading of it.

const sig = o => o.legs
  .map(l => `${l.ls[0]}${l.ratio > 1 ? l.ratio : ''}${l.type[0]}${l.strike}@${l.expiry}`)
  .join(' ');

test('short iron condor: inner strikes are sold, outer bought', () => {
  const o = parseTosOrder(
    'SELL -2 IRON CONDOR SPX 100 (Weeklys) 10 AUG 26 7795/7825/7680/7655 CALL/PUT @7.45 LMT');
  assert.equal(o.ticker, 'SPX');
  assert.equal(o.qty, 2);
  assert.equal(o.side, 'sell');
  assert.equal(o.unit, 7.45, 'a sold condor is a CREDIT — positive in the modeler');
  assert.equal(sig(o),
    'sc7795@2026-08-10 lc7825@2026-08-10 sp7680@2026-08-10 lp7655@2026-08-10');
});

test('reverse iron condor: the same shape with every leg flipped by the head', () => {
  const o = parseTosOrder(
    'BUY +1 IRON CONDOR SPX 100 (Weeklys) 10 AUG 26 7780/7790/7710/7700 CALL/PUT @4.51 LMT');
  assert.equal(o.unit, -4.51, 'bought is a DEBIT — negative');
  assert.equal(sig(o),
    'lc7780@2026-08-10 sc7790@2026-08-10 lp7710@2026-08-10 sp7700@2026-08-10');
});

// The pair that pins the whole convention: identical but for BUY/SELL.
test('put verticals: the head alone decides which strike is short', () => {
  const short = parseTosOrder('SELL -1 VERTICAL SPX 100 (Weeklys) 10 AUG 26 7705/7690 PUT @2.80 LMT');
  assert.equal(sig(short), 'sp7705@2026-08-10 lp7690@2026-08-10');
  assert.equal(short.unit, 2.80);

  const long = parseTosOrder('BUY +1 VERTICAL SPX 100 (Weeklys) 10 AUG 26 7705/7690 PUT @2.80 LMT');
  assert.equal(sig(long), 'lp7705@2026-08-10 sp7690@2026-08-10');
  assert.equal(long.unit, -2.80);
});

test('call verticals', () => {
  assert.equal(
    sig(parseTosOrder('SELL -1 VERTICAL SPX 100 (Weeklys) 10 AUG 26 7755/7770 CALL @5.10 LMT')),
    'sc7755@2026-08-10 lc7770@2026-08-10');
  assert.equal(
    sig(parseTosOrder('BUY +1 VERTICAL SPX 100 (Weeklys) 10 AUG 26 7765/7780 CALL @3.85 LMT')),
    'lc7765@2026-08-10 sc7780@2026-08-10');
});

test('calendars: the first date is the leg you buy', () => {
  assert.equal(
    sig(parseTosOrder('BUY +1 CALENDAR SPX 100 (Weeklys) 17 AUG 26/14 AUG 26 7735 PUT @5.55 LMT')),
    'lp7735@2026-08-17 sp7735@2026-08-14');
  assert.equal(
    sig(parseTosOrder('BUY +1 CALENDAR SPX 100 (Weeklys) 17 AUG 26/14 AUG 26 7765 CALL @5.35 LMT')),
    'lc7765@2026-08-17 sc7765@2026-08-14');
});

test('double calendar arrives as DBL DIAG with repeated strikes', () => {
  const o = parseTosOrder(
    'BUY +1 DBL DIAG SPX 100 (Weeklys) 17 AUG 26/14 AUG 26 7765/7715/7765/7715 CALL/PUT/CALL/PUT @10.80 LMT');
  assert.equal(sig(o),
    'lc7765@2026-08-17 lp7715@2026-08-17 sc7765@2026-08-14 sp7715@2026-08-14');
  assert.equal(o.unit, -10.80);
});

test('CUSTOM carries per-leg quantities, so a 3:1 CREEN survives the round trip', () => {
  const o = parseTosOrder(
    'BUY +1 3/1/-3/-1 CUSTOM SPX 100 (Weeklys) 10 AUG 26/10 AUG 26/7 AUG 26/7 AUG 26 '
    + '7745/7705/7740/7720 CALL/PUT/CALL/PUT @46.50 LMT');
  assert.equal(o.legs.length, 4);
  assert.equal(sig(o),
    'l3c7745@2026-08-10 lp7705@2026-08-10 s3c7740@2026-08-07 sp7720@2026-08-07');
  assert.equal(o.unit, -46.50);
  // The ratio is the point: 3 calls to 1 put, which no named strategy can express.
  assert.deepEqual(o.legs.map(l => l.ratio), [3, 1, 3, 1]);
});

// A real SELL-headed CUSTOM from ThinkorSwim: its ratio signs are relative to the head,
// so under SELL a POSITIVE ratio is SHORT. Reading the sign as absolute inverted every leg
// — a sold ratio iron condor (short 7835c/7780p, long 7925c/7660p, net credit) came in as
// its mirror (long the body, a debit). Head-relative signing fixes it.
test('a SELL-headed CUSTOM signs each leg off the head, not absolutely', () => {
  const o = parseTosOrder(
    'SELL -1 3/-3/2/-2 CUSTOM SPX 100 (Weeklys) 14 AUG 26/14 AUG 26/14 AUG 26/14 AUG 26 '
    + '7835/7925/7780/7660 CALL/CALL/PUT/PUT @13.10 LMT');
  assert.ok(o, 'parses');
  assert.equal(o.side, 'sell');
  assert.equal(sig(o),
    's3c7835@2026-08-14 l3c7925@2026-08-14 s2p7780@2026-08-14 l2p7660@2026-08-14',
    'short the inner call/put, long the outer wings — a sold iron condor, not its mirror');
  assert.equal(o.unit, 13.10, 'SELL @13.10 is a credit (positive in the modeler)');
  assert.deepEqual(o.legs.map(l => l.ratio), [3, 3, 2, 2]);
});

// A net-credit CUSTOM spread (a broken-wing butterfly) carries a NEGATIVE @price under its
// always-BUY head: that is a credit, not a debit.
test('a signed @-price on an always-BUY CUSTOM credit spread parses as a credit', () => {
  const o = parseTosOrder(
    'BUY +1 1/-2/1 CUSTOM SPX 100 (Weeklys) 19 AUG 26/19 AUG 26/19 AUG 26 7720/7700/7660 PUT/PUT/PUT @-6.08 LMT');
  assert.ok(o, 'the leading minus must not make the whole line unparseable');
  assert.equal(o.side, 'buy');
  assert.equal(o.unit, 6.08, 'BUY head + negative price = a CREDIT (positive in the modeler)');
  assert.equal(sig(o), 'lp7720@2026-08-19 s2p7700@2026-08-19 lp7660@2026-08-19');
});

test('a single leg needs no strategy name', () => {
  assert.equal(
    sig(parseTosOrder('BUY +1 SPX 100 (Weeklys) 10 AUG 26 7700 CALL @12.30 LMT')),
    'lc7700@2026-08-10');
});

test('butterfly and condor: wings take the head, the body the opposite', () => {
  const fly = parseTosOrder('BUY +1 BUTTERFLY SPX 100 (Weeklys) 10 AUG 26 7700/7750/7800 CALL @3.10 LMT');
  assert.equal(sig(fly), 'lc7700@2026-08-10 s2c7750@2026-08-10 lc7800@2026-08-10');
  const con = parseTosOrder('BUY +1 CONDOR SPX 100 (Weeklys) 10 AUG 26 7700/7725/7775/7800 CALL @4.20 LMT');
  assert.equal(sig(con),
    'lc7700@2026-08-10 sc7725@2026-08-10 sc7775@2026-08-10 lc7800@2026-08-10');
});

test('tolerates the formatting ThinkorSwim varies: missing LMT, no (Weeklys), odd spacing', () => {
  const base = sig(parseTosOrder('BUY +1 VERTICAL SPX 100 (Weeklys) 10 AUG 26 7765/7780 CALL @3.85 LMT'));
  assert.equal(sig(parseTosOrder('BUY +1 VERTICAL SPX 100 10 AUG 26 7765/7780 CALL @3.85')), base);
  assert.equal(sig(parseTosOrder('  buy +1 vertical spx 100 10 AUG 26 7765 / 7780 CALL @ 3.85 LMT  ')), base);
});

// ── BOT/SOLD: the filled-order form ──
// A working order reads BUY/SELL and ends in LMT; once it fills, ThinkorSwim restates it as
// BOT/SOLD with no order type on the end. Same grammar, so the fill parses to the same order.

test('BOT is BUY and SOLD is SELL, with or without the trailing LMT', () => {
  const buy  = parseTosOrder('BUY +5 IRON CONDOR SPX 100 (Weeklys) 11 AUG 26 7780/7790/7710/7700 CALL/PUT @3.25 LMT');
  const bot  = parseTosOrder('BOT +5 IRON CONDOR SPX 100 (Weeklys) 11 AUG 26 7780/7790/7710/7700 CALL/PUT @3.25');
  const sell = parseTosOrder('SELL -5 IRON CONDOR SPX 100 (Weeklys) 11 AUG 26 7755/7770/7735/7720 CALL/PUT @10.15 LMT');
  const sold = parseTosOrder('SOLD -5 IRON CONDOR SPX 100 (Weeklys) 11 AUG 26 7755/7770/7735/7720 CALL/PUT @10.15');

  // Absolute, not just relative to the BUY/SELL baseline: were BOT mapped to the wrong
  // direction, a bare deepEqual against an identically-broken BUY would still pass.
  assert.equal(bot.side, 'buy');
  assert.equal(bot.unit, -3.25, 'a bought condor is a DEBIT — negative in the modeler');
  assert.equal(bot.qty, 5);
  assert.equal(sig(bot), 'lc7780@2026-08-11 sc7790@2026-08-11 lp7710@2026-08-11 sp7700@2026-08-11');

  assert.equal(sold.side, 'sell');
  assert.equal(sold.unit, 10.15, 'a sold condor is a CREDIT — positive');
  assert.equal(sig(sold), 'sc7755@2026-08-11 lc7770@2026-08-11 sp7735@2026-08-11 lp7720@2026-08-11');

  // And nothing else about the parse may differ from the working order it filled from.
  assert.deepEqual(bot, buy);
  assert.deepEqual(sold, sell);
});


test('rejects what it cannot read instead of inventing legs', () => {
  assert.equal(parseTosOrder(''), null);
  assert.equal(parseTosOrder('hello world'), null);
  assert.equal(parseTosOrder('BUY +1 VERTICAL SPX 100 10 AUG 26 7765/7780 CALL'), null, 'no price');
  assert.equal(parseTosOrder('BUY +1 VERTICAL SPX 100 32 XYZ 26 7765/7780 CALL @1 LMT'), null, 'bad month');
  // A shape whose piece counts do not match its strategy must not be guessed at.
  assert.equal(parseTosOrder('BUY +1 VERTICAL SPX 100 10 AUG 26 7765/7780/7800 CALL @1 LMT'), null);
  assert.equal(parseTosOrder('BUY +1 3/1 CUSTOM SPX 100 10 AUG 26 7765/7780 CALL/PUT @1 LMT'), null,
    'CUSTOM needs one date per leg, not one shared date');
});

// An SPX monthly OPEX line, real ThinkorSwim output. The [AM] marks the
// AM-settled monthly, whose payoff is struck at SET on the Friday morning while the
// (Weeklys) leg on the SAME calendar date runs to that afternoon's close. The parser
// cannot read it, and MUST NOT learn to by dropping the token: both legs would then land
// on 2026-09-18 and the calendar would price as two identical legs — a flat zero graph
// presented as an answer. Rejecting it is correct; saying WHY is what these tests pin.
const AM_LINE = 'BUY +3 CALENDAR SPX 100 (Weeklys) 18 SEP 26/18 SEP 26 [AM] 7725 PUT @.40 LMT';

test('an AM-settled leg is rejected, and says so instead of blaming the paste', () => {
  assert.equal(parseTosOrder(AM_LINE), null, 'still unreadable — the reason is separate');
  const why = tosRejectReason(AM_LINE);
  assert.match(why, /AM-settled/, 'names what it found, not "that is not an order"');
});

test('tosRejectReason only speaks up when it recognises the problem', () => {
  assert.equal(tosRejectReason('hello world'), null, 'no idea — the generic message stands');
  assert.equal(tosRejectReason(''), null);
  assert.equal(
    tosRejectReason('BUY +1 VERTICAL SPX 100 (Weeklys) 10 AUG 26 7705/7690 PUT @2.80 LMT'),
    null, 'a line that parses has no reason to reject');
  assert.equal(
    tosRejectReason('the [AM] session was quiet'), null,
    'the token alone is not enough — it has to look like an order');
});


// The (Quarterlys) label, which none of the samples happened to contain.
test('reads the strings the Calendar IV risk graph hands over', () => {
  const dc = parseTosOrder(
    'BUY +3 DBL DIAG SPX 100 (Weeklys) 21 AUG 26/14 AUG 26 7800/7700/7800/7700 CALL/PUT/CALL/PUT @12.40 LMT');
  assert.equal(dc.qty, 3, 'the risk graph lot count becomes the card quantity');
  assert.equal(sig(dc),
    'lc7800@2026-08-21 lp7700@2026-08-21 sc7800@2026-08-14 sp7700@2026-08-14');

  const single = parseTosOrder(
    'BUY +1 CALENDAR SPX 100 (Quarterlys) 18 SEP 26/11 SEP 26 7750 CALL @6.20 LMT');
  assert.ok(single, '(Quarterlys) must be skipped like (Weeklys), not choke the parse');
  assert.equal(sig(single), 'lc7750@2026-09-18 sc7750@2026-09-11');
});

test('STRADDLE / STRANGLE parse to two same-direction legs', () => {
  const strad = parseTosOrder('BUY +1 STRADDLE SPX 100 (Weeklys) 11 AUG 26 7755 CALL/PUT @53.60 LMT');
  assert.equal(sig(strad), 'lc7755@2026-08-11 lp7755@2026-08-11', 'call + put at the one strike, both long');
  assert.equal(strad.unit, -53.60, 'bought straddle is a debit');
  const strang = parseTosOrder('BUY +1 STRANGLE SPX 100 (Weeklys) 11 AUG 26 7775/7740 CALL/PUT @38.15 LMT');
  assert.equal(sig(strang), 'lc7775@2026-08-11 lp7740@2026-08-11', 'call strike then put strike');
  const sold = parseTosOrder('SELL -1 STRADDLE SPX 100 (Weeklys) 11 AUG 26 7755 CALL/PUT @54.00 LMT');
  assert.equal(sold.side, 'sell'); assert.equal(sold.unit, 54.00, 'sold straddle is a credit (positive)');
});

// ── Into the ticket: firstTosOrder and tosTray ──

const side = (bid, ask, sym) => ({ bid, ask, stream_symbol: sym, delta: 0.3 });
const row = (strike, exp) => ({ strike, call: side(1, 1.2, `C${strike}@${exp}`), put: side(2, 2.2, `P${strike}@${exp}`) });
const chain = (exp, ...ks) => ({ [exp]: ks.map(k => row(k, exp)) });
const trayLegs = r => r.tray.legs.map(l => `${l.side} ${l.qty} ${l.type} ${l.strike} ${l.expiration} ${l.stream_symbol}`);

test('a SELL vertical: the first strike is sold, the limit is a credit', () => {
  const o = parseTosOrder('SELL -2 VERTICAL SPX 100 (Weeklys) 13 NOV 26 7600/7590 PUT @1.25 LMT');
  const r = tosTray(o, chain('2026-11-13', 7590, 7600, 7610));
  assert.deepEqual(trayLegs(r), ['sell 2 put 7600 2026-11-13 P7600@2026-11-13', 'buy 2 put 7590 2026-11-13 P7590@2026-11-13']);
  assert.equal(r.price, 1.25);
  assert.equal(r.effect, 'credit');
});

test('a BUY butterfly: the body is twice the wings, the limit a debit', () => {
  const o = parseTosOrder('BUY +1 BUTTERFLY SPY 100 20 NOV 26 600/605/610 CALL @.85 LMT');
  const r = tosTray(o, chain('2026-11-20', 600, 605, 610));
  assert.deepEqual(r.tray.legs.map(l => `${l.side} ${l.qty} ${l.strike}`), ['buy 1 600', 'sell 2 605', 'buy 1 610']);
  assert.equal(r.effect, 'debit');
  assert.equal(r.price, 0.85);
});

test('a SELL CUSTOM signs each leg off the head', () => {
  const o = parseTosOrder('SELL -1 1/-1 CUSTOM SPX 100 13 NOV 26/12 NOV 26 7840/7840 CALL/CALL @3.10 LMT');
  const r = tosTray(o, { ...chain('2026-11-13', 7840), ...chain('2026-11-12', 7840) });
  assert.deepEqual(r.tray.legs.map(l => `${l.side} ${l.expiration}`), ['sell 2026-11-13', 'buy 2026-11-12']);
});

test('a signed @-price is a credit under BUY', () => {
  const o = parseTosOrder('BUY +1 1/-2/1 CUSTOM SPX 100 13 NOV 26/13 NOV 26/13 NOV 26 7500/7550/7620 PUT/PUT/PUT @-.40 LMT');
  const r = tosTray(o, chain('2026-11-13', 7500, 7550, 7620));
  assert.equal(r.effect, 'credit');
  assert.equal(r.price, 0.4);
});

test('an even-money order leaves the limit to the ticket', () => {
  const r = tosTray(parseTosOrder('BUY +1 SPY 100 20 NOV 26 600 CALL @0 LMT'), chain('2026-11-20', 600));
  assert.equal(r.price, null);
  assert.equal(r.effect, null);
});

test('a leg the chain does not list loads nothing and is named', () => {
  const o = parseTosOrder('SELL -1 VERTICAL SPX 100 13 NOV 26 7600/7590 PUT @1.25 LMT');
  const r = tosTray(o, chain('2026-11-13', 7600));
  assert.equal(r.tray, undefined);
  assert.match(r.error, /2026-11-13 7590 put/);
  assert.match(tosTray(o, {}).error, /7600 put, 2026-11-13 7590 put/, 'an expiration the ticker has not got');
});

test('more than the ticket holds is refused, not clipped', () => {
  assert.match(tosTray(parseTosOrder('BUY +600 BUTTERFLY SPY 100 20 NOV 26 600/605/610 CALL @.85 LMT'),
    chain('2026-11-20', 600, 605, 610)).error, /1,000/);
  const legs = Array.from({ length: 13 }, (_, i) => ({ ls: 'long', type: 'call', strike: 600 + i, expiry: '2026-11-20', ratio: 1 }));
  assert.match(tosTray({ qty: 1, unit: -1, legs }, {}).error, /at most 12 legs/);
});

test('the same contract twice is refused rather than cancelling itself out', () => {
  const o = parseTosOrder('BUY +1 1/-1 CUSTOM SPY 100 20 NOV 26/20 NOV 26 600/600 CALL/CALL @.10 LMT');
  assert.match(tosTray(o, chain('2026-11-20', 600)).error, /twice/);
});

test('firstTosOrder takes the first order and counts the lines after it', () => {
  const r = firstTosOrder('\nBUY +1 SPY 100 20 NOV 26 600 CALL @1 LMT\n\nSELL -1 SPY 100 20 NOV 26 610 CALL @1 LMT\n');
  assert.equal(r.order.legs[0].strike, 600);
  assert.equal(r.extra, 1);
  assert.equal(r.error, null);
});

test('firstTosOrder explains what it cannot load', () => {
  assert.match(firstTosOrder('  ').error, /Paste/);
  assert.match(firstTosOrder('hello').error, /isn't a ThinkorSwim order/);
  assert.match(firstTosOrder('BUY +3 CALENDAR SPX 100 (Weeklys) 18 SEP 26/18 SEP 26 [AM] 7725 PUT @.40 LMT').error, /AM-settled/);
  assert.equal(firstTosOrder('SELL -1 /ES 50 30 NOV 26 7000 PUT @37.00 LMT').error, FUTURES);
  assert.equal(firstTosOrder('SELL -1 /ESZ26 1/50 NOV 26 (Wk4) /EW4X26 7000 PUT @37.00 LMT').error, FUTURES);
});
