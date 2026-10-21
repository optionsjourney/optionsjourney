// Opening Range Breakout: the high/low of each session's first `minutes` from 09:30, held
// across the rest of that day's 09:30–16:00 bars, plus the first bar each day that closes
// above the range (long breakout) and the first that closes below it (short breakout).
//
// A bar counts toward the range only when it lies wholly inside the window, so a timeframe
// coarser than the range (a 1h chart with a 30m ORB) draws nothing rather than a wrong range.
// Breakouts are only looked for once the range is complete (a bar at or after its end).
//
// Bar times are New York wall clock written as a UTC epoch (chart_feed.py), so the clock and
// date are read in UTC, as in expected_move.js.
//
// init(candles) -> { days }; update(candle) -> { days } with today's entry recomputed from
// today's bars only. Rendering is owned by ChartPane (orb_renderer.js + axis price lines).

import { barDate } from './expected_move.js';

const OPEN_MIN = 570, CLOSE_MIN = 960;
const clockMinutes = (sec) => { const d = new Date(sec * 1000); return d.getUTCHours() * 60 + d.getUTCMinutes(); };

// Bar length in minutes: the smallest gap between consecutive bars; 0 when unknown.
export function inferBarMinutes(candles) {
  let best = Infinity;
  for (let i = 1; i < (candles?.length || 0); i++) {
    const g = (candles[i].time - candles[i - 1].time) / 60;
    if (g > 0 && g < best) best = g;
  }
  return Number.isFinite(best) ? best : 0;
}

// One day's ORB from that day's bars (sorted), or null when no bar fits inside the window.
// { date, start, rangeEnd, end, high, low, mid, complete, breakouts: [{time, dir: 'up'|'down', price}] }
// where a breakout's price is the bar's low (up) or high (down), the edge its arrow sits off.
export function computeDay(bars, minutes, barMinutes) {
  const endMin = OPEN_MIN + minutes;
  let day = null;
  for (const b of bars) {
    const m = clockMinutes(b.time);
    if (m < OPEN_MIN || m >= CLOSE_MIN) continue;
    if (m + barMinutes <= endMin) {
      if (!day) day = { date: barDate(b.time), start: b.time, rangeEnd: b.time, end: b.time,
        high: b.high, low: b.low, complete: false, breakouts: [] };
      day.rangeEnd = b.time;
      day.end = b.time;
      if (b.high > day.high) day.high = b.high;
      if (b.low < day.low) day.low = b.low;
      continue;
    }
    if (!day) return null; // the session's first bar already runs past the window
    day.end = b.time;
    if (m < endMin) continue; // straddles the window's end: neither range nor breakout
    day.complete = true;
    const has = dir => day.breakouts.some(x => x.dir === dir);
    if (b.close > day.high && !has('up')) day.breakouts.push({ time: b.time, dir: 'up', price: b.low });
    if (b.close < day.low && !has('down')) day.breakouts.push({ time: b.time, dir: 'down', price: b.high });
  }
  if (day) day.mid = (day.high + day.low) / 2;
  return day;
}

export function computeORB(candles, minutes, barMinutes = inferBarMinutes(candles)) {
  const byDate = new Map();
  for (const c of candles || []) {
    const d = barDate(c.time);
    if (!byDate.has(d)) byDate.set(d, []);
    byDate.get(d).push(c);
  }
  const days = [];
  for (const bars of byDate.values()) {
    const day = computeDay(bars, minutes, barMinutes);
    if (day) days.push(day);
  }
  return days;
}

export class ORB {
  constructor({ minutes = 30 } = {}) {
    this.minutes = minutes;
    this._days = [];
    this._today = null;     // date of the latest bar
    this._todayBars = [];
    this._barMinutes = 0;
  }

  init(candles) {
    candles = candles || [];
    this._barMinutes = inferBarMinutes(candles);
    this._days = computeORB(candles, this.minutes, this._barMinutes);
    const last = candles[candles.length - 1];
    this._today = last ? barDate(last.time) : null;
    this._todayBars = last ? candles.filter(c => barDate(c.time) === this._today) : [];
    return { days: this._days };
  }

  update(candle) {
    const date = barDate(candle.time);
    if (date !== this._today) {
      this._today = date;
      this._todayBars = [];
    }
    const bars = this._todayBars;
    const last = bars[bars.length - 1];
    if (last && last.time === candle.time) bars[bars.length - 1] = candle;
    else {
      if (last && !this._barMinutes) this._barMinutes = (candle.time - last.time) / 60;
      bars.push(candle);
    }
    const day = computeDay(bars, this.minutes, this._barMinutes);
    const prev = this._days[this._days.length - 1];
    const keep = prev && prev.date === date ? this._days.slice(0, -1) : this._days;
    this._days = day ? [...keep, day] : keep;
    return { days: this._days };
  }
}
