// The Multi-Chart Grid page: a tab strip, a layout picker, a shape picker, and one ChartPane per
// slot. Layouts live in the DB (layout_store.js); each tab is a layout plus its own tickers
// (tab_store.js). Only the tab on show has panes, so a tab in the background subscribes to
// nothing; a hidden page tears its panes down too (visibility.js). Bars come from the chart
// feed through one CandleRouter per page (../charts/candle_router.js).
import { ChartPane } from './pane.js';
import { CandleRouter } from '../charts/candle_router.js';
import { LayoutStore, newPane } from './layout_store.js';
import { TabStore, tabTicker, withTabSymbols, symbolsOf } from './tab_store.js';
import { suspendWhenHidden } from './visibility.js';
import { GridSync, diffPanes, popoutUrl } from './grid_sync.js';
import { linkedSymbolFor } from './link.js';
import { WatchlistDrawer, allSameSymbol } from './watchlist.js';
import { NewsPanel, SideColumn } from './news.js';
import { GridResizer, validSizes } from './resize.js';
import { prevCloseFetcher } from './prev_close.js';
import { SHAPES, SHAPE_ORDER, paneCount, applyShape, resizePanes } from './shapes.js';

export function shapeThumb(key) {
  const el = document.createElement('div');
  el.className = 'shape-thumb';
  for (let i = 0; i < paneCount(key); i++) el.appendChild(document.createElement('span'));
  applyShape(el, key);
  return el;
}

// The "Trade" button shows on any layout with a linked pane: it pops the Trade page out on
// the linked ticker. Null when no pane is linked.
export const tradeSymbolFor = panes => (panes || []).find(p => p.linked && p.symbol)?.symbol ?? null;
// Layouts (lower-cased names) that keep their own ticker when the layout menu carries one over.
const PINNED_SYMBOL_LAYOUTS = new Set(['spx-0dte']);

// The ticker the news panel follows when the grid changes under it: the linked panes' ticker,
// else the first pane's (the one every pane shares, on a one-symbol layout).
export const focusSymbolFor = panes => {
  return tradeSymbolFor(panes) ?? (panes || []).find(p => p.symbol)?.symbol ?? null;
};

export const tradeUrl = sym => `/trade?ticker=${encodeURIComponent(sym)}`;

export class GridDashboard {
  constructor({ root, socket, bare = false, store = null, tabStore = null, PaneClass = ChartPane,
    socketFactory = () => (window.io ? window.io({ forceNew: true }) : null),
    search = window.location.search, visibilityDoc = globalThis.document, hiddenGraceMs = undefined }) {
    this.root = root;
    this.socket = socket;
    this.bare = bare;
    this.PaneClass = PaneClass;
    this.router = new CandleRouter(socket);
    this.store = store || new LayoutStore();
    this.tabs = tabStore || new TabStore();
    this.tabId = null;            // the tab this window shows
    this._lastShared = new Map(); // tab id -> the ticker its one-symbol layouts last showed
    this.suspended = false;       // panes torn down while the page is hidden
    this.sync = new GridSync({
      onLayout: (id, payload, tabId) => this._onRemoteLayout(id, payload, tabId),
      onTabs: () => this._run(() => this._onRemoteTabs()),
    });
    if (bare) root.classList.add('bare');
    this.panes = new Map();       // pane id -> ChartPane, in slot order
    this.shape = 'eq4';
    this.sizes = null;            // dragged column/row sizes for this shape; null = its defaults
    this.socketFactory = socketFactory;
    this.search = search;
    this._build();
    this.ready = this._start();
    this._unwatchHidden = suspendWhenHidden({ doc: visibilityDoc, graceMs: hiddenGraceMs,
      onSuspend: () => this._suspend(), onResume: () => this._resume() });
  }

  async _start() {
    try {
      await this.store.init();
    } catch (e) {
      console.error('layouts failed to load', e);
      this.mainEl.textContent = `Couldn't load layouts (${e.message}). Reload to try again.`;
      return;
    }
    const active = this.store.active();
    await this.tabs.init({ hasLayout: id => !!this.store.get(id), fallbackLayoutId: active?.id,
                           fallbackSymbols: symbolsOf(active?.payload) });
    const list = this.tabs.list();
    this.tabId = this.tabs.remembered() || list.find(t => t.layoutId === active?.id)?.id || list[0]?.id || null;
    const tab = this._tab();
    if (tab && tab.layoutId !== active?.id) await this.store.setActive(tab.layoutId);
    await this._openFromUrl();
    window.addEventListener('pagehide', () => {
      this.store.flush({ keepalive: true });
      this.tabs.flush({ keepalive: true });
    });
  }

  _tab() { return this.tabId ? this.tabs.get(this.tabId) : null; }

  // ?layout=<name>&symbol=<ticker> (the Morning Brief's "Open chart"): switch to the saved layout
  // with that name, then load the symbol the way a watchlist click does. Either part may be
  // missing; an unknown layout leaves the active one. The query is dropped from the address bar
  // afterwards so a reload doesn't re-apply it.
  async _openFromUrl() {
    const q = new URLSearchParams(this.search || '');
    const name = (q.get('layout') || '').trim().toLowerCase();
    const symbol = (q.get('symbol') || '').trim().toUpperCase();
    if (name) {
      const target = this.store.list().find(l => String(l.name || '').trim().toLowerCase() === name);
      if (target && target.id !== this.store.active()?.id) {
        const open = this.tabs.list().find(t => t.layoutId === target.id);
        if (open) { this.tabId = open.id; this.tabs.remember(open.id); }
        await this.store.flush();
        await this.store.setActive(target.id);
      }
    }
    this._loadActiveLayout();
    if (symbol && /^\/?[A-Z][A-Z0-9.]{0,9}$/.test(symbol)) this.applyWatchlistSymbol(symbol);
    if ((name || symbol) && this.search === window.location.search && window.history?.replaceState) {
      q.delete('layout'); q.delete('symbol');
      const rest = q.toString();
      window.history.replaceState(null, '', window.location.pathname + (rest ? `?${rest}` : ''));
    }
  }

  _build() {
    this.root.innerHTML = `
      <header class="grid-header">
        <h1 class="grid-title">Live Multi-Chart Dashboard</h1>
        <div class="grid-header-left">
          <div class="layout-picker">
            <button class="layout-picker-btn" type="button">
              <span class="layout-picker-name">…</span>
              <span class="layout-picker-caret">▾</span>
            </button>
            <div class="layout-picker-menu hidden"></div>
          </div>
          <div class="shape-picker">
            <button class="shape-picker-btn" type="button" title="Grid shape"></button>
            <div class="shape-menu hidden"></div>
          </div>
        </div>
        <div class="grid-header-right"></div>
        <div class="grid-tabs" role="tablist"></div>
      </header>
      <div class="grid-body">
        <main class="grid-main"></main>
        <div class="side-col hidden">
          <aside class="watchlist-drawer hidden"></aside>
          <aside class="news-panel hidden"></aside>
        </div>
      </div>
    `;
    this.mainEl = this.root.querySelector('.grid-main');
    this.tabsEl = this.root.querySelector('.grid-tabs');
    this.resizer = new GridResizer({
      el: this.mainEl,
      getShape: () => this.shape,
      getSizes: () => this.sizes,
      apply: (sizes) => applyShape(this.mainEl, this.shape, sizes),
      onChange: (sizes) => { this.sizes = sizes; this._save(); },
    });
    this.pickerBtn = this.root.querySelector('.layout-picker-btn');
    this.pickerName = this.root.querySelector('.layout-picker-name');
    this.pickerMenu = this.root.querySelector('.layout-picker-menu');
    this.shapeBtn = this.root.querySelector('.shape-picker-btn');
    this.shapeMenu = this.root.querySelector('.shape-menu');
    this.headerRight = this.root.querySelector('.grid-header-right');

    if (!this.bare) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'grid-popout-btn grid-popout-grid-btn';
      b.textContent = '⧉ Pop out grid';
      b.title = 'Open this grid in its own window, without the app nav (for a second monitor)';
      b.addEventListener('click', () => window.open('/chart/grid?bare=1', 'chart-grid-bare', 'width=1600,height=900'));
      this.headerRight.appendChild(b);
    }

    for (const key of SHAPE_ORDER) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'shape-option';
      b.dataset.shape = key;
      b.title = `${SHAPES[key].label} chart${paneCount(key) > 1 ? 's' : ''}`;
      b.appendChild(shapeThumb(key));
      b.addEventListener('click', () => { this.shapeMenu.classList.add('hidden'); this.setShape(key); });
      this.shapeMenu.appendChild(b);
    }
    this.shapeBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      this.pickerMenu.classList.add('hidden');
      this.shapeMenu.classList.toggle('hidden');
    });
    this.pickerBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      this.shapeMenu.classList.add('hidden');
      this._toggleLayoutMenu();
    });
    document.addEventListener('click', () => {
      this.pickerMenu.classList.add('hidden');
      this.shapeMenu.classList.add('hidden');
      this.tabsEl.querySelector('.grid-tab-add-menu')?.classList.add('hidden');
    });

    this.tradeBtn = document.createElement('button');
    this.tradeBtn.type = 'button';
    this.tradeBtn.className = 'grid-popout-btn grid-trade-btn hidden';
    this.tradeBtn.textContent = '↗ Trade';
    this.tradeBtn.title = 'Open the linked ticker on the Trade page';
    this.tradeBtn.addEventListener('click', () => {
      const sym = this._tradeSymbol();
      if (sym) window.open(tradeUrl(sym), 'trade-popout', 'popup,width=1400,height=950');
    });
    this.headerRight.prepend(this.tradeBtn);

    // Watchlist drawer: its own socket so its quote subscription never collides with others.
    const wlBtn = document.createElement('button');
    wlBtn.type = 'button';
    wlBtn.className = 'grid-watchlist-btn';
    wlBtn.textContent = '☰ Watchlist';
    wlBtn.title = 'Show or hide the watchlist';
    this.headerRight.prepend(wlBtn);
    this.watchlist = new WatchlistDrawer({
      el: this.root.querySelector('.watchlist-drawer'),
      socketFactory: this.socketFactory,
      onPick: (sym) => { this.applyWatchlistSymbol(sym); this.news?.setSymbol(sym); },
      chartSymbols: () => [...this.panes.values()].map(p => p.state.symbol),
    });
    // News shares the watchlist's column (news.js): both open = watchlist on top, news below.
    const newsBtn = document.createElement('button');
    newsBtn.type = 'button';
    newsBtn.className = 'grid-watchlist-btn grid-news-btn';
    newsBtn.textContent = '📰 News';
    newsBtn.title = 'Show or hide headlines for the ticker on the chart';
    wlBtn.after(newsBtn);
    this.side = new SideColumn({ el: this.root.querySelector('.side-col'),
      topEl: this.root.querySelector('.watchlist-drawer'), bottomEl: this.root.querySelector('.news-panel') });
    this.news = new NewsPanel({ el: this.root.querySelector('.news-panel'),
      onOpenChange: (open) => { newsBtn.classList.toggle('on', open); this.side.layout(); } });
    newsBtn.addEventListener('click', () => this.news.toggle());
    this.news.init();
    const markWl = () => { wlBtn.classList.toggle('on', this.watchlist.isOpen); this.side.layout(); };
    markWl();
    wlBtn.addEventListener('click', () => { this.watchlist.toggle(); markWl(); });
    this.watchlist.init().then(markWl);
  }

  // The linked panes' ticker, as shown now (a watchlist pick may have changed it).
  _tradeSymbol() {
    return tradeSymbolFor([...this.panes.values()].map(p => p.state));
  }

  // The news panel follows the grid's focus ticker when it changes; a watchlist pick on a grid of
  // different tickers sets the news ticker without moving the focus, so it sticks until then.
  _followFocus() {
    const sym = focusSymbolFor([...this.panes.values()].map(p => p.state));
    if (sym && sym !== this._focus) { this._focus = sym; this.news?.setSymbol(sym); }
  }

  _refreshTradeButton() {
    this.tradeBtn.classList.toggle('hidden', !this._tradeSymbol());
  }

  _renderShapeButton() {
    this.shapeBtn.innerHTML = '';
    this.shapeBtn.appendChild(shapeThumb(this.shape));
    this.shapeBtn.insertAdjacentHTML('beforeend', '<span class="layout-picker-caret">▾</span>');
    this.shapeMenu.querySelectorAll('[data-shape]').forEach(b => b.classList.toggle('active', b.dataset.shape === this.shape));
  }

  // ---- layout menu ----
  _toggleLayoutMenu() {
    if (this.pickerMenu.classList.contains('hidden')) {
      this._renderLayoutMenu();
      this.pickerMenu.classList.remove('hidden');
    } else {
      this.pickerMenu.classList.add('hidden');
    }
  }

  _renderLayoutMenu() {
    const activeId = this.store.active()?.id;
    const menu = this.pickerMenu;
    menu.innerHTML = '';
    for (const l of this.store.list()) {
      const row = document.createElement('div');
      row.className = 'layout-menu-row' + (l.id === activeId ? ' active' : '');
      const nameSpan = document.createElement('span');
      nameSpan.className = 'layout-menu-row-name';
      nameSpan.textContent = l.name;
      nameSpan.addEventListener('click', () => this._run(async () => {
        await this.store.flush();
        await this.store.setActive(l.id);
        this._loadActiveLayout();
        this._carryLinkedSymbol();
        this.pickerMenu.classList.add('hidden');
      }));
      row.appendChild(nameSpan);
      const dupBtn = document.createElement('span');
      dupBtn.className = 'layout-menu-row-dup';
      dupBtn.textContent = '⧉';
      dupBtn.title = `Duplicate "${l.name}"`;
      dupBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        const n = prompt('New layout name:', `${l.name} copy`);
        if (!n) return;
        this._run(async () => {
          await this.store.duplicate(l.id, n.trim());
          this._loadActiveLayout();
          this.pickerMenu.classList.add('hidden');
        });
      });
      row.appendChild(dupBtn);
      menu.appendChild(row);
    }
    menu.appendChild(this._makeDivider());
    menu.appendChild(this._menuAction('➕ New blank layout', () => {
      const n = prompt('New layout name:', 'New Layout');
      if (!n) return;
      this._run(async () => {
        await this.store.saveAsNew(n.trim(), { shape: 'eq4', panes: [] });
        this._loadActiveLayout();
        this.pickerMenu.classList.add('hidden');
      });
    }));
    menu.appendChild(this._menuAction('⧉ Duplicate current', () => {
      const cur = this.store.active();
      if (!cur) return;
      const n = prompt('Name for the copy:', `${cur.name} copy`);
      if (!n || !n.trim()) return;
      this._run(async () => {
        await this.store.flush();
        await this.store.duplicate(cur.id, n.trim());
        this._loadActiveLayout();
        this.pickerMenu.classList.add('hidden');
      });
    }));
    const renameRow = this._menuAction('✏️ Rename current', () => {
      const cur = this.store.active();
      if (!cur) return;
      const n = prompt('Layout name:', cur.name);
      if (!n) return;
      this._run(async () => {
        await this.store.rename(cur.id, n.trim());
        this._refreshPickerName();
        this._renderLayoutMenu();
      });
    });
    menu.appendChild(renameRow);
    const removeRow = this._menuAction('🗑️ Delete current', () => {
      const cur = this.store.active();
      if (!cur || !confirm(`Delete "${cur.name}"?`)) return;
      this._run(async () => {
        // remove() rebuilds the cache from the server; flush first so a pending edit isn't dropped.
        await this.store.flush();
        if (await this.store.remove(cur.id)) await this._afterLayoutRemoved(cur.id);
        this.pickerMenu.classList.add('hidden');
      });
    });
    if (this.store.list().length <= 1) {
      removeRow.classList.add('disabled');
      removeRow.style.pointerEvents = 'none';
    }
    menu.appendChild(removeRow);
  }

  _run(fn) {
    fn().catch(e => { console.error(e); alert(`Layout change failed: ${e.message}`); });
  }

  _menuAction(label, onClick) {
    const row = document.createElement('div');
    row.className = 'layout-menu-row layout-menu-action';
    row.textContent = label;
    row.addEventListener('click', onClick);
    return row;
  }

  _makeDivider() {
    const d = document.createElement('div');
    d.className = 'layout-menu-divider';
    return d;
  }

  _refreshPickerName() {
    this.pickerName.textContent = this.store.active()?.name || '(none)';
  }

  // ---- panes ----
  setShape(key) {
    if (!SHAPES[key]) return;
    const current = [...this.panes.values()].map(p => p.state);
    const next = resizePanes(current, paneCount(key), newPane);
    const keep = new Set(next.map(s => s.id));
    for (const [id, p] of this.panes) if (!keep.has(id)) p.destroy();
    const old = this.panes;
    this.panes = new Map();
    for (const s of next) this.panes.set(s.id, old.get(s.id) || this._makePane(s));
    this.shape = key;
    this.sizes = null;
    this._applyShape();
    this._save();
  }

  _applyShape() {
    this.mainEl.innerHTML = '';
    for (const pane of this.panes.values()) this.mainEl.appendChild(pane.root);
    applyShape(this.mainEl, this.shape, this.sizes);
    this.resizer.render();
    this._renderShapeButton();
    this.watchlist?.markOnChart();
  }

  _makePane(state) {
    const paneRoot = document.createElement('section');
    paneRoot.className = 'pane';
    paneRoot.id = `pane-${state.id}`;
    return new this.PaneClass({
      root: paneRoot,
      state: { ...state },
      router: this.router,
      onStateChange: () => this._save(),
      getPrevClose: (symbol) => this.getPrevClose(symbol),
      onSymbolChange: (src) => {
        for (const p of this.panes.values()) if (p !== src && p.state.linked) p.setSymbol(src.state.symbol);
      },
      onLinkToggle: (src) => {
        this._refreshTradeButton();
        if (!src.state.linked) return;
        const sym = linkedSymbolFor(this._currentPayload().panes, src.state.id);
        if (sym) src.setSymbol(sym);
      },
      onPopout: () => {
        const layout = this.store.active();
        if (!layout) return;
        this.store.flush();
        this.tabs.flush();
        window.open(popoutUrl(layout.id, state.id, this.tabId), `chart-grid-${this.tabId || ''}-${state.id}`,
          'width=1100,height=700');
      },
    });
  }

  // Canonical previous-day close (prev_close.js), shared by every pane on the page.
  getPrevClose(symbol) {
    this._prevClose = this._prevClose || prevCloseFetcher();
    return this._prevClose(symbol);
  }

  _currentPayload() {
    return {
      shape: this.shape,
      ...(this.sizes ? { sizes: this.sizes } : {}),
      panes: [...this.panes.values()].map(p => ({
        id: p.state.id,
        symbol: p.state.symbol,
        timeframe: p.state.timeframe,
        indicators: p.state.indicators,
        ...(p.state.linked ? { linked: true } : {}),
        ...(p.state.fitCandles === false ? { fitCandles: false } : {}),
      })),
    };
  }

  // A watchlist click: load `symbol` into every pane, but only when they all show one symbol
  // (a 1-chart layout, or one ticker on several timeframes). A grid of different tickers is
  // never touched. True when the grid took the symbol.
  // The one symbol every pane shows (a 1-chart layout, or one ticker on several timeframes),
  // or null for a grid of different tickers.
  _sharedSymbol() {
    const syms = [...this.panes.values()].map(p => p.state.symbol);
    return allSameSymbol(syms) ? syms[0] : null;
  }

  // The ticker one-symbol layouts share, or null when this layout keeps its own ticker: a grid
  // of different tickers, or a pinned layout (SPX-0DTE is always SPX).
  _carriedSymbol() {
    if (PINNED_SYMBOL_LAYOUTS.has((this.store.active()?.name || '').trim().toLowerCase())) return null;
    return this._sharedSymbol();
  }

  // Picking a one-symbol layout from the menu loads the ticker last shown on a one-symbol
  // layout, so Single -> Single - 4 pane keeps the ticker. Mixed grids and pinned layouts
  // neither set nor take it.
  _carryLinkedSymbol() {
    const sym = this._lastShared.get(this.tabId);
    const cur = this._carriedSymbol();
    if (sym && cur && cur !== sym) this.applyWatchlistSymbol(sym);
  }

  applyWatchlistSymbol(symbol) {
    const panes = [...this.panes.values()];
    if (!allSameSymbol(panes.map(p => p.state.symbol))) return false;
    let changed = false;
    for (const p of panes) changed = p.setSymbol(symbol) || changed;
    if (changed) this._save();
    return true;
  }

  // The layout keeps the latest tickers too (what a new tab on it starts with); the tab keeps
  // its own.
  _save() {
    if (this.suspended) return;
    const carried = this._carriedSymbol();
    if (carried) this._lastShared.set(this.tabId, carried);
    this.watchlist?.markOnChart();
    this._followFocus();
    const layout = this.store.active();
    if (!layout) return;
    const payload = this._currentPayload();
    this.store.updatePayload(layout.id, payload);
    if (this.tabId) this.tabs.setSymbols(this.tabId, symbolsOf(payload));
    this.sync.publish(layout.id, payload, this.tabId);
    this._renderTabs();
  }

  // Another window saved a layout. Only the one this window shows is applied, without saving.
  // It came from tab `tabId`: that tab takes its tickers; this window's tab, if another one on
  // the same layout, keeps its own. (No tab: a window from before tabs; it speaks for ours.)
  _onRemoteLayout(id, payload, tabId = null) {
    this.store.applyRemote(id, payload);
    const from = tabId || this.tabId;
    if (from && this.tabs.get(from)?.layoutId === id) this.tabs.applyRemote(from, symbolsOf(this.store.get(id).payload));
    this._renderTabs();
    if (id !== this.store.active()?.id || this.suspended) return;
    const after = withTabSymbols(this.store.get(id).payload, this._tab()?.symbols);
    const { reload, changed } = diffPanes(this._currentPayload(), after);
    if (reload) { this._loadActiveLayout(); this.watchlist?.markOnChart(); return; }
    const sizes = validSizes(this.shape, after.sizes);
    if (JSON.stringify(sizes) !== JSON.stringify(this.sizes)) {
      this.sizes = sizes;
      applyShape(this.mainEl, this.shape, sizes);
      this.resizer.position();
    }
    for (const pid of changed) {
      const next = after.panes.find(p => p.id === pid);
      this.panes.get(pid)?.applyState(next);
    }
    this._refreshTradeButton();
    this._followFocus();
    this.watchlist?.markOnChart();
  }

  // Show the active layout in this window's tab. A tab on another layout (the layout menu picked
  // one, or made one) is moved onto it, starting from that layout's tickers.
  _loadActiveLayout() {
    for (const p of this.panes.values()) p.destroy();
    this.panes = new Map();
    this.suspended = false;
    const layout = this.store.active();
    const tab = this._tab();
    if (layout && tab && tab.layoutId !== layout.id) {
      this.tabs.setLayout(tab.id, layout.id, symbolsOf(layout.payload)).then(() => this.sync.publishTabs());
    }
    const payload = withTabSymbols(layout?.payload, tab?.symbols) || { shape: 'eq4', panes: [] };
    this.shape = SHAPES[payload.shape] ? payload.shape : 'eq4';
    this.sizes = validSizes(this.shape, payload.sizes);
    for (const s of resizePanes(payload.panes || [], paneCount(this.shape), newPane)) {
      this.panes.set(s.id, this._makePane(s));
    }
    this._applyShape();
    this._refreshPickerName();
    this._refreshTradeButton();
    this._followFocus();
    this._renderTabs();
    if (!this._lastShared.get(this.tabId)) {
      const carried = this._carriedSymbol();
      if (carried) this._lastShared.set(this.tabId, carried);
    }
  }

  // ---- tabs ----
  _renderTabs() {
    const el = this.tabsEl;
    if (!el) return;
    const menuOpen = el.querySelector('.grid-tab-add-menu') && !el.querySelector('.grid-tab-add-menu.hidden');
    el.innerHTML = '';
    const tabs = this.tabs.list();
    for (const t of tabs) {
      const layout = this.store.get(t.layoutId);
      if (!layout) continue;
      const b = document.createElement('div');
      b.className = 'grid-tab' + (t.id === this.tabId ? ' active' : '');
      b.dataset.tab = t.id;
      b.title = layout.name;
      const name = document.createElement('span');
      name.className = 'grid-tab-name';
      name.textContent = layout.name;
      b.appendChild(name);
      const ticker = tabTicker(layout.payload.panes, t.symbols);
      if (ticker) {
        const tk = document.createElement('span');
        tk.className = 'grid-tab-ticker';
        tk.textContent = ticker;
        b.appendChild(tk);
      }
      if (tabs.length > 1) {
        const x = document.createElement('span');
        x.className = 'grid-tab-close';
        x.textContent = '×';
        x.title = 'Close tab (the layout is kept)';
        x.addEventListener('click', (e) => { e.stopPropagation(); this._run(() => this._closeTab(t.id)); });
        b.appendChild(x);
      }
      b.addEventListener('click', () => this._run(() => this._selectTab(t.id)));
      el.appendChild(b);
    }
    const wrap = document.createElement('div');
    wrap.className = 'grid-tab-add';
    const add = document.createElement('button');
    add.type = 'button';
    add.className = 'grid-tab-add-btn';
    add.textContent = '+';
    add.title = 'Open a layout in a new tab';
    const menu = document.createElement('div');
    menu.className = 'grid-tab-add-menu' + (menuOpen ? '' : ' hidden');
    for (const l of this.store.list()) {
      const row = document.createElement('div');
      row.className = 'layout-menu-row';
      const n = document.createElement('span');
      n.className = 'layout-menu-row-name';
      n.textContent = l.name;
      row.appendChild(n);
      row.addEventListener('click', (e) => {
        e.stopPropagation();
        menu.classList.add('hidden');
        this._run(() => this._openTab(l.id));
      });
      menu.appendChild(row);
    }
    add.addEventListener('click', (e) => {
      e.stopPropagation();
      this.pickerMenu.classList.add('hidden');
      this.shapeMenu.classList.add('hidden');
      menu.classList.toggle('hidden');
    });
    wrap.append(add, menu);
    el.appendChild(wrap);
  }

  // Show tab `id` in this window. The tab left behind keeps its tickers and subscribes to nothing.
  async _selectTab(id) {
    const tab = this.tabs.get(id);
    if (!tab || id === this.tabId) return;
    await this.store.flush();
    await this.tabs.flush();
    this.tabId = id;
    this.tabs.remember(id);
    if (tab.layoutId !== this.store.active()?.id) await this.store.setActive(tab.layoutId);
    this._loadActiveLayout();
  }

  async _openTab(layoutId) {
    const layout = this.store.get(layoutId);
    if (!layout) return;
    const tab = await this.tabs.open(layoutId, symbolsOf(layout.payload));
    this.sync.publishTabs();
    await this._selectTab(tab.id);
  }

  async _closeTab(id) {
    const tabs = this.tabs.list();
    if (tabs.length <= 1) return;
    if (id === this.tabId) {
      const i = tabs.findIndex(t => t.id === id);
      await this._selectTab((tabs[i + 1] || tabs[i - 1]).id);
    }
    await this.tabs.close(id);
    this.sync.publishTabs();
    this._renderTabs();
  }

  // The layout menu deleted a layout; the server closed its tabs. Land on a tab still open, or
  // open one on the layout the server made active.
  async _afterLayoutRemoved(layoutId) {
    this.tabs.dropLayout(layoutId);
    await this.tabs.reload();
    if (!this._tab()) {
      let next = this.tabs.list().find(t => this.store.get(t.layoutId));
      const active = this.store.active();
      if (!next && active) next = await this.tabs.open(active.id, symbolsOf(active.payload));
      this.tabId = next?.id || null;
      if (next) this.tabs.remember(next.id);
    }
    const tab = this._tab();
    if (tab && tab.layoutId !== this.store.active()?.id) await this.store.setActive(tab.layoutId);
    this.sync.publishTabs();
    this._loadActiveLayout();
  }

  // Another window opened, closed or moved a tab. Follow this window's tab if it moved to
  // another layout; if it was closed, show the first one left.
  async _onRemoteTabs() {
    await this.tabs.reload();
    let tab = this._tab();
    if (!tab) {
      tab = this.tabs.list()[0] || null;
      this.tabId = tab?.id || null;
    }
    if (tab && !this.store.get(tab.layoutId)) {
      await this.store.flush();
      await this.store.init();
    }
    if (tab && this.store.get(tab.layoutId) && tab.layoutId !== this.store.active()?.id) {
      await this.store.setActive(tab.layoutId);
      if (!this.suspended) this._loadActiveLayout();
      return;
    }
    this._renderTabs();
  }

  // ---- hidden page ----
  _suspend() {
    for (const p of this.panes.values()) p.destroy();
    this.panes = new Map();
    this.suspended = true;
    this.mainEl.innerHTML = '<div class="grid-paused">Paused while this page is hidden</div>';
    this.watchlist?.pause();
    this.news?.pause();
  }

  _resume() {
    this._loadActiveLayout();
    this.watchlist?.resume();
    this.news?.resume();
  }
}
