// Draggable borders between the grid's columns and rows. A layout's sizes are stored as
// {cols: [fr...], rows: [fr...]} for its shape; none stored = the shape's own proportions.
// A border moves a whole column/row line (CSS grid tracks), and only shows where two
// different panes actually meet, so a featured pane spanning the line has no handle across it.
import { SHAPES } from './shapes.js';

export const MIN_PX = 120;

// 'repeat(2, 1fr)' -> [1, 1]; '2fr 1fr 1fr' -> [2, 1, 1].
export function trackFrs(spec) {
  const m = /^repeat\((\d+),\s*([\d.]+)fr\)$/.exec(spec.trim());
  if (m) return Array(Number(m[1])).fill(Number(m[2]));
  return spec.trim().split(/\s+/).map(t => parseFloat(t));
}

export function defaultSizes(key) {
  const s = SHAPES[key];
  return { cols: trackFrs(s.columns), rows: trackFrs(s.rows) };
}

// Stored sizes if they fit this shape's track counts, else null (use the shape's defaults).
export function validSizes(key, sizes) {
  if (!SHAPES[key] || !sizes || typeof sizes !== 'object') return null;
  const d = defaultSizes(key);
  const ok = (a, n) => Array.isArray(a) && a.length === n && a.every(v => Number.isFinite(v) && v > 0);
  return ok(sizes.cols, d.cols.length) && ok(sizes.rows, d.rows.length)
    ? { cols: [...sizes.cols], rows: [...sizes.rows] } : null;
}

const grid = key => SHAPES[key].areas.map(r => r.split(' '));

// Where the border after track `index` separates two panes: [{index, from, to}] with
// from..to (exclusive) the cross-axis tracks the handle spans, one entry per contiguous run.
export function borderSegments(key, axis) {
  const g = grid(key);
  const nTracks = axis === 'col' ? g[0].length : g.length;
  const nCross = axis === 'col' ? g.length : g[0].length;
  const at = (track, cross) => (axis === 'col' ? g[cross][track] : g[track][cross]);
  const out = [];
  for (let i = 0; i < nTracks - 1; i++) {
    let start = null;
    for (let c = 0; c <= nCross; c++) {
      const split = c < nCross && at(i, c) !== at(i + 1, c);
      if (split && start === null) start = c;
      if (!split && start !== null) { out.push({ index: i, from: start, to: c }); start = null; }
    }
  }
  return out;
}

// Track sizes after dragging the border after track i by delta px; both neighbours keep MIN_PX.
export function dragPair(px, i, delta, min = MIN_PX) {
  const out = [...px];
  const total = px[i] + px[i + 1];
  const a = Math.min(Math.max(px[i] + delta, min), total - min);
  out[i] = a;
  out[i + 1] = total - a;
  return out;
}

// Back to the shape's proportion between tracks i and i+1, keeping their combined share.
export function resetPair(frs, defaults, i) {
  const out = [...frs];
  const total = frs[i] + frs[i + 1];
  const d = defaults[i] + defaults[i + 1];
  out[i] = (total * defaults[i]) / d;
  out[i + 1] = (total * defaults[i + 1]) / d;
  return out;
}

// px tracks -> fr numbers that sum to the track count (readable, stable when saved).
export function toFrs(px) {
  const sum = px.reduce((a, b) => a + b, 0);
  return px.map(v => Math.round((v / sum) * px.length * 1000) / 1000);
}

const same = (a, b) => a.length === b.length && a.every((v, i) => Math.abs(v - b[i]) < 0.01);

export function isDefault(key, sizes) {
  const d = defaultSizes(key);
  const norm = a => toFrs(a);
  return !sizes || (same(norm(sizes.cols), norm(d.cols)) && same(norm(sizes.rows), norm(d.rows)));
}

function measureGrid(el) {
  const cs = getComputedStyle(el);
  const px = s => s.split(/\s+/).map(parseFloat);
  return {
    cols: px(cs.gridTemplateColumns), rows: px(cs.gridTemplateRows),
    gap: parseFloat(cs.columnGap) || 0, rowGap: parseFloat(cs.rowGap) || 0,
    padLeft: parseFloat(cs.paddingLeft) || 0, padTop: parseFloat(cs.paddingTop) || 0,
  };
}

// Owns the handle layer inside the grid element. `apply(sizes)` sets the grid tracks;
// `onChange(sizes)` fires once per finished drag or reset (sizes null = shape defaults).
export class GridResizer {
  constructor({ el, getShape, getSizes, apply, onChange, measure = measureGrid }) {
    Object.assign(this, { el, getShape, getSizes, apply, onChange, measure });
    this.layer = document.createElement('div');
    this.layer.className = 'grid-resize-layer';
    if (typeof ResizeObserver === 'function') {
      this._ro = new ResizeObserver(() => this.position());
      this._ro.observe(el);
    }
  }

  // Rebuild the handles for the current shape (call after the grid's children are replaced).
  render() {
    this.layer.innerHTML = '';
    this.el.appendChild(this.layer);
    const key = this.getShape();
    for (const axis of ['col', 'row']) {
      for (const seg of borderSegments(key, axis)) {
        const h = document.createElement('div');
        h.className = `grid-resize-handle ${axis}`;
        h.dataset.axis = axis;
        h.dataset.index = seg.index;
        h.title = 'Drag to resize · double-click to reset';
        h._seg = seg;
        h.addEventListener('pointerdown', e => this._start(e, h));
        h.addEventListener('dblclick', () => this._reset(h));
        this.layer.appendChild(h);
      }
    }
    this.position();
  }

  position() {
    const m = this.measure(this.el);
    if (!m.cols.every(Number.isFinite) || !m.rows.every(Number.isFinite)) return;
    const offsets = (tracks, gap, pad) => {
      const o = [pad];
      tracks.forEach((t, i) => o.push(o[i] + t + gap));
      return o; // o[i] = start of track i
    };
    const xs = offsets(m.cols, m.gap, m.padLeft), ys = offsets(m.rows, m.rowGap, m.padTop);
    for (const h of this.layer.children) {
      const { index, from, to } = h._seg;
      if (h.dataset.axis === 'col') {
        h.style.left = `${xs[index + 1] - m.gap / 2}px`;
        h.style.top = `${ys[from]}px`;
        h.style.height = `${ys[to] - ys[from] - m.rowGap}px`;
      } else {
        h.style.top = `${ys[index + 1] - m.rowGap / 2}px`;
        h.style.left = `${xs[from]}px`;
        h.style.width = `${xs[to] - xs[from] - m.gap}px`;
      }
    }
  }

  _current() {
    return this.getSizes() || defaultSizes(this.getShape());
  }

  _start(e, h) {
    e.preventDefault();
    const axis = h.dataset.axis, i = Number(h.dataset.index);
    const m = this.measure(this.el);
    const startPx = axis === 'col' ? m.cols : m.rows;
    const origin = axis === 'col' ? e.clientX : e.clientY;
    const other = this._current()[axis === 'col' ? 'rows' : 'cols'];
    let sizes = null;
    h.setPointerCapture?.(e.pointerId);
    h.classList.add('dragging');
    document.body.classList.add(axis === 'col' ? 'grid-resizing-col' : 'grid-resizing-row');
    const move = (ev) => {
      const px = dragPair(startPx, i, (axis === 'col' ? ev.clientX : ev.clientY) - origin);
      sizes = axis === 'col' ? { cols: toFrs(px), rows: other } : { cols: other, rows: toFrs(px) };
      this.apply(sizes);
      this.position();
    };
    const up = () => {
      h.removeEventListener('pointermove', move);
      h.removeEventListener('pointerup', up);
      h.removeEventListener('pointercancel', up);
      h.classList.remove('dragging');
      document.body.classList.remove('grid-resizing-col', 'grid-resizing-row');
      if (sizes) this.onChange(isDefault(this.getShape(), sizes) ? null : sizes);
    };
    h.addEventListener('pointermove', move);
    h.addEventListener('pointerup', up);
    h.addEventListener('pointercancel', up);
  }

  _reset(h) {
    const key = this.getShape(), axis = h.dataset.axis === 'col' ? 'cols' : 'rows';
    const cur = this._current();
    const next = { ...cur, [axis]: resetPair(cur[axis], defaultSizes(key)[axis], Number(h.dataset.index)) };
    const sizes = isDefault(key, next) ? null : next;
    this.apply(sizes);
    this.position();
    this.onChange(sizes);
  }

  destroy() { this._ro?.disconnect(); this.layer.remove(); }
}
