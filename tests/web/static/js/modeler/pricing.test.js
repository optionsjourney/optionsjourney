import { describe, it, expect, vi } from '../vitest_shim.js';
import * as pricing from '../../../../../src/optionsjourney/web/static/js/modeler/pricing.js';
import { ncdf, npdf, bsm, impliedVol, lnProbBelow, expiryMs, defaultIV, strikeIncrement } from '../../../../../src/optionsjourney/web/static/js/modeler/pricing.js';
import { curveStats, buildPayoffFns, toModelerLeg } from '../../../../../src/optionsjourney/web/static/js/modeler/pricing.js';

describe('pricing module resolves', () => {
  it('imports without error', () => {
    expect(pricing).toBeDefined();
  });
});

describe('ncdf', () => {
  it('returns 0.5 at x=0', () => {
    expect(ncdf(0)).toBeCloseTo(0.5, 6);
  });
  it('is symmetric: ncdf(-x) = 1 - ncdf(x)', () => {
    expect(ncdf(-1.5)).toBeCloseTo(1 - ncdf(1.5), 6);
  });
});

describe('npdf', () => {
  it('returns the standard normal peak at x=0', () => {
    expect(npdf(0)).toBeCloseTo(0.3989422804, 6);
  });
});

describe('bsm', () => {
  it('prices an ATM call using a known reference value', () => {
    // S=100, K=100, T=1yr, r=0.05, sigma=0.20 -> call ≈ 10.4506
    const price = bsm(100, 100, 1, 0.05, 0.20, true);
    expect(price).toBeCloseTo(10.4506, 3);
  });

  it('prices the matching put using put-call parity', () => {
    const call = bsm(100, 100, 1, 0.05, 0.20, true);
    const put = bsm(100, 100, 1, 0.05, 0.20, false);
    // C - P = S - K*e^(-rT)
    const parity = 100 - 100 * Math.exp(-0.05 * 1);
    expect(call - put).toBeCloseTo(parity, 6);
  });

  it('returns intrinsic value when T<=0', () => {
    expect(bsm(110, 100, 0, 0.05, 0.20, true)).toBe(10);
    expect(bsm(90, 100, 0, 0.05, 0.20, false)).toBe(10);
    expect(bsm(90, 100, 0, 0.05, 0.20, true)).toBe(0);
  });

  it('returns discounted intrinsic when sig<=0', () => {
    // No time value with zero vol: call = max(0, S*e^(rT) - K) * e^(-rT)
    const price = bsm(100, 90, 1, 0.05, 0, true);
    expect(price).toBeGreaterThan(0);
    expect(price).toBeCloseTo(Math.max(0, 100 * Math.exp(0.05) - 90) * Math.exp(-0.05), 6);
  });
});

describe('impliedVol', () => {
  it('round-trips: pricing at a known vol then solving recovers that vol', () => {
    const S = 100, K = 105, T = 0.25, r = 0.05, trueVol = 0.30;
    const price = bsm(S, K, T, r, trueVol, true);
    const solved = impliedVol(S, K, T, r, price, true);
    expect(solved).toBeCloseTo(trueVol, 3);
  });

  it('round-trips for a put', () => {
    const S = 4500, K = 4400, T = 0.08, r = 0.05, trueVol = 0.22;
    const price = bsm(S, K, T, r, trueVol, false);
    const solved = impliedVol(S, K, T, r, price, false);
    expect(solved).toBeCloseTo(trueVol, 3);
  });

  it('returns null for a price outside the no-arbitrage band', () => {
    // A call price above spot is impossible.
    expect(impliedVol(100, 100, 0.5, 0.05, 150, true)).toBeNull();
  });

  it('returns null when T<=0 or price<=0', () => {
    expect(impliedVol(100, 100, 0, 0.05, 5, true)).toBeNull();
    expect(impliedVol(100, 100, 0.5, 0.05, 0, true)).toBeNull();
  });
});

describe('lnProbBelow', () => {
  it('returns 0.5 at the spot level (x = spot)', () => {
    const spot = 100, sigma = 0.2, T = 1;
    // At x = spot: log(x/spot) = 0, so ncdf((0 + 0.5*sigma^2*T) / (sigma*sqrt(T))) = ncdf(sigma*sqrt(T))
    // That's not 0.5. The mean-adjusted point where lnProbBelow = 0.5 needs log(x/spot) = -0.5*sigma^2*T
    // so x = spot * exp(-0.5*sigma^2*T)
    const meanPoint = spot * Math.exp(-0.5 * sigma * sigma * T);
    expect(lnProbBelow(meanPoint, spot, sigma, T)).toBeCloseTo(0.5, 5);
  });

  it('returns null for non-positive inputs', () => {
    expect(lnProbBelow(0, 100, 0.2, 1)).toBeNull();
    expect(lnProbBelow(100, 100, 0, 1)).toBeNull();
  });
});

describe('expiryMs', () => {
  it('returns a timestamp for 4pm ET on the given date', () => {
    const ms = expiryMs('2026-06-19'); // June -> EDT (UTC-4)
    const d = new Date(ms);
    expect(d.getUTCHours()).toBe(20); // 16:00 EDT = 20:00 UTC
  });

  it('adjusts for standard time (EST, UTC-5) in winter', () => {
    const ms = expiryMs('2026-01-16');
    const d = new Date(ms);
    expect(d.getUTCHours()).toBe(21); // 16:00 EST = 21:00 UTC
  });
});

describe('expiryMs cost', () => {
  // The Eastern-time conversion (toLocaleString with a timeZone) is ~60us a call. A 56-leg book's
  // T+0 curve used to redo it for every leg at every one of 301 price points -- ~1s per draw.
  it('converts each expiry date once, however many legs and points are priced', () => {
    const spy = vi.spyOn(Date.prototype, 'toLocaleString');
    try {
      // Dates no other test uses, so nothing is remembered from before.
      const legs = [
        { ls: 'short', type: 'put', strike: 7665, expiry: '2031-03-14', qty: 22, premium: 20 },
        { ls: 'short', type: 'call', strike: 7880, expiry: '2031-03-14', qty: 22, premium: 18 },
        { ls: 'long', type: 'put', strike: 7665, expiry: '2031-03-21', qty: 22, premium: 30 },
        { ls: 'long', type: 'call', strike: 7880, expiry: '2031-03-21', qty: 22, premium: 28 },
      ];
      const gs = buildPayoffFns({ ticker: 'SPX', legs, net: -17930 }, 7738, null, 0, null);
      for (let i = 0; i <= 300; i++) gs.t0payoff(7400 + i * 2);
      expect(spy.mock.calls.length).toBeLessThanOrEqual(4);   // 2 per distinct date
    } finally {
      spy.mockRestore();
    }
  });

  it('gives the same instant on a repeat call, either side of a DST change', () => {
    for (const d of ['2031-03-07', '2031-03-10', '2031-10-31', '2031-11-03']) {
      expect(expiryMs(d)).toBe(expiryMs(d));
    }
    expect(new Date(expiryMs('2031-03-07')).getUTCHours()).toBe(21);   // EST
    expect(new Date(expiryMs('2031-03-10')).getUTCHours()).toBe(20);   // EDT
    expect(new Date(expiryMs('2031-11-03')).getUTCHours()).toBe(21);   // EST again
  });

  it('keys on the date alone, whatever time suffix the caller passes', () => {
    expect(expiryMs('2031-03-14T00:00:00')).toBe(expiryMs('2031-03-14'));
  });
});

describe('defaultIV', () => {
  it('returns known defaults for index tickers', () => {
    expect(defaultIV('SPX')).toBe(0.17);
    expect(defaultIV('RUT')).toBe(0.20);
    expect(defaultIV('VIX')).toBe(0.80);
  });

  it('returns 0.25 for an unknown ticker', () => {
    expect(defaultIV('ZZZZ')).toBe(0.25);
  });
});

describe('strikeIncrement', () => {
  it('prefers the live chain increment when given', () => {
    expect(strikeIncrement('SPX', [], 2.5)).toBe(2.5);
  });

  it('falls back to known-ticker defaults', () => {
    expect(strikeIncrement('SPX', [], null)).toBe(5);
    expect(strikeIncrement('SPY', [], null)).toBe(1);
  });

  it('falls back to the GCD of leg strike gaps for unknown tickers', () => {
    const legs = [{ type: 'call', strike: 100 }, { type: 'call', strike: 110 }, { type: 'call', strike: 130 }];
    expect(strikeIncrement('ZZZZ', legs, null)).toBe(10);
  });

  it('falls back to 1 when nothing else is available', () => {
    expect(strikeIncrement('ZZZZ', [], null)).toBe(1);
  });
});

describe('curveStats', () => {
  it('finds the exact breakeven of a simple long call (K + premium)', () => {
    // Long 1 call, strike 100, paid $5 premium -> payoff(S) = max(0,S-100) - 5
    const payoff = S => Math.max(0, S - 100) - 5;
    const stats = curveStats(payoff, 50, 150, [100]);
    expect(stats.breakevens.length).toBe(1);
    expect(stats.breakevens[0]).toBeCloseTo(105, 1);
  });

  it('reports bounded max loss for a defined-risk iron condor', () => {
    // Short 100/105 call spread + short 90/85 put spread, $200 credit, $500 wide each side
    // Max profit = credit; max loss = width*100 - credit (both bounded).
    const payoff = S => {
      let pnl = 200; // net credit
      pnl += -Math.max(0, S - 100) * 100 + Math.max(0, S - 105) * 100; // short call spread
      pnl += -Math.max(0, 90 - S) * 100 + Math.max(0, 85 - S) * 100;  // short put spread
      return pnl;
    };
    const stats = curveStats(payoff, 50, 150, [85, 90, 100, 105]);
    expect(stats.maxProfit).toBe(200);
    expect(stats.maxLoss).toBeGreaterThan(-Infinity);
    expect(stats.maxLoss).toBeLessThan(0);
  });

  it('detects unbounded loss for a naked short call', () => {
    const payoff = S => 300 - Math.max(0, S - 100) * 100; // short 1 call, $300 credit
    const stats = curveStats(payoff, 50, 150, [100]);
    expect(stats.maxLoss).toBe(-Infinity);
    expect(stats.maxProfit).toBe(300);
  });

  it('reports a finite max loss for a naked short put — the stock stops at $0', () => {
    const payoff = S => 280 - Math.max(0, 75 - S) * 100; // short 1 put, $280 credit
    const stats = curveStats(payoff, 60, 110, [75]);
    expect(stats.maxLoss).toBeCloseTo(280 - 7500, 0);
    expect(stats.maxProfit).toBe(280);
  });

  it('does NOT misreport a multi-expiry calendar as unbounded', () => {
    // Front leg at true intrinsic beyond its strike; back leg still has BSM time value
    // near the sampling window edge, so its slope has not saturated there. The back leg's
    // presence must make the net slope collapse toward zero far out, not read as unbounded.
    const K = 100, r = 0.045, backIv = 0.20, backT = 0.5;
    const payoff = S => {
      const front = -Math.max(0, S - K) * 100; // short front-month call (intrinsic)
      const back = bsm(S, K, backT, r, backIv, true) * 100; // long back-month call (BSM)
      return front + back - 50; // small net debit paid for the calendar
    };
    const stats = curveStats(payoff, 80, 120, [K]);
    expect(stats.maxLoss).toBeGreaterThan(-Infinity);
    expect(stats.maxProfit).toBeLessThan(Infinity);
  });
});

describe('toModelerLeg', () => {
  it('maps a tracker2 API leg into the modeler leg shape', () => {
    const apiLeg = {
      leg_type: 'PUT', side: 'SHORT', strike: 4400,
      expiration_date: '2026-09-11', entry_price: 3.55,
      quantity: 6, multiplier: 100, live_price: 5.40,
    };
    const out = toModelerLeg(apiLeg);
    expect(out).toEqual({ ls: 'short', type: 'put', strike: 4400, expiry: '2026-09-11', qty: 6, premium: 5.40, multiplier: 100 });
  });

  it('falls back to the magnitude of entry_price when live_price is null', () => {
    const apiLeg = {
      leg_type: 'CALL', side: 'LONG', strike: 100,
      expiration_date: '2026-09-11', entry_price: -2.10,
      quantity: 2, multiplier: 100, live_price: null,
    };
    const out = toModelerLeg(apiLeg);
    expect(out.premium).toBe(2.10);
    expect(out.ls).toBe('long');
  });

  it('carries through a non-100 multiplier (e.g. /ES futures options use 50)', () => {
    const apiLeg = {
      leg_type: 'CALL', side: 'LONG', strike: 5000,
      expiration_date: '2026-09-11', entry_price: 10,
      quantity: 1, multiplier: 50, live_price: null,
    };
    const out = toModelerLeg(apiLeg);
    expect(out.multiplier).toBe(50);
  });

  it('defaults multiplier to 100 when the API leg omits it', () => {
    const apiLeg = {
      leg_type: 'CALL', side: 'LONG', strike: 100,
      expiration_date: '2026-09-11', entry_price: 2,
      quantity: 1, live_price: null,
    };
    const out = toModelerLeg(apiLeg);
    expect(out.multiplier).toBe(100);
  });
});

describe('buildPayoffFns', () => {
  it('returns null when there are no legs', () => {
    expect(buildPayoffFns({ ticker: 'SPX', legs: [], net: 0 }, 4500, null, 0, null)).toBeNull();
  });

  it('scales the auto-window width by IV and sqrt(T), not a fixed percentage of spot', () => {
    // OptionsJourney: Tracker2's fixed 2026-09-11 expiry has passed (no time left, so no SD
    // window); a date 30 days out keeps the test meaning what it says.
    const exp = new Date(Date.now() + 30 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10);
    const legs = [{ ls: 'short', type: 'put', strike: 4400, expiry: exp, qty: 1, premium: 50 }];
    const lowIvPos = { ticker: 'SPX', legs, net: 500 };
    const gs = buildPayoffFns(lowIvPos, 4500, { [`atm|${exp}`]: 0.10 }, 0, null);
    const gsHighIv = buildPayoffFns(lowIvPos, 4500, { [`atm|${exp}`]: 0.40 }, 0, null);
    expect(gs).not.toBeNull();
    const lowIvWidth = gs.autoSmax - gs.autoSmin;
    const highIvWidth = gsHighIv.autoSmax - gsHighIv.autoSmin;
    expect(highIvWidth).toBeGreaterThan(lowIvWidth);
  });

  it('produces a payoff function whose value at a strike matches intrinsic at expiry (asOfMin far beyond expiry)', () => {
    const legs = [{ ls: 'long', type: 'call', strike: 100, expiry: '2026-01-01', qty: 1, premium: 5 }];
    const pos = { ticker: 'ZZZZ', legs, net: -500 };
    const gs = buildPayoffFns(pos, 100, null, 0, null);
    expect(gs.payoff(120)).toBeCloseTo((120 - 100) * 100 - 500, 6);
    expect(gs.payoff(90)).toBeCloseTo(-500, 6);
  });

  it('scales the intrinsic payoff by a leg\'s own multiplier instead of a hardcoded 100 (e.g. /ES futures options use 50)', () => {
    // Same strike/premium/qty at multiplier 100 vs multiplier 50 (e.g. an /ES futures
    // option, per models/legs.py) — the multiplier-50 payoff must be exactly half the
    // multiplier-100 payoff at every S, since intrinsic value * qty * multiplier is linear
    // in multiplier.
    const legs100 = [{ ls: 'long', type: 'call', strike: 5000, expiry: '2026-01-01', qty: 1, premium: 10, multiplier: 100 }];
    const legs50 = [{ ls: 'long', type: 'call', strike: 5000, expiry: '2026-01-01', qty: 1, premium: 10, multiplier: 50 }];
    const pos100 = { ticker: '/ES', legs: legs100, net: -1000 }; // -10 premium * 1 qty * 100
    const pos50 = { ticker: '/ES', legs: legs50, net: -500 };    // -10 premium * 1 qty * 50

    const gs100 = buildPayoffFns(pos100, 5000, null, 0, null);
    const gs50 = buildPayoffFns(pos50, 5000, null, 0, null);

    // Deep ITM at expiry: intrinsic value dominates, so P&L should scale ~exactly by multiplier.
    const pnl100 = gs100.payoff(5200);
    const pnl50 = gs50.payoff(5200);
    expect(pnl100).toBeCloseTo((5200 - 5000) * 1 * 100 - 1000, 6);
    expect(pnl50).toBeCloseTo((5200 - 5000) * 1 * 50 - 500, 6);
    expect(pnl50).toBeCloseTo(pnl100 / 2, 6);
  });

  it('widens the auto-window for a diagonal far enough to show the back leg\'s curve flattening, not just past the (possibly nearby) strikes', () => {
    // Regression test for a real bug: a diagonal (short front-week call / long back-week call
    // at a DIFFERENT, nearby strike) had its theoretical curve still visibly climbing at the
    // edge of the auto-window, because the window was padded by a fraction of the strike gap
    // (only 4 points here) rather than by how far the back leg's own BSM-priced time value
    // actually extends. A back-expiry-expected-move-based pad is required to reach the curve's
    // real turning point, which can sit tens of points past strikes that are close together.
    const front = new Date(Date.now() + 2 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10);
    const back = new Date(Date.now() + 5 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10);
    const legs = [
      { ls: 'short', type: 'call', strike: 775, expiry: front, qty: 1, premium: 3.8 },
      { ls: 'long', type: 'call', strike: 771, expiry: back, qty: 1, premium: 7.13 },
    ];
    const pos = { ticker: 'SPY', legs, net: -333 };
    const spot = 766.22;
    const liveIVs = { [`atm|${front}`]: 0.14, [`atm|${back}`]: 0.14 };
    const gs = buildPayoffFns(pos, spot, liveIVs, 0, null);
    expect(gs).not.toBeNull();

    // The window must extend well past the far strike (775) — a strike-span-only pad (4 points
    // * some small multiple) could never reach the ~20+ points needed for this back expiry's
    // time value to flatten out.
    expect(gs.autoSmax).toBeGreaterThan(785);

    // The curve's value at the window's right edge must be closer to its long-run (flattened)
    // level than to its value just past the strikes — i.e. the window doesn't cut the curve off
    // mid-climb. Comparing the slope near the strikes (steep) against the slope at the window
    // edge (near flat) directly verifies this, rather than just asserting a wide window exists.
    const h = 1;
    const slopeNearStrikes = (gs.t0payoff(780 + h) - gs.t0payoff(780 - h)) / (2 * h);
    const slopeAtWindowEdge = (gs.t0payoff(gs.autoSmax) - gs.t0payoff(gs.autoSmax - 2 * h)) / (2 * h);
    expect(Math.abs(slopeAtWindowEdge)).toBeLessThan(Math.abs(slopeNearStrikes) * 0.5);
  });

  it('keeps the original tight 8% strike pad for single-expiry positions (no regression from the multi-expiry window fix)', () => {
    // A short-dated expiry (a few days out, same order of magnitude as other single-expiry
    // tests in this file) so the pre-existing SD-based sizing itself stays tight — isolating
    // this test to the strike-pad behavior the multi-expiry fix must not have touched.
    const soon = new Date(Date.now() + 3 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10);
    const legs = [
      { ls: 'short', type: 'call', strike: 105, expiry: soon, qty: 1, premium: 2 },
      { ls: 'short', type: 'put', strike: 95, expiry: soon, qty: 1, premium: 2 },
    ];
    const pos = { ticker: 'ZZZZ', legs, net: 400 };
    const gs = buildPayoffFns(pos, 100, null, 0, null);
    // A single-expiry iron-condor-style position's window should stay close to its strikes —
    // not balloon out to a back-expiry-scale pad, since there is no back expiry.
    expect(gs.autoSmax).toBeLessThan(115);
    expect(gs.autoSmin).toBeGreaterThan(85);
  });

  it('defaults a leg with no multiplier field to 100 in the payoff calc', () => {
    const legs = [{ ls: 'long', type: 'call', strike: 100, expiry: '2026-01-01', qty: 1, premium: 5 }]; // no multiplier
    const pos = { ticker: 'ZZZZ', legs, net: -500 };
    const gs = buildPayoffFns(pos, 100, null, 0, null);
    expect(gs.payoff(120)).toBeCloseTo((120 - 100) * 100 - 500, 6);
  });

  // Regression test for a real, user-reported bug: a wide double calendar's at-expiry payoff
  // curve (two separate humps, one per strike, since the front legs go to intrinsic and the
  // back legs are BSM-priced at their remaining DTE) priced BOTH back legs off one flat ATM
  // IV, discarding real skew (an OTM put typically trades at a different IV than an OTM call
  // at the same expiry). The T+0/Theoretical curve already calibrated each leg to its OWN
  // live premium (back-solved IV, carrying skew); the at-expiry curve did not, so its two
  // peaks came out shaped by a single flat vol assumption instead of the actual skewed
  // market. Verified against a reference tool: with real put skew, the put-side peak should
  // come out taller than the call-side peak — this test constructs legs whose live premiums
  // imply exactly that skew (a materially higher put IV than call IV) and checks the at-expiry
  // curve's two peaks reflect it, not a symmetric/averaged shape.
  it('a multi-expiry position\'s at-expiry curve prices each back leg with its OWN implied IV (skew), not one flat ATM average', () => {
    const spot = 7717.64;
    // Relative to today (not hardcoded absolute dates, which would drift stale as the
    // calendar advances and silently corrupt this test's premiums -- impliedVol solves
    // against time-to-expiry FROM NOW, so a "front" date that has since become today/past
    // stops reproducing the intended frontT/backT below).
    const front = new Date(Date.now() + 1 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10);
    const back = new Date(Date.now() + 2 * 24 * 60 * 60 * 1000).toISOString().slice(0, 10);
    const frontT = 1 / 365; // ~1 day out
    const backT = 2 / 365; // ~2 days remaining for the back leg at front expiry
    // Deliberately skewed: the put trades at meaningfully higher IV than the call, matching
    // real index skew (OTM puts richer than OTM calls).
    const putIv = 0.22, callIv = 0.13;
    const shortPutPrem = bsm(spot, 7605, frontT, 0.045, putIv, false);
    const shortCallPrem = bsm(spot, 7715, frontT, 0.045, callIv, true);
    const longPutPrem = bsm(spot, 7605, backT, 0.045, putIv, false);
    const longCallPrem = bsm(spot, 7715, backT, 0.045, callIv, true);
    const legs = [
      { ls: 'short', type: 'put', strike: 7605, expiry: front, qty: 1, premium: shortPutPrem },
      { ls: 'short', type: 'call', strike: 7715, expiry: front, qty: 1, premium: shortCallPrem },
      { ls: 'long', type: 'put', strike: 7605, expiry: back, qty: 1, premium: longPutPrem },
      { ls: 'long', type: 'call', strike: 7715, expiry: back, qty: 1, premium: longCallPrem },
    ];
    const net = (shortPutPrem + shortCallPrem - longPutPrem - longCallPrem) * 100;
    const gs = buildPayoffFns({ ticker: 'SPX', legs, net }, spot, null, 0, null);

    let putPeak = -Infinity, callPeak = -Infinity;
    for (let S = 7550; S <= 7660; S += 1) putPeak = Math.max(putPeak, gs.payoff(S));
    for (let S = 7660; S <= 7850; S += 1) callPeak = Math.max(callPeak, gs.payoff(S));

    // With this much put-over-call skew, the put-side (long 7605P) back leg carries
    // meaningfully more BSM time value at front expiry than a flat-IV model would give it,
    // so its peak must come out clearly taller than the call side's -- not roughly equal
    // (which is what pricing both off one shared/averaged IV would produce).
    expect(putPeak).toBeGreaterThan(callPeak * 1.1);
  });

  // A deep-ITM 0DTE mark routinely sits BELOW intrinsic (wide market, and the index spot and
  // the option mid aren't sampled at the same instant). impliedVol rightly returns null there,
  // but falling back to the ticker default IV (0.17) handed a 0DTE ITM call several dollars of
  // phantom time value -- a real combined graph read ~$200 below its live marks,
  // and jumped by that much as spot ticked across the leg's mark + strike.
  it('prices a leg whose mark is below intrinsic at ~intrinsic, not at the default IV', () => {
    const spot = 7731.7;
    const exp = new Date(Date.now() + 6 * 60 * 60 * 1000).toISOString().slice(0, 10);
    const T = Math.max(1e-4, (expiryMs(exp) - Date.now()) / (365 * 24 * 60 * 60 * 1000));
    const leg = { ls: 'short', type: 'call', strike: 7680, expiry: exp, qty: 1, premium: 50.35 };
    // net = what the leg was sold for at the mark, so T+0 at spot = (mark - model value) * 100
    const gs = buildPayoffFns({ ticker: 'SPX', legs: [leg], net: 50.35 * 100 }, spot, null, 0, null);
    const intrinsic = spot - 7680 * Math.exp(-0.045 * T);
    const modelValue = 50.35 - gs.t0payoff(spot) / 100;
    expect(modelValue).toBeGreaterThanOrEqual(intrinsic - 0.01);
    expect(modelValue).toBeLessThan(intrinsic + 0.1);
  });
});
