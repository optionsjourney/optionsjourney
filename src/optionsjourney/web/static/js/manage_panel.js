// The journal's Manage panel (#174; Tracker2's manage-bot drawer): a row's gear fetches
// journal.panel's HTML into a panel that slides in from the right. Closed by ✕, Esc or a
// click on the backdrop. A form in the panel posts as usual and comes back to the journal
// with ?panel=<key>, which opens the panel again here, with the page's messages in it.

// The page's query without its panel=…, as "?a=1" (or "").
export function withoutPanel(search) {
  const params = new URLSearchParams(search);
  params.delete('panel');
  const rest = params.toString();
  return rest ? '?' + rest : '';
}

// The panel's URL for a trade (or the alert panel's, which has its trades in its query),
// told which journal view to come back to.
export function panelUrl(base, pathname, search) {
  return base + (base.includes('?') ? '&' : '?') + 'back=' + encodeURIComponent(pathname + withoutPanel(search));
}

export function install(doc, win) {
  const drawer = doc.getElementById('manage-drawer');
  if (!drawer) return;
  const backdrop = drawer.querySelector('[data-manage-backdrop]');
  const box = drawer.querySelector('[data-manage-panel-box]');
  const body = drawer.querySelector('[data-manage-body]');
  const title = drawer.querySelector('#manage-drawer-title');
  let opener = null;

  function show() {
    drawer.classList.remove('hidden');
    win.requestAnimationFrame(() => {
      backdrop.classList.remove('opacity-0');
      box.classList.remove('translate-x-full');
    });
  }

  function close() {
    if (drawer.classList.contains('hidden')) return;
    backdrop.classList.add('opacity-0');
    box.classList.add('translate-x-full');
    win.setTimeout(() => { drawer.classList.add('hidden'); body.innerHTML = ''; }, 300);
    const loc = win.location;
    if (new URLSearchParams(loc.search).has('panel')) {
      win.history.replaceState(null, '', loc.pathname + withoutPanel(loc.search) + loc.hash);
    }
    if (opener) opener.focus();
  }

  async function open(base, notices = []) {
    title.textContent = 'Manage this trade';
    body.textContent = 'Loading…';
    show();
    try {
      const loc = win.location;
      const response = await win.fetch(panelUrl(base, loc.pathname, loc.search), { headers: { Accept: 'text/html' } });
      if (!response.ok) throw new Error(String(response.status));
      body.innerHTML = await response.text();
    } catch (e) {
      body.textContent = "This trade's panel couldn't be loaded. Reload the page and try again.";
      return;
    }
    const heading = body.querySelector('[data-panel-title]');
    if (heading) { title.textContent = heading.textContent; heading.remove(); }
    notices.forEach((n) => body.prepend(n));  // the outcome of the form just sent
    try {
      const { install: fields } = await import(drawer.dataset.filterFieldsSrc);
      fields(body);
    } catch (e) { /* every box shows without it */ }
    try {
      const { install: alerts } = await import(drawer.dataset.alertRowsSrc);
      alerts(body);
    } catch (e) { /* one blank alert row without it */ }
    const first = body.querySelector('input:not([type=hidden]), select, button, a');
    if (first) first.focus({ preventScroll: true });
  }

  doc.addEventListener('click', (e) => {
    const gear = e.target.closest('[data-manage-panel]');
    if (!gear) return;
    e.preventDefault();
    opener = gear;
    open(gear.dataset.managePanel);
  });
  drawer.querySelector('[data-manage-close]').addEventListener('click', close);
  backdrop.addEventListener('click', close);
  doc.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && !drawer.classList.contains('hidden')) close();
  });

  const key = new URLSearchParams(win.location.search).get('panel');
  if (key) {
    const gear = [...doc.querySelectorAll('[data-manage-panel]')].find((g) => g.dataset.panelKey === key);
    const base = gear ? gear.dataset.managePanel : '/journal/trades/' + encodeURI(key) + '/panel';
    opener = gear || null;
    const notices = [...doc.querySelectorAll('main > p.notice')].map((n) => n.cloneNode(true));
    open(base, notices);
  }
}

if (typeof document !== 'undefined' && typeof window !== 'undefined') {
  install(document, window);
}
