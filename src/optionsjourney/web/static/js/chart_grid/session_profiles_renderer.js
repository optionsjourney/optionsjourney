// v5 series primitive for per-session Volume Profiles (indicators/session_profiles.js): each
// session's histogram starts at its first bar and grows right, its longest bar MAX_WIDTH_FRAC of
// the session's width (each session scaled to its own peak). The POC is a solid line from the
// session's start to the first bar that trades through it, or to the right edge while it is
// still naked; VAH/VAL are dashed lines across the session only. Projects times/prices every
// paint, so it follows zoom and pan.

const COLOR_NORMAL = 'rgba(96, 165, 250, 0.22)';
const COLOR_VA     = 'rgba(96, 165, 250, 0.42)';
const VA_LINE      = 'rgba(96, 165, 250, 0.8)';
export const POC_COLOR = '#fbc02d';
const MAX_WIDTH_FRAC = 0.9;

function withAlpha(hex, alpha) {
  const m = /^#([0-9a-f]{6})$/i.exec(hex || '');
  if (!m) return hex;
  const n = parseInt(m[1], 16);
  return `rgba(${n >> 16}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

class SessionProfilesRenderer {
  constructor(src) { this._src = src; }
  draw(target) {
    const { snapshot, color, timeScale, series } = this._src;
    if (!timeScale || !series || !snapshot?.sessions?.length) return;
    const bs = snapshot.bucketSize;
    const half = (timeScale.options?.().barSpacing ?? 6) / 2;
    target.useBitmapCoordinateSpace(({ context: ctx, bitmapSize, horizontalPixelRatio: hr, verticalPixelRatio: vr }) => {
      ctx.save();
      const gap = Math.max(1, Math.round(vr));
      const hline = (y, x0, x1) => {
        const py = Math.round(y * vr) + 0.5;
        ctx.beginPath();
        ctx.moveTo(x0, py);
        ctx.lineTo(x1, py);
        ctx.stroke();
      };
      for (const s of snapshot.sessions) {
        const xs = timeScale.timeToCoordinate(s.start);
        const xe = timeScale.timeToCoordinate(s.end);
        if (xs == null || xe == null) continue;
        const left = Math.round((xs - half) * hr), right = Math.round((xe + half) * hr);
        const maxVol = s.buckets.reduce((m, b) => Math.max(m, b.volume), 0);
        if (!maxVol || right <= left) continue;
        const maxLen = (right - left) * MAX_WIDTH_FRAC;
        for (const b of s.buckets) {
          const y0 = series.priceToCoordinate(b.price);
          const y1 = series.priceToCoordinate(b.price + bs);
          if (y0 == null || y1 == null) continue;
          const top = Math.round(Math.min(y0, y1) * vr);
          const bot = Math.round(Math.max(y0, y1) * vr);
          const len = Math.max(1, Math.round((b.volume / maxVol) * maxLen));
          ctx.fillStyle = b.price === s.poc ? withAlpha(color, 0.75)
            : (b.price >= s.valueAreaLow && b.price <= s.valueAreaHigh) ? COLOR_VA : COLOR_NORMAL;
          ctx.fillRect(left, top, len, Math.max(1, bot - top - gap));
        }
        ctx.lineWidth = Math.max(1, Math.round(hr));
        ctx.strokeStyle = VA_LINE;
        ctx.setLineDash([4 * hr, 3 * hr]);
        for (const level of [s.vahLevel, s.valLevel]) {
          const y = series.priceToCoordinate(level);
          if (y != null) hline(y, left, right);
        }
        ctx.setLineDash([]);
        const yp = series.priceToCoordinate(s.pocLevel);
        if (yp != null) {
          const xt = s.pocEnd == null ? null : timeScale.timeToCoordinate(s.pocEnd);
          const end = s.pocEnd == null ? bitmapSize.width
            : xt == null ? right : Math.round((xt + half) * hr);
          ctx.strokeStyle = color;
          hline(yp, left, end);
        }
      }
      ctx.restore();
    });
  }
}

class SessionProfilesPaneView {
  constructor(src) { this._src = src; }
  renderer() { return new SessionProfilesRenderer(this._src); }
}

export class SessionProfilesPrimitive {
  constructor(color = POC_COLOR) {
    this.color = color;
    this.snapshot = null;
    this.timeScale = null;
    this.series = null;
    this._requestUpdate = null;
    this._paneViews = [new SessionProfilesPaneView(this)];
  }
  attached({ chart, series, requestUpdate }) {
    this.timeScale = chart.timeScale();
    this.series = series;
    this._requestUpdate = requestUpdate;
  }
  detached() { this.timeScale = null; this.series = null; this._requestUpdate = null; }
  updateAllViews() {}
  paneViews() { return this._paneViews; }
  setSnapshot(snapshot) {
    this.snapshot = snapshot;
    this._requestUpdate?.();
  }
  // The POC's colour (the indicator row's swatch).
  setColor(color) {
    this.color = color;
    this._requestUpdate?.();
  }
}
