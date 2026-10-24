// The top bar's bell (#272): a click opens a list under it, instead of going to the
// Alerts page. First what needs fixing or a look (problems, then notes), then the newest
// alerts with the new ones marked, and a link to the whole page. Opening the list marks
// those alerts read, so the badge drops to the problems that are still there.
// Cmd/ctrl/middle-click still opens the page. Server: GET /api/alerts/recent.

export function timeAgo(iso, now = Date.now()) {
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return '';
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 60) return 'just now';
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

// What the list shows, from the API's reply: plain data, so it can be tested without a page.
export function bellView(data, now = Date.now()) {
  const standing = (data && data.standing) || [];
  const recent = (data && data.recent) || [];
  return {
    standing: standing.map(a => ({
      tone: a.level === 'problem' ? 'problem' : 'note',
      title: a.title, detail: a.detail, link: a.link,
    })),
    recent: recent.map(a => ({
      title: a.title, detail: a.detail, link: a.link || '/alerts',
      when: timeAgo(a.created_at, now), unread: !!a.unread,
    })),
    empty: !standing.length && !recent.length,
  };
}

function el(doc, tag, className, text) {
  const node = doc.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

function row(doc, item, extraClass) {
  const link = el(doc, 'a', `bell__row ${extraClass}`);
  link.href = item.link;
  const head = el(doc, 'div', 'bell__head');
  head.append(el(doc, 'span', 'bell__title', item.title));
  if (item.when) head.append(el(doc, 'span', 'bell__when', item.when));
  link.append(head);
  if (item.detail) link.append(el(doc, 'div', 'bell__detail', item.detail));
  return link;
}

function render(doc, list, view) {
  list.replaceChildren();
  if (view.empty) {
    list.append(el(doc, 'p', 'bell__empty', 'Nothing yet. Alerts show up here when they go off.'));
    return;
  }
  for (const item of view.standing) list.append(row(doc, item, `bell__row--${item.tone}`));
  for (const item of view.recent) {
    const r = row(doc, item, item.unread ? 'bell__row--new' : '');
    if (item.unread) r.dataset.unread = '';
    list.append(r);
  }
}

export function install(doc, win) {
  const bell = doc.querySelector('[data-alert-bell]');
  if (!bell || typeof win.fetch !== 'function') return null;
  let panel = null;

  function build() {
    panel = el(doc, 'div', 'bell');
    panel.id = 'alert-bell-panel';
    panel.setAttribute('role', 'dialog');
    panel.setAttribute('aria-label', 'Recent alerts');
    panel.hidden = true;
    panel.append(el(doc, 'div', 'bell__heading', 'Recent alerts'));
    const list = el(doc, 'div', 'bell__list');
    list.dataset.list = '';
    const all = el(doc, 'a', 'bell__all', 'All alerts →');
    all.href = bell.getAttribute('href');
    panel.append(list, all);
    doc.body.append(panel);
  }

  async function load() {
    const list = panel.querySelector('[data-list]');
    if (!list.childElementCount) list.append(el(doc, 'p', 'bell__empty', 'Loading…'));
    try {
      const res = await win.fetch('/api/alerts/recent', { headers: { Accept: 'application/json' } });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      render(doc, list, bellView(await res.json()));
    } catch (e) {
      list.replaceChildren(el(doc, 'p', 'bell__empty', "Couldn't load your alerts. Open the Alerts page instead."));
    }
  }

  const isOpen = () => !!panel && !panel.hidden;
  function close() {
    if (!isOpen()) return;
    panel.hidden = true;
    bell.setAttribute('aria-expanded', 'false');
  }
  function open() {
    if (!panel) build();
    const r = bell.getBoundingClientRect();
    panel.style.top = `${Math.round(r.bottom + 6)}px`;
    panel.style.right = `${Math.max(8, Math.round(win.innerWidth - r.right))}px`;
    panel.hidden = false;
    bell.setAttribute('aria-expanded', 'true');
    return load();
  }

  bell.setAttribute('aria-haspopup', 'dialog');
  bell.setAttribute('aria-expanded', 'false');
  bell.addEventListener('click', e => {
    if (e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;  // a new tab
    e.preventDefault();
    if (isOpen()) close(); else open();
  });
  doc.addEventListener('pointerdown', e => {
    if (isOpen() && !panel.contains(e.target) && !bell.contains(e.target)) close();
  });
  doc.addEventListener('keydown', e => { if (e.key === 'Escape') close(); });
  win.addEventListener('resize', close);
  return { open, close, isOpen };
}

if (typeof document !== 'undefined' && typeof window !== 'undefined') install(document, window);
