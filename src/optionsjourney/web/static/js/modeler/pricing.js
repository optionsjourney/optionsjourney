// pricing.js — pure Black-Scholes / payoff-curve engine.
//
// Ported from ntflux/static/js/modeler_math.js (itself ported from
// docs/vendor/navigationstation-index.snapshot.html). This module is PURE:
// no `document`, `window`, `localStorage`, or any DOM access. It must import
// and run under plain Node.

// ── Normal distribution helpers ─────────────────────────────────────────────

// Standard normal CDF (Abramowitz & Stegun 7.1.26 approximation).
export function ncdf(x) {
  const t = 1 / (1 + 0.2316419 * Math.abs(x));
  const poly = t * (0.319381530 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + t * 1.330274429))));
  const v = 1 - Math.exp(-x * x / 2) / Math.sqrt(2 * Math.PI) * poly;
  return x >= 0 ? v : 1 - v;
}

// Standard normal PDF.
export function npdf(x) {
  return Math.exp(-x * x / 2) / 2.5066282746310002;
}

// ── Black-Scholes-Merton pricing ────────────────────────────────────────────

export function bsm(S, K, T, r, sig, isCall) {
  if (T <= 0) return isCall ? Math.max(0, S - K) : Math.max(0, K - S);
  if (sig <= 0) return isCall ? Math.max(0, S * Math.exp(r * T) - K) * Math.exp(-r * T) : Math.max(0, K * Math.exp(-r * T) - S);
  const sq = sig * Math.sqrt(T);
  const d1 = (Math.log(S / K) + (r + sig * sig / 2) * T) / sq, d2 = d1 - sq;
  if (isCall) return S * ncdf(d1) - K * Math.exp(-r * T) * ncdf(d2);
  return K * Math.exp(-r * T) * ncdf(-d2) - S * ncdf(-d1);
}

// Newton's method implied vol solver, seeded with the Brenner-Subrahmanyam approximation.
// Back-solve implied volatility, or null when no volatility reproduces the price.
//
// Newton alone is not enough here and used to fail silently. Its seed is the
// Brenner-Subrahmanyam approximation, which is only valid at the money; for a
// short-dated OTM option it lands far too low, in a region where vega is ~0, so the
// old `if (vega < 1e-10) break` bailed on the FIRST iteration and returned the seed
// as though it were an answer. On a real 3-DTE SPX iron condor that gave 0.0177 for a
// leg whose true vol was 0.0729, and the leg then repriced to $0.00 — three of four
// legs valued at nothing, which is what made the T+0 curve look like a naked short.
//
// So: Newton for speed, then VERIFY. If its answer does not reproduce the price, fall
// back to bisection, which cannot fail — Black-Scholes price is strictly increasing in
// sigma, so the root is always bracketed by [lo, hi]. Anything still unreproducible
// (a crossed or stale quote outside the no-arbitrage band) returns null, and every
// caller treats null as "no live vol here" and falls back.
export function impliedVol(S, K, T, r, price, isCall, iter = 60) {
  if (!(T > 0) || !(price > 0) || !(S > 0) || !(K > 0)) return null;

  // Outside the no-arbitrage band no sigma can match, and bisection would converge to
  // a bound and quietly lie about it.
  const disc = K * Math.exp(-r * T);
  const floor = isCall ? Math.max(0, S - disc) : Math.max(0, disc - S);
  const ceil = isCall ? S : disc;
  if (price <= floor || price >= ceil) return null;

  const TOL = 1e-6;
  const reprices = sig => Math.abs(bsm(S, K, T, r, sig, isCall) - price) < 1e-4;

  let sig = Math.min(Math.max(Math.sqrt(2 * Math.PI / T) * price / S, 0.01), 5.0);
  for (let i = 0; i < iter; i++) {
    const diff = bsm(S, K, T, r, sig, isCall) - price;
    if (Math.abs(diff) < TOL) break;
    const sq = sig * Math.sqrt(T);
    const d1 = (Math.log(S / K) + (r + sig * sig / 2) * T) / sq;
    const vega = S * Math.sqrt(T) * Math.exp(-d1 * d1 / 2) / Math.sqrt(2 * Math.PI);
    if (!(vega > 1e-8)) break;                       // hand it to bisection
    const next = sig - diff / vega;
    if (!isFinite(next) || next <= 0 || next > 5) break;
    const settled = Math.abs(next - sig) < 1e-12;
    sig = next;
    if (settled) break;
  }
  if (reprices(sig)) return sig;

  let lo = 1e-4, hi = 5.0;
  if (bsm(S, K, T, r, hi, isCall) < price) return null;   // beyond the model's reach
  for (let i = 0; i < 200; i++) {
    const mid = (lo + hi) / 2;
    if (bsm(S, K, T, r, mid, isCall) < price) lo = mid; else hi = mid;
    if (hi - lo < 1e-9) break;
  }
  const out = (lo + hi) / 2;
  return reprices(out) ? out : null;
}

// P(S_T < x) under a risk-neutral lognormal with MEAN(S_T)=spot (the +0.5σ²T term below is
// what pins the mean, not the median — the median is spot·exp(-0.5σ²T), strictly below spot
// for any σ,T>0), σ=vol, T=years.
export function lnProbBelow(x, spot, sigma, T) {
  if (!(x > 0) || !(spot > 0) || !(sigma > 0) || !(T > 0)) return null;
  const sq = sigma * Math.sqrt(T);
  return ncdf((Math.log(x / spot) + 0.5 * sigma * sigma * T) / sq);
}

// ── Expiration time ──────────────────────────────────────────────────────

// US options expire at market close = 4:00 PM America/New_York. Return that instant (ms),
// DST-aware (16:00 ET is 20:00 UTC in EDT, 21:00 UTC in EST).
//
// Remembered per date: the timezone conversion is ~60us, and the T+0 curve asks for every leg
// at every price point -- a 56-leg book redid it ~17k times per draw (~1s) on a handful of dates.
const _expiryMsByDate = new Map();
export function expiryMs(dateStr) {
  const d = String(dateStr).slice(0, 10);
  let ms = _expiryMsByDate.get(d);
  if (ms === undefined) {
    const g = new Date(d + 'T16:00:00Z');
    const off = new Date(g.toLocaleString('en-US', { timeZone: 'America/New_York' })) - new Date(g.toLocaleString('en-US', { timeZone: 'UTC' }));
    ms = g.getTime() - off;
    _expiryMsByDate.set(d, ms);
  }
  return ms;
}

// ── Ticker defaults ──────────────────────────────────────────────────────

// Default IV fallback when no live IV is available for a ticker's expiry.
export function defaultIV(ticker) {
  const t = (ticker || '').toUpperCase().replace(/[^A-Z]/g, '');
  if (/^(SPX|SPXW|SPY|XSP)$/.test(t)) return 0.17;
  if (/^(QQQ|NDX|NQ|NQX)$/.test(t)) return 0.20;
  if (/^(RUT|IWM|RUA)$/.test(t)) return 0.20;
  if (/^(VIX|UVXY|VXX|SVXY)$/.test(t)) return 0.80;
  return 0.25;
}

// Strike increment for the ticker. The live chain increment always wins (it's the ground
// truth); otherwise fall back to a known-ticker default (a position's own strikes are
// unreliable — e.g. a hedged IC's strikes can be 15-20 apart even though SPX trades 5-wide),
// then the GCD of the position's strike gaps, then 1.
export function strikeIncrement(ticker, legs, chainIncrement) {
  if (Number.isFinite(chainIncrement) && chainIncrement > 0) return chainIncrement;
  const t = (ticker || '').toUpperCase();
  if (/SPX|XSP|RUT/.test(t)) return 5;
  if (/NDX/.test(t)) return 10;
  if (/SPY|QQQ|IWM/.test(t)) return 1;
  const ks = [...new Set((legs || []).filter(l => l.type !== 'stock').map(l => l.strike).filter(Boolean))].sort((a, b) => a - b);
  const gcd = (a, b) => { a = Math.round(a); b = Math.round(b); while (b) { [a, b] = [b, a % b]; } return a; };
  let g = 0;
  for (let i = 1; i < ks.length; i++) { const d = ks[i] - ks[i - 1]; if (d > 0) g = g ? gcd(g, d) : d; }
  return (g && isFinite(g)) ? g : 1;
}

// ── Payoff curve construction ───────────────────────────────────────────────

const RATE = 0.045;

// Build expiry / T+0 payoff functions (and position greeks) for a modeled position.
//
// pos: {ticker, legs: [{ls:'long'|'short', type:'call'|'put'|'stock', strike, expiry, qty, premium}], net}
// spot: current underlying price, or null
// liveIVs: {'atm|YYYY-MM-DD': ivFraction} or null
// asOfMin: minutes elapsed from now toward front expiry (time slider), 0 = now
// flatIV: a flat IV fraction applied to every leg (IV slider), or null = use each leg's own IV
//
// Returns null when there is nothing to graph (no legs at all).
export function buildPayoffFns(pos, spot, liveIVs, asOfMin, flatIV) {
  const legs = pos.legs || [];
  const optLegs = legs.filter(l => l.type === 'call' || l.type === 'put');
  const stockLegs = legs.filter(l => l.type === 'stock');
  if (!optLegs.length && !stockLegs.length) return null; // nothing to graph
  const strikes = optLegs.map(l => l.strike).filter(Boolean);

  const expiries = [...new Set(optLegs.map(l => (l.expiry || '').slice(0, 10)).filter(Boolean))].sort();
  const frontExpiry = expiries[0] || '';
  const isMultiExpiry = expiries.length > 1;
  const backExpiry = isMultiExpiry ? expiries[expiries.length - 1] : '';

  // ── Default view width ──────────────────────────────────────────────────────
  // Scaled to the front expiry's expected move, NOT to a fixed percentage of spot.
  //
  // A fixed percentage cannot work across strategies. 12% of SPX is ±915 points, which
  // renders a 50-point iron condor as a vertical spike filling ~3% of the graph. An
  // SD-scaled window tracks BOTH implied volatility and time to expiry, so a 1-DTE
  // condor and a 45-DTE one each fill a similar share of the width with no per-strategy
  // special-casing: measured 25-29% across condors, butterflies and double calendars
  // from 0DTE to 45DTE.
  //
  // 1.5 SD covers an ~87% probability range — tight enough for the position to be
  // readable, wide enough that a short position's loss zone stays on screen. Axis-drag
  // zoom is always available for a closer or wider look.
  const SD_MULTIPLE = 1.5;

  // Deliberately uses the MODEL IV and time-to-expiry from NOW, ignoring the IV and Time
  // sliders. buildPayoffFns re-runs on every slider tick, so keying the window off the
  // slider values would make the whole graph rescale continuously while dragging.
  const sdHalfRange = (S) => {
    if (!(S > 0) || !frontExpiry) return 0;
    const T = (expiryMs(frontExpiry) - Date.now()) / (365 * 24 * 60 * 60 * 1000);
    if (!(T > 0)) return 0;                       // at or past expiry — the floor takes over
    const atm = liveIVs && liveIVs[`atm|${frontExpiry}`];
    const iv = atm > 0.01 ? atm : defaultIV(pos.ticker);
    return S * iv * Math.sqrt(T) * SD_MULTIPLE;
  };

  const strikeSpan = strikes.length ? Math.max(...strikes) - Math.min(...strikes) : 0;

  let center, halfRange;
  if (spot) {
    center = spot;
    halfRange = Math.max(
      sdHalfRange(spot),  // primary rule
      strikeSpan * 0.6,   // safety net: a position wider than 1.5 SD must still fit
      spot * 0.004,       // floor: at expiry SD collapses to 0 and the window would vanish
    );
  } else if (strikes.length) {
    center = (Math.max(...strikes) + Math.min(...strikes)) / 2;
    halfRange = Math.max(sdHalfRange(center), strikeSpan * 0.6, center * 0.004);
  } else if (stockLegs.length) {
    center = stockLegs[0].premium || 100;
    halfRange = center * 0.3;
  } else {
    return null;
  }

  let autoSmin = Math.max(0.01, center - halfRange);
  let autoSmax = center + halfRange;
  // Never let a strike sit off-screen. Reachable when the position reaches beyond 1.5 SD
  // — far-OTM wings, or a long-dated leg whose strikes sit well away from spot.
  if (strikes.length) {
    // A multi-expiry position (calendar/diagonal) needs a different padding rule than a
    // single-expiry one. Its T+0 curve doesn't hug the intrinsic V-shape near the strikes —
    // the back leg is still BSM-priced there, and the curve's peak/turning point routinely
    // sits well beyond the strikes themselves, at a distance driven by the BACK expiry's own
    // remaining time value, not by how far apart the two strikes happen to be. A real diagonal
    // (short 775C front-week / long 771C back-week, spot 766, strikes only 4 apart) had its
    // theoretical curve still visibly rising at the edge of a strike-span-padded window — the
    // chart looked like a narrow, still-climbing slice instead of the actual hump shape, which
    // only flattens out around 20+ points past the strikes. Padding by a fraction of the
    // strike span (4 points here) can never reach that distance; padding by the back expiry's
    // OWN expected-move-from-now (the same SD-based approach sdHalfRange already uses for the
    // front expiry, just evaluated at the back expiry's own absolute DTE) scales correctly
    // regardless of how close together the strikes are. Single-expiry positions (iron condors,
    // verticals, butterflies) keep the original, tighter 8% pad — their curves are genuinely
    // flat/intrinsic past the strikes and don't need extra room.
    if (isMultiExpiry && backExpiry) {
      const Tback = (expiryMs(backExpiry) - Date.now()) / (365 * 24 * 60 * 60 * 1000);
      const backAtm = liveIVs && liveIVs[`atm|${backExpiry}`];
      const backIv = backAtm > 0.01 ? backAtm : defaultIV(pos.ticker);
      const backSd = Tback > 0 ? center * backIv * Math.sqrt(Tback) * SD_MULTIPLE : 0;
      const pad = Math.max((autoSmax - autoSmin) * 0.08, backSd);
      autoSmin = Math.max(0.01, Math.min(autoSmin, Math.min(...strikes) - pad));
      autoSmax = Math.max(autoSmax, Math.max(...strikes) + pad);
    } else {
      const pad = (autoSmax - autoSmin) * 0.08;
      autoSmin = Math.max(0.01, Math.min(autoSmin, Math.min(...strikes) - pad));
      autoSmax = Math.max(autoSmax, Math.max(...strikes) + pad);
    }
  }

  // Pure stock: payoff is entirely cost-basis driven, no options math needed.
  if (!optLegs.length && stockLegs.length) {
    const stockPayoff = S => stockLegs.reduce((pnl, l) => pnl + (l.ls === 'short' ? -1 : 1) * (S - (l.premium || 0)) * (l.qty || 1), 0);
    return { payoff: stockPayoff, t0payoff: null, autoSmin, autoSmax, strikes: [], frontExpiry: '' };
  }

  const frontDate = frontExpiry ? new Date(expiryMs(frontExpiry)) : null;

  // Per-leg IV for the T+0 curve: live ATM IV for that leg's expiry, else the ticker default.
  // Per-leg IV, back-solved from that leg's OWN live mid wherever we have one.
  //
  // liveIVs only ever carries `atm|<expiry>` keys, so a flat-per-expiry rule cannot
  // reproduce a real structure's skew. Calibrating each leg to its own mid makes the
  // model agree with the market at spot, and carries real skew/term structure into the
  // curve's shape instead of one ATM number. Costs one Newton solve per LEG.
  const YEAR_MS = 365 * 24 * 60 * 60 * 1000;
  // Theta is quoted per TRADING day, not per calendar day (TRADING_DAYS = 252) — this
  // matches broker convention (ThinkorSwim etc.) and is the more honest number over a
  // weekend, when two calendar days pass with the market shut.
  const TRADING_DAYS = 252;
  const legIV = new Map();
  for (const l of optLegs) {
    const exp = (l.expiry || '').slice(0, 10);
    const atmIv = liveIVs && liveIVs[`atm|${exp}`];
    const fallback = atmIv > 0.01 ? atmIv : defaultIV(pos.ticker);
    let iv = fallback;
    const prem = l.premium;
    if (spot > 0 && prem > 0 && l.strike > 0 && exp) {
      const T = (expiryMs(exp) - Date.now()) / YEAR_MS;
      if (T > 0) {
        // Bounds reject a solve that failed to converge or that a stale/crossed quote
        // pushed somewhere absurd; the ATM fallback is better than a garbage vol.
        // A mark at/below intrinsic (routine on deep-ITM 0DTE: wide market, spot and mid
        // sampled at different instants) has no solution. The ATM fallback would hand that
        // leg phantom time value, so solve against a hair above the no-arbitrage floor
        // instead -- the leg then prices at ~intrinsic, which is what its mark is saying.
        const isCall = l.type === 'call';
        const disc = l.strike * Math.exp(-RATE * T);
        const floor = isCall ? Math.max(0, spot - disc) : Math.max(0, disc - spot);
        const solved = impliedVol(spot, l.strike, T, RATE, Math.max(prem, floor + 0.01), isCall);
        if (solved > 0.005 && solved < 5) iv = solved;
        else if (prem <= floor) iv = 0.005;
      }
    }
    legIV.set(l, iv);
  }

  // Back-leg IV for multi-expiry positions (e.g. calendars): needed so that, on the *front*
  // expiry's payoff curve, the back leg is priced by BSM at its remaining DTE instead of being
  // taken to intrinsic value. Prefers legIV's OWN per-strike solve (same one the T+0 curve
  // uses) over a flat ATM number -- real skew means an OTM put and an OTM call trade at
  // different IVs even for the same expiry, and pricing both back legs off one flat ATM
  // average erased that skew from the at-expiry curve while the T+0 curve (which already used
  // legIV) kept it. A real, confirmed bug: a wide double calendar's two front-expiry peaks
  // came out nearly symmetric here while a reference tool (which prices with skew) showed the
  // put-side peak clearly taller than the call-side one, because SPX's put skew makes an OTM
  // put's IV higher than an OTM call's at the same delta-equivalent distance from spot.
  const backLegIV = new Map();
  if (isMultiExpiry) {
    for (const l of optLegs) {
      const exp = (l.expiry || '').slice(0, 10);
      if (exp === frontExpiry || !exp) continue;
      const backDate = new Date(expiryMs(exp));
      const Tback = Math.max(0.5 / 365, (backDate - frontDate) / (365 * 24 * 60 * 60 * 1000));
      const atmIv = liveIVs && liveIVs[`atm|${exp}`];
      const iv = legIV.get(l) ?? (atmIv > 0.01 ? atmIv : defaultIV(pos.ticker));
      backLegIV.set(l, { T: Tback, iv });
    }
  }

  // Expiry (intrinsic) payoff curve. Legs at the front expiry go to intrinsic value; legs at a
  // later expiry (calendars) are still priced by BSM at their remaining time.
  const payoff = S => {
    let pnl = pos.net || 0;
    for (const l of legs) {
      const sign = l.ls === 'short' ? -1 : 1, qty = l.qty || 1;
      if (l.type === 'call' || l.type === 'put') {
        const exp = (l.expiry || '').slice(0, 10);
        const isBack = isMultiExpiry && exp !== frontExpiry && backLegIV.has(l);
        if (isBack) {
          const { T, iv } = backLegIV.get(l);
          const useIv = flatIV != null ? flatIV : iv;
          pnl += sign * bsm(S, l.strike, T, RATE, useIv, l.type === 'call') * qty * (l.multiplier || 100);
        } else {
          pnl += sign * (l.type === 'call' ? Math.max(0, S - l.strike) : Math.max(0, l.strike - S)) * qty * (l.multiplier || 100);
        }
      } else if (l.type === 'stock') {
        pnl += sign * S * qty;
      }
    }
    return pnl;
  };

  // "As-of" time for the T+0 curve — the time slider shifts it forward from now toward expiry.
  const asOf = new Date(Date.now() + (asOfMin || 0) * 60000);
  // Each option leg's years-to-expiry (from the as-of time) and vol don't depend on the price,
  // so work them out once here rather than at every point the curves and greeks are priced at.
  const legTiv = new Map(optLegs.map(l => [l, {
    T: (new Date(expiryMs((l.expiry || '').slice(0, 10))) - asOf) / (365 * 24 * 60 * 60 * 1000),
    iv: flatIV != null ? flatIV : (legIV.get(l) ?? defaultIV(pos.ticker)),
  }]));
  const t0payoff = optLegs.length ? S => {
    let pnl = pos.net || 0;
    for (const l of legs) {
      const sign = l.ls === 'short' ? -1 : 1, qty = l.qty || 1;
      if (l.type === 'call' || l.type === 'put') {
        const { T, iv } = legTiv.get(l);
        const val = (T <= 0) ? (l.type === 'call' ? Math.max(0, S - l.strike) : Math.max(0, l.strike - S))
                              : bsm(S, l.strike, T, RATE, iv, l.type === 'call');
        pnl += sign * val * qty * (l.multiplier || 100);
      } else if (l.type === 'stock') {
        pnl += (l.ls === 'short' ? -1 : 1) * S * qty;
      }
    }
    return pnl;
  } : null;

  // The T+0 curve is NEVER re-anchored. A fresh model's T+0 value at spot is the real
  // mid-vs-model basis, and belongs on screen rather than being hidden by an offset that
  // silently drops the moment a slider moves.
  const anchoredT0 = t0payoff;

  // Position greeks at underlying price S, evaluated at the as-of time / IV the graph is
  // showing (so they react to the time & IV sliders). Delta is a raw (per-share) sum with
  // no contract multiplier; Gamma carries the x100 contract multiplier (reads as delta-change
  // per 1-point move in the underlying, per contract); Theta/Vega are dollars ($/day,
  // $/vol-point) — matching the standard broker convention.
  const greeksAt = optLegs.length ? S => {
    let D = 0, G = 0, Th = 0, V = 0;
    for (const l of legs) {
      const sign = l.ls === 'short' ? -1 : 1, qty = l.qty || 1;
      if (l.type === 'stock') { D += sign * qty * 0.01; continue; }
      if (l.type !== 'call' && l.type !== 'put') continue;
      const { T, iv } = legTiv.get(l);
      const isC = l.type === 'call';
      const mult = l.multiplier || 100;
      if (!(T > 0) || !(iv > 0) || !(S > 0) || !(l.strike > 0)) {
        // At/after expiry → intrinsic delta, no time value.
        const itm = isC ? S > l.strike : S < l.strike;
        D += sign * qty * (itm ? (isC ? 1 : -1) : 0) * mult;
        continue;
      }
      const sq = iv * Math.sqrt(T);
      const d1 = (Math.log(S / l.strike) + (RATE + 0.5 * iv * iv) * T) / sq, d2 = d1 - sq;
      const delta = isC ? ncdf(d1) : ncdf(d1) - 1;
      const gamma = npdf(d1) / (S * sq);
      const vega = S * npdf(d1) * Math.sqrt(T); // per 1.00 of vol
      const theta = (-(S * npdf(d1) * iv) / (2 * Math.sqrt(T)) - (isC ? 1 : -1) * RATE * l.strike * Math.exp(-RATE * T) * ncdf(isC ? d2 : -d2)) / TRADING_DAYS;
      D += sign * qty * delta * mult; G += sign * qty * gamma * mult;
      V += sign * qty * vega * 0.01 * mult; Th += sign * qty * theta * mult;
    }
    return { delta: D, gamma: G, theta: Th, vega: V };
  } : null;

  return { payoff, t0payoff: anchoredT0, autoSmin, autoSmax, strikes, frontExpiry, greeksAt };
}

// ── Curve statistics ─────────────────────────────────────────────────────

// Sample a payoff function across [Smin, Smax] (plus every strike, since piecewise-linear
// payoff peaks/troughs fall exactly on strikes and a uniform grid can miss them) to derive
// max profit, max loss, and breakeven prices (linear interpolation between adjacent samples
// wherever the curve crosses zero).
export function curveStats(payoff, Smin, Smax, strikes) {
  const N = 601;
  const samples = Array.from({ length: N }, (_, i) => Smin + (Smax - Smin) * i / (N - 1));
  const values = samples.map(payoff);
  const strikeVals = (strikes || []).map(payoff);

  // The payoff at the price floor too: the price stops at $0, so that is where a
  // short put's real worst case (and a long put's best case) sits.
  const floorS = Math.max(Smin * 1e-6, 1e-9);
  const allVals = values.concat(strikeVals, floorS < Smin ? [payoff(floorS)] : []);
  const maxProfit = Math.max(...allVals);
  const maxLoss = Math.min(...allVals);

  const breakevens = [];
  for (let i = 1; i < samples.length; i++) {
    const y0 = values[i - 1], y1 = values[i];
    if (y0 === 0) { breakevens.push(samples[i - 1]); continue; }
    if ((y0 < 0 && y1 > 0) || (y0 > 0 && y1 < 0)) {
      const x0 = samples[i - 1], x1 = samples[i];
      const t = y0 / (y0 - y1);
      breakevens.push(x0 + t * (x1 - x0));
    }
  }

  // Unbounded-tail detection, via slope PERSISTENCE far beyond the sampling window rather
  // than the sign of a single slope sample at the window edge. A single-expiry leg is priced
  // to true intrinsic (Math.max(0, S-K)) at every S, so beyond its strike the payoff's slope
  // holds an exactly constant sign and magnitude out to infinity — e.g. a naked short/long
  // call/put or a straddle. But a multi-expiry position (calendar / double calendar /
  // diagonal) prices its back leg(s) by BSM, whose slope only approaches ITS OWN intrinsic
  // slope asymptotically. Over the strike-span-driven sampling window, that back leg is
  // nowhere near saturated — its slope AT the window edge is nearly as steep as a naked
  // option's. A single slope sample at the edge cannot tell the two apart; that produced a
  // real regression this replaces (an SPX double calendar reported "Max Loss: Unlimited"
  // instead of a correct, finite figure).
  //
  // The fix: once you get far enough past the window that a same-strike (or near-same-
  // strike) back leg's delta has saturated toward its own intrinsic slope, it cancels against
  // the front leg (already at true intrinsic beyond its own strike) and the NET slope
  // collapses toward zero. A genuinely unbounded position has no such offsetting leg, so its
  // slope does not collapse — it keeps the same sign and magnitude arbitrarily far out. So:
  // probe two points well beyond each edge (one several sampling-window widths out, one an
  // order of magnitude further) and call a side unbounded only when the slope (a) has the
  // SAME SIGN at both probes and (b) has NOT meaningfully decayed between them. The decay
  // check is a RATIO of the two slopes' magnitudes (not an absolute difference), so it holds
  // at any price scale — a $30 stock and a 6000-point index both work off the same constants.
  const SLOPE_EPS = 1e-6;
  const PERSIST_RATIO = 0.5; // far probe must retain >=50% of the near probe's slope magnitude to count as persistent (not decaying)

  // Local secant slope around x, stepped proportional to x so it stays well-conditioned at
  // any price scale; the step is always strictly less than x, so x-h can never reach zero or
  // go negative (bsm/buildPayoffFns are undefined at S<=0).
  const localSlope = x => {
    const h = Math.abs(x) * 1e-3 || 1e-9;
    return (payoff(x + h) - payoff(x - h)) / (2 * h);
  };

  // Probe distance is a MULTIPLE OF PRICE, not of the sampling window's halfRange. The window
  // is a display concern — it's sized to make a position readable, and scales with the front
  // expiry's SD, which can collapse toward the `spot * 0.004` floor as the front leg nears
  // expiry. What matters for this test is being far enough out in PRICE that every leg's
  // option value has collapsed to intrinsic, regardless of window width, tenor or IV. Use
  // Smax/Smin themselves as the scale anchor (not halfRange) so a position whose window is
  // asymmetric around center still gets probes proportional to the actual price level.
  const center = (Smax + Smin) / 2 || Math.max(Math.abs(Smax), Math.abs(Smin), 1);
  const upScale = Math.max(Math.abs(Smax), Math.abs(center), 1);

  const upNear = localSlope(Smax + 4 * upScale);
  const upFar = localSlope(Smax + 40 * upScale);

  // A side is unbounded only when the far probe keeps the same sign as the near probe AND has
  // not collapsed toward zero relative to it (both probes are non-negligible in magnitude).
  const persistent = (near, far) => {
    if (Math.abs(near) < SLOPE_EPS || Math.abs(far) < SLOPE_EPS) return 0;
    if ((near > 0) !== (far > 0)) return 0;
    if (Math.abs(far) < Math.abs(near) * PERSIST_RATIO) return 0;
    return far;
  };
  const rightSlope = persistent(upNear, upFar);
  // Right edge: persistent positive slope -> profit runs away upward; persistent negative
  // slope -> loss runs away upward. The LEFT side is never unbounded: the price stops at
  // $0, so even a slope that persists all the way down (a naked short put, a long put)
  // ends at the finite floor payoff sampled above.
  const unboundedProfit = rightSlope > SLOPE_EPS;
  const unboundedLoss = rightSlope < -SLOPE_EPS;

  return {
    maxProfit: unboundedProfit ? Infinity : maxProfit,
    maxLoss: unboundedLoss ? -Infinity : maxLoss,
    breakevens,
  };
}

// ── tracker2 API leg adapter ─────────────────────────────────────────────

// Maps a leg object shaped like tracker2's GET /api/positions/<id>/chart-data response
// ({leg_type, side, strike, expiration_date, entry_price, quantity, multiplier, live_price})
// into the shape buildPayoffFns/curveStats expect
// ({ls, type, strike, expiry, qty, premium}).
//
// premium is always a positive magnitude — the sign of the leg's P&L contribution is
// carried entirely by `ls` (matching ntflux's convention), not by the sign of premium
// itself. Prefers the live quote (live_price) since that reflects the market right now;
// falls back to the magnitude of the signed entry_price (+received/-paid) only when no
// live quote came back from the backend (e.g. quote service was unavailable).
export function toModelerLeg(apiLeg) {
  return {
    ls: apiLeg.side === 'SHORT' ? 'short' : 'long',
    type: apiLeg.leg_type === 'CALL' ? 'call' : 'put',
    strike: apiLeg.strike,
    expiry: apiLeg.expiration_date,
    qty: apiLeg.quantity,
    premium: Math.abs(apiLeg.live_price != null ? apiLeg.live_price : apiLeg.entry_price),
    multiplier: apiLeg.multiplier || 100,
  };
}
