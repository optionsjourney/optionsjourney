// Daily expected move, Flux's 0DTE "SD Bands" (ntflux static/js/zero_dte_em.js):
//
//   SD = open × (vol index at the 09:30 open / 100) / √365
//
// with lines at open ± 1 SD and ± 2 SD, fixed for the session. √365, not √252: Cboe computes
// its volatility indexes over calendar days, and 252 would overstate the move by ~20%. For the
// S&P the index is VIX1D, falling back per day to VIX9D, then VIX (Flux's candidate order);
// Nasdaq uses VXN and Russell RVX (no 1-day index exists for them).
//
// Drawn across each day's 09:30–16:00 bars only (intraday timeframes); every other bar is
// whitespace. A line still joins across whitespace, so a day's last point is flagged
// `gapAfter` when another day follows and the pane draws that segment transparent.
//
// Bar times — the chart's and the vol indexes' — are New York wall clock written as a UTC
// epoch (chart_feed.py), so session date and clock are read in UTC.

const BAR_DATE = new Intl.DateTimeFormat('en-US', {
  timeZone: 'UTC', year: 'numeric', month: '2-digit', day: '2-digit',
});
export const barDate = (sec) => BAR_DATE.format(new Date(sec * 1000));
const clockMinutes = (sec) => { const d = new Date(sec * 1000); return d.getUTCHours() * 60 + d.getUTCMinutes(); };
const OPEN_MIN = 570, CLOSE_MIN = 960;
const inRth = (sec) => { const m = clockMinutes(sec); return m >= OPEN_MIN && m < CLOSE_MIN; };

// DXLink names (plain, no '$'). DXLink has no VXN under any name (probed 2026-10-02:
// VXN, VXN.X, $VXN, VXNCLS); Schwab does, so it is read from /api/chart/pricehistory.
export const SCHWAB_ONLY = { VXN: '$VXN' };
const SP = ['VIX1D', 'VIX9D', 'VIX'];
const VOL_INDEXES = {
  SPX: SP, SPXW: SP, SPY: SP, XSP: SP, XSPW: SP, '/ES': SP, '/MES': SP,
  NDX: ['VXN'], NDXP: ['VXN'], QQQ: ['VXN'], '/NQ': ['VXN'], '/MNQ': ['VXN'],
  RUT: ['RVX'], RUTW: ['RVX'], IWM: ['RVX'], '/RTY': ['RVX'], '/M2K': ['RVX'],
};

// The volatility indexes for a chart symbol, best first; [] if none. Futures contracts
// (/ESZ6) map by root.
export function volIndexesFor(symbol) {
  const s = String(symbol || '').toUpperCase();
  if (VOL_INDEXES[s]) return VOL_INDEXES[s];
  const root = /^(\/[A-Z0-9]+?)[FGHJKMNQUVXZ]\d{1,2}$/.exec(s)?.[1];
  return (root && VOL_INDEXES[root]) || [];
}

// date -> the open of a vol index's first bar at or after 09:30 that day. Non-positive opens
// are skipped (DXLink's zero-open history marker; ThetaData zero-fills 09:30 for Flux).
export function openByDate(bars) {
  const out = new Map();
  for (const b of [...(bars || [])].sort((x, y) => x.time - y.time)) {
    if (!(b.open > 0) || !inRth(b.time)) continue;
    const d = barDate(b.time);
    if (!out.has(d)) out.set(d, b.open);
  }
  return out;
}

export const SQRT_365 = Math.sqrt(365);

export class ExpectedMove {
  // sources: one Map(date -> 09:30 open) per vol index, best first.
  constructor({ sources = [] } = {}) {
    this.setSources(sources);
  }

  setSources(sources) {
    this._sources = sources;
    this._days = new Map(); // date -> levels
  }

  _volFor(date) {
    for (const [i, m] of this._sources.entries()) {
      const v = m?.get(date);
      if (v > 0) return { vol: v, source: i };
    }
    return null;
  }

  _levelsFor(candle) {
    const date = barDate(candle.time);
    if (this._days.has(date)) return this._days.get(date);
    const v = this._volFor(date);
    if (!v) return null;
    const open = candle.open;
    const sigma = (open * v.vol) / 100 / SQRT_365;
    const lv = { open, vol: v.vol, source: v.source, sigma,
      up1: open + sigma, dn1: open - sigma, up2: open + 2 * sigma, dn2: open - 2 * sigma };
    this._days.set(date, lv);
    return lv;
  }

  _point(candle) {
    if (!inRth(candle.time)) return { time: candle.time };
    const lv = this._levelsFor(candle); // the day's first RTH bar anchors it
    return lv ? { time: candle.time, ...lv } : { time: candle.time };
  }

  init(candles) {
    this._days = new Map();
    const points = candles.map(c => this._point(c));
    // Lines join across whitespace; flag each day's last point when another day follows.
    let prev = null;
    for (const p of points) {
      if (p.up1 == null) continue;
      if (prev && barDate(prev.time) !== barDate(p.time)) prev.gapAfter = true;
      prev = p;
    }
    return { initialPoints: points };
  }

  // A forming 09:30 bar's open is final, so live updates never move a day's levels.
  update(candle) {
    return { point: this._point(candle) };
  }
}
