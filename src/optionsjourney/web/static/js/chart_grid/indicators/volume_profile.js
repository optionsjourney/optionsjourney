// Volume Profile approximated from OHLCV: each bar's volume is spread evenly over the
// buckets its high–low range covers (open/close when a bar has no high/low).
// With a sessionKey(time) -> key, only the latest session's bars count (a new session starts
// a fresh profile); without one, every bar given is a single composite profile.
// The forming bar is in the snapshot and is replaced as it ticks; it is added for good when
// a bar with a later time arrives.
// snapshot() returns { buckets: [{price, volume}...], poc, valueAreaLow, valueAreaHigh }.
export class VolumeProfile {
  constructor({ bucketSize = 0.25, valueAreaPercent = 0.7, sessionKey = null } = {}) {
    this.bucketSize = bucketSize;
    this.valueAreaPercent = valueAreaPercent;
    this.sessionKey = sessionKey;
    this._buckets = new Map();
    this._forming = null;
  }

  _bucketFor(price) {
    // Round first so 7837.25 / 0.25 lands on its own bucket, not one below.
    return Math.floor(Math.round((price / this.bucketSize) * 1e6) / 1e6) * this.bucketSize;
  }

  _add(map, candle) {
    const vol = candle.volume || 0;
    if (!vol) return;
    const hi = candle.high ?? Math.max(candle.open, candle.close);
    const lo = candle.low ?? Math.min(candle.open, candle.close);
    const first = Math.round(this._bucketFor(lo) / this.bucketSize);
    const last = Math.round(this._bucketFor(hi) / this.bucketSize);
    const share = vol / (last - first + 1);
    for (let i = first; i <= last; i++) {
      const b = Number((i * this.bucketSize).toFixed(8));
      map.set(b, (map.get(b) || 0) + share);
    }
  }

  _sameSession(a, b) {
    return !this.sessionKey || this.sessionKey(a.time) === this.sessionKey(b.time);
  }

  init(candles) {
    this._buckets = new Map();
    this._forming = null;
    const all = candles || [];
    if (!all.length) return this.snapshot();
    const last = all[all.length - 1];
    const bars = all.filter(c => this._sameSession(c, last));
    for (let i = 0; i < bars.length - 1; i++) this._add(this._buckets, bars[i]);
    this._forming = last;
    return this.snapshot();
  }

  update(candle) {
    const prev = this._forming;
    if (prev && candle.time > prev.time) {
      if (this._sameSession(prev, candle)) this._add(this._buckets, prev);
      else this._buckets = new Map();
    }
    if (!prev || candle.time >= prev.time) this._forming = candle;
    return this.snapshot();
  }

  snapshot() {
    const all = new Map(this._buckets);
    if (this._forming) this._add(all, this._forming);
    const buckets = [...all.entries()]
      .map(([price, volume]) => ({ price, volume }))
      .sort((a, b) => a.price - b.price);
    if (buckets.length === 0) {
      return { buckets, poc: null, valueAreaLow: null, valueAreaHigh: null };
    }
    const total = buckets.reduce((a, b) => a + b.volume, 0);
    // Indices like SPX have zero volume on every bar — VP is meaningless.
    if (total === 0) {
      return { buckets: [], poc: null, valueAreaLow: null, valueAreaHigh: null };
    }
    const poc = buckets.reduce((m, b) => (b.volume > m.volume ? b : m), buckets[0]).price;
    const idxPoc = buckets.findIndex(b => b.price === poc);
    let lo = idxPoc, hi = idxPoc;
    let acc = buckets[idxPoc].volume;
    const target = total * this.valueAreaPercent;
    while (acc < target && (lo > 0 || hi < buckets.length - 1)) {
      const upVol = hi + 1 < buckets.length ? buckets[hi + 1].volume : -1;
      const dnVol = lo - 1 >= 0 ? buckets[lo - 1].volume : -1;
      if (upVol >= dnVol) { hi++; acc += upVol; }
      else { lo--; acc += dnVol; }
    }
    return {
      buckets, poc,
      valueAreaLow: buckets[lo].price,
      valueAreaHigh: buckets[hi].price,
    };
  }
}

// A profile drawn from another feed's bars: the symbol's own 1m bars under a longer-timeframe
// pane (an hour bar's volume spread over its whole range buries where it traded), or an
// index's future (SPX from /ES). A future's bars are moved onto the index's prices by the
// basis, future × scale − index at the latest bar time both have. The pane's own bars only
// set the basis. setSourceBars and the pane bars' init and update each return the redrawn
// snapshot.
export const VP_PROXY = {
  SPX: { root: '/ES', scale: 1 },
  XSP: { root: '/ES', scale: 0.1 },
  NDX: { root: '/NQ', scale: 1 },
  RUT: { root: '/RTY', scale: 1 },
};

// paneSeconds: the index bars' length (default: exact times only). A 15m bar's close is its
// last minute's, so it pairs with the last proxy bar inside it, not the proxy bar at its open.
export function basisAt(proxyBars, indexBars, scale = 1, paneSeconds = 1) {
  if (!proxyBars.length || !indexBars.length) return null;
  const span = Math.max(paneSeconds, 1);
  let j = proxyBars.length - 1;
  for (let i = indexBars.length - 1; i >= 0; i--) {
    const t = indexBars[i].time;
    while (j >= 0 && proxyBars[j].time >= t + span) j--;
    if (j < 0) break;
    if (proxyBars[j].time >= t) return proxyBars[j].close * scale - indexBars[i].close;
  }
  return proxyBars[proxyBars.length - 1].close * scale - indexBars[indexBars.length - 1].close;
}

export class SourceVolumeProfile {
  constructor({ scale = 1, basis = false, paneSeconds = 1, ...opts } = {}) {
    this.scale = scale;
    this.basis = basis;
    this.paneSeconds = paneSeconds;
    this._opts = opts;
    this.bucketSize = opts.bucketSize ?? 0.25;
    this._source = [];
    this._index = [];
  }

  setSourceBars(bars) {
    this._source = bars || [];
    return this._compute();
  }

  init(candles) {
    this._index = candles || [];
    return this._compute();
  }

  // A pane bar only moves the basis; its own volume (SPY's, for SPX) is not used.
  update(candle) {
    if (!this.basis) return this._last || this._compute();
    const last = this._index[this._index.length - 1];
    if (!last || candle.time > last.time) this._index = [...this._index, candle];
    else if (candle.time === last.time) this._index = [...this._index.slice(0, -1), candle];
    return this._compute();
  }

  _compute() {
    const vp = new VolumeProfile(this._opts);
    if (!this.basis) return (this._last = vp.init(this._source));
    const basis = basisAt(this._source, this._index, this.scale, this.paneSeconds);
    if (basis == null) return (this._last = vp.init([]));
    const k = this.scale;
    const shift = p => (p == null ? p : p * k - basis);
    return (this._last = vp.init(this._source.map(b => ({ ...b,
      open: shift(b.open), high: shift(b.high), low: shift(b.low), close: shift(b.close) }))));
  }
}
