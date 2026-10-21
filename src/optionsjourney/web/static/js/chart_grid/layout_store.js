// The Multi-Chart Grid's saved layouts, in Postgres (/api/chart_layouts) and nowhere else.
// One is active: the server remembers it (is_active), so any browser opens the same grid.
// Edits are PUT after DEBOUNCE_MS of quiet, so typing a ticker sends one write. A payload save
// sends only the payload and a rename only the name, so neither reverts the other made in
// another window. A failed save is retried after RETRY_MS (1, 2, 5, 10, 30 s, then every 30 s)
// with the layout's latest state; a 404 means the layout was deleted and stops it. On first load,
// layouts still in this browser's localStorage (the old store) are uploaded once, then the key
// is removed.
//
// A layout is {id, name, payload: {shape, panes: [{id, symbol, timeframe, indicators}]}, isActive}.
// Pane ids are stable, so pop-outs can name their pane across reloads.
import { SHAPES, shapeOf, paneCount, resizePanes } from './shapes.js';
import { validSizes } from './resize.js';

const LEGACY_KEY = 'chart_grid_layouts_v1';
export const DEBOUNCE_MS = 500;
export const RETRY_MS = [1000, 2000, 5000, 10000, 30000];

export function uid() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  return 'l-' + Math.random().toString(36).slice(2, 11);
}
const paneId = () => 'p-' + Math.random().toString(36).slice(2, 10);

export const newPane = () => ({ id: paneId(), symbol: 'SPY', timeframe: '5m', indicators: { active: {} } });

export function normalizePayload(payload) {
  const panes = (Array.isArray(payload?.panes) ? payload.panes : [])
    .filter(p => p && typeof p === 'object')
    .map(p => ({ ...p, id: p.id || paneId() }));
  const shape = shapeOf({ ...(payload || {}), panes });
  const sizes = validSizes(shape, payload?.sizes);
  return { shape, panes: resizePanes(panes, paneCount(shape), newPane), ...(sizes ? { sizes } : {}) };
}

// A stored payload that normalising had to fill in (no shape, a pane without an id, panes
// padded): its new pane ids live only in this page until it is saved back.
function needsPersist(stored, normal) {
  const panes = Array.isArray(stored?.panes) ? stored.panes.filter(p => p && typeof p === 'object') : [];
  return !(stored && typeof stored.shape === 'string' && Object.hasOwn(SHAPES, stored.shape))
    || panes.some(p => !p.id) || panes.length < normal.panes.length;
}

function safeStorage() {
  try { return globalThis.localStorage || null; } catch (e) { return null; }
}

const JSON_HEADERS = { 'Content-Type': 'application/json' };

export class LayoutStore {
  constructor({ fetchImpl = (...a) => fetch(...a), storage = safeStorage(), debounceMs = DEBOUNCE_MS,
                setTimer = (f, ms) => setTimeout(f, ms), clearTimer = id => clearTimeout(id) } = {}) {
    this._fetch = fetchImpl;
    this._storage = storage;
    this._debounceMs = debounceMs;
    this._setTimer = setTimer;
    this._clearTimer = clearTimer;
    this._layouts = new Map();
    this._pending = new Map();   // layout id -> debounce timer
    this._retry = new Map();     // layout id -> { fields: Set<'payload'|'name'>, attempt, timer }
  }

  // ---- loading ----
  async init() {
    let rows = await this._getList();
    if (!rows.length) rows = await this._importLegacy();
    if (!rows.length) {
      const def = await this._create(uid(), 'Default', normalizePayload({ shape: 'eq4' }));
      rows = [def];
    }
    this._layouts = new Map(rows.map(r => [r.id, this._row(r)]));
    // Save legacy rows back once, so a pop-out or a second window finds the same pane ids.
    await Promise.all(rows.filter(r => needsPersist(r.payload, this._layouts.get(r.id).payload))
      .map(r => this._put(r.id, ['payload'])));
    if (!this.active()) await this.setActive(this.list()[0].id);
  }

  async open(id) {
    const r = await this._fetch(`/api/chart_layouts/${encodeURIComponent(id)}`);
    if (!r.ok) return null;
    const row = await r.json();
    const layout = this._row(row);
    this._layouts.set(layout.id, layout);
    if (needsPersist(row.payload, layout.payload)) await this._put(layout.id, ['payload']);
    return layout;
  }

  _row(r) {
    return { id: r.id, name: r.name, payload: normalizePayload(r.payload), isActive: !!r.isActive };
  }

  async _getList() {
    const r = await this._fetch('/api/chart_layouts');
    if (!r.ok) throw new Error(`layouts: GET ${r.status}`);
    return r.json();
  }

  async _create(id, name, payload) {
    const r = await this._fetch('/api/chart_layouts', {
      method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ id, name, payload }),
    });
    if (!r.ok) throw new Error(`layouts: POST ${r.status}`);
    return r.json();
  }

  async _importLegacy() {
    let state = null;
    try { state = JSON.parse(this._storage?.getItem(LEGACY_KEY) || 'null'); } catch (e) { state = null; }
    const old = state && state.layouts && typeof state.layouts === 'object' ? Object.values(state.layouts) : [];
    if (!old.length) return [];
    const rows = [];
    for (const l of old) {
      rows.push(await this._create(l.id || uid(), l.name || 'Layout', normalizePayload(l.payload)));
    }
    const activeId = rows.some(r => r.id === state.activeId) ? state.activeId : rows[0].id;
    await this._activate(activeId);
    rows.forEach(r => { r.isActive = r.id === activeId; });
    try { this._storage?.removeItem(LEGACY_KEY); } catch (e) { /* private mode */ }
    return rows;
  }

  // ---- read ----
  list() { return [...this._layouts.values()].sort((a, b) => a.name.localeCompare(b.name)); }
  active() { return [...this._layouts.values()].find(l => l.isActive) || null; }
  get(id) { return this._layouts.get(id) || null; }

  // ---- write ----
  async _activate(id) {
    const r = await this._fetch(`/api/chart_layouts/${encodeURIComponent(id)}/activate`, { method: 'PUT' });
    if (!r.ok) throw new Error(`layouts: activate ${r.status}`);
  }

  async setActive(id) {
    if (!this._layouts.has(id)) return false;
    await this._activate(id);
    for (const l of this._layouts.values()) l.isActive = l.id === id;
    return true;
  }

  updatePayload(id, payload) {
    const layout = this._layouts.get(id);
    if (!layout) return;
    layout.payload = payload;
    this._schedule(id);
  }

  updatePane(id, pane) {
    const layout = this._layouts.get(id);
    if (!layout) return;
    layout.payload = { ...layout.payload,
      panes: layout.payload.panes.map(p => (p.id === pane.id ? { ...pane } : p)) };
    this._schedule(id);
  }

  // Another window saved this layout: take its payload, send nothing.
  applyRemote(id, payload) {
    const layout = this._layouts.get(id);
    if (layout) layout.payload = normalizePayload(payload);
  }

  _schedule(id) {
    if (this._pending.has(id)) this._clearTimer(this._pending.get(id));
    this._pending.set(id, this._setTimer(() => { this._pending.delete(id); this._put(id, ['payload']); }, this._debounceMs));
  }

  // PUT the layout's current value of each field; a failure queues a retry of those fields.
  async _put(id, fields, { keepalive = false } = {}) {
    const layout = this._layouts.get(id);
    if (!layout) return;
    const body = {};
    if (fields.includes('payload')) body.payload = layout.payload;
    if (fields.includes('name')) body.name = layout.name;
    let status = 0;
    try {
      const r = await this._fetch(`/api/chart_layouts/${encodeURIComponent(id)}`, {
        method: 'PUT', headers: JSON_HEADERS, keepalive, body: JSON.stringify(body),
      });
      status = r.status;
      if (r.ok) { this._saved(id, fields); return; }
    } catch (e) {
      console.warn('layout save failed', e);
    }
    if (status === 404) {
      console.warn('layout save: layout gone', id);
      this._dropRetry(id);
      return;
    }
    if (status) console.warn('layout save failed', status);
    this._queueRetry(id, fields);
  }

  _saved(id, fields) {
    const entry = this._retry.get(id);
    if (!entry) return;
    fields.forEach(f => entry.fields.delete(f));
    if (!entry.fields.size) this._dropRetry(id);
  }

  _dropRetry(id) {
    const entry = this._retry.get(id);
    if (entry?.timer != null) this._clearTimer(entry.timer);
    this._retry.delete(id);
  }

  _queueRetry(id, fields) {
    let entry = this._retry.get(id);
    if (!entry) { entry = { fields: new Set(), attempt: 0, timer: null }; this._retry.set(id, entry); }
    fields.forEach(f => entry.fields.add(f));
    if (entry.timer != null) return;
    const ms = RETRY_MS[Math.min(entry.attempt, RETRY_MS.length - 1)];
    entry.attempt++;
    entry.timer = this._setTimer(() => {
      entry.timer = null;
      // A debounced save on its way carries the newest payload itself.
      if (this._pending.has(id)) entry.fields.delete('payload');
      const send = [...entry.fields];
      entry.fields.clear();
      if (send.length) this._put(id, send);
    }, ms);
  }

  // Send pending edits and queued retries now (page hide, before a switch).
  async flush({ keepalive = false } = {}) {
    const sends = new Map();
    for (const [id, timer] of this._pending) { this._clearTimer(timer); sends.set(id, new Set(['payload'])); }
    this._pending.clear();
    for (const [id, entry] of this._retry) {
      if (entry.timer != null) { this._clearTimer(entry.timer); entry.timer = null; }
      const set = sends.get(id) || new Set();
      entry.fields.forEach(f => set.add(f));
      entry.fields.clear();
      if (set.size) sends.set(id, set);
    }
    await Promise.all([...sends].map(([id, set]) => this._put(id, [...set], { keepalive })));
  }

  async rename(id, name) {
    const layout = this._layouts.get(id);
    if (!layout || !name) return;
    layout.name = name;
    await this._put(id, ['name']);
  }

  async saveAsNew(name, payload) {
    const row = await this._create(uid(), name, normalizePayload(payload));
    const layout = this._row(row);
    this._layouts.set(layout.id, layout);
    await this.setActive(layout.id);
    return layout;
  }

  async duplicate(sourceId, name) {
    const source = this._layouts.get(sourceId);
    if (!source) return null;
    const copy = JSON.parse(JSON.stringify(source.payload));
    copy.panes = copy.panes.map(p => ({ ...p, id: paneId() }));
    return this.saveAsNew(name, copy);
  }

  async remove(id) {
    if (this._layouts.size <= 1 || !this._layouts.has(id)) return false;
    if (this._pending.has(id)) { this._clearTimer(this._pending.get(id)); this._pending.delete(id); }
    this._dropRetry(id);
    const r = await this._fetch(`/api/chart_layouts/${encodeURIComponent(id)}`, { method: 'DELETE' });
    if (!r.ok) return false;
    // The server picks the next active layout; take its word for it.
    const rows = await this._getList();
    this._layouts = new Map(rows.map(x => [x.id, this._row(x)]));
    if (!this.active() && this._layouts.size) await this.setActive(this.list()[0].id);
    return true;
  }
}
