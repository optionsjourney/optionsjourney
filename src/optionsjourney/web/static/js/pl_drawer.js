// The P/L calendar's drawer (#301; Tracker2's day, week and month drawers): a click (or Enter)
// on any [data-period-start] cell loads GET /metrics/calendar/period for that range, with the
// page's filters, as HTML. Opens and closes like schedule_drawer.js.

export function installPlDrawer(doc, win) {
  const drawer = doc.getElementById('pl-drawer');
  if (!drawer) return;
  const backdrop = drawer.querySelector('[data-pl-backdrop]');
  const panel = drawer.querySelector('[data-pl-panel]');
  const body = doc.getElementById('pl-drawer-body');
  const title = doc.getElementById('pl-drawer-title');
  let token = 0;  // a slow answer for an earlier cell must not overwrite a later one
  let opener = null;
  const isOpen = () => !drawer.classList.contains('hidden');

  async function load(start, end) {
    const mine = ++token;
    body.innerHTML = '<p class="muted small">Loading…</p>';
    const url = new URL(drawer.dataset.url, win.location.href);
    url.searchParams.set('first', start);
    url.searchParams.set('last', end);
    try {
      const res = await win.fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const html = await res.text();
      if (mine === token) body.innerHTML = html;
    } catch (e) {
      if (mine === token) body.innerHTML = `<p class="notice notice--warning small">Couldn't load the trades (${e.message}).</p>`;
    }
  }

  function open(cell) {
    opener = cell;
    title.textContent = cell.dataset.periodTitle;
    drawer.classList.remove('hidden');
    win.requestAnimationFrame(() => {
      backdrop.classList.remove('opacity-0');
      panel.classList.remove('translate-x-full');
    });
    load(cell.dataset.periodStart, cell.dataset.periodEnd);
    drawer.querySelector('[data-pl-close]').focus({ preventScroll: true });
  }

  function close() {
    if (!isOpen()) return;
    backdrop.classList.add('opacity-0');
    panel.classList.add('translate-x-full');
    win.setTimeout(() => drawer.classList.add('hidden'), 300);
    if (opener) opener.focus();
  }

  doc.querySelectorAll('[data-period-start]').forEach(cell => {
    cell.addEventListener('click', () => open(cell));
    cell.addEventListener('keydown', e => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); open(cell); }
    });
  });
  drawer.querySelector('[data-pl-close]').addEventListener('click', close);
  backdrop.addEventListener('click', close);
  doc.addEventListener('keydown', e => {
    if (e.key !== 'Escape' || !isOpen()) return;
    close();
    e.stopImmediatePropagation();
  }, true);
}
