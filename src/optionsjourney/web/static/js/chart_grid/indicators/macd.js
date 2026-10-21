import { EMA } from './ema.js';

export class MACD {
  constructor({ fast = 12, slow = 26, signal = 9 } = {}) {
    this.fast = fast;
    this.slow = slow;
    this.signalPeriod = signal;
    this.emaFast = new EMA({ period: fast });
    this.emaSlow = new EMA({ period: slow });
    this.emaSignal = new EMA({ period: signal });
    // Buffer of macd values for signal EMA seeding (SMA convention).
    this._signalSeedBuf = [];
    this._signalSeeded = false;
  }

  _maybeSeedSignal(time, macdVal) {
    if (this._signalSeeded) return null;
    this._signalSeedBuf.push({ time, close: macdVal });
    if (this._signalSeedBuf.length === this.signalPeriod) {
      this.emaSignal.init(this._signalSeedBuf);
      this._signalSeeded = true;
      const sum = this._signalSeedBuf.reduce((a, b) => a + b.close, 0);
      return sum / this.signalPeriod;
    }
    return null;
  }

  init(candles) {
    this.emaFast = new EMA({ period: this.fast });
    this.emaSlow = new EMA({ period: this.slow });
    this.emaSignal = new EMA({ period: this.signalPeriod });
    this._signalSeedBuf = [];
    this._signalSeeded = false;

    const fastPts = this.emaFast.init(candles).initialPoints;
    const slowPts = this.emaSlow.init(candles).initialPoints;
    const points = [];
    for (let i = 0; i < candles.length; i++) {
      const f = fastPts[i].value, s = slowPts[i].value;
      const macd = (f !== null && s !== null) ? f - s : null;
      let signalVal = null, hist = null;
      if (macd !== null) {
        if (!this._signalSeeded) {
          signalVal = this._maybeSeedSignal(candles[i].time, macd);
        } else {
          const out = this.emaSignal.update(
            { time: candles[i].time, close: macd }, { isClose: true }
          );
          signalVal = out.point.value;
        }
        if (signalVal !== null) hist = macd - signalVal;
      }
      points.push({ time: candles[i].time, macd, signal: signalVal, histogram: hist });
    }
    return { initialPoints: points };
  }

  update(candle, { isClose }) {
    const fOut = this.emaFast.update(candle, { isClose });
    const sOut = this.emaSlow.update(candle, { isClose });
    const f = fOut.point.value, s = sOut.point.value;
    const macd = (f !== null && s !== null) ? f - s : null;
    let signalVal = null, hist = null;
    if (macd !== null) {
      if (!this._signalSeeded) {
        if (isClose) signalVal = this._maybeSeedSignal(candle.time, macd);
      } else {
        const out = this.emaSignal.update(
          { time: candle.time, close: macd }, { isClose }
        );
        signalVal = out.point.value;
      }
      if (signalVal !== null) hist = macd - signalVal;
    }
    return { point: { time: candle.time, macd, signal: signalVal, histogram: hist } };
  }
}
