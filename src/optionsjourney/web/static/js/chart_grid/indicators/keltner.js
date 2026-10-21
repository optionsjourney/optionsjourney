// Keltner Channels, TradingView's defaults: EMA(period) of close ± mult × ATR(atrPeriod), the
// ATR Wilder-smoothed (RMA) and both seeded with a simple average. init(candles) ->
// { initialPoints: [{time, middle, upper, lower}] }, all null until both have seeded.
// update(candle, {isClose}): isClose commits, otherwise a tentative point off the committed
// state; a candle with the last committed time replaces that bar.
export class Keltner {
  constructor({ period = 20, mult = 2, atrPeriod = 10 } = {}) {
    this.period = period;
    this.mult = mult;
    this.atrPeriod = atrPeriod;
    this.k = 2 / (period + 1);
    this._state = Keltner._empty();
    this._prior = null;              // the state before the last committed candle
    this._lastCommittedTime = null;
  }

  static _empty() {
    return { n: 0, closeSum: 0, ema: null, trSum: 0, atr: null, prevClose: null };
  }

  // The state after `c`, from `s` (left unchanged).
  _step(s, c) {
    const tr = s.prevClose === null ? c.high - c.low
      : Math.max(c.high - c.low, Math.abs(c.high - s.prevClose), Math.abs(c.low - s.prevClose));
    const n = s.n + 1;
    const next = { ...s, n, prevClose: c.close };
    if (s.ema !== null) next.ema = c.close * this.k + s.ema * (1 - this.k);
    else {
      next.closeSum = s.closeSum + c.close;
      if (n === this.period) next.ema = next.closeSum / this.period;
    }
    if (s.atr !== null) next.atr = (s.atr * (this.atrPeriod - 1) + tr) / this.atrPeriod;
    else {
      next.trSum = s.trSum + tr;
      if (n === this.atrPeriod) next.atr = next.trSum / this.atrPeriod;
    }
    return next;
  }

  _point(time, s) {
    if (s.ema === null || s.atr === null) return { time, middle: null, upper: null, lower: null };
    const w = this.mult * s.atr;
    return { time, middle: s.ema, upper: s.ema + w, lower: s.ema - w };
  }

  init(candles) {
    this._state = Keltner._empty();
    this._prior = null;
    this._lastCommittedTime = null;
    const initialPoints = [];
    for (const c of candles) {
      this._prior = this._state;
      this._state = this._step(this._state, c);
      this._lastCommittedTime = c.time;
      initialPoints.push(this._point(c.time, this._state));
    }
    return { initialPoints };
  }

  update(candle, { isClose }) {
    const same = candle.time === this._lastCommittedTime && this._prior !== null;
    const base = same ? this._prior : this._state;
    const next = this._step(base, candle);
    if (isClose) {
      this._prior = base;
      this._state = next;
      this._lastCommittedTime = candle.time;
    }
    return { point: this._point(candle.time, next) };
  }
}
