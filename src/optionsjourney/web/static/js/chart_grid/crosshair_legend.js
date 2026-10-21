// Hover readout for a grid pane: the date/time and OHLC of the bar under the
// crosshair, and the price at the cursor. Bar times are read in UTC: the chart feed
// writes intraday times as New York wall clock in a UTC epoch (chart_feed.py) and
// daily (and weekly, monthly) times as 00:00 UTC of the session date, so UTC shows the
// ET clock and date.

export const isDailyOrLonger = tf => tf === '1d' || tf === '1w' || tf === '1mo';

const fmt = (locale, opts) => new Intl.DateTimeFormat(locale, { timeZone: 'UTC', ...opts });

export function formatBarTime(timeSec, timeframe, locale = 'en-US') {
  const d = new Date(timeSec * 1000);
  if (isDailyOrLonger(timeframe)) {
    return fmt(locale, { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' }).format(d);
  }
  const day = fmt(locale, { weekday: 'short', month: 'short', day: 'numeric' }).format(d);
  const time = fmt(locale, { hour: '2-digit', minute: '2-digit', hour12: false }).format(d);
  return `${day} ${time}`;
}

// Axis tick label: "MMM D" at day/month/year ticks, "HH:MM" otherwise.
export function formatTick(timeSec, tickMarkType, timeframe, locale = 'en-US') {
  const d = new Date(timeSec * 1000);
  // tickMarkType: 0=year, 1=month, 2=dayOfMonth, 3=time, 4=timeWithSeconds
  if (tickMarkType <= 2) return fmt(locale, { month: 'short', day: 'numeric' }).format(d);
  return fmt(locale, { hour: '2-digit', minute: '2-digit', hour12: false }).format(d);
}

export function legendHtml(bar, timeframe, cursorPrice) {
  const f = (v) => v.toFixed(2);
  const dir = bar.close >= bar.open ? 'up' : 'down';
  const chg = bar.close - bar.open;
  const pct = bar.open ? (chg / bar.open) * 100 : 0;
  const sign = chg >= 0 ? '+' : '';
  return `<span class="lg-time">${formatBarTime(bar.time, timeframe)}</span>`
    + `<span>O <b class="${dir}">${f(bar.open)}</b></span>`
    + `<span>H <b class="${dir}">${f(bar.high)}</b></span>`
    + `<span>L <b class="${dir}">${f(bar.low)}</b></span>`
    + `<span>C <b class="${dir}">${f(bar.close)}</b></span>`
    + `<span class="lg-chg ${dir}">${sign}${f(chg)} (${sign}${pct.toFixed(2)}%)</span>`
    + (cursorPrice != null ? `<span class="lg-cursor">@ ${f(cursorPrice)}</span>` : '');
}

// Wire the readout to a chart. Shows the last bar when the cursor leaves the plot.
export function attachCrosshairLegend({ chart, series, el, getTimeframe, getLastBar }) {
  const show = (bar, price) => {
    if (!bar) { el.innerHTML = ''; return; }
    el.innerHTML = legendHtml(bar, getTimeframe(), price);
  };
  const handler = (param) => {
    const bar = param?.time != null ? param.seriesData?.get(series) : null;
    if (!bar || bar.open == null) { show(getLastBar(), null); return; }
    // Only the price pane's y maps to a price; indicator panes below have their own scales.
    const onPricePane = param.point && (param.paneIndex == null || param.paneIndex === 0);
    const price = onPricePane ? series.coordinateToPrice(param.point.y) : null;
    show({ ...bar, time: param.time }, price);
  };
  chart.subscribeCrosshairMove(handler);
  return {
    refresh: () => show(getLastBar(), null),
    detach: () => chart.unsubscribeCrosshairMove(handler),
  };
}
