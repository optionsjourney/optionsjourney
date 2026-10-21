// Bollinger Bands: SMA(period) ± mult × the POPULATION standard deviation of the same
// window (TradingView's convention). init(candles) -> { initialPoints: [{time, middle,
// upper, lower}] }, all null until the window fills. update(candle, {isClose}) mirrors
// sma.js: isClose commits, otherwise a tentative point off a copy of the window.
export class Bollinger {
  constructor({ period = 20, mult = 2 } = {}) {
    this.period = period;
    this.mult = mult;
    this._window = [];
    this._lastCommittedTime = null;
  }

  _point(time, win) {
    if (win.length < this.period) return { time, middle: null, upper: null, lower: null };
    const mean = win.reduce((a, b) => a + b, 0) / this.period;
    const sd = Math.sqrt(win.reduce((a, b) => a + (b - mean) ** 2, 0) / this.period);
    return { time, middle: mean, upper: mean + this.mult * sd, lower: mean - this.mult * sd };
  }

  _push(win, candle, replace) {
    if (replace) win[win.length - 1] = candle.close;
    else {
      win.push(candle.close);
      if (win.length > this.period) win.shift();
    }
  }

  init(candles) {
    this._window = [];
    this._lastCommittedTime = null;
    const initialPoints = [];
    for (const c of candles) {
      this._push(this._window, c, false);
      this._lastCommittedTime = c.time;
      initialPoints.push(this._point(c.time, this._window));
    }
    return { initialPoints };
  }

  update(candle, { isClose }) {
    const same = candle.time === this._lastCommittedTime;
    if (isClose) {
      this._push(this._window, candle, same);
      this._lastCommittedTime = candle.time;
      return { point: this._point(candle.time, this._window) };
    }
    const win = [...this._window];
    this._push(win, candle, same);
    return { point: this._point(candle.time, win) };
  }
}
