// Bar helpers for the charts (from Tracker2's analyzer/chart_lines.js): the futures session a
// 5m bar belongs to, and a live bar merged into a chart's bars. No DOM.

// The trading day a futures 5m bar belongs to. Bar times are ET wall clock written as UTC,
// and the session opens at 18:00 ET for the next date, so VWAP and Prev Day split there.
export function futuresSessionKey(timeSec) {
  return new Date((timeSec + 6 * 3600) * 1000).toISOString().slice(0, 10);
}

// A live bar from the chart feed into a chart's bars (sorted by time); returns a new array.
export function mergeBar(candles, bar) {
  const out = (candles || []).slice();
  const last = out[out.length - 1];
  if (!last || bar.time > last.time) { out.push(bar); return out; }
  if (bar.time === last.time) { out[out.length - 1] = bar; return out; }
  const i = out.findIndex(c => c.time >= bar.time);
  out.splice(i, out[i].time === bar.time ? 1 : 0, bar);
  return out;
}
