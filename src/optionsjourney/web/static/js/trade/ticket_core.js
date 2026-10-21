// The Trade page's ticket, without the DOM: the review request, the risk graph's legs and the
// spread alert's contracts, built from the chain's tray (chain_tray.js). node:test covers it.
import { legMid } from './chain_tray.js';
import { defaultLimit, defaultEffect } from './trade_ticket.js';

const gcd = (a, b) => (b ? gcd(b, a % b) : a);
const r2 = n => Math.round(n * 100) / 100;

// Units of the order: Tracker2's rule (trade_ticket.defaultLimit), the quantities' gcd.
export function units(tray) {
  return tray.legs.reduce((g, l) => gcd(g, l.qty), 0);
}

// The limit and effect when the user has set neither. A leg with no ask has no mid: the limit is
// null (the box stays empty, you type it) rather than a sum that quietly leaves that leg out.
export function defaultTicket(tray) {
  if (!tray.legs.length) return { limit: null, effect: 'credit' };
  const quoted = tray.legs.every(l => Number(l.ask) > 0);
  return { limit: quoted ? defaultLimit(tray) : null, effect: defaultEffect(tray) };
}

// Bots slice 17: the page's ticker, so the server finds a futures ticket's series (/ES); left
// out when the page has none, as before.
const withTicker = (body, ticker) => (ticker ? { ...body, ticker } : body);

export function reviewRequest(tray, { accountId, price, effect, tif, ticker }) {
  if (!tray.legs.length) return { error: 'Pick a bid (sell) or ask (buy) first.' };
  if (accountId == null) return { error: 'Pick an account.' };
  const p = Number(price);
  if (!(p > 0)) return { error: 'Set a limit price above 0.' };
  const u = units(tray);
  return withTicker({
    account_id: accountId,
    legs: tray.legs.map(l => ({ stream_symbol: l.stream_symbol, side: l.side, ratio: l.qty / u })),
    quantity: u,
    price: r2(effect === 'debit' ? -p : p),
    tif,
  }, ticker);
}

// The risk graph's legs (slice 1's shape), each at its mid; realizedPl shifts the cost
// basis from the mids to the limit, so the graph shows the trade as it would be placed.
export function graphLegs(tray, { limit, effect, mult }) {
  const legs = tray.legs.map(l => {
    const mid = legMid(l);
    return {
      leg_type: l.type === 'put' ? 'PUT' : 'CALL',
      side: l.side === 'sell' ? 'SHORT' : 'LONG',
      strike: l.strike,
      expiration_date: l.expiration,
      entry_price: r2(l.side === 'sell' ? mid : -mid),
      quantity: l.qty,
      multiplier: mult,
      live_price: mid,
      stream_symbol: l.stream_symbol,
    };
  });
  // Basis from the rounded entry prices, so the legs add up to the limit exactly.
  const p = Number(limit);
  const signed = effect === 'debit' ? -p : p;
  const basis = legs.reduce((s, l) => s + l.entry_price * l.quantity, 0);
  const realizedPl = p > 0 ? (signed * units(tray) - basis) * mult : 0;
  return { legs, realizedPl };
}

export function alertContracts(tray) {
  return tray.legs.map(l => ({ expiry: l.expiration, strike: l.strike, right: l.type,
    qty: l.side === 'sell' ? -l.qty : l.qty }));
}

// The Confirm button's words: exactly the summary Review stored with the code.
export function confirmLabel(summary) {
  return `Send: ${summary}`;
}

// The same summary split for the button: one line per leg, then price and time in force.
export function confirmParts(summary) {
  const i = summary.indexOf(' · ');
  const legs = i < 0 ? summary : summary.slice(0, i);
  return { legs: legs.split(' / '), terms: i < 0 ? '' : summary.slice(i + 3) };
}

// The working list's rows. price is the logged limit without its sign, effect says which side
// it is on (null when the order isn't yours): Replace keeps that side.
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

// An option symbol as the Send button writes it ("SPXW 261023C07645000" -> "SPXW 23 Oct 7645C");
// anything else (a stock, a future) as it is.
export function readableSymbol(symbol) {
  const m = /^([A-Z.]+)\s*(\d{2})(\d{2})(\d{2})([CP])(\d+(?:\.\d+)?)$/.exec(symbol || '');
  if (!m) return symbol;
  const [, root, , mm, dd, right, raw] = m;
  const strike = raw.length === 8 && !raw.includes('.') ? Number(raw) / 1000 : Number(raw);
  return `${root} ${Number(dd)} ${MONTHS[Number(mm) - 1]} ${strike}${right}`;
}

export function workingRows(orders) {
  return orders.map(o => ({
    id: o.order_id,
    lines: o.legs.map(l => {
      const qty = l.remaining != null && l.remaining !== l.quantity ? `${l.remaining} of ${l.quantity}` : `${l.quantity}`;
      return `${l.side === 'sell' ? 'Sell' : 'Buy'} ${qty} ${readableSymbol(l.symbol)}`;
    }),
    status: o.status,
    yours: !!o.yours,
    price: o.price == null ? null : Math.abs(o.price),
    effect: o.price == null ? null : o.price < 0 ? 'debit' : 'credit',
    // A partly filled order can only be cancelled: a replace would go out at the full size.
    canReplace: o.legs.every(l => l.remaining == null || l.remaining === l.quantity),
  }));
}

// The signed price for Replace (+ credit, − debit), or null when the typed price isn't usable.
export function replacePrice(row, typed) {
  const p = Number(typed);
  if (typed === '' || !(p > 0) || !row.effect) return null;
  return r2(row.effect === 'debit' ? -p : p);
}

// Bots slice 15, Manage with a bot. The one-time token a bot send is claimed on (Tracker2's
// analyzer_entry.TOKEN: 12 hex characters, made once when the box opens for a reviewed ticket).
export function newToken(bytes) {
  return Array.from(bytes.slice(0, 6), b => b.toString(16).padStart(2, '0')).join('');
}

// The ticket as a bot's entry: its legs and size, the bot, the token and the review's code (the
// server uses the code up, so one review sends one order, by Send or by the bot). The bot's chase
// sets the price, so the limit and time in force aren't sent.
export function botRequest(tray, { accountId, botId, token, code, ticker }) {
  if (!tray.legs.length) return { error: 'Pick a bid (sell) or ask (buy) first.' };
  if (accountId == null) return { error: 'Pick an account.' };
  if (botId == null) return { error: 'Pick a bot.' };
  const u = units(tray);
  return withTicker({
    account_id: accountId,
    bot_id: botId,
    token,
    code,
    quantity: u,
    legs: tray.legs.map(l => ({ stream_symbol: l.stream_symbol, side: l.side, ratio: l.qty / u })),
  }, ticker);
}

// What the bot picker asks about: the ticket's legs and size on its account (POST /api/trade/bots).
export function pickerRequest(tray, accountId, ticker) {
  const u = units(tray);
  return withTicker({ account_id: accountId, quantity: u,
    legs: tray.legs.map(l => ({ stream_symbol: l.stream_symbol, side: l.side, ratio: l.qty / u })) }, ticker);
}

// The picker's two lists: the bots that can take the ticket, and the rest with why not.
export function botLists(bots) {
  const all = bots || [];
  return { offered: all.filter(b => b.problem == null), refused: all.filter(b => b.problem != null) };
}

export function botSendLabel(name, futures = false) {
  return futures ? `Rest the limit as ${name}'s entry` : `Send as ${name}'s entry`;
}

// Bots slice 17 (Tracker2's futures Limit): what a futures bot does with the reviewed ticket.
export function futuresBotNote(summary) {
  return `Futures: profit target only, no stop. The bot rests your limit (${summary}) at `
    + 'TastyTrade as its entry, at any hour, until it fills or you cancel it from the run page. '
    + 'When it fills, the bot rests a profit target on each group there. It uses up this review.';
}

// Manual bots (#158): the Manage with choices under Send, "Place only" (the plain send) first.
export function manageOptions(bots) {
  return [{ value: '', label: 'Place only' },
    ...(bots || []).map(b => ({ value: String(b.id), label: b.name }))];
}

// What Send posts (POST /api/trade/confirm): manual_bot_id only when a manual bot is chosen.
export function confirmRequest(accountId, code, manualBotId) {
  const body = { account_id: accountId, code };
  if (manualBotId != null && manualBotId !== '') body.manual_bot_id = Number(manualBotId);
  return body;
}

// The review's costs (the server's preview_costs) as rows for the ticket: [label, text, tone].
// The trade (credit or debit), commission, fees, what it nets, then buying power: the
// change and what's left. A row the broker didn't give is left out.
const money = n => `$${Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
// A review's costs (POST /api/trade/review) in the shape chain_stats.stripItems reads from
// Tracker2's bp-preview, so the strip's BP Eff., BP After and Fees show what your broker
// said (#211). Fees are commission plus fees. null without costs.
export function reviewBp(costs) {
  if (!costs) return null;
  const fees = costs.commission == null && costs.fees == null ? null
    : Math.round(((costs.commission || 0) + (costs.fees || 0)) * 100) / 100;
  return { status: 'success', available: true, ok: true, bp_change: costs.bp_change ?? null,
    new_buying_power: costs.bp_after ?? null, fees };
}

export function costRows(costs) {
  if (!costs) return [];
  const rows = [];
  const flow = n => `${money(n)} ${n >= 0 ? 'credit' : 'debit'}`;
  if (costs.value != null) rows.push(['Trade', flow(costs.value), '']);
  if (costs.commission != null) rows.push(['Commission', money(costs.commission), '']);
  if (costs.fees != null) rows.push(['Fees', money(costs.fees), '']);
  if (costs.value != null && (costs.commission != null || costs.fees != null)) {
    const net = Math.round((costs.value - (costs.commission || 0) - (costs.fees || 0)) * 100) / 100;
    rows.push(['Net', flow(net), 'strong']);
  }
  if (costs.bp_change != null) rows.push(['Buying power effect', `${costs.bp_change < 0 ? '−' : '+'}${money(costs.bp_change)}`, 'strong']);
  if (costs.bp_after != null) rows.push(['Buying power after', `${costs.bp_after < 0 ? '−' : ''}${money(costs.bp_after)}`, costs.bp_after < 0 ? 'bad' : '']);
  return rows;
}
