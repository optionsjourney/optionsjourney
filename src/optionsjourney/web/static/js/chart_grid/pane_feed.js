// The bars of one grid pane: the chart feed's (symbol, interval) through the page's
// CandleRouter, with an index's volume taken from its ETF (SPX has none of its own), and
// Schwab's price history once if DXLink has sent nothing after FALLBACK_MS. Futures get no
// fallback, because Schwab would chart the stock ES: after FALLBACK_MS with nothing from DXLink
// they report 'no data' instead of spinning. A futures root (/ES) charts on its
// active contract, looked up through /api/chart/futures_contract. Schwab has no 4h, weekly or
// monthly bars: those are built from its 30m and daily bars (aggregateBars). DXLink's monthly
// bars only reach back to about 2022-12, so a 1mo pane puts Schwab's older months (ten years
// of daily bars, rolled up) in front of DXLink's first bar (BACKFILL).
import { BarStepper } from '../charts/bar_stepper.js';

export const VOLUME_PROXY = { SPX: 'SPY', NDX: 'QQQ', RUT: 'IWM', XSP: 'SPY' };
export const FALLBACK_MS = 10000;
export const TIMEFRAMES = ['1m', '5m', '15m', '30m', '1h', '4h', '1d', '1w', '1mo'];
const FREQ = { '1m': 1, '5m': 5, '15m': 15, '30m': 30, '1h': 60, '4h': 30, '1d': 1440, '1w': 1440, '1mo': 1440 };
const PERIOD_DAYS = { '1m': 4, '5m': 7, '15m': 14, '30m': 21, '1h': 45, '4h': 60, '1d': 760,
  '1w': 1830, '1mo': 3660 };
// Intervals whose DXLink history is short: Schwab's bars from before DXLink's first one go in front.
const BACKFILL = new Set(['1mo']);
const DAY = 86400;

// The bucket a Schwab bar falls in, in the chart feed's own times: 4h bars start 09:30 and
// 13:30 (intraday times are ET wall clock in a UTC epoch), weeks on Monday and months on the
// 1st at 00:00 UTC. Schwab's daily bars sit at midnight ET, the same UTC date.
const BUCKET = {
  '4h': t => { const d = Math.floor(t / DAY) * DAY; return d + ((t - d) < 810 * 60 ? 570 : 810) * 60; },
  '1w': t => { const d = Math.floor(t / DAY) * DAY; return d - ((new Date(d * 1000).getUTCDay() + 6) % 7) * DAY; },
  '1mo': t => { const x = new Date(t * 1000); return Date.UTC(x.getUTCFullYear(), x.getUTCMonth(), 1) / 1000; },
};

// Time-ordered bars rolled up to `interval`; other intervals come back as they are.
export function aggregateBars(bars, interval) {
  const bucket = BUCKET[interval];
  if (!bucket) return bars;
  const out = [];
  for (const b of bars) {
    const time = bucket(b.time);
    const cur = out[out.length - 1];
    if (cur && cur.time === time) {
      cur.high = Math.max(cur.high, b.high);
      cur.low = Math.min(cur.low, b.low);
      cur.close = b.close;
      cur.volume = (cur.volume || 0) + (b.volume || 0);
    } else {
      out.push({ time, open: b.open, high: b.high, low: b.low, close: b.close, volume: b.volume || 0 });
    }
  }
  return out;
}

export const isFutures = s => String(s || '').startsWith('/');

export async function fetchContract(root) {
  try {
    const r = await fetch(`/api/chart/futures_contract?root=${encodeURIComponent(root)}`);
    const d = await r.json().catch(() => null);
    return r.ok && d && d.symbol ? d.symbol : null;
  } catch (e) {
    return null;
  }
}

async function fetchSchwab(symbol, interval) {
  try {
    const r = await fetch(`/api/chart/pricehistory/${encodeURIComponent(symbol)}`
      + `?frequency=${FREQ[interval]}&period=${PERIOD_DAYS[interval]}`);
    const d = await r.json().catch(() => null);
    return r.ok && d && !d.error && Array.isArray(d.candles) ? aggregateBars(d.candles, interval) : [];
  } catch (e) {
    return [];
  }
}

export class PaneFeed {
  constructor({ router, symbol, interval, onBars, onStep, onError,
                resolveContract = fetchContract, fetchHistory = fetchSchwab,
                fallbackMs = FALLBACK_MS, setTimer = (f, ms) => setTimeout(f, ms),
                clearTimer = id => clearTimeout(id) }) {
    Object.assign(this, { router, symbol, interval, onBars, onStep, onError,
      resolveContract, fetchHistory, fallbackMs, setTimer, clearTimer });
    this.proxy = VOLUME_PROXY[symbol] || null;
    this.feedSymbol = null;
    this._unsubs = [];
    this._stopped = false;
    this._dx = false;            // DXLink has sent a snapshot
    this._bars = null;
    this._raw = null;            // the bars before backfill and proxy volume: DXLink's, or Schwab's
    this._older = [];            // BACKFILL: Schwab's bars, used before the first of _raw
    this._olderVol = new Map();  // BACKFILL: the proxy's Schwab volumes for those
    this._proxyVol = new Map();
    this._stepper = new BarStepper();
    this._timer = null;
  }

  async start() {
    let sym = this.symbol;
    if (isFutures(sym)) {
      sym = await this.resolveContract(sym);
      if (this._stopped) return;
      if (!sym) { this.onError(`no contract for ${this.symbol}`); return; }
    }
    this.feedSymbol = sym;
    this._unsubs.push(this.router.subscribe(sym, this.interval, {
      onSnapshot: bars => this._onSnapshot(bars),
      onBar: bar => this._onBar(bar),
    }));
    if (this.proxy) {
      this._unsubs.push(this.router.subscribe(this.proxy, this.interval, {
        onSnapshot: bars => this._onProxySnapshot(bars),
        onBar: bar => { if (!this._stopped) this._proxyVol.set(bar.time, bar.volume); },
      }));
    }
    this._timer = this.setTimer(() => this._fallback(), this.fallbackMs);
    if (BACKFILL.has(this.interval) && !isFutures(this.symbol)) this._backfill();
  }

  stop() {
    this._stopped = true;
    this._unsubs.forEach(u => u());
    this._unsubs = [];
    if (this._timer != null) this.clearTimer(this._timer);
  }

  _withVol(bar) {
    if (!this.proxy || bar.volume) return bar;
    return { ...bar, volume: this._proxyVol.get(bar.time) || this._olderVol.get(bar.time) || 0 };
  }

  _emitBars(bars) {
    this._raw = bars;
    const first = bars.length ? bars[0].time : Infinity;
    const older = this._older.filter(b => b.time < first);
    this._bars = [...older, ...bars].map(b => this._withVol(b));
    this._stepper.seed(this._bars);
    this.onBars(this._bars);
  }

  _onSnapshot(bars) {
    if (this._stopped) return;
    this._dx = true;
    this._emitBars(bars);
  }

  _onBar(bar) {
    if (this._stopped || !this._bars) return;
    const r = this._stepper.step(this._withVol(bar));
    if (r.type === 'revise') {
      this._emitBars(this.router.bars(this.feedSymbol, this.interval) || []);
      return;
    }
    this.onStep(r);
  }

  _onProxySnapshot(bars) {
    if (this._stopped) return;
    this._proxyVol = new Map(bars.map(b => [b.time, b.volume]));
    if (this._raw) this._emitBars(this._raw);
  }

  // Schwab's longer history for a BACKFILL interval. Shown once there are bars to put it in
  // front of; a failed fetch just leaves DXLink's bars on their own.
  async _backfill() {
    const [bars, proxyBars] = await Promise.all([
      this.fetchHistory(this.symbol, this.interval),
      this.proxy ? this.fetchHistory(this.proxy, this.interval) : Promise.resolve(null),
    ]);
    if (this._stopped) return;
    this._older = bars || [];
    if (proxyBars) this._olderVol = new Map(proxyBars.map(b => [b.time, b.volume]));
    if (this._raw) this._emitBars(this._raw);
  }

  async _fallback() {
    this._timer = null;
    if (this._stopped || this._dx) return;
    if (isFutures(this.symbol)) { this.onError('no data'); return; }
    const [bars, proxyBars] = await Promise.all([
      this.fetchHistory(this.symbol, this.interval),
      this.proxy && !this._proxyVol.size ? this.fetchHistory(this.proxy, this.interval) : Promise.resolve(null),
    ]);
    if (this._stopped || this._dx) return;
    if (proxyBars && proxyBars.length) this._proxyVol = new Map(proxyBars.map(b => [b.time, b.volume]));
    if (!bars.length) { this.onError('no data'); return; }
    this._emitBars(bars);
  }
}
