// Session-resetting VWAP with +/-1sigma and +/-2sigma bands.
// Session boundary = America/New_York wall-clock date change, or wherever `sessionKey(time)`
// changes when one is given (futures sessions open at 18:00 ET).
export class VWAP {
  constructor({ tz = 'America/New_York', sessionKey = null } = {}) {
    this.tz = tz;
    this._keyFn = sessionKey;
    this._fmt = new Intl.DateTimeFormat('en-US', {
      timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit',
    });
    this._reset();
  }

  _reset() {
    this._sumPV = 0;
    this._sumV = 0;
    this._sumPPV = 0;
    this._lastSessionKey = null;
    this._lastCommittedTime = null;
  }

  _sessionKey(timeSec) {
    if (this._keyFn) return this._keyFn(timeSec);
    return this._fmt.format(new Date(timeSec * 1000));
  }

  _hlc3(c) { return (c.high + c.low + c.close) / 3; }

  _calc(candle, commit) {
    const key = this._sessionKey(candle.time);
    if (key !== this._lastSessionKey) {
      this._sumPV = 0; this._sumV = 0; this._sumPPV = 0;
      this._lastSessionKey = key;
    }
    const p = this._hlc3(candle);
    const v = candle.volume || 0;
    const sumPV = this._sumPV + p * v;
    const sumV = this._sumV + v;
    const sumPPV = this._sumPPV + p * p * v;
    if (commit) {
      this._sumPV = sumPV; this._sumV = sumV; this._sumPPV = sumPPV;
      this._lastCommittedTime = candle.time;
    }
    if (sumV === 0) return { time: candle.time, vwap: null };
    const vwap = sumPV / sumV;
    const variance = Math.max(0, (sumPPV / sumV) - vwap * vwap);
    const sigma = Math.sqrt(variance);
    return {
      time: candle.time,
      vwap,
      upper1: vwap + sigma,
      lower1: vwap - sigma,
      upper2: vwap + 2 * sigma,
      lower2: vwap - 2 * sigma,
    };
  }

  init(candles) {
    this._reset();
    return { initialPoints: candles.map(c => this._calc(c, true)) };
  }

  update(candle, { isClose }) {
    return { point: this._calc(candle, isClose) };
  }
}
