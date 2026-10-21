// Tracker-owned (not vendored -- VENDORED.md patch 16): pure helpers for the drawing tools on
// the Multi-Chart Grid's charts -- the saved shape of a drawing, time <-> bar-index conversion, Fibonacci levels,
// hit testing and dragging. No DOM;
// chart_drawings_layer.js draws them and handles the mouse.
//
// A drawing is {id, type, p: [{time, price}, ...]} -- one point for 'hline', two for the rest.
// `time` is a bar time (Unix seconds, as the chart's bars), or past either end of the bars,
// a time a whole number of bar steps away, so a line can reach into the future.

export const TOOLS = ['hline', 'trend', 'rect', 'fib'];
export const FIB_LEVELS = [0, 0.236, 0.382, 0.5, 0.618, 0.786, 1];
const POINTS = { hline: 1, trend: 2, rect: 2, fib: 2 };

// The Analyzer's old localStorage key for a chart's drawings; only drawing_store.js's
// one-time import still reads it.
export function drawingsKey(symbol, frame) { return `analyzer_drawings_${symbol}_${frame}`; }

const validPoint = q => q && Number.isFinite(q.time) && Number.isFinite(q.price);

// The valid drawings of a list, each with only its known fields; anything else gives [].
export function cleanDrawings(list) {
  if (!Array.isArray(list)) return [];
  return list.filter(d => d && typeof d.id === 'string' && TOOLS.includes(d.type)
    && Array.isArray(d.p) && d.p.length === POINTS[d.type] && d.p.every(validPoint))
    .map(d => ({ id: d.id, type: d.type, p: d.p.map(({ time, price }) => ({ time, price })) }));
}

// The saved drawings from JSON text, or [] for anything unreadable; a malformed drawing is dropped alone.
export function loadDrawings(raw) {
  let parsed;
  try { parsed = JSON.parse(raw); } catch (e) { return []; }
  return cleanDrawings(parsed);
}

export function pointsFor(type) { return POINTS[type]; }

// Bar index of a time (fractional between bars), extrapolated `step` seconds per bar past
// either end. `times` is the bars' sorted times. null without bars.
export function timeToLogical(times, t, step) {
  const n = times.length;
  if (!n) return null;
  if (t <= times[0]) return (t - times[0]) / step;
  if (t >= times[n - 1]) return n - 1 + (t - times[n - 1]) / step;
  let lo = 0, hi = n - 1;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (times[mid] <= t) lo = mid; else hi = mid;
  }
  return lo + (t - times[lo]) / (times[hi] - times[lo]);
}

// The time at a bar index, rounded to a whole bar: a bar's own time, or past the ends,
// whole steps away from the end bar. null without bars.
export function logicalToTime(times, l, step) {
  const n = times.length;
  if (!n || !Number.isFinite(l)) return null;
  const i = Math.round(l);
  if (i < 0) return times[0] + i * step;
  if (i > n - 1) return times[n - 1] + (i - (n - 1)) * step;
  return times[i];
}

// Retracement levels from the first point to the second: 0 at the second, 1 at the first.
export function fibLevels(p) {
  const [a, b] = p;
  return FIB_LEVELS.map(level => ({ level, price: b.price + (a.price - b.price) * level }));
}

export function distToSegment(px, py, ax, ay, bx, by) {
  const dx = bx - ax, dy = by - ay;
  const len2 = dx * dx + dy * dy;
  const t = len2 ? Math.max(0, Math.min(1, ((px - ax) * dx + (py - ay) * dy) / len2)) : 0;
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

// The topmost shape under (x, y): {id, handle} where handle is the index of the point grabbed,
// or null for the body. `shapes` are drawings in pixels: {id, type, pts: [{x, y}], levels: [y]}
// (levels for fib only). A point off the visible time range has x null; such shapes are skipped,
// except an hline, which spans the whole pane.
export function hitTest(shapes, x, y, tol = 6) {
  for (let i = shapes.length - 1; i >= 0; i--) {
    const s = shapes[i];
    if (s.type === 'hline') {
      if (s.pts[0].y != null && Math.abs(y - s.pts[0].y) <= tol) return { id: s.id, handle: null };
      continue;
    }
    const [a, b] = s.pts;
    if ([a.x, a.y, b.x, b.y].some(v => v == null)) continue;
    const h = s.pts.findIndex(q => Math.hypot(x - q.x, y - q.y) <= tol);
    if (h >= 0) return { id: s.id, handle: h };
    const x0 = Math.min(a.x, b.x), x1 = Math.max(a.x, b.x);
    const y0 = Math.min(a.y, b.y), y1 = Math.max(a.y, b.y);
    if (s.type === 'trend') {
      if (distToSegment(x, y, a.x, a.y, b.x, b.y) <= tol) return { id: s.id, handle: null };
    } else if (s.type === 'rect') {
      if (x >= x0 - tol && x <= x1 + tol && y >= y0 - tol && y <= y1 + tol) return { id: s.id, handle: null };
    } else if (s.type === 'fib') {
      if (x >= x0 - tol && x <= x1 + tol && (s.levels || []).some(ly => Math.abs(y - ly) <= tol)) {
        return { id: s.id, handle: null };
      }
    }
  }
  return null;
}

// Points (as {l: bar index, price}) after a drag of dl bars and dp price: the grabbed point
// alone (handle = its index), or every point (handle null).
export function dragPoints(orig, handle, dl, dp) {
  return orig.map((q, i) => (handle == null || handle === i ? { l: q.l + dl, price: q.price + dp } : { ...q }));
}
