// Tracker-owned (not vendored): pure helpers for the Trade page's ticket (from Tracker2's analyzer Trade window).
// No DOM, so node:test covers it (tests/web/static/js/trade/trade_ticket.test.js).
// Spec: docs/superpowers/specs/2026-09-27-analyzer-bot-trade-design.md
import { legMid, netMid } from './chain_tray.js';

const r2 = n => Math.round(n * 100) / 100;
const gcd = (a, b) => (b ? gcd(b, a % b) : a);

export function newToken(rand = a => crypto.getRandomValues(a)) {
  return Array.from(rand(new Uint8Array(6)), b => b.toString(16).padStart(2, '0')).join('');
}

function wireLegs(tray) {
  return tray.legs.map(l => ({ expiration: l.expiration, strike: l.strike, type: l.type,
    side: l.side, qty: l.qty, mid: r2(legMid(l)) }));
}

export function tradeBotsRequest(tray, accountId, ticker) {
  return { account_id: accountId, legs: wireLegs(tray), ...(ticker ? { ticker } : {}) };
}

// Futures tick sizes: ticks is [{threshold, value}] (threshold null = the last tier).
// The tick for a price is the first row whose threshold exceeds |price|; default 0.01.
export function tickAt(ticks, price) {
  const p = Math.abs(Number(price) || 0);
  for (const t of ticks || []) if (t.threshold == null || p < Number(t.threshold)) return Number(t.value);
  return ticks && ticks.length ? Number(ticks[ticks.length - 1].value) : 0.01;
}

// Same rounding as the server: a credit rounds DOWN to the tick, a debit UP.
export function snapPrice(price, ticks, effect) {
  const tick = tickAt(ticks, price);
  const n = Math.abs(Number(price) || 0) / tick;
  const steps = effect === 'debit' ? Math.ceil(n - 1e-9) : Math.floor(n + 1e-9);
  return Math.round(steps * tick * 100) / 100;
}

export function defaultLimit(tray) {
  const unit = tray.legs.reduce((g, l) => gcd(g, l.qty), 0) || 1;
  const net = tray.legs.reduce((s, l) => s + legMid(l) * l.qty * (l.side === 'sell' ? 1 : -1), 0);
  return r2(Math.abs(net / unit));
}

// Which way a Limit goes when nothing says otherwise: the tray's mid net (0 = credit,
// as the server rounds it).
export function defaultEffect(tray) {
  return netMid(tray) < 0 ? 'debit' : 'credit';
}

// "Place only": the account's hidden no-exits strategy, offered after every match.
export const PLACE_ONLY = 'place-only';
const PLACE_ONLY_ROW = { id: PLACE_ONLY, name: 'Place only — no exits', mode: 'live', place_only: true,
  exits: ['Places the order; no stop or target — you manage it'], chase: null };

export function withPlaceOnly(bots) {
  return [...bots, PLACE_ONLY_ROW];
}

// The only matching strategy; Place only when nothing matches; else let the user pick.
export function defaultBotId(bots) {
  const real = bots.filter(b => b.id !== PLACE_ONLY);
  if (real.length === 1) return real[0].id;
  return real.length ? null : PLACE_ONLY;
}

const OVERNIGHT_ROOTS = new Set(['SPX', 'SPXW', 'XSP', 'VIX', 'VIXW', 'RUT', 'RUTW']);
export const overnightEligible = ticker => OVERNIGHT_ROOTS.has(String(ticker || '').toUpperCase());
// The Trade window offers GTC Overnight only where it can be sent: an index root on a
// non-Schwab account (Schwab silently gets GTC). An unknown broker still shows it --
// the server falls back to GTC safely.
export const overnightOffered = (ticker, broker) => overnightEligible(ticker) && broker !== 'SCHWAB';
// The profit target's starting choice: the strategy's own exit_time_in_force.
export const defaultExitTif = bot => (bot && bot.exit_time_in_force === 'GTC_OVERNIGHT' ? 'GTC_OVERNIGHT' : 'GTC');

// effect: 'credit' or 'debit' -- the Limit's direction, sent with it (a Chase prices off mids).
// entryTif (Limit only) / exitTif (not Place only): 'GTC' | 'GTC_OVERNIGHT'.
export function tradeRequest({ tray, accountId, ticker, botId, mode, price, effect, token, live, entryTif, exitTif }) {
  const placeOnly = botId === PLACE_ONLY;
  const body = { ...(placeOnly ? { place_only: true } : { bot_id: botId }),
    account_id: accountId, ticker, legs: wireLegs(tray),
    entry: mode === 'limit' ? { mode, price, ...(effect ? { effect } : {}), ...(entryTif ? { tif: entryTif } : {}) } : { mode }, token };
  if (!placeOnly && exitTif) body.exit_tif = exitTif;
  if (live) body.confirm = 'TRADE';
  return body;
}

// demo: the page is the demo's (data-demo on #trade-ticket): its words never say real.
export function sendLabel(bot, demo = false) {
  if (!(bot && bot.mode === 'live')) return 'Send dry run';
  return demo ? 'Send order (demo)' : 'Send LIVE order';
}

export function sendWarning(demo = false) {
  return demo ? "This sends an order to the demo's pretend broker. Nothing here is real money."
    : 'This sends a live order to your broker.';
}

export function acceptedWords(demo = false) {
  return demo ? "The demo's pretend broker accepted it." : 'Your broker accepted it.';
}

const UNKNOWN = 'Order state unknown — check the broker';
const unknownAnswer = (res, http) => !res || !(http > 0) || http >= 500;

// Send may be pressed again only when nothing can have been placed: a 400
// (every refusal returns before the run is claimed) or a guard skip other
// than 'duplicate'. No answer or a 5xx may come after an order went out, so
// it is an unknown state, never a retry. The token is reused regardless.
export function canResend(res, http) {
  if (unknownAnswer(res, http)) return false;
  if (res.status !== 'success') return http >= 400 && http < 500;
  const r = res.result || {};
  return r.status === 'skipped' && r.skip_reason !== 'duplicate';
}

// http: the response's HTTP status (0 when there was no answer). Omitted,
// only the body is judged (the cancel call's result).
// placeOnly: a Place only order, which nothing protects.
export function resultView(res, http = 200, placeOnly = false) {
  if (unknownAnswer(res, http)) return { tone: 'error', text: UNKNOWN, canCancel: false, runId: null };
  if (res.status !== 'success') return { tone: 'error', text: res.message || "Couldn't send this trade", canCancel: false, runId: null };
  const r = res.result || {};
  const view = (tone, text, canCancel = false) => ({ tone, text, canCancel, runId: r.run_id ?? null });
  switch (r.status) {
    case 'working': return view('ok', `Working at ${r.price} — ${placeOnly ? 'no exits will be placed' : 'the strategy protects it when it fills'}`, true);
    case 'filled': return view('ok', `Filled at ${r.fill_price} — ${placeOnly ? 'no exits placed' : 'exits placed by the strategy'}`);
    case 'partial': return view('ok', `Partly filled — ${placeOnly ? 'no exits placed' : 'filled contracts are protected'}`);
    case 'dry_run': return view('muted', 'Dry run recorded — nothing was sent');
    case 'cancelled': return view('muted', 'Cancelled');
    case 'short': return view('error', `Partly filled — the rest was given up${placeOnly ? '' : '; filled contracts are protected'}`);
    case 'missed': return view('error', 'Not filled — the chase ran out');
    case 'skipped': return view('muted', `Not sent — ${r.detail || r.skip_reason || 'refused'}`);
    case 'running': return view('error', 'Order state unknown — check the broker now');
    default: return view('error', (r.errors && r.errors.join('; ')) || r.error || r.detail || 'Order failed');
  }
}
