// The Pending drawer (#292): every order still waiting at the broker, in every account. Opened
// by any [data-pending-open] button, built like the schedule drawer (schedule_drawer.js).
//
// Reads GET /api/pending (the list, every 30 s while open) and /api/pending/markets (what each
// order would fill at, every 2 s). Change price and Cancel post to /api/trade/replace and
// /cancel, the same safe path the Trade page uses; afterwards the row reads "updating…" and
// the list reloads 2 s later. The toolbar button's count comes from /api/pending?count=1 on
// load and every 5 min while the drawer is closed. Polling stops while the tab is hidden.

import { replacePrice } from '../trade/ticket_core.js';
import { FILTERS, buttonLabel, filterOrders, chipCounts, rowView, asOfText } from './model.js';

const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

export const FILTER_KEY = 'pending.filter';
export const CANCEL_TEXT = 'This cancels the order at your broker.';
const FILTER_NAME = { all: 'All', '0dte': '0DTE', calendars: 'Calendars', options: 'Options' };
const COLUMNS = ['From', 'Symbol', 'Status', 'Market', 'Price', 'Time', 'Description', 'Actions'];
const BROKERS = { SCHWAB: 'Schwab', TASTYTRADE: 'TastyTrade' };
const LIST_MS = 30000, MARKETS_MS = 2000, COUNT_MS = 300000, SETTLE_MS = 2000;

export function installPendingDrawer(doc, win) {
  const drawer = doc.getElementById('pending-drawer');
  if (!drawer) return;
  const backdrop = drawer.querySelector('[data-pending-backdrop]');
  const panel = drawer.querySelector('[data-pending-panel]');
  const body = doc.getElementById('pending-body');
  const chipsEl = doc.getElementById('pending-chips');
  const outsideEl = doc.getElementById('pending-outside');
  const statusEl = doc.getElementById('pending-status');   // the header's "as of 10:42" / Loading… / error
  const openers = [...doc.querySelectorAll('[data-pending-open]')];

  let accounts = [];
  let loaded = false;
  let error = null;      // the whole list failed to load
  let asOf = '';         // the last listing's as_of
  let marks = {};
  let filter = 'all';
  let outsideOnly = false;
  let edit = null;       // {id, kind: 'change'|'cancel', value}: the one row with an inline control open
  let updating = new Set();
  let notes = {};        // order id -> the last action's message, shown in its row
  let token = 0;
  let timers = [];
  let opener = null;
  let hideTimer = null;
  try { filter = win.localStorage.getItem(FILTER_KEY) || 'all'; } catch { /* default */ }
  if (!FILTERS.includes(filter)) filter = 'all';

  const isOpen = () => !drawer.classList.contains('hidden');
  const visible = () => doc.visibilityState !== 'hidden';
  const getJson = async url => {
    const res = await win.fetch(url, { headers: { Accept: 'application/json' } });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || data.message || `HTTP ${res.status}`);
    return data;
  };

  function setButton(count, errors) {
    const label = buttonLabel(count, errors);
    for (const b of openers) {
      b.hidden = !label;
      if (label) b.textContent = label;
      if (b.classList) b.classList.toggle('toolbar-pill--warn', !!errors);   // the red tint when an account failed
    }
  }

  async function refreshCount() {
    if (isOpen() || !visible()) return;
    try {
      const d = await getJson(`${drawer.dataset.pendingUrl}?count=1`);
      setButton(d.count, d.errors);
    } catch { /* keep the last label */ }
  }

  async function load() {
    const mine = ++token;
    try {
      const d = await getJson(drawer.dataset.pendingUrl);
      if (mine !== token) return;
      accounts = d.accounts;
      asOf = d.as_of || '';
      error = null;
      setButton(d.count, d.errors);
      updating = new Set();
    } catch (e) {
      if (mine !== token) return;
      error = e.message;
    }
    loaded = true;
    draw();
    refreshMarks();
  }

  async function refreshMarks() {
    if (!isOpen() || !visible()) return;
    const ids = accounts.flatMap(a => a.orders.map(o => o.order_id));
    if (!ids.length) return;
    try {
      const d = await getJson(`${drawer.dataset.marketsUrl}?ids=${ids.map(encodeURIComponent).join(',')}`);
      marks = d.marks || {};
      for (const cell of body.querySelectorAll('[data-market]')) {
        const row = rowByCell(cell.dataset.market);
        if (row) cell.innerHTML = moneyHtml(rowView(row, marks[row.order_id]).marketParts, 'mid');
      }
    } catch { /* the market column keeps its last values */ }
  }

  const rowByCell = id => accounts.flatMap(a => a.orders).find(o => o.order_id === id);

  const mini = (cls, attrs, text) => `<button type="button" class="mini-btn mini-btn--${cls}" ${attrs}>${text}</button>`;

  function actionsHtml(v, accountId) {
    const attrs = act => `data-act="${act}" data-id="${esc(v.id)}" data-account="${esc(accountId)}"`;
    if (updating.has(v.id)) return '<span class="pending-reason">updating…</span>';
    const note = notes[v.id] ? `<div class="pending-reason">${esc(notes[v.id])}</div>` : '';
    if (edit && edit.id === v.id && edit.kind === 'cancel') {
      return `<div class="pending-reason pending-reason--strong">${esc(CANCEL_TEXT)}</div><div class="pending-row__buttons">
        ${mini('gray', attrs('cancel-keep'), 'Keep')}${mini('red', attrs('cancel-confirm'), 'Confirm')}</div>`;
    }
    if (edit && edit.id === v.id && edit.kind === 'change') {
      return `${note}<div class="pending-row__buttons"><input type="number" min="0" step="0.05" value="${esc(edit.value)}"
          data-act="price" data-id="${esc(v.id)}" aria-label="New limit price" class="pending-row__price">
        <span class="pending-side pending-side--${v.effect === 'debit' ? 'db' : 'cr'}">${v.effect === 'debit' ? 'db' : 'cr'}</span>
        ${mini('blue', attrs('change-confirm'), 'Confirm')}${mini('gray', attrs('change-keep'), 'Keep')}</div>`;
    }
    const buttons = [
      v.actions.change ? mini('blue', attrs('change'), 'Change price') : '',
      v.actions.cancel ? mini('red', attrs('cancel'), 'Cancel') : '',
    ].join('');
    const reason = !buttons && v.actions.reason ? `<span class="pending-reason">${esc(v.actions.reason)}</span>` : '';
    return `${buttons ? `<div class="pending-row__buttons">${buttons}</div>` : ''}${reason}${note}`;
  }

  // "mid 1.25 cr" with the side coloured; an em dash when there is no price.
  function moneyHtml(parts, label = '') {
    if (!parts) return '<span class="muted">—</span>';
    return `${label ? `<span class="pending-tiny">${label}</span> ` : ''}<span class="pending-num">${esc(parts.value)}</span> `
      + `<span class="pending-side pending-side--${parts.side}">${parts.side}</span>`;
  }

  function legsHtml(v) {
    return v.legs.map(l => `<div class="pending-leg">
        <span class="pending-leg__qty pending-leg__qty--${l.side}">${l.qty > 0 ? '+' : ''}${esc(l.qty)}</span>
        ${l.expiry ? `<span>${esc(l.expiry)}</span> <span class="pending-leg__strike">${esc(l.strike)}</span> <span>${esc(l.type)}</span>`
          : `<span>${esc(l.symbol)}</span>`}
        <span class="pending-badge pending-badge--${l.side}">${esc(l.action)}</span>
        ${l.left != null ? `<span class="pending-tiny">${esc(l.left)} left</span>` : ''}</div>`).join('');
  }

  function fromHtml(v) {
    const who = v.ownerKind === 'outside' ? `<span class="pending-tag">${esc(v.from)}</span>`
      : `<span class="pending-owner">${esc(v.from)}</span>`;
    const link = v.link && /^\/(?!\/)/.test(v.link) ? ` <a class="pending-link" href="${esc(v.link)}" title="Open">↗</a>` : '';
    return who + link;
  }

  function rowHtml(order, accountId) {
    const v = rowView(order, marks[order.order_id]);
    const cells = [
      fromHtml(v),
      `<span class="pending-symbol">${esc(v.symbol)}</span>`,
      `<span class="pending-status"><span class="pending-dot" aria-hidden="true"></span>${esc(v.statusText)}</span>`,
      `<span data-market="${esc(v.id)}">${moneyHtml(v.marketParts, 'mid')}</span>`,
      moneyHtml(v.priceParts),
      `<span class="pending-num">${esc(v.time)}</span>`,
      legsHtml(v),
      actionsHtml(v, accountId),
    ];
    const cls = ['from', 'symbol', 'status', 'market', 'price', 'time', 'description', 'actions'];
    return `<tr${updating.has(v.id) ? ' class="is-busy"' : ''}>${cells.map((c, i) =>
      `<td class="pending-col--${cls[i]}" data-label="${COLUMNS[i]}">${c}</td>`).join('')}</tr>`;
  }

  function section(a) {
    const rows = a.orders.map(o => rowHtml(o, a.account_id)).join('');
    return `<section class="pending-account">
      <h4 class="pending-account__head">${esc(a.label)} · ${esc(BROKERS[a.broker] || a.broker)}</h4>
      ${a.error ? `<p class="pending-error">${esc(a.error)}</p>` : ''}
      ${rows ? `<table class="pending-table"><thead><tr>${COLUMNS.map(c => `<th${c === 'Actions' ? ' class="pending-col--actions"' : ''}>${c}</th>`).join('')}</tr></thead>
        <tbody>${rows}</tbody></table>` : ''}
    </section>`;
  }

  function setStatus(text, bad = false) {
    if (!statusEl) return;
    statusEl.textContent = text;
    statusEl.classList.toggle('negative', bad);
  }

  function draw() {
    const counts = chipCounts(accounts, outsideOnly);
    chipsEl.innerHTML = FILTERS.map(f => `<button type="button" class="chip-pill${f === filter ? ' chip-pill--on' : ''}"
      role="tab" aria-selected="${f === filter}" data-filter="${f}">${FILTER_NAME[f]} ${counts[f]}</button>`).join('');
    if (!loaded) { setStatus('Loading…'); body.innerHTML = '<p class="muted small">Loading…</p>'; return; }
    if (error) {
      setStatus("Couldn't load", true);
      body.innerHTML = `<p class="pending-error">Couldn't load pending orders: ${esc(error)}</p>`;
      return;
    }
    setStatus(asOfText(asOf));
    const shown = filterOrders(accounts, filter, outsideOnly);
    body.innerHTML = shown.length ? shown.map(section).join('') : '<p class="muted">Nothing pending.</p>';
  }

  async function act(kind, id, accountId) {
    const order = rowByCell(id);
    if (!order) return;   // it went away in a reload since the control was opened
    let payload;
    if (kind === 'replace') {
      const row = rowView(order, null);
      const price = replacePrice(row, edit && edit.value);
      if (price == null) { notes[id] = 'Set a limit price above 0.'; draw(); return; }
      payload = { account_id: accountId, order_id: id, price };
    } else {
      payload = { account_id: accountId, order_id: id };
    }
    edit = null;
    delete notes[id];
    updating.add(id);
    draw();
    try {
      const res = await win.fetch(`${drawer.dataset[kind === 'replace' ? 'replaceUrl' : 'cancelUrl']}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      });
      const d = await res.json();
      notes[id] = d.message || '';
    } catch (e) {
      notes[id] = `Couldn't ${kind === 'replace' ? 'change' : 'cancel'} the order: ${e.message}`;
    }
    win.setTimeout(async () => {
      try { await load(); } finally { updating.delete(id); draw(); }   // a failed reload must not leave "updating…"
    }, SETTLE_MS);
  }

  body.addEventListener('click', e => {
    const b = e.target.closest('[data-act]');
    if (!b) return;
    const { act: what, id } = b.dataset;
    const accountId = Number(b.dataset.account);
    if (what === 'cancel') edit = { id, kind: 'cancel' };
    else if (what === 'change') edit = { id, kind: 'change', value: Math.abs(rowByCell(id).price ?? 0).toFixed(2) };
    else if (what === 'cancel-keep' || what === 'change-keep') edit = null;
    else if (what === 'cancel-confirm') return act('cancel', id, accountId);
    else if (what === 'change-confirm') return act('replace', id, accountId);
    else return;
    draw();
  });
  body.addEventListener('input', e => {
    if (e.target.dataset && e.target.dataset.act === 'price' && edit) edit.value = e.target.value;
  });
  chipsEl.addEventListener('click', e => {
    const b = e.target.closest('[data-filter]');
    if (!b) return;
    filter = b.dataset.filter;
    try { win.localStorage.setItem(FILTER_KEY, filter); } catch { /* not remembered */ }
    draw();
  });
  outsideEl.addEventListener('change', e => { outsideOnly = !!e.target.checked; draw(); });

  function stopPolling() { timers.forEach(t => win.clearInterval(t)); timers = []; }

  function open(e) {
    opener = e.currentTarget;
    edit = null; notes = {}; loaded = false; marks = {}; updating = new Set();
    win.clearTimeout(hideTimer);
    drawer.classList.remove('hidden');
    win.requestAnimationFrame(() => {
      backdrop.classList.remove('opacity-0');
      panel.classList.remove('translate-x-full');
    });
    draw();
    load();
    stopPolling();
    timers = [
      win.setInterval(() => { if (isOpen() && visible() && !edit && !updating.size) load(); }, LIST_MS),
      win.setInterval(refreshMarks, MARKETS_MS),
    ];
  }

  function close() {
    if (!isOpen()) return;
    stopPolling();
    backdrop.classList.add('opacity-0');
    panel.classList.add('translate-x-full');
    hideTimer = win.setTimeout(() => { drawer.classList.add('hidden'); refreshCount(); }, 300);
    if (opener) opener.focus();
  }

  openers.forEach(btn => btn.addEventListener('click', open));
  drawer.querySelector('[data-pending-close]').addEventListener('click', close);
  drawer.querySelector('[data-pending-refresh]').addEventListener('click', () => load());
  backdrop.addEventListener('click', close);
  doc.addEventListener('visibilitychange', () => {
    if (!visible()) return;
    if (isOpen()) load(); else refreshCount();
  });
  doc.addEventListener('keydown', e => {
    if (e.key !== 'Escape' || !isOpen()) return;
    close();
    e.stopImmediatePropagation();
  }, true);

  refreshCount();
  win.setInterval(refreshCount, COUNT_MS);
}
