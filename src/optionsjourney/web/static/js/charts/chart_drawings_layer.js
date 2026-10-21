// Tracker-owned (not vendored -- VENDORED.md patch 16): drawing tools on a chart
// (charts_panel.js FrameChart) or a Multi-Chart Grid pane -- horizontal line, trend line, rectangle and Fibonacci
// retracement, drawn by a v5 series primitive on the candle series.
//
// Pick a tool, then drag (or click, click) on the chart; a horizontal line is one click. With no
// tool picked, click a drawing to select it, drag it or its end points, and Delete/Backspace
// removes it; Esc drops a tool or the selection. While a tool is picked (or a drawing is being
// dragged) the chart doesn't pan. Drawings are saved in the DB per symbol and timeframe (drawing_store.js),
// so every chart of that symbol and timeframe, on any page, shows the same ones and follows changes live.
// Pure helpers: chart_drawings.js.
import * as D from './chart_drawings.js';
import { getDrawingStore } from './drawing_store.js';

const COLOR = '#f59e0b';
const RECT_FILL = 'rgba(245, 158, 11, 0.12)';
const FIB_COLOR = '#94a3b8';
const STEP = { '1m': 60, '5m': 300, '15m': 900, '30m': 1800, '1h': 3600, '4h': 14400, '1d': 86400,
  '1w': 604800, '1mo': 2592000 };
const TOOL_BUTTONS = [
  { tool: 'hline', label: '—', title: 'Horizontal line' },
  { tool: 'trend', label: '╱', title: 'Trend line' },
  { tool: 'rect', label: '▭', title: 'Rectangle' },
  { tool: 'fib', label: 'Fib', title: 'Fibonacci retracement' },
];

const newId = () => Math.random().toString(36).slice(2, 10);

class Renderer {
  constructor(layer) { this.layer = layer; }
  draw(target) {
    const shapes = this.layer.shapes();
    if (!shapes.length) return;
    target.useMediaCoordinateSpace(({ context: ctx, mediaSize }) => {
      ctx.save();
      ctx.font = '10px sans-serif';
      for (const s of shapes) this.drawShape(ctx, s, mediaSize.width);
      ctx.restore();
    });
  }

  drawShape(ctx, s, width) {
    const selected = s.id === this.layer.selectedId;
    ctx.lineWidth = selected ? 2 : 1;
    ctx.strokeStyle = COLOR;
    ctx.fillStyle = COLOR;
    const line = (x0, y0, x1, y1) => { ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke(); };
    if (s.type === 'hline') {
      const y = s.pts[0].y;
      if (y == null) return;
      line(0, y, width, y);
      ctx.textAlign = 'right';
      ctx.fillText(s.p[0].price.toFixed(2), width - 4, y - 3);
      return;
    }
    const [a, b] = s.pts;
    if ([a.x, a.y, b.x, b.y].some(v => v == null)) return;
    if (s.type === 'trend') {
      line(a.x, a.y, b.x, b.y);
    } else if (s.type === 'rect') {
      ctx.fillStyle = RECT_FILL;
      ctx.fillRect(Math.min(a.x, b.x), Math.min(a.y, b.y), Math.abs(b.x - a.x), Math.abs(b.y - a.y));
      ctx.strokeRect(Math.min(a.x, b.x), Math.min(a.y, b.y), Math.abs(b.x - a.x), Math.abs(b.y - a.y));
    } else if (s.type === 'fib') {
      const x0 = Math.min(a.x, b.x), x1 = Math.max(a.x, b.x);
      ctx.strokeStyle = FIB_COLOR;
      ctx.fillStyle = FIB_COLOR;
      ctx.textAlign = 'left';
      D.fibLevels(s.p).forEach(({ level, price }, i) => {
        const y = s.levels[i];
        if (y == null) return;
        line(x0, y, x1, y);
        ctx.fillText(`${level} (${price.toFixed(2)})`, x0 + 2, y - 3);
      });
      ctx.setLineDash([4, 3]);
      line(a.x, a.y, b.x, b.y);
      ctx.setLineDash([]);
    }
    if (selected) {
      ctx.fillStyle = COLOR;
      for (const q of s.pts) { ctx.beginPath(); ctx.arc(q.x, q.y, 3.5, 0, 2 * Math.PI); ctx.fill(); }
    }
  }
}

class Primitive {
  constructor(layer) {
    this.views = [{ renderer: () => new Renderer(layer), zOrder: () => 'top' }];
  }
  attached({ requestUpdate }) { this.requestUpdate = requestUpdate; }
  detached() { this.requestUpdate = null; }
  updateAllViews() { /* the renderer reads the layer each paint */ }
  paneViews() { return this.views; }
}

// The layer whose drawing is selected, across every chart on the page: Delete acts on that one.
let selectedLayer = null;

export class DrawingLayer {
  // chart: {chart, candleSeries, plotEl, candles}. toolsEl: where the tool buttons go.
  // opts.interval: '1m' ... '1d'; opts.store: {load, save, watch} (drawing_store.js);
  // opts.onToolChange(tool|null): called whenever the picked tool changes.
  constructor(chart, toolsEl, { interval = '5m', store = getDrawingStore(), onToolChange = null } = {}) {
    this.fc = chart;
    this.onToolChange = onToolChange;
    this.toolsEl = toolsEl;
    this.store = store;
    this.interval = interval;
    this.step = STEP[interval] || 300;
    this.drawings = [];
    this.symbol = null;
    this.tool = null;
    this.selectedId = null;
    this.preview = null;     // the drawing being placed: {type, p}
    this.drag = null;        // {id, handle, orig: [{l, price}], l0, price0}
    this._unwatch = null;
    this._loadGen = 0;
    this.primitive = new Primitive(this);
    chart.candleSeries.attachPrimitive(this.primitive);
    this.renderTools();
    this._onToolClick = e => this.onToolClick(e);
    this._onDown = e => this.onDown(e);
    this._onMove = e => this.onMove(e);
    this._onUp = e => this.onUp(e);
    this._onKey = e => this.onKey(e);
    toolsEl.addEventListener('click', this._onToolClick);
    chart.plotEl.addEventListener('pointerdown', this._onDown, true);
    chart.plotEl.addEventListener('pointermove', this._onMove, true);
    window.addEventListener('pointerup', this._onUp, true);
    window.addEventListener('keydown', this._onKey);
  }

  destroy() {
    this._destroyed = true;
    if (this._unwatch) this._unwatch();
    this._unwatch = null;
    if (selectedLayer === this) selectedLayer = null;
    this.toolsEl.removeEventListener('click', this._onToolClick);
    this.fc.plotEl.removeEventListener('pointerdown', this._onDown, true);
    this.fc.plotEl.removeEventListener('pointermove', this._onMove, true);
    window.removeEventListener('pointerup', this._onUp, true);
    window.removeEventListener('keydown', this._onKey);
    try { this.fc.candleSeries.detachPrimitive(this.primitive); } catch (e) { /* chart gone */ }
  }

  // --- saved drawings ---
  setSymbol(symbol) { this.setTarget(symbol, this.interval); }

  setTarget(symbol, interval) {
    if (symbol === this.symbol && interval === this.interval) return;
    if (this._unwatch) this._unwatch();
    this._unwatch = null;
    this.symbol = symbol || null;
    this.interval = interval;
    this.step = STEP[interval] || 300;
    this.select(null);
    this.preview = null;
    this.drag = null;
    this.drawings = [];
    if (this.symbol) this._unwatch = this.store.watch(this.symbol, this.interval, () => this.reload());
    this.reload();
  }

  async reload() {
    const gen = ++this._loadGen;
    const drawings = this.symbol ? await this.store.load(this.symbol, this.interval) : [];
    if (gen !== this._loadGen || this.drag || this._destroyed) return;
    this.drawings = drawings;
    if (!this.drawings.some(d => d.id === this.selectedId)) this.select(null);
    this.renderTools();
    this.repaint();
  }

  save() {
    if (!this.symbol) return;
    this._loadGen++;                   // a load still in flight predates this change
    this.store.save(this.symbol, this.interval, this.drawings);
    this.renderTools();
  }

  // Select a drawing here (null clears); a selection here clears any other chart's.
  select(id) {
    this.selectedId = id;
    if (id) {
      if (selectedLayer && selectedLayer !== this) selectedLayer.select(null);
      selectedLayer = this;
    } else if (selectedLayer === this) {
      selectedLayer = null;
    }
    this.repaint();
  }

  // --- toolbar ---
  renderTools() {
    const btn = (data, label, title, on) => `<button type="button" ${data} title="${title}" class="text-[10px] min-w-[1.5rem] px-1.5 py-0.5 rounded border ${on
      ? 'border-amber-500 bg-amber-50 dark:bg-amber-900/40 text-amber-700 dark:text-amber-300'
      : 'border-gray-300 dark:border-gray-600 text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200'}">${label}</button>`;
    this.toolsEl.innerHTML = TOOL_BUTTONS.map(b => btn(`data-tool="${b.tool}"`, b.label,
      `${b.title} (Esc to cancel)`, this.tool === b.tool)).join('')
      + (this.drawings.length ? btn('data-tool-clear', '✕', 'Remove every drawing on this chart', false) : '');
  }
  onToolClick(e) {
    const b = e.target.closest('button');
    if (!b) return;
    if (b.hasAttribute('data-tool-clear')) {
      if (!window.confirm('Remove every drawing on this chart?')) return;
      this.drawings = [];
      this.select(null);
      this.save();
      this.repaint();
      return;
    }
    this.setTool(this.tool === b.dataset.tool ? null : b.dataset.tool);
  }
  setTool(tool) {
    this.tool = tool;
    this.preview = null;
    if (tool) this.select(null);
    this.lockChart(!!tool);
    this.fc.plotEl.style.cursor = tool ? 'crosshair' : '';
    this.renderTools();
    this.repaint();
    this.onToolChange?.(tool);
  }
  lockChart(on) {
    this.fc.chart.applyOptions({ handleScroll: !on, handleScale: !on });
  }

  // --- coordinates ---
  times() {
    const c = this.fc.candles;
    if (this._timesOf !== c) { this._timesOf = c; this._times = c.map(k => k.time); }
    return this._times;
  }
  toX(time) {
    const l = D.timeToLogical(this.times(), time, this.step);
    if (l == null) return null;
    const x = this.fc.chart.timeScale().logicalToCoordinate(l);
    return x == null ? null : x;
  }
  toY(price) {
    const y = this.fc.candleSeries.priceToCoordinate(price);
    return y == null ? null : y;
  }
  // The pointer as {x, y, l (whole bar), time, price}, or null outside the plot pane.
  at(e) {
    const r = this.fc.plotEl.getBoundingClientRect();
    const x = e.clientX - r.left, y = e.clientY - r.top;
    const ts = this.fc.chart.timeScale();
    if (x < 0 || y < 0 || x > ts.width() || y > r.height - ts.height()) return null;
    const raw = ts.coordinateToLogical(x);
    const price = this.fc.candleSeries.coordinateToPrice(y);
    if (raw == null || price == null || !this.times().length) return null;
    const l = Math.round(raw);
    return { x, y, l, time: D.logicalToTime(this.times(), l, this.step), price };
  }

  // Every drawing (and the one being placed) in pixels, for the renderer and hit testing.
  shapes() {
    if (!this.fc.chart || !this.times().length) return [];
    const list = this.preview ? [...this.drawings, { id: '__preview', ...this.preview }] : this.drawings;
    return list.map(d => ({
      ...d,
      pts: d.p.map(q => ({ x: d.type === 'hline' ? null : this.toX(q.time), y: this.toY(q.price) })),
      levels: d.type === 'fib' ? D.fibLevels(d.p).map(v => this.toY(v.price)) : undefined,
    }));
  }
  repaint() { if (this.primitive && this.primitive.requestUpdate) this.primitive.requestUpdate(); }

  // --- mouse ---
  onDown(e) {
    if (e.button !== 0) return;
    const pt = this.at(e);
    if (!pt) return;
    const here = { time: pt.time, price: pt.price };
    if (this.tool) {
      e.preventDefault();
      if (this.tool === 'hline') { this.commit({ type: 'hline', p: [here] }); return; }
      if (this.preview) { this.preview.p[1] = here; this.commit(this.preview); return; }   // second click
      this.preview = { type: this.tool, p: [here, { ...here }] };
      this.downAt = { x: pt.x, y: pt.y };
      this.repaint();
      return;
    }
    const hit = D.hitTest(this.shapes(), pt.x, pt.y);
    if (!hit) {
      if (this.selectedId) this.select(null);
      return;                                    // the chart pans as usual
    }
    e.preventDefault();
    this.select(hit.id);
    const d = this.drawings.find(x => x.id === hit.id);
    this.drag = {
      id: hit.id, handle: hit.handle, l0: pt.l, price0: pt.price,
      orig: d.p.map(q => ({ l: D.timeToLogical(this.times(), q.time, this.step), price: q.price })),
    };
    this.lockChart(true);
    this.repaint();
  }

  onMove(e) {
    const pt = this.preview || this.drag || !this.tool ? this.at(e) : null;
    if (this.preview) {
      if (pt) { this.preview.p[1] = { time: pt.time, price: pt.price }; this.repaint(); }
      return;
    }
    if (this.drag) {
      if (!pt) return;
      const d = this.drawings.find(x => x.id === this.drag.id);
      if (!d) return;
      const moved = D.dragPoints(this.drag.orig, this.drag.handle, pt.l - this.drag.l0, pt.price - this.drag.price0);
      d.p = moved.map(q => ({ time: D.logicalToTime(this.times(), q.l, this.step), price: q.price }));
      this.repaint();
      return;
    }
    if (!this.tool) {
      const hit = pt && D.hitTest(this.shapes(), pt.x, pt.y);
      this.fc.plotEl.style.cursor = hit ? (hit.handle == null ? 'move' : 'grab') : '';
    }
  }

  onUp(e) {
    if (this.drag) {
      this.drag = null;
      this.lockChart(!!this.tool);
      this.save();
      return;
    }
    // A drag places a drawing on release; a click leaves it following the pointer for a second click.
    if (this.preview && this.downAt) {
      const pt = this.at(e);
      const far = pt && Math.hypot(pt.x - this.downAt.x, pt.y - this.downAt.y) > 4;
      this.downAt = null;
      if (far) { this.preview.p[1] = { time: pt.time, price: pt.price }; this.commit(this.preview); }
    }
  }

  commit(d) {
    const drawing = { id: newId(), type: d.type, p: d.p.map(q => ({ ...q })) };
    this.preview = null;
    this.downAt = null;
    this.drawings.push(drawing);
    this.save();
    this.setTool(null);
    this.select(drawing.id);
    this.repaint();
  }

  onKey(e) {
    const tag = (e.target && e.target.tagName) || '';
    if (/^(INPUT|TEXTAREA|SELECT)$/.test(tag) || (e.target && e.target.isContentEditable)) return;
    if (e.key === 'Escape') {
      if (this.tool || this.preview) this.setTool(null);
      else if (this.selectedId) this.select(null);
      return;
    }
    if ((e.key === 'Delete' || e.key === 'Backspace') && this.selectedId && selectedLayer === this) {
      e.preventDefault();
      this.drawings = this.drawings.filter(d => d.id !== this.selectedId);
      this.select(null);
      this.save();
      this.repaint();
    }
  }
}
