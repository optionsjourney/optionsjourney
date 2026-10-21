// The journal's risk-graph drawer (templates/_risk_drawer.html), from Tracker2's analyzer drawer.
// The risk graph runs in an iframe of /risk-graph?trade=…&embed=1 rather than on the page
// itself, so its element ids and window handlers never collide with the journal's. Links keep their href, so a modified click (or a page without the
// drawer, e.g. the risk-graph pop-out) still opens a new tab.
//
// Every open loads fresh, and close blanks the frame once the slide-out finishes, so a hidden
// risk graph isn't left polling the chain every 30s or holding a live-quote subscription.

const SLIDE_MS = 300;

export function embedUrl(href, base) {
  const u = new URL(href, base);
  u.searchParams.set('embed', '1');
  return u.pathname + u.search;
}

export function fullUrl(href, base) {
  const u = new URL(href, base);
  u.searchParams.delete('embed');
  return u.pathname + u.search;
}

// Only a plain left click opens the drawer; cmd/ctrl/shift/alt-click and other buttons keep the
// browser's own behaviour (new tab / window).
export function shouldIntercept(e) {
  return !(e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || (e.button && e.button !== 0));
}

// One window per trade set, reused by name: popping the same trades out again focuses their window.
export function popoutName(url) {
  const q = new URL(url, 'http://x').searchParams;
  const key = q.get('positions') || q.get('ticker') || 'popout';
  return `risk-${key.replace(/[^A-Za-z0-9]+/g, '-')}`;
}

// risk_graph.js _popoutUrl when the page has it (Picture-in-Picture in Arc), else a popup window.
function popoutWindow(win, url) {
  const name = popoutName(url);
  if (typeof win._popoutUrl === 'function') { win._popoutUrl(url, name, 'Risk graph'); return; }
  const w = win.open(url, name, 'popup,width=1400,height=950');
  if (w) w.focus();
}

export function installRiskDrawer(doc, win) {
  const root = doc.getElementById('risk-drawer');
  if (!root) return null;
  const panel = root.querySelector('[data-risk-panel]');
  const backdrop = root.querySelector('[data-risk-backdrop]');
  const frame = root.querySelector('[data-risk-frame]');
  const popoutBtn = root.querySelector('[data-risk-popout]');
  let closeTimer = null;
  let popout = null;

  const isOpen = () => !root.classList.contains('hidden') && !panel.classList.contains('translate-x-full');

  // The header's Pop out closes the drawer and opens the same risk graph in its own window, nav-less
  // (embed=1) like every pop-out, so it never turns into a general app window. opts.popout
  // overrides it (a fixed window name).
  function open(href, opts = {}) {
    if (closeTimer) win.clearTimeout(closeTimer);
    closeTimer = null;
    const url = embedUrl(href, win.location.href);
    popout = opts.popout || (() => popoutWindow(win, url));
    frame.src = url;
    root.classList.remove('hidden');
    win.requestAnimationFrame(() => {
      panel.classList.remove('translate-x-full');
      backdrop.classList.remove('opacity-0');
    });
  }

  function close() {
    if (!isOpen()) return;
    panel.classList.add('translate-x-full');
    backdrop.classList.add('opacity-0');
    closeTimer = win.setTimeout(() => {
      closeTimer = null;
      root.classList.add('hidden');
      frame.src = 'about:blank';
    }, SLIDE_MS);
  }

  root.querySelector('[data-risk-close]').addEventListener('click', close);
  if (popoutBtn) {
    popoutBtn.addEventListener('click', () => {
      const go = popout;
      close();
      if (go) go();
    });
  }
  backdrop.addEventListener('click', close);
  // Capture phase: the journal's own Escape handler would otherwise also close the risk-graph or
  // run drawer underneath. (Escape pressed inside the iframe never reaches this page.)
  doc.addEventListener('keydown', e => {
    if (e.key !== 'Escape' || !isOpen()) return;
    e.stopImmediatePropagation();
    e.preventDefault();
    close();
  }, true);

  // Called from the links' inline onclick (journal.html, journal_grid.js).
  win.riskLinkClick = (e, href) => {
    if (!shouldIntercept(e)) return true;
    e.preventDefault();
    open(href);
    return false;
  };
  const api = { open, close };
  win.riskDrawer = api;
  return api;
}
