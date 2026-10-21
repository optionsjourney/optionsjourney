// The Multi-Chart Grid's news panel: Finnhub company headlines (/api/news/<symbol>) for the
// ticker the grid is on. It shares the right-hand column with the watchlist (SideColumn):
// either one fills it alone, and with both open the watchlist sits on top and news below, split
// by a draggable divider. Refreshed every 5 minutes while open and the page is visible.

const OPEN_KEY = 'chart_grid_news_open';
const SPLIT_KEY = 'chart_grid_side_split';
const REFRESH_MS = 5 * 60_000;
export const MIN_SPLIT = 0.15;
export const MAX_SPLIT = 0.85;

function safeStorage() {
  try { return window.localStorage; } catch { return null; }
}

// Futures (/ES) and indices ($SPX, ^VIX) have no company news.
export const hasCompanyNews = sym => /^[A-Z][A-Z0-9.]{0,9}$/.test(sym || '');

export function timeAgo(epochSec, nowMs = Date.now()) {
  const s = Math.max(0, Math.round(nowMs / 1000 - epochSec));
  if (s < 60) return 'now';
  if (s < 3600) return `${Math.floor(s / 60)}m`;
  if (s < 86400) return `${Math.floor(s / 3600)}h`;
  return `${Math.floor(s / 86400)}d`;
}

export const clampSplit = f => Math.min(MAX_SPLIT, Math.max(MIN_SPLIT, Number.isFinite(f) ? f : 0.5));

export class NewsPanel {
  constructor({ el, fetchNews = sym => fetch(`/api/news/${encodeURIComponent(sym)}`).then(r => r.json()),
    storage = safeStorage(), onOpenChange = () => {} }) {
    this.el = el;
    this.fetchNews = fetchNews;
    this.storage = storage;
    this.onOpenChange = onOpenChange;
    this.symbol = null;
    this.items = [];
    this.note = '';
    this.isOpen = false;
    this.paused = false;
    this.timer = null;
    this._seq = 0;
    el.classList.add('news-panel');
    el.innerHTML = `
      <div class="nw-head">
        <span class="nw-title">News</span><span class="nw-symbol"></span>
        <button type="button" class="nw-refresh" title="Refresh">↻</button>
      </div>
      <div class="nw-body"></div>`;
    el.querySelector('.nw-refresh').addEventListener('click', () => this.refresh());
  }

  init() {
    let open = false;
    try { open = this.storage?.getItem(OPEN_KEY) === '1'; } catch { /* private window */ }
    this.setOpen(open, { remember: false });
  }

  toggle() { this.setOpen(!this.isOpen); }

  setOpen(open, { remember = true } = {}) {
    this.isOpen = open;
    this.el.classList.toggle('hidden', !open);
    if (remember) { try { this.storage?.setItem(OPEN_KEY, open ? '1' : '0'); } catch { /* ignore */ } }
    this._schedule();
    if (open) this.refresh();
    this.onOpenChange(open);
  }

  pause() { this.paused = true; this._schedule(); }
  resume() { this.paused = false; this._schedule(); if (this.isOpen) this.refresh(); }

  _schedule() {
    clearInterval(this.timer);
    this.timer = this.isOpen && !this.paused ? setInterval(() => this.refresh(), REFRESH_MS) : null;
  }

  setSymbol(sym) {
    if (!sym || sym === this.symbol) return;
    this.symbol = sym;
    this.items = [];
    this.note = '';
    this.render();
    if (this.isOpen) this.refresh();
  }

  async refresh() {
    const sym = this.symbol;
    if (!sym) { this.render(); return; }
    if (!hasCompanyNews(sym)) { this.items = []; this.note = 'No company news for futures or indices'; this.render(); return; }
    const seq = ++this._seq;
    this.el.classList.add('loading');
    let res;
    try { res = await this.fetchNews(sym); } catch (e) { res = { news: [], error: e.message }; }
    if (seq !== this._seq || sym !== this.symbol) return; // a newer symbol or refresh won
    this.el.classList.remove('loading');
    this.items = Array.isArray(res?.news) ? res.news : [];
    this.note = res?.error ? `Couldn't load news (${res.error})` : (res?.note || (this.items.length ? '' : 'No headlines in the last 7 days'));
    this.render();
  }

  render() {
    this.el.querySelector('.nw-symbol').textContent = this.symbol || '';
    const body = this.el.querySelector('.nw-body');
    body.innerHTML = '';
    if (this.note || !this.items.length) {
      const n = document.createElement('div');
      n.className = 'nw-note';
      n.textContent = this.note || (this.symbol ? 'Loading…' : 'Pick a ticker');
      body.appendChild(n);
      return;
    }
    const now = Date.now();
    for (const it of this.items) {
      const a = document.createElement('a');
      a.className = 'nw-item';
      a.href = it.url || '#';
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
      if (it.summary) a.title = it.summary;
      const h = document.createElement('span');
      h.className = 'nw-headline';
      h.textContent = it.headline;
      const m = document.createElement('span');
      m.className = 'nw-meta';
      m.textContent = `${it.source || ''}${it.source ? ' · ' : ''}${timeAgo(it.datetime, now)}`;
      a.append(h, m);
      body.appendChild(a);
    }
  }
}

// The right-hand column holding the watchlist (top) and news (bottom). Sizes the two by the
// stored split when both are open; either alone fills the column; neither hides it.
export class SideColumn {
  constructor({ el, topEl, bottomEl, storage = safeStorage() }) {
    this.el = el;
    this.topEl = topEl;
    this.bottomEl = bottomEl;
    this.storage = storage;
    let f = 0.5;
    try { f = parseFloat(this.storage?.getItem(SPLIT_KEY) ?? '0.5'); } catch { /* ignore */ }
    this.split = clampSplit(f);
    this.divider = document.createElement('div');
    this.divider.className = 'side-split';
    this.divider.title = 'Drag to resize';
    topEl.after(this.divider);
    this.divider.addEventListener('pointerdown', e => this._drag(e));
  }

  layout() {
    const top = !this.topEl.classList.contains('hidden');
    const bottom = !this.bottomEl.classList.contains('hidden');
    this.el.classList.toggle('hidden', !top && !bottom);
    this.divider.classList.toggle('hidden', !(top && bottom));
    this.topEl.style.flex = top && bottom ? `${this.split} 1 0%` : '';
    this.bottomEl.style.flex = top && bottom ? `${1 - this.split} 1 0%` : '';
  }

  _drag(e) {
    e.preventDefault();
    const rect = this.el.getBoundingClientRect();
    if (!rect.height) return;
    this.divider.setPointerCapture?.(e.pointerId);
    this.divider.classList.add('dragging');
    const move = ev => { this.split = clampSplit((ev.clientY - rect.top) / rect.height); this.layout(); };
    const up = () => {
      this.divider.removeEventListener('pointermove', move);
      this.divider.removeEventListener('pointerup', up);
      this.divider.removeEventListener('pointercancel', up);
      this.divider.classList.remove('dragging');
      try { this.storage?.setItem(SPLIT_KEY, String(this.split)); } catch { /* ignore */ }
    };
    this.divider.addEventListener('pointermove', move);
    this.divider.addEventListener('pointerup', up);
    this.divider.addEventListener('pointercancel', up);
  }
}
