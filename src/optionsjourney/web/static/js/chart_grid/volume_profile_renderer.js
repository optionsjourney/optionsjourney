// v5 series primitive: renders horizontal Volume Profile bars anchored to
// the right edge of the price pane. Bars grow leftward; length scales with
// volume relative to the snapshot's max. POC and value-area buckets are
// colored distinctly from the rest.

const COLOR_NORMAL = 'rgba(96, 165, 250, 0.25)';   // soft blue
const COLOR_VA     = 'rgba(96, 165, 250, 0.45)';   // stronger blue for value area
const COLOR_POC    = 'rgba(251, 192, 45, 0.7)';    // yellow POC

const MAX_WIDTH_FRAC = 0.25;  // bars occupy at most 25% of chart width

class VolumeProfileRenderer {
  constructor(snapshot, getPriceCoord, bucketSize) {
    this._snapshot = snapshot;
    this._getPriceCoord = getPriceCoord;
    this._bucketSize = bucketSize;
  }
  draw(target) {
    const snap = this._snapshot;
    if (!snap || !snap.buckets || snap.buckets.length === 0) return;
    const maxVol = snap.buckets.reduce((m, b) => Math.max(m, b.volume), 0);
    if (maxVol === 0) return;
    target.useBitmapCoordinateSpace(({ context: ctx, bitmapSize, horizontalPixelRatio, verticalPixelRatio }) => {
      ctx.save();
      const rightEdge = bitmapSize.width;
      const maxBarPx = bitmapSize.width * MAX_WIDTH_FRAC;
      // 1px gap in CSS pixels = verticalPixelRatio in bitmap pixels.
      const gapPx = Math.max(1, Math.round(verticalPixelRatio));

      for (const b of snap.buckets) {
        const y0 = this._getPriceCoord(b.price);
        const y1 = this._getPriceCoord(b.price + this._bucketSize);
        if (y0 === null || y1 === null) continue;
        // Round to integer bitmap pixels so adjacent bars don't share
        // fractional pixels (which alpha-blends and creates "mushed" look).
        const top = Math.round(Math.min(y0, y1) * verticalPixelRatio);
        const bot = Math.round(Math.max(y0, y1) * verticalPixelRatio);
        const height = Math.max(1, bot - top - gapPx);
        const barLen = Math.max(1, Math.round((b.volume / maxVol) * maxBarPx));

        let color = COLOR_NORMAL;
        if (b.price === snap.poc) color = COLOR_POC;
        else if (snap.valueAreaLow !== null && b.price >= snap.valueAreaLow
                 && b.price <= snap.valueAreaHigh) color = COLOR_VA;

        ctx.fillStyle = color;
        ctx.fillRect(rightEdge - barLen, top, barLen, height);
      }
      ctx.restore();
    });
  }
}

class VolumeProfilePaneView {
  constructor(primitive) { this._primitive = primitive; }
  renderer() {
    return new VolumeProfileRenderer(
      this._primitive._snapshot,
      (price) => this._primitive._series?.priceToCoordinate(price),
      this._primitive._bucketSize,
    );
  }
}

export class VolumeProfilePrimitive {
  constructor(bucketSize = 0.25) {
    this._snapshot = null;
    this._bucketSize = bucketSize;
    this._series = null;
    this._paneViews = [new VolumeProfilePaneView(this)];
  }
  attached({ series }) {
    this._series = series;
  }
  detached() {
    this._series = null;
  }
  updateAllViews() {}
  paneViews() { return this._paneViews; }
  setSnapshot(snapshot) {
    this._snapshot = snapshot;
  }
}
