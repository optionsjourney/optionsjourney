// The Multi-Chart Grid's watchlist drawer: named groups of tickers with live Schwab quotes
// (services/watchlist_quote_bridge.py; REST every 20 s when the stream can't serve them).
// Groups are stored server-side (/api/watchlist). Clicking a ticker hands it to the grid,
// which loads it only when every pane shows one symbol (GridDashboard.applyWatchlistSymbol).
// A read-only Trading group on top lists the underlyings of open positions (minus SPX); it is
// rebuilt server-side (/api/watchlist/trading) and refreshed every minute while the drawer is open.

const OPEN_KEY = 'chart_grid_watchlist_open';
const TRADING_COLLAPSED_KEY = 'chart_grid_watchlist_trading_collapsed';
const TRADING_ID = '__trading';
const TRADING_REFRESH_MS = 60_000;
const SOURCE_LABEL = { live: 'live', dxlink: 'live · tasty', rest: '20s', stale: 'stale', none: 'quotes unavailable' };

export function allSameSymbol(symbols) {
  return symbols.length > 0 && symbols.every(s => s === symbols[0]);
}

export function moveItem(arr, from, to) {
  const out = arr.slice();
  const [item] = out.splice(from, 1);
  out.splice(to, 0, item);
  return out;
}

export function allSymbols(groups) {
  const out = [];
  for (const g of groups) for (const s of g.symbols || []) if (!out.includes(s)) out.push(s);
  return out;
}

const asGroups = (x) => (Array.isArray(x) ? x : []);
const num = (v) => (v === null || v === undefined || Number.isNaN(Number(v)) ? null : Number(v));
export function fmtPrice(v) { const n = num(v); return n === null ? '—' : n.toFixed(2); }
export function fmtChange(v) { const n = num(v); return n === null ? '—' : `${n > 0 ? '+' : ''}${n.toFixed(2)}`; }
export function fmtPct(v) { const n = num(v); return n === null ? '—' : `${n > 0 ? '+' : ''}${n.toFixed(2)}%`; }
export function changeClass(v) { const n = num(v); return n > 0 ? 'up' : n < 0 ? 'down' : ''; }

async function call(method, url, body) {
  const r = await fetch(url, { method, headers: { 'Content-Type': 'application/json' },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error_message || `HTTP ${r.status}`);
  return data;
}

export class WatchlistApi {
  list() { return call('GET', '/api/watchlist'); }
  trading() { return call('GET', '/api/watchlist/trading'); }
  createGroup(name) { return call('POST', '/api/watchlist/groups', { name }); }
  updateGroup(id, patch) { return call('PUT', `/api/watchlist/groups/${encodeURIComponent(id)}`, patch); }
  deleteGroup(id) { return call('DELETE', `/api/watchlist/groups/${encodeURIComponent(id)}`); }
  reorder(ids) { return call('PUT', '/api/watchlist/order', { ids }); }
}

function safeStorage() {
  try { return window.localStorage; } catch { return null; }
}

export class WatchlistDrawer {
  constructor({ el, socketFactory, onPick, chartSymbols = () => [], api = new WatchlistApi(),
    storage = safeStorage() }) {
    this.el = el;
    this.socketFactory = socketFactory;
    this.onPick = onPick;
    this.chartSymbols = chartSymbols;
    this.api = api;
    this.storage = storage;
    this.groups = [];
    this.trading = [];         // symbols of the read-only Trading group
    this.tradingCollapsed = false;
    this.tradingTimer = null;
    this.quotes = {};          // symbol -> {last, net_chg, net_pct, mark, mark_pct}
    this.source = null;
    this.picked = null;
    this.isOpen = false;
    this.socket = null;
    this.drag = null;          // {kind: 'group'|'symbol', groupId, index}
    el.classList.add('watchlist-drawer');
  }

  async init() {
    try { this.groups = asGroups(await this.api.list()); } catch (e) { console.error('watchlist load failed', e); }
    await this._loadTrading();
    let open = false;
    try { open = this.storage?.getItem(OPEN_KEY) === '1'; } catch { /* private window */ }
    try { this.tradingCollapsed = this.storage?.getItem(TRADING_COLLAPSED_KEY) === '1'; } catch { /* ignore */ }
    this.render();
    this.setOpen(open, { remember: false });
  }

  toggle() { this.setOpen(!this.isOpen); }

  // The page is hidden (visibility.js): stop the quote stream and the Trading refresh until
  // resume(). The drawer stays open.
  pause() {
    this.paused = true;
    if (this.isOpen) this._unsubscribe();
    clearInterval(this.tradingTimer);
    this.tradingTimer = null;
  }

  resume() {
    this.paused = false;
    if (this.isOpen) this.setOpen(true, { remember: false });
  }

  setOpen(open, { remember = true } = {}) {
    this.isOpen = open;
    this.el.classList.toggle('hidden', !open);
    if (remember) { try { this.storage?.setItem(OPEN_KEY, open ? '1' : '0'); } catch { /* ignore */ } }
    if (open) this._subscribe(); else this._unsubscribe();
    clearInterval(this.tradingTimer);
    this.tradingTimer = open ? setInterval(() => this.refreshTrading(), TRADING_REFRESH_MS) : null;
  }

  _allGroups() {
    return [{ id: TRADING_ID, name: 'Trading', collapsed: this.tradingCollapsed, symbols: this.trading,
      dynamic: true }, ...this.groups];
  }

  _symbols() { return allSymbols(this._allGroups()); }

  async _loadTrading() {
    if (!this.api.trading) return false;
    try {
      const res = await this.api.trading();
      const next = Array.isArray(res?.symbols) ? res.symbols : [];
      const changed = next.join(',') !== this.trading.join(',');
      this.trading = next;
      return changed;
    } catch (e) { console.warn('watchlist trading load failed', e); return false; }
  }

  async refreshTrading() {
    if (!(await this._loadTrading())) return;
    this.render();
    if (this.isOpen && !this.paused) this._subscribe();
  }

  _ensureSocket() {
    if (this.socket || !this.socketFactory) return this.socket;
    try { this.socket = this.socketFactory(); } catch (e) { console.warn('watchlist socket failed', e); }
    if (!this.socket) return null;
    this.socket.on('watchlist_quotes', (msg) => this._onQuotes(msg));
    this.socket.on('watchlist_quotes_status', () => this._setSource('none'));
    this.socket.on('connect', () => { if (this.isOpen && !this.paused) this._subscribe(); });
    return this.socket;
  }

  _subscribe() {
    const s = this._ensureSocket();
    if (!s) { this._setSource('none'); return; }
    s.emit('subscribe_watchlist_quotes', { symbols: this._symbols() });
  }

  _unsubscribe() {
    if (this.socket) this.socket.emit('unsubscribe_watchlist_quotes');
  }

  _onQuotes(msg) {
    Object.assign(this.quotes, msg.quotes || {});
    for (const sym of Object.keys(msg.quotes || {})) {
      this.el.querySelectorAll(`.wl-row[data-symbol="${sym}"]`).forEach(r => this._fillRow(r, sym));
    }
    if (msg.source) this._setSource(msg.source);
  }

  _setSource(source) {
    this.source = source;
    const b = this.el.querySelector('.wl-source');
    if (b) { b.textContent = SOURCE_LABEL[source] || ''; b.dataset.source = source; }
  }

  _fillRow(row, sym) {
    const q = this.quotes[sym] || {};
    const set = (cls, text, color) => {
      const c = row.querySelector(cls);
      c.textContent = text;
      c.classList.remove('up', 'down');
      if (color) c.classList.add(color);
    };
    set('.wl-last', fmtPrice(q.last));
    set('.wl-net', fmtChange(q.net_chg), changeClass(q.net_chg));
    set('.wl-netpct', fmtPct(q.net_pct), changeClass(q.net_pct));
    set('.wl-mark', fmtPrice(q.mark));
    set('.wl-markpct', fmtPct(q.mark_pct), changeClass(q.mark_pct));
  }

  markOnChart() {
    const on = new Set(this.chartSymbols());
    this.el.querySelectorAll('.wl-row').forEach(r => r.classList.toggle('on-chart', on.has(r.dataset.symbol)));
  }

  // ---- rendering ----
  render() {
    this.el.innerHTML = `
      <div class="wl-head">
        <span class="wl-title">Watchlist</span>
        <span class="wl-source"></span>
        <button type="button" class="wl-add-group" title="New group">+ Group</button>
      </div>
      <div class="wl-cols"><span>Symbol</span><span>Last</span><span>Net Chng</span><span>Net % Chg</span><span>Mark</span><span>Mark % Chg</span></div>
      <div class="wl-body"></div>`;
    this.el.querySelector('.wl-add-group').addEventListener('click', () => this._addGroup());
    const body = this.el.querySelector('.wl-body');
    body.appendChild(this._groupEl(this._allGroups()[0], -1));
    this.groups.forEach((g, gi) => body.appendChild(this._groupEl(g, gi)));
    if (this.source) this._setSource(this.source);
    this.markOnChart();
  }

  _groupEl(g, gi) {
    const sec = document.createElement('section');
    sec.className = 'wl-group';
    sec.dataset.id = g.id;
    const head = document.createElement('div');
    head.className = 'wl-group-head';
    if (g.dynamic) {
      sec.classList.add('wl-dynamic');
      head.title = 'Underlyings of your open positions (SPX left out; futures by root, e.g. /ES). Updates automatically.';
      head.innerHTML = `
        <button type="button" class="wl-caret">${g.collapsed ? '▸' : '▾'}</button>
        <span class="wl-group-name"></span><span class="wl-count">(${g.symbols.length})</span>
        <span class="wl-auto">auto</span>`;
      head.querySelector('.wl-group-name').textContent = g.name;
      head.querySelector('.wl-caret').addEventListener('click', () => this._toggleTrading());
      sec.appendChild(head);
      if (!g.collapsed) g.symbols.forEach((sym, si) => sec.appendChild(this._rowEl(g, sym, si)));
      return sec;
    }
    head.draggable = true;
    head.innerHTML = `
      <button type="button" class="wl-caret">${g.collapsed ? '▸' : '▾'}</button>
      <span class="wl-group-name"></span><span class="wl-count">(${g.symbols.length})</span>
      <span class="wl-group-actions">
        <button type="button" class="wl-add" title="Add symbols">+</button>
        <button type="button" class="wl-rename" title="Rename">✎</button>
        <button type="button" class="wl-delete" title="Delete group">✕</button>
      </span>`;
    head.querySelector('.wl-group-name').textContent = g.name;
    head.querySelector('.wl-caret').addEventListener('click', () => this._save(g, { collapsed: !this._group(g.id).collapsed }));
    head.querySelector('.wl-add').addEventListener('click', () => this._addSymbols(g));
    head.querySelector('.wl-rename').addEventListener('click', () => this._rename(g));
    head.querySelector('.wl-delete').addEventListener('click', () => this._delete(g));
    head.addEventListener('dragstart', (e) => { this.drag = { kind: 'group', index: gi }; e.dataTransfer?.setData('text/plain', g.id); });
    head.addEventListener('dragover', (e) => { if (this.drag?.kind === 'group') e.preventDefault(); });
    head.addEventListener('drop', (e) => { e.preventDefault(); this._dropGroup(gi); });
    sec.appendChild(head);
    if (!g.collapsed) g.symbols.forEach((sym, si) => sec.appendChild(this._rowEl(g, sym, si)));
    return sec;
  }

  _rowEl(g, sym, si) {
    const row = document.createElement('div');
    row.className = 'wl-row';
    row.dataset.symbol = sym;
    row.innerHTML = `<span class="wl-sym"></span><span class="wl-last"></span><span class="wl-net"></span>
      <span class="wl-netpct"></span><span class="wl-mark"></span><span class="wl-markpct"></span>
      ${g.dynamic ? '' : '<button type="button" class="wl-remove" title="Remove">✕</button>'}`;
    row.querySelector('.wl-sym').textContent = sym;
    if (sym === this.picked) row.classList.add('picked');
    this._fillRow(row, sym);
    row.addEventListener('click', (e) => {
      if (e.target.closest('.wl-remove')) return;
      this.picked = sym;
      this.el.querySelectorAll('.wl-row').forEach(r => r.classList.toggle('picked', r.dataset.symbol === sym));
      this.onPick?.(sym);
    });
    if (g.dynamic) return row;
    row.draggable = true;
    row.querySelector('.wl-remove').addEventListener('click', () =>
      this._save(g, { symbols: this._group(g.id).symbols.filter(s => s !== sym) }));
    row.addEventListener('dragstart', (e) => {
      e.stopPropagation();
      this.drag = { kind: 'symbol', groupId: g.id, index: si };
      e.dataTransfer?.setData('text/plain', sym);
    });
    row.addEventListener('dragover', (e) => { if (this.drag?.kind === 'symbol' && this.drag.groupId === g.id) e.preventDefault(); });
    row.addEventListener('drop', (e) => {
      e.preventDefault();
      if (this.drag?.kind === 'symbol' && this.drag.groupId === g.id && this.drag.index !== si) {
        this._save(g, { symbols: moveItem(this._group(g.id).symbols, this.drag.index, si) });
      }
      this.drag = null;
    });
    return row;
  }

  // ---- edits (each one saves, re-renders, and re-subscribes if the symbol set changed) ----
  _group(id) { return this.groups.find(x => x.id === id); }

  _toggleTrading() {
    this.tradingCollapsed = !this.tradingCollapsed;
    try { this.storage?.setItem(TRADING_COLLAPSED_KEY, this.tradingCollapsed ? '1' : '0'); } catch { /* ignore */ }
    this.render();
  }

  async _save(g, patch) {
    const before = this._symbols().join(',');
    // Optimistic, so a second edit made while this PUT is pending builds on it, not on a stale group.
    this.groups = this.groups.map(x => (x.id === g.id ? { ...x, ...patch } : x));
    try {
      const updated = await this.api.updateGroup(g.id, patch);
      this.groups = this.groups.map(x => (x.id === g.id ? updated : x));
    } catch (e) {
      window.alert?.(`Watchlist: ${e.message}`);
      try { this.groups = asGroups(await this.api.list()); } catch { /* keep what we have */ }
    }
    this.render();
    if (this.isOpen && this._symbols().join(',') !== before) this._subscribe();
  }

  _addSymbols(g) {
    const raw = window.prompt(`Add symbols to ${g.name} (comma or space separated; futures like /ES)`);
    if (!raw) return;
    g = this._group(g.id) || g;
    const add = raw.split(/[\s,]+/).map(s => s.trim().toUpperCase()).filter(Boolean);
    this._save(g, { symbols: [...g.symbols, ...add.filter(s => !g.symbols.includes(s))] });
  }

  _rename(g) {
    g = this._group(g.id) || g;
    const name = window.prompt('Rename group', g.name);
    if (name && name.trim() && name.trim() !== g.name) this._save(g, { name: name.trim() });
  }

  async _delete(g) {
    if (!window.confirm(`Delete group ${g.name}?`)) return;
    try { await this.api.deleteGroup(g.id); } catch (e) { window.alert?.(`Watchlist: ${e.message}`); }
    try { this.groups = asGroups(await this.api.list()); } catch { /* keep */ }
    this.render();
    if (this.isOpen && !this.paused) this._subscribe();
  }

  async _addGroup() {
    const name = window.prompt('New group name');
    if (!name || !name.trim()) return;
    try { this.groups = [...this.groups, await this.api.createGroup(name.trim())]; }
    catch (e) { window.alert?.(`Watchlist: ${e.message}`); }
    this.render();
  }

  async _dropGroup(toIndex) {
    const d = this.drag;
    this.drag = null;
    if (d?.kind !== 'group' || d.index === toIndex) return;
    this.groups = moveItem(this.groups, d.index, toIndex);
    this.render();
    try {
      const res = await this.api.reorder(this.groups.map(g => g.id));
      if (Array.isArray(res)) this.groups = res;
    }
    catch (e) { window.alert?.(`Watchlist: ${e.message}`); }
    this.render();
  }
}
