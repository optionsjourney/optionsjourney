// The Multi-Chart Grid's shapes (spec section 2b): named CSS grid templates. Letters a..j
// are panes 1..10 in order. In a featured shape pane 1 (a) is the large slot.
export const SHAPES = {
  eq1:  { label: '1',  columns: '1fr',            rows: '1fr',            areas: ['a'] },
  eq2:  { label: '2',  columns: 'repeat(2, 1fr)', rows: '1fr',            areas: ['a b'] },
  eq3:  { label: '3',  columns: 'repeat(3, 1fr)', rows: '1fr',            areas: ['a b c'] },
  eq4:  { label: '4',  columns: 'repeat(2, 1fr)', rows: 'repeat(2, 1fr)', areas: ['a b', 'c d'] },
  eq6:  { label: '6',  columns: 'repeat(3, 1fr)', rows: 'repeat(2, 1fr)', areas: ['a b c', 'd e f'] },
  eq8:  { label: '8',  columns: 'repeat(4, 1fr)', rows: 'repeat(2, 1fr)', areas: ['a b c d', 'e f g h'] },
  eq9:  { label: '9',  columns: 'repeat(3, 1fr)', rows: 'repeat(3, 1fr)', areas: ['a b c', 'd e f', 'g h i'] },
  eq10: { label: '10', columns: 'repeat(5, 1fr)', rows: 'repeat(2, 1fr)', areas: ['a b c d e', 'f g h i j'] },
  'big-l-2':   { label: '1 + 2',   columns: '1fr 1fr',     rows: '1fr 1fr', areas: ['a b', 'a c'] },
  'big-l-2x2': { label: '1 + 2×2', columns: '2fr 1fr 1fr', rows: '1fr 1fr', areas: ['a b c', 'a d e'] },
  'big-t-3':   { label: '1 + 3',   columns: 'repeat(3, 1fr)', rows: '2fr 1fr', areas: ['a a a', 'b c d'] },
};

export const SHAPE_ORDER = ['eq1', 'eq2', 'eq3', 'eq4', 'eq6', 'eq8', 'eq9', 'eq10',
  'big-l-2', 'big-l-2x2', 'big-t-3'];

const LETTERS = 'abcdefghij';

export function paneCount(key) {
  return new Set(SHAPES[key].areas.join(' ').split(' ')).size;
}

// The shape a stored payload asks for. A payload saved before shapes has only `count` (the old
// grid let panes be closed, so any count 1..10 exists), and its shape comes from how many panes
// it holds (panes.length, else count): the shape with exactly that many, an equal grid before a
// featured one (5 -> big-l-2x2), else the smallest equal grid that holds them all (7 -> eq8), so
// loading never drops a pane.
const EQUAL = SHAPE_ORDER.filter(k => k.startsWith('eq'));
const FEATURED = SHAPE_ORDER.filter(k => !k.startsWith('eq'));

export function shapeOf(payload) {
  if (payload && typeof payload.shape === 'string' && Object.hasOwn(SHAPES, payload.shape)) return payload.shape;
  const panes = Array.isArray(payload?.panes) ? payload.panes.length : 0;
  const n = panes > 0 ? panes : Number(payload?.count);
  if (!Number.isInteger(n) || n < 1) return 'eq4';
  const exact = [...EQUAL, ...FEATURED].find(k => paneCount(k) === n);
  if (exact) return exact;
  return EQUAL.find(k => paneCount(k) >= n) || EQUAL[EQUAL.length - 1];
}

export function resizePanes(panes, n, makePane) {
  const out = panes.slice(0, n);
  while (out.length < n) out.push(makePane());
  return out;
}

// `sizes` ({cols, rows} in fr, from resize.js) overrides the shape's own proportions.
export function applyShape(el, key, sizes = null) {
  const s = SHAPES[key];
  const fr = a => a.map(v => `${v}fr`).join(' ');
  el.style.display = 'grid';
  el.style.gridTemplateColumns = sizes ? fr(sizes.cols) : s.columns;
  el.style.gridTemplateRows = sizes ? fr(sizes.rows) : s.rows;
  el.style.gridTemplateAreas = s.areas.map(r => `"${r}"`).join(' ');
  [...el.children].slice(0, paneCount(key)).forEach((child, i) => { child.style.gridArea = LETTERS[i]; });
}
