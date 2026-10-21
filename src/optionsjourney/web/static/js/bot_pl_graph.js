// The bot page's P/L graph (#208, Tracker2 f52ae8a0): a fold that starts closed, as in
// Tracker2, and opens again on the next visit if it was left open. One choice for every
// bot. line_chart.js draws the graph; it redraws itself when the fold opens.

export const KEY = 'bot-pl-graph-open';

// Whether to open the fold: only when it was left open. Unreadable storage counts as closed.
export function wasOpen(storage) {
  try {
    return storage.getItem(KEY) === '1';
  } catch {
    return false;
  }
}

export function remember(storage, open) {
  try {
    if (open) storage.setItem(KEY, '1');
    else storage.removeItem(KEY);
  } catch { /* no storage: it opens closed next time, as before */ }
}

export function install(doc, win) {
  const fold = doc.getElementById('pl-graph');
  if (!fold) return;
  let storage = null;
  try {
    storage = win.localStorage;
  } catch { /* storage blocked: the fold just starts closed */ }
  if (storage && wasOpen(storage)) fold.open = true;
  fold.addEventListener('toggle', () => {
    if (storage) remember(storage, fold.open);
  });
}

if (typeof document !== 'undefined') install(document, window);
