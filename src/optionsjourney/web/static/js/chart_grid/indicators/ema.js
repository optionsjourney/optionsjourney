// Exponential moving average with SMA seeding (standard convention).
// init(candles) -> { initialPoints }  // value=null before period satisfied
// update(candle, { isClose }) -> { point }
//   isClose=true commits the candle and advances EMA state.
//   isClose=false returns a tentative value computed against the prior
//     committed EMA, so live ticks can update the last bar's EMA without
//     contaminating future values.
export class EMA {
  constructor({ period }) {
    this.period = period;
    this.k = 2 / (period + 1);
    this._committedEma = null;     // EMA of the last closed candle
    this._priorCommitted = null;   // EMA of the candle before the last closed one
    this._committedTime = null;
  }

  init(candles) {
    this._committedEma = null;
    this._priorCommitted = null;
    this._committedTime = null;
    const points = [];
    let sum = 0;
    for (let i = 0; i < candles.length; i++) {
      const c = candles[i];
      sum += c.close;
      if (i < this.period - 1) {
        points.push({ time: c.time, value: null });
      } else if (i === this.period - 1) {
        const seed = sum / this.period;
        this._priorCommitted = null;
        this._committedEma = seed;
        this._committedTime = c.time;
        points.push({ time: c.time, value: seed });
      } else {
        const next = c.close * this.k + this._committedEma * (1 - this.k);
        this._priorCommitted = this._committedEma;
        this._committedEma = next;
        this._committedTime = c.time;
        points.push({ time: c.time, value: next });
      }
    }
    return { initialPoints: points };
  }

  update(candle, { isClose }) {
    if (this._committedEma === null) {
      return { point: { time: candle.time, value: null } };
    }
    const isSameBucketAsCommitted = candle.time === this._committedTime;
    // For same-bucket tentative updates we want to derive from the EMA
    // *before* the currently-committed bar; otherwise from the committed EMA.
    const basis = isSameBucketAsCommitted
      ? (this._priorCommitted ?? this._committedEma)
      : this._committedEma;
    const next = candle.close * this.k + basis * (1 - this.k);
    if (isClose) {
      if (!isSameBucketAsCommitted) {
        this._priorCommitted = this._committedEma;
      }
      this._committedEma = next;
      this._committedTime = candle.time;
    }
    return { point: { time: candle.time, value: next } };
  }
}
