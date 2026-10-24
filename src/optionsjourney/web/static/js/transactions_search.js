// Transactions search as you type (#287): a moment after typing stops, the results
// replace the table, without pressing Enter. It asks for the same page Enter opens,
// with the other filters kept and back to page 1, and takes its results part.

const WAIT_MS = 250;  // after the last key, so a word isn't searched letter by letter

// The page's address for these form values: no empty search, and page 1.
export function searchUrl(path, params) {
  const kept = new URLSearchParams();
  for (const [name, value] of params) {
    if (name === 'page' || (name === 'q' && !value.trim())) continue;
    kept.append(name, name === 'q' ? value.trim() : value);
  }
  const query = kept.toString();
  return query ? `${path}?${query}` : path;
}

export function install(doc, win) {
  const form = doc.querySelector('form[data-tx-search]');
  const box = form?.querySelector('input[name="q"]');
  if (!box || !doc.getElementById('tx-results')) return;
  let shown = box.value.trim();
  let timer = null;
  let latest = 0;

  const run = async () => {
    const q = box.value.trim();
    if (q === shown) return;
    shown = q;
    const mine = ++latest;  // also turns away a search still on its way back
    const url = searchUrl(win.location.pathname, new win.FormData(form));
    try {
      const reply = await win.fetch(url, { headers: { Accept: 'text/html' } });
      if (!reply.ok) return;
      const html = await reply.text();
      if (mine !== latest) return;  // a newer search has started: this one is stale
      const found = new win.DOMParser().parseFromString(html, 'text/html');
      const results = found.getElementById('tx-results');
      if (!results) return;
      doc.getElementById('tx-results').replaceWith(doc.importNode(results, true));
      win.history.replaceState(null, '', url);
    } catch {
      // Not reachable just now: Enter still searches the usual way
    }
  };

  box.addEventListener('input', () => {
    win.clearTimeout(timer);
    timer = win.setTimeout(run, WAIT_MS);
  });
}

if (typeof document !== 'undefined') install(document, window);
