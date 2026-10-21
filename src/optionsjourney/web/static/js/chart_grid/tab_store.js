// The Multi-Chart Grid's open tabs, in Postgres (/api/chart_tabs). A tab is a layout plus its
// own pane tickers: {id, layoutId, position, symbols: {paneId: ticker}}. The layout is the
// template (shape, timeframes, indicators, links), so two tabs on "Single - 4 pane" can chart
// different tickers. Which tab a window shows is its own; the last one picked in this browser
// is remembered in localStorage so a reload comes back to it.
//
// Ticker edits are PUT after DEBOUNCE_MS of quiet. When /api/chart_tabs can't be read the store
// keeps its tabs in memory only, so the grid still works (one tab per window, nothing saved).
import { uid, DEBOUNCE_MS } from './layout_store.js';

const SELECTED_KEY = 'chart_grid_tab_v1';
const JSON_HEADERS = { 'Content-Type': 'application/json' };

function safeStorage() {
  try { return globalThis.localStorage || null; } catch (e) { return null; }
}

// The ticker `panes` show with `symbols` laid over them: the linked panes' ticker, else the one
// ticker every pane shows, else null (a grid of different tickers).
export function tabTicker(panes, symbols = {}) {
  const shown = (panes || []).map(p => ({ ...p, symbol: symbols[p.id] || p.symbol }));
  const linked = shown.find(p => p.linked && p.symbol);
  if (linked) return linked.symbol;
  const syms = new Set(shown.map(p => p.symbol));
  return syms.size === 1 ? [...syms][0] : null;
}

// {paneId: ticker} for every pane of `payload`.
export const symbolsOf = payload => Object.fromEntries((payload?.panes || []).map(p => [p.id, p.symbol]));

// `payload` with this tab's tickers laid over its panes.
export function withTabSymbols(payload, symbols = {}) {
  if (!payload) return payload;
  return { ...payload, panes: (payload.panes || []).map(p => (symbols[p.id] ? { ...p, symbol: symbols[p.id] } : p)) };
}

export class TabStore {
  constructor({ fetchImpl = (...a) => fetch(...a), storage = safeStorage(), debounceMs = DEBOUNCE_MS,
                setTimer = (f, ms) => setTimeout(f, ms), clearTimer = id => clearTimeout(id) } = {}) {
    this._fetch = fetchImpl;
    this._storage = storage;
    this._debounceMs = debounceMs;
    this._setTimer = setTimer;
    this._clearTimer = clearTimer;
    this._tabs = new Map();
    this._pending = new Map();   // tab id -> debounce timer
    this.offline = false;
  }

  // ---- loading ----
  // Read the tabs; drop any whose layout is gone (`hasLayout`); open one on `fallbackLayoutId`
  // when none are left.
  async init({ hasLayout = () => true, fallbackLayoutId = null, fallbackSymbols = {} } = {}) {
    await this.reload();
    for (const t of this.list()) if (!hasLayout(t.layoutId)) this._tabs.delete(t.id);
    if (!this._tabs.size && fallbackLayoutId) await this.open(fallbackLayoutId, fallbackSymbols);
  }

  async reload() {
    if (this.offline) return;
    let rows = null;
    try {
      const r = await this._fetch('/api/chart_tabs');
      if (r.ok) rows = await r.json();
    } catch (e) { rows = null; }
    if (!Array.isArray(rows)) {
      console.warn('chart tabs unavailable; keeping them in this window only');
      this.offline = true;
      return;
    }
    // A ticker edit not yet sent wins over the server's copy.
    const next = new Map(rows.map(r => [r.id, this._row(r)]));
    for (const id of this._pending.keys()) if (next.has(id)) next.get(id).symbols = this._tabs.get(id)?.symbols || {};
    this._tabs = next;
  }

  _row(r) {
    return { id: r.id, layoutId: r.layoutId, position: r.position || 0, symbols: { ...(r.symbols || {}) } };
  }

  // ---- read ----
  list() { return [...this._tabs.values()].sort((a, b) => a.position - b.position); }
  get(id) { return this._tabs.get(id) || null; }

  // The tab this browser showed last, if it is still open.
  remembered() {
    let id = null;
    try { id = this._storage?.getItem(SELECTED_KEY) || null; } catch (e) { id = null; }
    return id && this._tabs.has(id) ? id : null;
  }

  remember(id) {
    try { this._storage?.setItem(SELECTED_KEY, id); } catch (e) { /* private window */ }
  }

  // ---- write ----
  async open(layoutId, symbols = {}) {
    const last = this.list().at(-1);
    const tab = { id: uid(), layoutId, position: last ? last.position + 1 : 0, symbols: { ...symbols } };
    this._tabs.set(tab.id, tab);
    await this._send('POST', '/api/chart_tabs', tab);
    return tab;
  }

  async close(id) {
    if (!this._tabs.has(id)) return;
    this._cancel(id);
    this._tabs.delete(id);
    await this._send('DELETE', `/api/chart_tabs/${encodeURIComponent(id)}`);
  }

  // Point a tab at another layout, starting from `symbols` (that layout's saved tickers).
  async setLayout(id, layoutId, symbols = {}) {
    const tab = this._tabs.get(id);
    if (!tab || tab.layoutId === layoutId) return;
    this._cancel(id);
    tab.layoutId = layoutId;
    tab.symbols = { ...symbols };
    await this._send('PUT', `/api/chart_tabs/${encodeURIComponent(id)}`, { layoutId, symbols: tab.symbols });
  }

  // A layout was deleted (the server closed its tabs): forget them here too.
  dropLayout(layoutId) {
    for (const t of this.list()) if (t.layoutId === layoutId) { this._cancel(t.id); this._tabs.delete(t.id); }
  }

  // The tab's tickers changed here: saved after DEBOUNCE_MS of quiet.
  setSymbols(id, symbols) {
    const tab = this._tabs.get(id);
    if (!tab || JSON.stringify(tab.symbols) === JSON.stringify(symbols)) return;
    tab.symbols = { ...symbols };
    this._cancel(id);
    this._pending.set(id, this._setTimer(() => { this._pending.delete(id); this._putSymbols(id); }, this._debounceMs));
  }

  // Another window saved this tab's tickers: take them, send nothing.
  applyRemote(id, symbols) {
    const tab = this._tabs.get(id);
    if (tab) tab.symbols = { ...symbols };
  }

  async flush({ keepalive = false } = {}) {
    const ids = [...this._pending.keys()];
    ids.forEach(id => this._cancel(id));
    await Promise.all(ids.map(id => this._putSymbols(id, { keepalive })));
  }

  _cancel(id) {
    if (this._pending.has(id)) { this._clearTimer(this._pending.get(id)); this._pending.delete(id); }
  }

  _putSymbols(id, { keepalive = false } = {}) {
    const tab = this._tabs.get(id);
    if (!tab) return Promise.resolve();
    return this._send('PUT', `/api/chart_tabs/${encodeURIComponent(id)}`, { symbols: tab.symbols }, { keepalive });
  }

  async _send(method, url, body, { keepalive = false } = {}) {
    if (this.offline) return;
    try {
      const r = await this._fetch(url, { method, keepalive, ...(body ? { headers: JSON_HEADERS, body: JSON.stringify(body) } : {}) });
      if (!r.ok) console.warn(`chart tab ${method} failed`, r.status);
    } catch (e) {
      console.warn(`chart tab ${method} failed`, e);
    }
  }
}
