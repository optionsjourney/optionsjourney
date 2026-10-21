// v5 series primitive: the MA cross arrows, drawn a fixed pixel gap beyond each bar's
// high/low. lightweight-charts' own markers sit tight against the candle with no setting
// for the gap, which made them hard to read. Up arrows hang below the low pointing up;
// down arrows sit above the high pointing down, each also clear of the crossing lines
// (marker.level), which run through the bar at a cross.

export const GAP_PX = 10;    // candle wick (or crossing line) to arrow tip
export const SIZE_PX = 12;   // arrow length; the head is as wide as the arrow is long

// The arrow's tip y and direction for a marker, in media pixels; null when off-scale.
export function arrowTip(marker, bar, priceToY) {
  const up = marker.shape === 'arrowUp';
  const level = marker.level ?? (up ? bar.low : bar.high);
  const y = priceToY(up ? Math.min(bar.low, level) : Math.max(bar.high, level));
  if (y == null) return null;
  return { y: up ? y + GAP_PX : y - GAP_PX, dir: up ? -1 : 1 };
}

class CrossArrowsRenderer {
  constructor(src) { this._src = src; }
  draw(target) {
    const { markers, bars, timeScale, series } = this._src;
    if (!timeScale || !series || !markers.length) return;
    target.useBitmapCoordinateSpace(({ context: ctx, horizontalPixelRatio: hr, verticalPixelRatio: vr }) => {
      ctx.save();
      for (const m of markers) {
        const bar = bars.get(m.time);
        const x = timeScale.timeToCoordinate(m.time);
        if (!bar || x == null) continue;
        const tip = arrowTip(m, bar, p => series.priceToCoordinate(p));
        if (!tip) continue;
        // dir -1: tip on top, body below (up arrow); dir 1: tip at the bottom (down arrow).
        const cx = x * hr, ty = tip.y * vr, len = SIZE_PX * vr, back = -tip.dir;
        const headLen = len * 0.55, headHalf = SIZE_PX * 0.5 * hr, shaftHalf = SIZE_PX * 0.16 * hr;
        ctx.fillStyle = m.color;
        ctx.beginPath();
        ctx.moveTo(cx, ty);
        ctx.lineTo(cx + headHalf, ty + back * headLen);
        ctx.lineTo(cx + shaftHalf, ty + back * headLen);
        ctx.lineTo(cx + shaftHalf, ty + back * len);
        ctx.lineTo(cx - shaftHalf, ty + back * len);
        ctx.lineTo(cx - shaftHalf, ty + back * headLen);
        ctx.lineTo(cx - headHalf, ty + back * headLen);
        ctx.closePath();
        ctx.fill();
      }
      ctx.restore();
    });
  }
}

class CrossArrowsPaneView {
  constructor(src) { this._src = src; }
  renderer() { return new CrossArrowsRenderer(this._src); }
}

export class CrossArrowsPrimitive {
  constructor() {
    this.markers = [];
    this.bars = new Map();
    this.timeScale = null;
    this.series = null;
    this._requestUpdate = null;
    this._paneViews = [new CrossArrowsPaneView(this)];
  }
  attached({ chart, series, requestUpdate }) {
    this.timeScale = chart.timeScale();
    this.series = series;
    this._requestUpdate = requestUpdate;
  }
  detached() { this.timeScale = null; this.series = null; this._requestUpdate = null; }
  updateAllViews() {}
  paneViews() { return this._paneViews; }
  // markers from findCrosses; candles to place them on (by time).
  setArrows(markers, candles) {
    this.markers = markers || [];
    this.bars = new Map((candles || []).map(c => [c.time, c]));
    this._requestUpdate?.();
  }
}
