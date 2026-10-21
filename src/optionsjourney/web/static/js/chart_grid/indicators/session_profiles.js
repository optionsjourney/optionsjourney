// Per-session Volume Profiles (Sierra-style): one profile for each of the last `days` sessions,
// each drawn inside its own session's bars. A session is built from its source's 1m bars when
// the 1m history reaches back to the session's start, otherwise from 5m bars (1m goes back about
// 4 days, 5m about 10). The source is the pane's own symbol, or an index's future (SPX from /ES)
// moved by the basis: future × scale − index, rounded to the bucket size so every profile stays
// on the index's bucket grid. The pane's own bars only set each session's time span, the basis,
// and where a POC is first traded through.
// snapshot() returns { bucketSize, sessions: [{ key, start, end, current, source, buckets,
//   poc, valueAreaLow, valueAreaHigh, pocLevel, valLevel, vahLevel, pocEnd }] }:
//   buckets/poc/valueArea* are bucket bottoms as VolumeProfile gives them; pocLevel is the POC
//   bucket's middle, valLevel/vahLevel the value area's outer edges; pocEnd is the time of the
//   first later pane bar whose range crosses pocLevel (null: never, a naked POC), and the
//   forming session's own last bar time.
import { VolumeProfile, basisAt } from './volume_profile.js';
import { mergeBar } from '../../charts/bars.js';

const round8 = v => Number(v.toFixed(8));

export class SessionProfiles {
  constructor({ days = 5, bucketSize = 0.25, sessionKey, scale = 1, basis = false,
                paneSeconds = 60, valueAreaPercent = 0.7 } = {}) {
    Object.assign(this, { days, bucketSize, sessionKey, scale, basis, paneSeconds, valueAreaPercent });
    this._pane = [];
    this._src = { '1m': { bars: [], groups: null }, '5m': { bars: [], groups: null } };
    this._cache = new Map(); // `${key}|${source}` -> { sig, snap }
  }

  setFine(bars) { return this._setSource('1m', bars); }
  setCoarse(bars) { return this._setSource('5m', bars); }
  updateFine(bar) { return this._updateSource('1m', bar); }
  updateCoarse(bar) { return this._updateSource('5m', bar); }

  init(paneBars) {
    this._pane = paneBars || [];
    return this.snapshot();
  }

  update(bar) {
    this._pane = mergeBar(this._pane, bar);
    return this.snapshot();
  }

  _setSource(name, bars) {
    this._src[name] = { bars: bars || [], groups: null };
    return this.snapshot();
  }

  // A live bar only touches its own session's group, so ticks don't regroup days of bars.
  _updateSource(name, bar) {
    const src = this._src[name];
    src.bars = mergeBar(src.bars, bar);
    const groups = src.groups;
    const lastKey = groups && [...groups.keys()].pop();
    if (groups && lastKey === this.sessionKey(bar.time)) {
      groups.set(lastKey, mergeBar(groups.get(lastKey), bar));
    } else {
      src.groups = null;
    }
    return this.snapshot();
  }

  _groups(name) {
    const src = this._src[name];
    if (!src.groups) src.groups = this._group(src.bars);
    return src.groups;
  }

  _group(bars) {
    const out = new Map();
    for (const b of bars) {
      const k = this.sessionKey(b.time);
      if (!out.has(k)) out.set(k, []);
      out.get(k).push(b);
    }
    return out;
  }

  // One session's profile in the source's prices, cached until its bars change.
  _profile(key, name, bars) {
    const last = bars[bars.length - 1];
    const sig = `${bars.length}|${last.time}|${last.close}|${last.high}|${last.low}|${last.volume}`;
    const ck = `${key}|${name}`;
    const hit = this._cache.get(ck);
    if (hit && hit.sig === sig) return hit.snap;
    const snap = new VolumeProfile({ bucketSize: this.bucketSize / this.scale,
      valueAreaPercent: this.valueAreaPercent }).init(bars);
    this._cache.set(ck, { sig, snap });
    return snap;
  }

  snapshot() {
    const bs = this.bucketSize;
    const out = { bucketSize: bs, sessions: [] };
    const pane = this._group(this._pane);
    const keys = [...pane.keys()].slice(-this.days);
    if (!keys.length) return out;
    let shift = 0;
    if (this.basis) {
      const fine = this._src['1m'].bars.length ? this._src['1m'].bars : this._src['5m'].bars;
      const b = basisAt(fine, this._pane, this.scale, this.paneSeconds);
      if (b == null) return out;
      shift = Math.round(b / bs) * bs;
    }
    const k = this.scale;
    const move = p => round8(p * k - shift);
    const fine = this._groups('1m'), coarse = this._groups('5m');
    const lastKey = keys[keys.length - 1];
    for (const key of keys) {
      const f = fine.get(key), c = coarse.get(key);
      const name = f && (!c || f[0].time <= c[0].time) ? '1m' : c ? '5m' : null;
      if (!name) continue;
      const snap = this._profile(key, name, name === '1m' ? f : c);
      if (snap.poc == null) continue;
      const bars = pane.get(key);
      const s = {
        key, source: name, current: key === lastKey,
        start: bars[0].time, end: bars[bars.length - 1].time,
        buckets: snap.buckets.map(b => ({ price: move(b.price), volume: b.volume })),
        poc: move(snap.poc), valueAreaLow: move(snap.valueAreaLow), valueAreaHigh: move(snap.valueAreaHigh),
      };
      s.pocLevel = round8(s.poc + bs / 2);
      s.valLevel = s.valueAreaLow;
      s.vahLevel = round8(s.valueAreaHigh + bs);
      s.pocEnd = s.current ? s.end : this._firstTouch(s.pocLevel, s.end);
      out.sessions.push(s);
    }
    return out;
  }

  _firstTouch(level, after) {
    for (const b of this._pane) {
      if (b.time <= after) continue;
      const hi = b.high ?? Math.max(b.open, b.close), lo = b.low ?? Math.min(b.open, b.close);
      if (lo <= level && level <= hi) return b.time;
    }
    return null;
  }
}
