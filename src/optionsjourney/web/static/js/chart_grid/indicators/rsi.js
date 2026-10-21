// RSI with Wilder smoothing.
export class RSI {
  constructor({ period = 14 } = {}) {
    this.period = period;
    this._avgGain = null;
    this._avgLoss = null;
    this._prevClose = null;
    this._committedTime = null;
  }

  init(candles) {
    this._avgGain = null; this._avgLoss = null;
    this._prevClose = null; this._committedTime = null;
    const points = [];
    let gainSum = 0, lossSum = 0;
    for (let i = 0; i < candles.length; i++) {
      const c = candles[i];
      if (i === 0) {
        points.push({ time: c.time, value: null });
        this._prevClose = c.close;
        continue;
      }
      const change = c.close - this._prevClose;
      const gain = change > 0 ? change : 0;
      const loss = change < 0 ? -change : 0;
      if (i < this.period) {
        gainSum += gain; lossSum += loss;
        points.push({ time: c.time, value: null });
      } else if (i === this.period) {
        gainSum += gain; lossSum += loss;
        this._avgGain = gainSum / this.period;
        this._avgLoss = lossSum / this.period;
        const rs = this._avgLoss === 0 ? Infinity : this._avgGain / this._avgLoss;
        points.push({ time: c.time, value: 100 - 100 / (1 + rs) });
      } else {
        this._avgGain = (this._avgGain * (this.period - 1) + gain) / this.period;
        this._avgLoss = (this._avgLoss * (this.period - 1) + loss) / this.period;
        const rs = this._avgLoss === 0 ? Infinity : this._avgGain / this._avgLoss;
        points.push({ time: c.time, value: 100 - 100 / (1 + rs) });
      }
      this._prevClose = c.close;
      this._committedTime = c.time;
    }
    return { initialPoints: points };
  }

  update(candle, { isClose }) {
    if (this._avgGain === null || this._avgLoss === null || this._prevClose === null) {
      return { point: { time: candle.time, value: null } };
    }
    const change = candle.close - this._prevClose;
    const gain = change > 0 ? change : 0;
    const loss = change < 0 ? -change : 0;
    const nextAvgGain = (this._avgGain * (this.period - 1) + gain) / this.period;
    const nextAvgLoss = (this._avgLoss * (this.period - 1) + loss) / this.period;
    const rs = nextAvgLoss === 0 ? Infinity : nextAvgGain / nextAvgLoss;
    const v = 100 - 100 / (1 + rs);
    if (isClose && candle.time !== this._committedTime) {
      this._avgGain = nextAvgGain;
      this._avgLoss = nextAvgLoss;
      this._prevClose = candle.close;
      this._committedTime = candle.time;
    }
    return { point: { time: candle.time, value: v } };
  }
}
