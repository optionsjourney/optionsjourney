// Paste a ThinkorSwim order into the ticket (#312): the order-string parser, ported from
// Tracker2's modeler_tos_parse.js, and tosTray(), which matches its legs to the chain.
// Pure, no DOM, so node:test covers it (tests/web/static/js/trade/tos_parse.test.js);
// trade_page.js owns the paste box.
//
// GRAMMAR (every rule from real ThinkorSwim output)
//   <BUY|BOT|SELL|SOLD> <±qty> [ratios] [STRATEGY] <TICKER> <mult> [(Weeklys)] <dates> <strikes> <rights> @<price> [LMT]
// BOT/SOLD are filled orders: same grammar, no trailing LMT.
//
// For every named strategy the FIRST item of each pair takes the head's direction and the
// second the opposite: SELL -1 VERTICAL ... 7705/7690 PUT is short 7705, long 7690. CUSTOM's
// signed ratios are relative to the head too (a leg is long when its sign matches the head),
// and only CUSTOM carries unequal quantities.
//
// The @price is signed relative to the head: under BUY positive = debit, negative = credit,
// under SELL the reverse. `unit` is (buy ? -1 : 1) * price: positive = credit.
import * as T from './chain_tray.js';

const MONTHS = { JAN: 1, FEB: 2, MAR: 3, APR: 4, MAY: 5, JUN: 6, JUL: 7, AUG: 8, SEP: 9, OCT: 10, NOV: 11, DEC: 12 };

const DATE_RE = '\\d{1,2}\\s+[A-Za-z]{3}\\s+\\d{2,4}';

const LINE_RE = new RegExp(
  '^\\s*(BUY|BOT|SELL|SOLD)\\s+([+-]?\\d+)\\s+' + // 1 head, 2 signed qty
  '(.*?)\\s*' +                                    // 3 ratios and/or strategy name
  '([A-Za-z][A-Za-z.]{0,5}|\\/[A-Za-z][A-Za-z0-9]{0,3})\\s+(\\d+)\\b' + // 4 underlying or futures root, 5 contract size
  '\\s*(?:\\([^)]*\\))?\\s+' +                     // (Weeklys) / (Quarterlys), ignored
  '(' + DATE_RE + '(?:\\s*/\\s*' + DATE_RE + ')*)\\s+' +   // 6 dates
  '([\\d.]+(?:\\s*/\\s*[\\d.]+)*)\\s+' +           // 7 strikes
  '((?:CALL|PUT)(?:\\s*/\\s*(?:CALL|PUT))*)\\s+' + // 8 rights
  '@\\s*([-+]?[\\d.]+)\\s*(?:LMT|LIMIT)?\\s*$',     // 9 price (optionally signed)
  'i',
);

const RATIOS_RE = /^([+-]?\d+(?:\s*\/\s*[+-]?\d+)+)\s*(.*)$/;

const splitList = s => String(s).split('/').map(x => x.trim()).filter(Boolean);

// "10 AUG 26" -> "2026-08-10".
function toIso(d) {
  const m = /^(\d{1,2})\s+([A-Za-z]{3})\s+(\d{2,4})$/.exec(String(d).trim());
  if (!m) return null;
  const mon = MONTHS[m[2].toUpperCase()];
  if (!mon) return null;
  const yr = m[3].length <= 2 ? 2000 + Number(m[3]) : Number(m[3]);
  return `${yr}-${String(mon).padStart(2, '0')}-${String(Number(m[1])).padStart(2, '0')}`;
}

const leg = (ls, type, strike, expiry, ratio = 1) => ({ ls, type, strike, expiry, ratio });

// One order line -> { ticker, qty, side, unit, legs[], strategy, multiplier }, or null.
export function parseTosOrder(line) {
  const m = LINE_RE.exec(String(line || '').replace(/\s+/g, ' ').trim());
  if (!m) return null;

  const head = m[1].toUpperCase();
  const buy = head === 'BUY' || head === 'BOT';
  const qty = Math.max(1, Math.abs(parseInt(m[2], 10) || 1));

  let ratios = null;
  let strategy = m[3].trim().toUpperCase();
  const rm = RATIOS_RE.exec(strategy);
  if (rm) { ratios = splitList(rm[1]).map(Number); strategy = rm[2].trim(); }

  const ticker = m[4].toUpperCase();
  // 100 for every equity line; any positive size after a futures root (/ES 50).
  const multiplier = parseInt(m[5], 10);
  if (ticker.startsWith('/') ? !(multiplier > 0) : multiplier !== 100) return null;
  const dates = splitList(m[6]).map(toIso);
  const strikes = splitList(m[7]).map(Number);
  const rights = splitList(m[8]).map(r => (r.toUpperCase() === 'PUT' ? 'put' : 'call'));
  const signedPrice = Number(m[9]);

  if (dates.some(d => !d) || strikes.some(k => !(k > 0)) || !isFinite(signedPrice)) return null;

  const unit = (buy ? -1 : 1) * signedPrice;
  const lead = buy ? 'long' : 'short';
  const opp = buy ? 'short' : 'long';
  const D = i => dates[Math.min(i, dates.length - 1)];
  const R = i => rights[Math.min(i, rights.length - 1)];

  let legs = null;

  if (strategy === 'CUSTOM') {
    const n = Math.max(strikes.length, dates.length, rights.length, ratios ? ratios.length : 0);
    if (!ratios || ratios.length !== n || strikes.length !== n || dates.length !== n || rights.length !== n) return null;
    legs = strikes.map((k, i) =>
      leg(((ratios[i] > 0) === buy) ? 'long' : 'short', rights[i], k, dates[i], Math.abs(ratios[i]) || 1));
  } else if (!strategy && strikes.length === 1) {
    legs = [leg(lead, R(0), strikes[0], D(0))];
  } else if ((strategy === 'STRADDLE' || strategy === 'STRANGLE') && dates.length === 1 && rights.length === 2) {
    // Both legs take the head's direction; strikes[i] pairs with rights[i].
    legs = rights.map((r, i) => leg(lead, r, strikes[Math.min(i, strikes.length - 1)], D(0)));
  } else if (strategy === 'VERTICAL' && strikes.length === 2 && dates.length === 1) {
    legs = [leg(lead, R(0), strikes[0], D(0)), leg(opp, R(0), strikes[1], D(0))];
  } else if (strategy === 'CALENDAR' && strikes.length === 1 && dates.length === 2) {
    legs = [leg(lead, R(0), strikes[0], dates[0]), leg(opp, R(0), strikes[0], dates[1])];
  } else if (strategy === 'DIAGONAL' && strikes.length === 2 && dates.length === 2) {
    legs = [leg(lead, R(0), strikes[0], dates[0]), leg(opp, R(0), strikes[1], dates[1])];
  } else if (strategy === 'BUTTERFLY' && strikes.length === 3 && dates.length === 1) {
    // Wings take the head's direction, the body the opposite, at ratio 2.
    legs = [leg(lead, R(0), strikes[0], D(0)),
            leg(opp, R(0), strikes[1], D(0), 2),
            leg(lead, R(0), strikes[2], D(0))];
  } else if (strategy === 'CONDOR' && strikes.length === 4 && dates.length === 1) {
    legs = [leg(lead, R(0), strikes[0], D(0)), leg(opp, R(0), strikes[1], D(0)),
            leg(opp, R(0), strikes[2], D(0)), leg(lead, R(0), strikes[3], D(0))];
  } else if (strategy === 'IRON CONDOR' && strikes.length === 4 && dates.length === 1) {
    // Two call strikes then two put strikes; within each pair the first is the acting leg.
    legs = [leg(lead, 'call', strikes[0], D(0)), leg(opp, 'call', strikes[1], D(0)),
            leg(lead, 'put', strikes[2], D(0)), leg(opp, 'put', strikes[3], D(0))];
  } else if ((strategy === 'DBL DIAG' || strategy === 'DOUBLE DIAGONAL') && strikes.length === 4 && dates.length === 2) {
    // Back expiry first as callK/putK, then the front; the back legs take the head's direction.
    const [back, front] = dates;
    legs = [leg(lead, 'call', strikes[0], back), leg(lead, 'put', strikes[1], back),
            leg(opp, 'call', strikes[2], front), leg(opp, 'put', strikes[3], front)];
  }

  if (!legs || !legs.length) return null;
  return { ticker, qty, side: buy ? 'buy' : 'sell', unit, legs, strategy: strategy || 'SINGLE', multiplier };
}

const ORDER_ISH = /^\s*(?:BUY|BOT|SELL|SOLD)\s+[+-]?\d+\b/i;

export const FUTURES = "Futures orders can't be pasted yet. Pick the legs from the chain instead.";

// Why a line that opens like an order was refused, or null when there is nothing specific
// to say. AM-settled legs are refused on purpose: SPX's AM monthly shares its date with the
// PM weekly, and the chain here is the weekly (SPXW), so taking it would trade the wrong contract.
export function tosRejectReason(line) {
  const s = String(line || '').replace(/\s+/g, ' ').trim();
  if (!ORDER_ISH.test(s) || parseTosOrder(s)) return null;
  if (/\[\s*AM\s*\]/i.test(s)) {
    return 'That order has an AM-settled leg (ThinkorSwim marks it [AM]). The Trade page only '
      + 'trades the PM-settled weekly on that date, so build this one from the chain.';
  }
  if (/^\s*(?:BUY|BOT|SELL|SOLD)\s+[+-]?\d+\s+(?:\S+\s+)*?\/[A-Za-z]/i.test(s)) return FUTURES;
  return null;
}

// The paste's first order, how many lines after it were left out, and the reason when
// nothing parsed: { order, extra, error }.
export function firstTosOrder(text) {
  const lines = String(text || '').split(/[\r\n]+/).map(l => l.trim()).filter(Boolean);
  if (!lines.length) return { order: null, extra: 0, error: 'Paste a ThinkorSwim order first.' };
  const i = lines.findIndex(l => parseTosOrder(l));
  if (i < 0) {
    return { order: null, extra: 0, error: lines.map(tosRejectReason).find(Boolean)
      || "That isn't a ThinkorSwim order. In ThinkorSwim, right-click the order, copy it, and paste it here." };
  }
  const order = parseTosOrder(lines[i]);
  if (order.ticker.startsWith('/')) return { order: null, extra: 0, error: FUTURES };
  return { order, extra: lines.length - 1 - i, error: null };
}

const legLabel = l => `${l.expiry} ${l.strike} ${l.type}`;

// The order's legs as chain cells in a tray, given each expiration's chain rows
// ({ '2026-11-13': rows }): { tray, price, effect } or { error }. A leg the chain doesn't
// list is named, never guessed. price/effect are the ticket's limit (null when the order said 0).
export function tosTray(order, rowsByExp) {
  if (order.legs.length > T.MAX_TRAY_LEGS) return { error: `The ticket holds at most ${T.MAX_TRAY_LEGS} legs.` };
  const seen = new Set(), missing = [];
  let tray = T.emptyTray();
  for (const l of order.legs) {
    const qty = order.qty * l.ratio;
    if (qty > 1000) return { error: 'That order is larger than the ticket allows (1,000 per leg).' };
    const key = T.legKey({ expiration: l.expiry, strike: l.strike, type: l.type });
    if (seen.has(key)) return { error: `${legLabel(l)} is in the order twice. Build this one from the chain.` };
    seen.add(key);
    const row = (rowsByExp[l.expiry] || []).find(r => Number(r.strike) === l.strike);
    const s = row && row[l.type];
    if (!s || !s.stream_symbol) { missing.push(legLabel(l)); continue; }
    tray = T.toggleCell(tray, { expiration: l.expiry, strike: row.strike, type: l.type, side: l.ls === 'long' ? 'buy' : 'sell',
      bid: s.bid, ask: s.ask, stream_symbol: s.stream_symbol, delta: s.delta });
    tray = T.setQty(tray, T.legKey({ expiration: l.expiry, strike: row.strike, type: l.type }), qty);
  }
  // Part of a spread is a different trade: load all of it or none.
  if (missing.length) return { error: `The chain doesn't list ${missing.join(', ')}. Nothing was loaded.` };
  const price = Math.abs(order.unit) > 0 ? Math.abs(order.unit) : null;
  return { tray, price, effect: price == null ? null : order.unit < 0 ? 'debit' : 'credit' };
}
