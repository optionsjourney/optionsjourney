// Turns the chart feed's bar changes into what the grid's indicators expect, which is the
// contract the old tick Aggregator had: a change to the forming bar is 'update'; a bar with a
// later time is 'close+open' (the previous bar is now final); a change to an older bar is
// 'revise', and the pane redraws from the whole series.
export class BarStepper {
  constructor() { this._last = null; }

  seed(bars) {
    this._last = bars && bars.length ? { ...bars[bars.length - 1] } : null;
  }

  step(bar) {
    const prev = this._last;
    if (!prev || bar.time > prev.time) {
      this._last = { ...bar };
      return prev ? { type: 'close+open', prevCandle: prev, candle: this._last }
        : { type: 'open', candle: this._last };
    }
    if (bar.time === prev.time) {
      this._last = { ...bar };
      return { type: 'update', candle: this._last };
    }
    return { type: 'revise', candle: { ...bar } };
  }
}
