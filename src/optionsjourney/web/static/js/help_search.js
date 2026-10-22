// Help search as you type: a moment after typing stops, the results replace the page,
// without pressing Enter. It asks for the same search page Enter opens and takes
// its results part, so there's one search and one way of showing it. Clearing the box
// puts back the page you were reading.

const WAIT_MS = 250;  // after the last key, so a word isn't searched letter by letter

export function searchUrl(action, query) {
  return `${action}?q=${encodeURIComponent(query)}`;
}

// What a change in the box should do: 'search', 'restore' the page you were reading,
// or 'wait' for more (one letter, or the same words as now).
export function nextStep(query, shown, readingAPage) {
  const q = query.trim();
  if (q === shown) return 'wait';
  if (q === '') return readingAPage ? 'restore' : 'search';
  if (q.length < 2) return 'wait';
  return 'search';
}

export function install(doc, win) {
  const form = doc.querySelector('form.help__search');
  const box = form?.querySelector('input[name="q"]');
  const body = () => doc.querySelector('.help__body');
  if (!box || !body()) return;
  const page = body();
  const pageUrl = win.location.pathname + win.location.search + win.location.hash;
  const pageTitle = doc.title;
  const readingAPage = page.tagName === 'ARTICLE';
  const action = form.getAttribute('action');
  let shown = readingAPage ? '' : box.value.trim();
  let timer = null;
  let latest = 0;

  const show = (node, url, title) => {
    body().replaceWith(node);
    doc.title = title;
    win.history.replaceState(null, '', url);
  };

  const run = async () => {
    const step = nextStep(box.value, shown, readingAPage);
    if (step === 'wait') return;
    const q = box.value.trim();
    shown = q;
    const mine = ++latest;  // also turns away a search still on its way back
    if (step === 'restore') {
      show(page, pageUrl, pageTitle);
      return;
    }
    const url = searchUrl(action, q);
    try {
      const reply = await win.fetch(url, { headers: { Accept: 'text/html' } });
      if (!reply.ok) return;
      const html = await reply.text();
      if (mine !== latest) return;  // a newer search has started: this one is stale
      const found = new win.DOMParser().parseFromString(html, 'text/html');
      const results = found.querySelector('.help__body');
      if (results) show(doc.importNode(results, true), url, found.title);
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
