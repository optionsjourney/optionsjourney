// Tracker-owned (not vendored -- VENDORED.md patch 16): the Option chain tray's stats strip
// -- Delta, POP, P50, Max Profit, Max Loss -- from the tray's legs at their mids (the same
// price the BP preview uses). Pure, no DOM
// (tests/web/static/js/trade/chain_stats.test.js); chain_panel.js renders it.
import { bsm, impliedVol, lnProbBelow, expiryMs } from '../modeler/pricing.js';
import { legMid } from './chain_tray.js';
import { TRADE_TIPS } from './trade_tips.js';

const MULT = 100;   // equity options; a futures tray passes its contract's (opts.mult)
const P50_PATHS = 2000;
const P50_MAX_STEPS = 60;
const YEAR_MS = 365 * 864e5;

const sideSign = l => (l.side === 'sell' ? 1 : -1);          // premium collected (+) or paid (-)
const intrinsic = (l, S) => (l.type === 'call' ? Math.max(0, S - l.strike) : Math.max(0, l.strike - S));
const cents = x => (Number.isFinite(x) ? Math.round(x * 100) / 100 : x);

// P&L at expiry if the underlying finishes at S.
function expiryPnl(legs, S, mult) {
  return legs.reduce((a, l) => a + sideSign(l) * (legMid(l) - intrinsic(l, S)) * l.qty * mult, 0);
}

// P&L before expiry: each leg re-priced by Black-Scholes at one flat vol.
// r=0, so this is also Black-76 for a futures option.
function modelPnl(legs, S, T, sigma, mult) {
  return legs.reduce((a, l) => a + sideSign(l) * (legMid(l) - bsm(S, l.strike, T, 0, sigma, l.type === 'call')) * l.qty * mult, 0);
}

function positionDelta(legs, mult) {
  if (legs.some(l => typeof l.delta !== 'number' || !Number.isFinite(l.delta))) return null;
  return legs.reduce((a, l) => a - sideSign(l) * l.delta * l.qty * mult, 0);
}

// One vol for the underlying: the quantity-weighted IV of the sold legs, implied from their
// mids (the bought legs when nothing is sold). null when no leg's mid yields a vol.
function trayVol(legs, spot, T) {
  const sold = legs.filter(l => l.side === 'sell');
  let w = 0, sum = 0;
  for (const l of sold.length ? sold : legs) {
    const iv = impliedVol(spot, l.strike, T, 0, legMid(l), l.type === 'call');
    if (iv > 0) { sum += iv * l.qty; w += l.qty; }
  }
  return w ? sum / w : null;
}

// Expiry payoff is piecewise linear with kinks at the strikes; beyond the top strike its
// slope is set by the calls alone.
function payoffShape(legs, mult) {
  const xs = [0, ...[...new Set(legs.map(l => l.strike))].sort((a, b) => a - b)];
  const ys = xs.map(x => expiryPnl(legs, x, mult));
  const tailSlope = legs.reduce((a, l) => a + (l.type === 'call' ? -sideSign(l) * l.qty * mult : 0), 0);
  return { xs, ys, tailSlope };
}

// Probability the expiry P&L is above zero: the lognormal mass of every interval where it is.
function probabilityOfProfit({ xs, ys, tailSlope }, spot, sigma, T) {
  const F = x => (x <= 0 ? 0 : x === Infinity ? 1 : lnProbBelow(x, spot, sigma, T));
  const mass = (a, b) => Math.max(0, F(b) - F(a));
  let p = 0;
  for (let i = 0; i + 1 < xs.length; i++) {
    const [x0, x1, y0, y1] = [xs[i], xs[i + 1], ys[i], ys[i + 1]];
    if (y0 > 0 && y1 > 0) p += mass(x0, x1);
    else if (y0 > 0 || y1 > 0) {
      const xz = x0 + (0 - y0) * (x1 - x0) / (y1 - y0);
      p += y0 > 0 ? mass(x0, xz) : mass(xz, x1);
    }
  }
  const xe = xs[xs.length - 1], ye = ys[ys.length - 1];
  if (ye > 0) p += tailSlope >= 0 ? mass(xe, Infinity) : mass(xe, xe + ye / -tailSlope);
  else if (tailSlope > 0) p += mass(xe - ye / tailSlope, Infinity);
  return p;
}

// Seeded PRNG (mulberry32) so P50 is the same number every time the tray is the same.
function rng(seed) {
  return () => {
    seed = (seed + 0x6D2B79F5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// Share of simulated paths on which the position is worth +50% of max profit at any step
// up to expiry (TastyTrade's P50 idea; a model, so expect a few points' difference).
function p50(legs, spot, sigma, T, maxProfit, mult) {
  const target = 0.5 * maxProfit;
  const steps = Math.min(P50_MAX_STEPS, Math.max(1, Math.round(T * 365)));
  const dt = T / steps, drift = -0.5 * sigma * sigma * dt, vol = sigma * Math.sqrt(dt);
  const rand = rng(1);
  let hits = 0;
  for (let p = 0; p < P50_PATHS; p++) {
    let S = spot;
    for (let i = 1; i <= steps; i++) {
      const z = Math.sqrt(-2 * Math.log(1 - rand())) * Math.cos(2 * Math.PI * rand());
      S *= Math.exp(drift + vol * z);
      if (modelPnl(legs, S, T - i * dt, sigma, mult) >= target) { hits++; break; }
    }
  }
  return hits / P50_PATHS;
}

// legs: the tray's legs ({expiration, strike, type, side, qty, bid, ask, delta}).
// opts: {spot, now = Date.now(), sigma (tests pin it; otherwise implied from the mids),
// mult (the contract multiplier: 100, or a futures series' own, /ES 50)}.
export function trayStats(legs, { spot, now = Date.now(), sigma, mult = MULT } = {}) {
  if (!legs || !legs.length) return null;
  const out = { delta: positionDelta(legs, mult), maxProfit: null, maxLoss: null, pop: null, p50: null,
    sigma: null, note: null };
  if (new Set(legs.map(l => l.expiration)).size > 1) { out.note = 'one expiration only'; return out; }

  const shape = payoffShape(legs, mult);
  out.maxProfit = shape.tailSlope > 0 ? Infinity : cents(Math.max(...shape.ys));
  out.maxLoss = shape.tailSlope < 0 ? -Infinity : cents(Math.min(...shape.ys));

  const T = (expiryMs(legs[0].expiration) - now) / YEAR_MS;
  if (!(spot > 0) || !(T > 0)) return out;
  const vol = sigma > 0 ? sigma : trayVol(legs, spot, T);
  if (!(vol > 0)) return out;
  out.sigma = vol;
  out.pop = probabilityOfProfit(shape, spot, vol, T);
  if (Number.isFinite(out.maxProfit) && out.maxProfit > 0) out.p50 = p50(legs, spot, vol, T, out.maxProfit, mult);
  return out;
}

const money = x => x.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const pct = x => `${Math.round(x * 100)}%`;
const DASH = '—';

// The strip's cells, TastyTrade-ticket style. bp: the latest /api/trade/bp-preview
// response, 'loading' while one is in flight, or null.
export function stripItems(stats, bp) {
  const s = stats || {};
  const note = s.note || null;
  const money2 = x => (x === Infinity || x === -Infinity ? 'Unlimited' : x == null ? DASH : money(x));
  let bpValue = DASH;
  if (bp === 'loading') bpValue = '…';
  else if (bp && bp.status === 'success' && bp.available === false) bpValue = 'n/a';
  else if (bp && bp.status === 'success' && bp.ok && bp.bp_change != null) bpValue = money(-bp.bp_change);
  const okBp = !!(bp && bp !== 'loading' && bp.status === 'success' && bp.ok);
  return [
    { label: 'Delta', key: 'stripDelta', tip: TRADE_TIPS.stripDelta, value: s.delta == null ? DASH : s.delta.toFixed(2), tone: '', title: null },
    { label: 'POP', key: 'pop', tip: TRADE_TIPS.pop, value: s.pop == null ? DASH : pct(s.pop), tone: '', title: note },
    { label: 'P50', key: 'p50', tip: TRADE_TIPS.p50, value: s.p50 == null ? DASH : pct(s.p50), tone: '', title: note },
    { label: 'Max Profit', key: 'maxProfit', tip: TRADE_TIPS.maxProfit, value: money2(s.maxProfit), tone: 'pos', title: note },
    { label: 'Max Loss', key: 'maxLoss', tip: TRADE_TIPS.maxLoss, value: money2(s.maxLoss), tone: 'neg', title: note },
    { label: 'BP Eff.', key: 'bpEff', tip: TRADE_TIPS.bpEff, value: bpValue, tone: '', title: null },
    { label: 'BP After', key: 'bpAfter', tip: TRADE_TIPS.bpAfter, value: okBp && bp.new_buying_power != null ? money(bp.new_buying_power) : DASH, tone: '', title: null },
    { label: 'Fees', key: 'fees', tip: TRADE_TIPS.fees, value: okBp && bp.fees != null ? money(Math.abs(bp.fees)) : DASH, tone: '', title: null },
  ];
}
