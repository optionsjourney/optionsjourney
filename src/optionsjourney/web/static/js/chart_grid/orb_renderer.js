// v5 series primitive for the Opening Range Breakout overlay: per day, a shaded box over the
// opening range, ThinkorSwim-style lines from 09:30 to the day's last session bar (green dashed
// ORH, cyan dotted midpoint, red dashed ORL), and an arrow on the
// first close beyond each side (up below the bar's low, down above its high, a fixed pixel gap
// off the candle). Projects times/prices every paint, so it follows zoom and pan.

export const UP_COLOR = '#22c39a';
export const DOWN_COLOR = '#f2545b';
const GAP_PX = 8, SIZE_PX = 9;
// [day key, colour, dash in media px] for each level line.
export const LEVELS = [
  ['high', '#22c55e', [10, 6]],
  ['mid', '#22d3ee', [3, 4]],
  ['low', '#ef4444', [10, 6]],
];

function withAlpha(hex, alpha) {
  const m = /^#([0-9a-f]{6})$/i.exec(hex || '');
  if (!m) return hex;
  const n = parseInt(m[1], 16);
  return `rgba(${n >> 16}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

class ORBRenderer {
  constructor(src) { this._src = src; }
  draw(target) {
    const { days, color, timeScale, series } = this._src;
    if (!timeScale || !series || !days.length) return;
    const half = (timeScale.options?.().barSpacing ?? 6) / 2;
    target.useBitmapCoordinateSpace(({ context: ctx, horizontalPixelRatio: hr, verticalPixelRatio: vr }) => {
      ctx.save();
      for (const d of days) {
        const x0 = timeScale.timeToCoordinate(d.start);
        const xr = timeScale.timeToCoordinate(d.rangeEnd);
        const x1 = timeScale.timeToCoordinate(d.end);
        const yh = series.priceToCoordinate(d.high);
        const yl = series.priceToCoordinate(d.low);
        if (x0 == null || x1 == null || yh == null || yl == null) continue;
        const left = Math.round((x0 - half) * hr), right = Math.round((x1 + half) * hr);
        const top = Math.round(yh * vr), bottom = Math.round(yl * vr);
        if (xr != null) {
          ctx.fillStyle = withAlpha(color, 0.12);
          ctx.fillRect(left, top, Math.round((xr + half) * hr) - left, bottom - top);
        }
        ctx.lineWidth = Math.max(2, Math.round(2 * hr));
        ctx.globalAlpha = d.complete ? 1 : 0.5; // still forming
        for (const [key, lineColor, dash] of LEVELS) {
          const y = series.priceToCoordinate(d[key]);
          if (y == null) continue;
          const py = Math.round(y * vr);
          ctx.strokeStyle = lineColor;
          ctx.setLineDash(dash.map(v => v * hr));
          ctx.beginPath();
          ctx.moveTo(left, py);
          ctx.lineTo(right, py);
          ctx.stroke();
        }
        ctx.setLineDash([]);
        ctx.globalAlpha = 1;
        for (const b of d.breakouts) {
          const x = timeScale.timeToCoordinate(b.time);
          if (x == null) continue;
          const up = b.dir === 'up';
          const y = series.priceToCoordinate(b.price);
          if (y == null) continue;
          // Tip GAP_PX off the candle, pointing at it; base SIZE_PX further away.
          const cx = x * hr, ty = (up ? y + GAP_PX : y - GAP_PX) * vr;
          const by = ty + (up ? 1 : -1) * SIZE_PX * vr, w = SIZE_PX * 0.6 * hr;
          ctx.fillStyle = up ? UP_COLOR : DOWN_COLOR;
          ctx.beginPath();
          ctx.moveTo(cx, ty);
          ctx.lineTo(cx + w, by);
          ctx.lineTo(cx - w, by);
          ctx.closePath();
          ctx.fill();
        }
      }
      ctx.restore();
    });
  }
}

class ORBPaneView {
  constructor(src) { this._src = src; }
  renderer() { return new ORBRenderer(this._src); }
}

export class ORBPrimitive {
  constructor(color) {
    this.color = color;
    this.days = [];
    this.timeScale = null;
    this.series = null;
    this._requestUpdate = null;
    this._paneViews = [new ORBPaneView(this)];
  }
  attached({ chart, series, requestUpdate }) {
    this.timeScale = chart.timeScale();
    this.series = series;
    this._requestUpdate = requestUpdate;
  }
  detached() { this.timeScale = null; this.series = null; this._requestUpdate = null; }
  updateAllViews() {}
  paneViews() { return this._paneViews; }
  // days from ORB.init/update.
  setDays(days) {
    this.days = days || [];
    this._requestUpdate?.();
  }
  setColor(color) {
    this.color = color;
    this._requestUpdate?.();
  }
}
