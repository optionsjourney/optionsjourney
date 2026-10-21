export class SMA {
  constructor({ period }) {
    this.period = period;
    this._window = [];
    this._lastCommittedTime = null;
  }

  init(candles) {
    this._window = [];
    this._lastCommittedTime = null;
    const points = [];
    for (const c of candles) {
      this._window.push(c.close);
      if (this._window.length > this.period) this._window.shift();
      this._lastCommittedTime = c.time;
      if (this._window.length < this.period) {
        points.push({ time: c.time, value: null });
      } else {
        const avg = this._window.reduce((a, b) => a + b, 0) / this.period;
        points.push({ time: c.time, value: avg });
      }
    }
    return { initialPoints: points };
  }

  update(candle, { isClose }) {
    if (isClose) {
      if (candle.time === this._lastCommittedTime) {
        this._window[this._window.length - 1] = candle.close;
      } else {
        this._window.push(candle.close);
        if (this._window.length > this.period) this._window.shift();
        this._lastCommittedTime = candle.time;
      }
      if (this._window.length < this.period) {
        return { point: { time: candle.time, value: null } };
      }
      const avg = this._window.reduce((a, b) => a + b, 0) / this.period;
      return { point: { time: candle.time, value: avg } };
    }
    const win = [...this._window];
    if (candle.time === this._lastCommittedTime) {
      win[win.length - 1] = candle.close;
    } else {
      win.push(candle.close);
      if (win.length > this.period) win.shift();
    }
    if (win.length < this.period) {
      return { point: { time: candle.time, value: null } };
    }
    const avg = win.reduce((a, b) => a + b, 0) / this.period;
    return { point: { time: candle.time, value: avg } };
  }
}
