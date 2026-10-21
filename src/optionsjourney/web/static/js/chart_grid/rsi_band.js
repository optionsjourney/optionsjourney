// v5 series primitive: shades the RSI pane between two levels (35–65).
// Attached to the RSI line series, so it lives and dies with that series and
// projects prices → y pixels through the RSI pane's own scale every paint.

const COLOR = 'rgba(202, 168, 64, 0.12)'; // dull yellow, low opacity

class RsiBandRenderer {
  constructor(top, bottom) { this._top = top; this._bottom = bottom; }
  draw() {}
  drawBackground(target) {
    if (this._top === null || this._bottom === null) return;
    target.useBitmapCoordinateSpace(({ context: ctx, bitmapSize, verticalPixelRatio }) => {
      const y0 = Math.round(Math.min(this._top, this._bottom) * verticalPixelRatio);
      const y1 = Math.round(Math.max(this._top, this._bottom) * verticalPixelRatio);
      ctx.fillStyle = COLOR;
      ctx.fillRect(0, y0, bitmapSize.width, y1 - y0);
    });
  }
}

class RsiBandPaneView {
  constructor(primitive) { this._primitive = primitive; }
  zOrder() { return 'bottom'; }
  renderer() {
    const p = this._primitive;
    const y = (v) => p._series?.priceToCoordinate(v) ?? null;
    return new RsiBandRenderer(y(p.high), y(p.low));
  }
}

export class RsiBandPrimitive {
  constructor(low = 35, high = 65) {
    this.low = low;
    this.high = high;
    this._series = null;
    this._paneViews = [new RsiBandPaneView(this)];
  }
  attached({ series }) { this._series = series; }
  detached() { this._series = null; }
  updateAllViews() { /* paneViews recreate renderer each frame */ }
  paneViews() { return this._paneViews; }
}
