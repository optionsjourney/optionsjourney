// Previous-trading-day OHLC reference levels (PDH, PDL, PDO, PDC) for
// intraday charts. Computes the most recent ET calendar day prior to the
// last candle's day, takes that session's high/low/open/close, and emits
// them as the "snapshot." Rendering is owned by ChartPane via createPriceLine.
// `dayKey(time)` overrides the ET calendar date (futures sessions open at 18:00 ET).
export class PrevDay {
  constructor({ dayKey = null } = {}) {
    this._keyFn = dayKey;
    this._fmt = new Intl.DateTimeFormat('en-US', {
      timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit',
    });
    this._snapshot = null;
  }

  _dayKey(timeSec) {
    if (this._keyFn) return this._keyFn(timeSec);
    return this._fmt.format(new Date(timeSec * 1000));
  }

  _compute(candles) {
    if (!candles || candles.length === 0) return null;
    const lastKey = this._dayKey(candles[candles.length - 1].time);
    // Walk backward to find the first candle whose ET date differs.
    let prevKey = null;
    let endIdx = -1;
    for (let i = candles.length - 1; i >= 0; i--) {
      const k = this._dayKey(candles[i].time);
      if (k !== lastKey) { prevKey = k; endIdx = i; break; }
    }
    if (prevKey === null) return null;
    // Walk back further to find the first candle of that prev day.
    let startIdx = endIdx;
    for (let i = endIdx; i >= 0; i--) {
      if (this._dayKey(candles[i].time) === prevKey) startIdx = i;
      else break;
    }
    let high = -Infinity, low = Infinity;
    for (let i = startIdx; i <= endIdx; i++) {
      if (candles[i].high > high) high = candles[i].high;
      if (candles[i].low < low) low = candles[i].low;
    }
    return {
      high, low,
      open: candles[startIdx].open,
      close: candles[endIdx].close,
    };
  }

  init(candles) {
    this._snapshot = this._compute(candles);
    this._candleBuffer = candles ? [...candles] : [];
    return this._snapshot;
  }

  update(candle, { isClose }) {
    // Only the boundary between days matters. On any close+open we recompute
    // since the prior-day set may have changed (i.e. a new day started).
    if (isClose) {
      this._candleBuffer.push(candle);
      // Cap buffer so it doesn't grow unbounded over a long live session.
      if (this._candleBuffer.length > 2000) {
        this._candleBuffer = this._candleBuffer.slice(-2000);
      }
      this._snapshot = this._compute(this._candleBuffer);
    }
    return this._snapshot;
  }
}
