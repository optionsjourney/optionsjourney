// Keeps the windows of one browser that show grid layouts in step without polling: the grid,
// a whole-grid pop-out (?bare=1) and single-chart pop-outs. After a window saves a layout it
// posts the new payload on BroadcastChannel 'chart_grid'; every other window showing that
// layout applies it. Which layout a window shows is its own: switching layouts is not synced.
// A message names the tab it came from (tab_store.js): a window on another tab of the same
// layout keeps its own tickers. A 'tabs' message says the tab list changed (opened, closed,
// pointed at another layout).
export const CHANNEL = 'chart_grid';

export class GridSync {
  constructor({ onLayout, onTabs = () => {}, Channel = globalThis.BroadcastChannel }) {
    this._ch = typeof Channel === 'function' ? new Channel(CHANNEL) : null;
    if (this._ch) {
      this._ch.onmessage = (e) => {
        const m = e && e.data;
        if (m && m.type === 'layout' && typeof m.layoutId === 'string' && m.payload) {
          onLayout(m.layoutId, m.payload, typeof m.tabId === 'string' ? m.tabId : null);
        } else if (m && m.type === 'tabs') {
          onTabs();
        }
      };
    }
  }

  publish(layoutId, payload, tabId = null) {
    if (!this._ch) return;
    try { this._ch.postMessage({ type: 'layout', layoutId, tabId, payload: JSON.parse(JSON.stringify(payload)) }); }
    catch (e) { console.warn('grid sync post failed', e); }
  }

  publishTabs() {
    try { this._ch?.postMessage({ type: 'tabs' }); } catch (e) { console.warn('grid sync post failed', e); }
  }

  close() { this._ch?.close(); this._ch = null; }
}

const paneSig = p => JSON.stringify([p.symbol, p.timeframe, p.indicators || {}, !!p.linked, p.fitCandles !== false]);

// What a window showing `oldPayload` must do to show `newPayload`: rebuild everything (shape
// or pane set changed) or reload just the listed panes.
export function diffPanes(oldPayload, newPayload) {
  const a = oldPayload?.panes || [], b = newPayload?.panes || [];
  if (oldPayload?.shape !== newPayload?.shape || a.length !== b.length
      || a.some((p, i) => p.id !== b[i].id)) {
    return { reload: true, changed: [] };
  }
  return { reload: false, changed: b.filter((p, i) => paneSig(p) !== paneSig(a[i])).map(p => p.id) };
}

export function popoutUrl(layoutId, paneId, tabId = null) {
  return `/chart/grid/popout?layout=${encodeURIComponent(layoutId)}&pane=${encodeURIComponent(paneId)}`
    + (tabId ? `&tab=${encodeURIComponent(tabId)}` : '');
}
