// Tips for the indicators in the chart's Indicators drawer (indicators/index.js wraps only the
// label output; the copied grid is otherwise untouched). Rendered with tip.js; ids are
// 'chart-<indicator id>'. The names are only in the DOM in that drawer: the lines themselves
// are drawn in the chart's canvas, where a tip can't go.
// Checked against indicators/vwap.js, orb.js, keltner.js, macd.js, rsi.js, expected_move.js
// and the series pane.js creates for them.
import { tipHtml } from '../tip.js';

export const CHART_TIPS = {
  vwap: "The average price paid today, weighted by volume, drawn as a yellow line with a dashed line one standard deviation above and below. It starts again at each New York date. It needs volume, so a symbol with none shows nothing.",
  orb: "The opening range: the high and low of the first 30 minutes after the 9:30 open, with a midpoint, held across the rest of that day. An arrow marks the first bar that closes above or below the range. Use + to add other lengths in minutes; a timeframe longer than the range draws nothing.",
  kc: "A middle line, the 20-bar EMA of the close, with bands 2 times the 10-bar ATR above and below it. Unlike Bollinger bands, its width follows the bars' high-to-low range, not the spread of closes. Fixed at 20, 2 and 10 here.",
  macd: "Shown in a panel below the chart: the 12-bar EMA minus the 26-bar EMA (blue), a 9-bar EMA of that line (orange), and bars for the gap between the two. All use the close.",
  rsi: "Shown in a panel below the chart: a 14-bar RSI of the close, from 0 to 100. The shaded band is 35 to 65; the line turns orange above 65 and red below 35.",
  exp_move: "Today's expected range, drawn as dashed lines one and two standard deviations above and below the 9:30 open. The size comes from a volatility index read at that open (VIX1D, else VIX9D, else VIX for S&P symbols; VXN for Nasdaq; RVX for Russell), as open x index / 100 / the square root of 365. Other symbols, and daily or longer charts, draw nothing.",
};

// Guide sections for "Learn more" (docs/guide/charts.md).
export const CHART_TIPS_MORE = {
  vwap: '/help/charts#indicators',
  orb: '/help/charts#indicators',
  kc: '/help/charts#indicators',
  macd: '/help/charts#indicators',
  rsi: '/help/charts#indicators',
  exp_move: '/help/charts#indicators',
};

// The tip for an indicator's label, or '' when it has none.
export function chartTipHtml(id, label) {
  const text = CHART_TIPS[id];
  return text ? tipHtml('chart-' + id, text, label, { more: CHART_TIPS_MORE[id] }) : '';
}
