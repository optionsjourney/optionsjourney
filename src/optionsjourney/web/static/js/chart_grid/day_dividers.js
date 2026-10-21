// v5 series primitive: draws thin vertical lines at session-boundary times.
// Attached to the candlestick series; reads chart timeScale every paint to
// project times → x pixels so the lines stay aligned through zoom/pan.

const COLOR = 'rgba(96, 165, 250, 0.35)'; // soft blue, low opacity

class DayDividerRenderer {
  constructor(times, getCoord) {
    this._times = times;
    this._getCoord = getCoord;
  }
  draw(target) {
    target.useBitmapCoordinateSpace(({ context: ctx, bitmapSize, horizontalPixelRatio }) => {
      ctx.save();
      ctx.strokeStyle = COLOR;
      ctx.lineWidth = Math.max(1, Math.round(horizontalPixelRatio));
      ctx.beginPath();
      for (const t of this._times) {
        const x = this._getCoord(t);
        if (x === null || x === undefined) continue;
        const px = Math.round(x * horizontalPixelRatio) + 0.5;
        ctx.moveTo(px, 0);
        ctx.lineTo(px, bitmapSize.height);
      }
      ctx.stroke();
      ctx.restore();
    });
  }
}

class DayDividerPaneView {
  constructor(primitive) { this._primitive = primitive; }
  renderer() {
    return new DayDividerRenderer(
      this._primitive._times,
      (t) => this._primitive._timeScale?.timeToCoordinate(t),
    );
  }
}

export class DayDividerPrimitive {
  constructor() {
    this._times = [];
    this._timeScale = null;
    this._paneViews = [new DayDividerPaneView(this)];
  }
  attached({ chart }) {
    this._chart = chart;
    this._timeScale = chart.timeScale();
  }
  detached() {
    this._chart = null;
    this._timeScale = null;
  }
  updateAllViews() { /* paneViews recreate renderer each frame */ }
  paneViews() { return this._paneViews; }
  setTimes(times) {
    this._times = times || [];
    // Force a redraw by toggling a no-op option; v5 redraws when the series
    // it's attached to redraws, which happens on update(). Rely on the next
    // chart event to repaint — for our use this is fine because setTimes is
    // called right after setData/update which triggers a paint.
  }
}

// Given an array of {time} candles, return the candle times where the ET
// calendar date differs from the previous candle's ET calendar date.
export function computeDayBoundaries(candles) {
  if (!candles || candles.length === 0) return [];
  const fmt = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/New_York', year: 'numeric', month: '2-digit', day: '2-digit',
  });
  const boundaries = [];
  let prevKey = null;
  for (const c of candles) {
    const key = fmt.format(new Date(c.time * 1000));
    if (prevKey !== null && key !== prevKey) {
      boundaries.push(c.time);
    }
    prevKey = key;
  }
  return boundaries;
}
