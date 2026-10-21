// The journal's grid view (#215, Tracker2's grid view): an overview table with a box per
// open trade, and one risk graph of the ticked trades together below it. The header box
// ticks all or none, an account's total ticks just that account, and the totals line
// covers the ticked trades. Clicking the totals line collapses the table to it; the
// choice is remembered. Open P/L comes from /api/open-pl every few seconds.
import { money, plText } from './open_pl.js';

export const MAX_GRAPHED = 50; // the risk graph takes at most this many (routes/risk_graph.MAX_TRADES)
const EVERY = 5000;
const RELOAD_AFTER_MS = 600; // quick clicks settle into one reload of the graph
const COLLAPSED = 'journal_grid_table_collapsed';

const capped = keys => keys.slice(0, MAX_GRAPHED);

// rows: [{key, account, premium, checked}]; pls: {key: open P/L or null}
export function totals(rows, pls) {
  const ticked = rows.filter(r => r.checked);
  const priced = ticked.filter(r => pls[r.key] !== undefined && pls[r.key] !== null);
  const sum = list => list.reduce((s, r) => s + pls[r.key], 0);
  const pl = priced.length ? sum(priced) : null;
  const premium = ticked.reduce((s, r) => s + Math.abs(Number(r.premium) || 0), 0);
  const accounts = [...new Set(rows.map(r => r.account))].map(account => {
    const mine = priced.filter(r => r.account === account);
    return { account, pl: mine.length ? sum(mine) : null };
  });
  return {
    pl, premium, accounts,
    percent: pl !== null && premium ? (pl / premium) * 100 : null,
    selected: ticked.length, count: rows.length, priced: priced.length,
  };
}

// The keys to tick after each kind of click
export const allOrNone = rows => (rows.some(r => r.checked) ? [] : capped(rows.map(r => r.key)));
export const onlyAccount = (rows, account) => capped(rows.filter(r => r.account === account).map(r => r.key));
export const toggled = (rows, key) => capped(rows.filter(r => (r.key === key ? !r.checked : r.checked)).map(r => r.key));

export const graphHref = keys => (keys.length ? '/risk-graph?trade=' + keys.map(encodeURIComponent).join(',') : null);

function readCollapsed(win) {
  try { return win.localStorage.getItem(COLLAPSED) === '1'; } catch (e) { return false; }
}

export function install(doc, win) {
  const box = doc.getElementById('grid-overview');
  if (!box) return;
  const trs = [...box.querySelectorAll('tr[data-grid-key]')];
  const rows = trs.map(tr => ({
    key: tr.dataset.gridKey, account: tr.dataset.account, premium: Number(tr.dataset.premium) || 0,
    tr, input: tr.querySelector('.grid-check'),
  }));
  rows.forEach(r => { r.checked = r.input.checked; });
  const all = doc.getElementById('grid-check-all');
  const toggle = doc.getElementById('grid-table-toggle');
  const table = doc.getElementById('grid-table');
  const frame = doc.getElementById('grid-graph');
  const empty = doc.getElementById('grid-graph-empty');
  const large = doc.getElementById('grid-graph-open');
  const title = doc.getElementById('grid-graph-title');
  let pls = {}, timer = null;

  function render() {
    const t = totals(rows, pls);
    const total = doc.getElementById('grid-total');
    total.textContent = t.pl === null ? '—' : plText(t.pl, t.premium);
    total.classList.toggle('positive', t.pl > 0);
    total.classList.toggle('negative', t.pl < 0);
    const waiting = t.selected && t.priced < t.selected ? ` · ${t.priced} of ${t.selected} priced` : '';
    doc.getElementById('grid-total-note').textContent =
      `on $${Math.round(t.premium).toLocaleString('en-US')} premium · ${t.selected} of ${t.count} ticked${waiting}`;
    const accounts = doc.getElementById('grid-accounts');
    accounts.replaceChildren(...(t.accounts.length > 1 ? t.accounts.map(a => {
      const b = doc.createElement('button');
      b.type = 'button';
      b.className = 'link small';
      b.title = 'Tick only this account';
      b.textContent = a.pl === null ? a.account : `${a.account}: ${money(a.pl)}`;
      b.addEventListener('click', () => pick(onlyAccount(rows, a.account)));
      return b;
    }) : []));
    rows.forEach(r => {
      r.input.checked = r.checked;
      r.tr.classList.toggle('grid-row--off', !r.checked);
      const cell = r.tr.querySelector('[data-grid-pl]');
      const pl = pls[r.key];
      cell.textContent = plText(pl, r.premium);
      cell.classList.toggle('positive', pl > 0);
      cell.classList.toggle('negative', pl < 0);
    });
    all.checked = t.selected > 0 && t.selected === t.count;
    all.indeterminate = t.selected > 0 && t.selected < t.count;
  }

  function reloadGraph(now) {
    win.clearTimeout(timer);
    const go = () => {
      const keys = rows.filter(r => r.checked).map(r => r.key);
      const href = graphHref(keys);
      title.textContent = keys.length > 1 ? `Risk graph · ${keys.length} trades together` : 'Risk graph';
      empty.hidden = !!href;
      frame.hidden = !href;
      large.hidden = !href;
      if (href) large.href = href;
      const src = href ? `${href}&embed=1` : 'about:blank';
      if (frame.getAttribute('src') !== src) frame.src = src;
    };
    if (now) go(); else timer = win.setTimeout(go, RELOAD_AFTER_MS);
  }

  function pick(keys) {
    const wanted = new Set(keys);
    rows.forEach(r => { r.checked = wanted.has(r.key); });
    render();
    reloadGraph(false);
  }

  function setCollapsed(on) {
    table.hidden = on;
    toggle.setAttribute('aria-expanded', String(!on));
    toggle.title = `${on ? 'Show' : 'Hide'} the trades`;
  }

  all.addEventListener('change', () => pick(allOrNone(rows)));
  // A click anywhere on a row ticks it, except on its links
  box.addEventListener('click', e => {
    const tr = e.target.closest('tr[data-grid-key]');
    if (!tr || e.target.closest('a')) return;
    pick(toggled(rows, tr.dataset.gridKey)); // render() sets the box (never preventDefault: it'd undo that)
  });
  toggle.addEventListener('click', () => {
    const on = !table.hidden;
    setCollapsed(on);
    try { win.localStorage.setItem(COLLAPSED, on ? '1' : '0'); } catch (e) { /* storage blocked */ }
  });
  large.addEventListener('click', e => {
    if (win.riskLinkClick) win.riskLinkClick(e, large.href);
  });

  async function refresh() {
    try {
      const response = await win.fetch(box.dataset.url, { headers: { Accept: 'application/json' } });
      if (!response.ok) throw new Error(response.status);
      pls = (await response.json()).positions || {};
      render();
    } catch (e) { /* keeps the last prices */ }
  }

  setCollapsed(readCollapsed(win));
  render();
  reloadGraph(true);
  refresh();
  win.setInterval(() => { if (!doc.hidden) refresh(); }, EVERY);
}

if (typeof document !== 'undefined' && typeof window !== 'undefined') install(document, window);
