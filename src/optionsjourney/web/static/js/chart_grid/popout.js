// One Multi-Chart Grid pane in its own window (/chart/grid/popout?layout=&pane=&tab=). It reads
// the pane from the DB layout, with the tickers of the grid tab it was popped out of laid over
// it, saves its own changes to that layout and tab, and follows the grid's changes over
// BroadcastChannel (grid_sync.js); changes from another tab on the same layout leave its ticker
// alone. It has its own Socket.IO connection, so closing the window drops its subscriptions
// (handle_disconnect); a hidden window tears its pane down (visibility.js).
import { ChartPane } from './pane.js';
import { CandleRouter } from '../charts/candle_router.js';
import { LayoutStore } from './layout_store.js';
import { TabStore, withTabSymbols, symbolsOf } from './tab_store.js';
import { GridSync } from './grid_sync.js';
import { linkedSymbolFor, applyLinkedSymbol } from './link.js';
import { suspendWhenHidden } from './visibility.js';

export async function installGridPopout(root, { layoutId, paneId, tabId = null, socket, visibilityDoc, hiddenGraceMs }) {
  const say = (text) => { root.innerHTML = ''; const p = document.createElement('p');
    p.className = 'p-4 text-sm text-gray-400'; p.textContent = text; root.appendChild(p); };
  const store = new LayoutStore();
  let layout;
  try { layout = await store.open(layoutId); } catch (e) { layout = null; }
  const tabs = new TabStore();
  if (tabId) await tabs.reload();
  const tab = () => (tabId ? tabs.get(tabId) : null);
  // This window works on the layout as its tab shows it.
  if (layout && tab()) store.applyRemote(layoutId, withTabSymbols(layout.payload, tab().symbols));
  const paneState = () => store.get(layoutId)?.payload.panes.find(p => p.id === paneId) || null;
  if (!paneState()) { say('This chart is no longer in its layout.'); return; }

  const router = new CandleRouter(socket);
  let pane = null;
  const setTitle = () => { document.title = `${pane.state.symbol} ${pane.state.timeframe} · ${layout.name}`; };
  const saveTab = () => { if (tab()) tabs.setSymbols(tabId, symbolsOf(store.get(layoutId).payload)); };
  const sync = new GridSync({
    onLayout: (id, payload, fromTab) => {
      if (id !== layoutId) return;
      const ours = !tab() || !fromTab || fromTab === tabId;
      store.applyRemote(id, ours ? payload : withTabSymbols(payload, tab().symbols));
      if (ours && tab()) tabs.applyRemote(tabId, symbolsOf(store.get(id).payload));
      if (!pane) return;
      const next = paneState();
      if (!next) { pane.destroy(); pane = null; say('This chart is no longer in its layout.'); return; }
      pane.applyState(next);
      setTitle();
    },
  });

  const makePane = () => {
    root.innerHTML = '';
    const paneRoot = document.createElement('section');
    paneRoot.className = 'pane pane-popout-window';
    root.appendChild(paneRoot);
    pane = new ChartPane({
      root: paneRoot, state: { ...paneState() }, router,
      onSymbolChange: (src) => {
        const payload = store.get(layoutId).payload;
        store.updatePayload(layoutId,
          { ...payload, panes: applyLinkedSymbol(payload.panes, src.state.id, src.state.symbol) });
      },
      onLinkToggle: (src) => {
        if (!src.state.linked) return;
        const sym = linkedSymbolFor(store.get(layoutId).payload.panes, src.state.id);
        if (sym) src.setSymbol(sym);
      },
      onStateChange: () => {
        store.updatePane(layoutId, { ...pane.state });
        saveTab();
        sync.publish(layoutId, store.get(layoutId).payload, tabId);
        setTitle();
      },
    });
    setTitle();
  };
  makePane();

  suspendWhenHidden({ doc: visibilityDoc, graceMs: hiddenGraceMs,
    onSuspend: () => { if (pane) { pane.destroy(); pane = null; say('Paused while this window is hidden.'); } },
    onResume: () => { if (!pane && paneState()) makePane(); },
  });
  window.addEventListener('pagehide', () => {
    store.flush({ keepalive: true });
    tabs.flush({ keepalive: true });
    sync.close();
  });
}
