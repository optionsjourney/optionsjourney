// The Trade page's option chain: Tracker2's chain panel (analyzer/chain_panel.js) with these
// OptionsJourney changes (docs/developer/charts.md): inline in a given element instead of an
// overlay; the ticker comes from the page (setTicker); the tray goes to onChange instead of
// "Send to Analyzer"; no account box, buying-power preview or working list (the ticket has
// those). Bid = sell, ask = buy, as in Tracker2. The Pos column (#210, Tracker2's f41682c5) marks
// what each account already holds, in that account's color, filtered by the header's Positions
// menu (chain_held.js); the accounts come from the page (opts.accounts).
import * as T from './chain_tray.js';
import { earningsExpiration, earningsLabel } from './chain_earnings.js';
import * as H from './chain_held.js';
import { tipHtml } from '../tip.js';
import { TRADE_TIPS, TRADE_MORE } from './trade_tips.js';

const REFRESH_MS = 30000;

let doc = null, root = null, onChange = () => {};
let ticker = null, expirations = [], earnings = null, expiration = null, rows = [], spot = null, showAll = false;
// The broker the chain is asked from first (the ticket account's), and the one that answered.
let broker = null, source = null;
const SOURCE_NAMES = { schwab: 'Schwab', tastytrade: 'TastyTrade' };
let mult = 100;
let tray = T.emptyTray();
let quotes = new Map();
let socket = null, refreshTimer = null;
let trayMsg = '';
let accounts = [];
let held = [], heldFilter = null, heldColors = new Map();   // Pos column (chain_held.js); null filter = all

const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmtK = k => (Math.round(k * 1000) / 1000).toString();
const fmtP = n => (n == null || !isFinite(n) ? '—' : Number(n).toFixed(2));
const fmtN = n => (n == null ? '—' : Number(n).toLocaleString('en-US'));
const fmtD = d => (d == null ? '—' : Number(d).toFixed(2));

export function installChainPanel(el, opts = {}) {
  root = el;
  doc = el.ownerDocument;
  onChange = opts.onChange || onChange;
  broker = opts.broker || null;
  accounts = opts.accounts || [];
  build();
  return {
    setTicker,
    setBroker(next) { if ((next || null) === broker) return; broker = next || null; rows = []; loadChain(); },
    setTray(next) {
      tray = next; trayMsg = '';
      const show = T.trayExpiration(tray, expirations, expiration);
      if (show !== expiration) { expiration = show; rows = []; renderExps(); loadChain().then(showTrayLeg); }
      else { renderTable(); showTrayLeg(); }
      emit();
    },
    info: () => ({ ticker, spot, mult }),
    rowsFor,
  };
}

// Scrolls the chain to the loaded trade's first leg in the expiration shown.
function showTrayLeg() {
  const leg = tray.legs.find(l => l.expiration === expiration);
  if (leg) $('table').querySelector(`td[data-strike="${leg.strike}"]`)?.closest('tr')?.scrollIntoView({ block: 'center' });
}

// One expiration's chain rows for the current ticker, without showing them (a pasted order, #312).
async function rowsFor(exp) {
  const r = await fetch(`/api/trade/chain-table?ticker=${encodeURIComponent(ticker)}&expiration=${exp}${broker ? `&broker=${broker}` : ''}`);
  const d = await r.json();
  return r.ok && d.status === 'success' ? d.rows || [] : [];
}

function emit() {
  onChange({ tray: T.withQuotes(tray, quotes), ticker, spot, mult, message: trayMsg });
}

function build() {
  root.innerHTML = `
    <div class="flex flex-col h-full">
      <div class="shrink-0 flex flex-wrap items-center gap-x-3 gap-y-0.5 px-3 py-2 border-b border-gray-200 dark:border-gray-700">
        <div class="text-sm font-semibold text-gray-900 dark:text-gray-100 whitespace-nowrap">Option chain <span data-ch="title"></span></div>
        <span data-ch="spot" class="text-xs text-gray-500 dark:text-gray-400 whitespace-nowrap"></span>
        <span data-ch="source" class="text-xs text-gray-500 dark:text-gray-400 whitespace-nowrap"></span>
        <span data-ch="earnings" class="text-xs font-medium text-amber-700 dark:text-amber-300"></span>
        <div data-ch="held" class="relative ml-auto text-[11px]"></div>
        <label class="text-[11px] text-gray-500 dark:text-gray-400 flex items-center gap-1 whitespace-nowrap">
          <input type="checkbox" data-ch="all" class="accent-blue-500"> Show all strikes</label>
      </div>
      <div data-ch="exps" class="shrink-0 flex gap-1 overflow-x-auto px-3 py-2 border-b border-gray-200 dark:border-gray-700"></div>
      <div data-ch="table" class="flex-1 min-h-0 overflow-auto text-xs"></div>
    </div>`;
  $('all').addEventListener('change', e => {
    showAll = e.target.checked; renderTable(); subscribe();
    $('table').querySelector('tr[data-atm]')?.scrollIntoView({ block: 'center' });
  });
  $('exps').addEventListener('click', e => {
    const b = e.target.closest('[data-exp]');
    if (b) { expiration = b.dataset.exp; renderExps(); loadChain(); }
  });
  $('held').addEventListener('click', e => {
    const m = $('held').querySelector('[data-held-menu]');
    if (e.target.closest('[data-held-toggle]')) { if (m) m.hidden = !m.hidden; return; }
    const pick = e.target.closest('[data-held-pick]');
    if (!pick) return;
    heldFilter = pick.dataset.heldPick === 'all' ? null : Number(pick.dataset.heldPick);
    renderHeld(true); if (expirations.length) renderExps(); renderTable();
  });
  doc.addEventListener('click', e => {
    const m = root.querySelector('[data-held-menu]');
    if (m && !m.hidden && !e.target.closest('[data-ch="held"]')) m.hidden = true;
  });
  $('table').addEventListener('click', e => {
    const c = e.target.closest('[data-cell]');
    if (!c) return;
    const row = rows.find(r => r.strike === Number(c.dataset.strike));
    const side = row && row[c.dataset.type];
    if (!side) return;
    const next = T.toggleCell(tray, { expiration, strike: row.strike, type: c.dataset.type,
      side: c.dataset.cell, bid: side.bid, ask: side.ask, stream_symbol: side.stream_symbol, delta: side.delta });
    trayMsg = next === tray ? `The ticket holds at most ${T.MAX_TRAY_LEGS} legs.` : '';
    tray = next;
    renderTable(); emit();
  });
  if (NARROW && NARROW.addEventListener) NARROW.addEventListener('change', () => renderTable());
}

function $(sel) { return root.querySelector(`[data-ch="${sel}"]`); }

async function setTicker(next) {
  if (next === ticker) return;
  ticker = next; tray = T.emptyTray(); expiration = null; rows = []; expirations = []; earnings = null; mult = 100;
  trayMsg = ''; spot = null;
  held = []; heldFilter = null; renderHeld();
  $('earnings').textContent = '';
  $('spot').textContent = '';
  $('title').textContent = ticker || '';
  emit();
  clearInterval(refreshTimer);
  if (!ticker) { $('exps').innerHTML = ''; $('table').innerHTML = ''; return; }
  connect();
  loadHeld();
  refreshTimer = setInterval(() => { loadChain(); loadHeld(); }, REFRESH_MS);
  await loadExpirations();
}

async function loadExpirations() {
  loadEarnings();
  $('table').innerHTML = '<div class="p-6 text-gray-500 dark:text-gray-400">Loading expirations…</div>';
  const want = ticker;
  let got = [];
  try {
    const r = await fetch(`/api/trade/expirations?ticker=${encodeURIComponent(want)}`);
    const d = await r.json();
    got = (d && d.expirations) || [];
  } catch (e) { got = []; }
  if (want !== ticker) return;
  expirations = got;
  if (!expirations.length) {
    $('table').innerHTML = `<div class="p-6 text-gray-500 dark:text-gray-400">No chain from your brokers for ${esc(ticker)} right now.</div>`;
    return;
  }
  expiration = expirations[0];
  renderExps();
  loadChain();
}

// What every account holds on the ticker, from the journal's open trades: on a new ticker and
// every 30 s. A failed refresh keeps the last good list.
async function loadHeld() {
  const want = ticker;
  let legs = null;
  try {
    const r = await fetch(`/api/trade/held-legs?ticker=${encodeURIComponent(want)}`);
    const d = await r.json();
    if (r.ok && d && d.status === 'success') legs = d.legs || [];
  } catch (e) { legs = null; }
  if (want !== ticker || legs == null) return;
  held = legs;
  heldColors = H.accountColors(accounts, held);
  if (heldFilter != null && !held.some(h => h.account_id === heldFilter)) heldFilter = null;
  renderHeld();
  if (expirations.length) renderExps();
  renderTable();
}

// The header's Positions menu: All accounts, then each account holding anything here, with its color.
function renderHeld(closeMenu = false) {
  const box = $('held');
  if (!box) return;
  const accts = H.heldAccounts(held, accounts, heldColors);
  if (!accts.length) { box.innerHTML = ''; return; }
  const dot = c => `<span class="inline-block w-2.5 h-2.5 rounded-full shrink-0" style="background:${c}"></span>`;
  const cur = accts.find(a => a.id === heldFilter);
  const menu = box.querySelector('[data-held-menu]');
  const wasOpen = !closeMenu && menu && !menu.hidden;   // a 30 s refresh keeps it open
  const item = (val, label, dots, on) => `<button type="button" data-held-pick="${val}" class="w-full flex items-center gap-2 px-3 py-1.5 text-left whitespace-nowrap hover:bg-gray-100 dark:hover:bg-gray-700 ${on ? 'font-semibold text-gray-900 dark:text-gray-100' : 'text-gray-600 dark:text-gray-300'}">
      <span class="flex gap-0.5">${dots}</span>${esc(label)}</button>`;
  box.innerHTML = `<button type="button" data-held-toggle class="flex items-center gap-1.5 px-2 py-0.5 rounded border border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 whitespace-nowrap">
      <span class="hidden sm:inline">Positions:</span>
      <span class="flex gap-0.5">${cur ? dot(cur.color) : accts.map(a => dot(a.color)).join('')}</span>
      ${esc(cur ? cur.label : 'All accounts')} ▾</button>
    <div data-held-menu ${wasOpen ? '' : 'hidden'} class="absolute right-0 top-full mt-1 z-20 min-w-[11rem] py-1 rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 shadow-lg">
      ${item('all', 'All accounts', accts.map(a => dot(a.color)).join(''), heldFilter == null)}
      ${accts.map(a => item(a.id, a.label, dot(a.color), a.id === heldFilter)).join('')}
    </div>`;
}

async function loadEarnings() {
  const want = ticker;
  let e = null;
  try {
    const r = await fetch(`/api/trade/earnings?ticker=${encodeURIComponent(want)}`);
    const d = await r.json();
    e = (r.ok && d && d.earnings) || null;
  } catch (err) { e = null; }
  if (want !== ticker) return;
  earnings = e;
  const note = earningsLabel(earnings);
  const days = note ? Math.round((Date.parse(earnings.date) - Date.parse(new Date().toLocaleDateString('en-CA',
    { timeZone: 'America/New_York' }))) / 86400000) : 0;
  $('earnings').textContent = note ? `${note} · ${days > 0 ? `in ${days}d` : 'today'}` : '';
  if (expirations.length) renderExps();
}

function dte(iso) {
  const today = new Date(Date.now()).toLocaleDateString('en-CA', { timeZone: 'America/New_York' });
  const days = Math.round((Date.parse(iso + 'T00:00:00Z') - Date.parse(today + 'T00:00:00Z')) / 86400000);
  return Math.max(0, days);
}

function renderExps() {
  const earnExp = earningsExpiration(expirations, earnings);
  const earnTip = esc(earningsLabel(earnings));
  const heldExps = H.heldExpirations(held, heldFilter);
  $('exps').innerHTML = expirations.map(e => `
    <button type="button" data-exp="${esc(e)}"${e === earnExp ? ` title="${earnTip}"` : ''} class="shrink-0 px-2 py-1 rounded border text-[11px] ${e === expiration
      ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/40 text-blue-700 dark:text-blue-300'
      : 'border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300'}">${esc(e.slice(5))} <span class="text-gray-400">${dte(e)}d</span>${e === earnExp
      ? ' <span data-earn class="ml-0.5 px-1 rounded bg-amber-500 text-white font-bold">E</span>' : ''}${heldExps.has(e)
      ? ` <span data-held-exp title="Positions held" class="inline-flex gap-px align-middle">${heldExps.get(e).map(id =>
        `<span class="inline-block w-1.5 h-1.5 rounded-full" style="background:${heldColors.get(id) || '#9ca3af'}"></span>`).join('')}</span>` : ''}</button>`).join('');
}

async function loadChain() {
  if (!expiration) return;
  const want = expiration, wantTicker = ticker;
  if (!rows.length) $('table').innerHTML = '<div class="p-6 text-gray-500 dark:text-gray-400">Loading chain…</div>';
  try {
    const r = await fetch(`/api/trade/chain-table?ticker=${encodeURIComponent(wantTicker)}&expiration=${want}${broker ? `&broker=${broker}` : ''}`);
    const d = await r.json();
    if (want !== expiration || wantTicker !== ticker) return;
    if (!r.ok || d.status !== 'success') throw new Error(d.message || `HTTP ${r.status}`);
    rows = d.rows || [];
    spot = d.spot != null ? d.spot : null;
    source = d.source || null;
    mult = d.multiplier || 100;
    for (const row of rows) for (const s of [row.call, row.put]) {
      const q = s && quotes.get(s.stream_symbol);
      if (q && q.ask > 0) { s.bid = q.bid; s.ask = q.ask; }
    }
  } catch (e) {
    if (want !== expiration || wantTicker !== ticker) return;
    rows = [];
    $('table').innerHTML = `<div class="p-6 text-red-600 dark:text-red-400">No chain from your brokers right now: ${esc(e.message)}</div>`;
    return;
  }
  $('spot').textContent = spot != null ? `Spot ${fmtP(spot)}` : '';
  // Says when the chain came from the other broker (the ticket's couldn't give it).
  // Futures options are TastyTrade's alone: Schwab's chain refuses futures.
  const fell = source && broker && source !== broker;
  const why = !fell ? '' : String(ticker || '').startsWith('/') ? ' (Schwab has no futures options)' : ` (${SOURCE_NAMES[broker] || broker} unavailable)`;
  $('source').textContent = source ? `Chain: ${SOURCE_NAMES[source] || source}${why}` : '';
  $('source').className = `text-xs whitespace-nowrap ${fell && !String(ticker || '').startsWith('/') ? 'text-amber-600 dark:text-amber-400' : 'text-gray-500 dark:text-gray-400'}`;
  renderTable();
  subscribe();
  emit();
}

const TONE = { ok: '', wide: 'bg-amber-50 dark:bg-amber-900/20', 'very-wide': 'bg-red-50 dark:bg-red-900/25' };
const NARROW = typeof matchMedia === 'function' ? matchMedia('(max-width: 639px)') : null;
const narrow = () => !!(NARROW && NARROW.matches);
// Pos (held contracts) replaced Vol (#210); on a phone it shows only when something on the
// ticker is held.
const COLS = {
  wide: { call: ['oi', 'pos', 'delta', 'spread', 'bid', 'ask'], put: ['bid', 'ask', 'spread', 'delta', 'pos', 'oi'] },
  narrow: { call: ['delta', 'bid', 'ask'], put: ['bid', 'ask', 'delta'] },
  narrowHeld: { call: ['pos', 'delta', 'bid', 'ask'], put: ['bid', 'ask', 'delta', 'pos'] },
};
// OI only when the chain has it (Schwab's does; TastyTrade's stream doesn't).
let statsFor = null, hasOi = false;
const colsFor = compact => {
  if (statsFor !== rows) { statsFor = rows; hasOi = rows.some(r => [r.call, r.put].some(x => x && x.oi != null)); }
  const c = COLS[!compact ? 'wide' : held.length ? 'narrowHeld' : 'narrow'];
  const keep = k => k !== 'oi' || hasOi;
  return { call: c.call.filter(keep), put: c.put.filter(keep) };
};
const HEAD = { oi: 'OI', pos: 'Pos', delta: 'Δ', spread: 'Spread', bid: 'Bid', ask: 'Ask' };

function posCell(row, type, pad) {
  const hs = H.heldAt(held, { expiration, strike: row.strike, type }, heldFilter);
  if (!hs.length) return `<td class="${pad}"></td>`;
  const name = id => (accounts.find(a => a.id === id) || {}).label || `Account ${id}`;
  const tip = hs.map(h => `${name(h.account_id)}: ${H.fmtQty(h.qty)}`).join('\n');
  return `<td data-held class="${pad} text-right whitespace-nowrap" title="${esc(tip)}">${hs.map(h =>
    `<span class="inline-block ml-0.5 px-1 rounded font-semibold tabular-nums text-gray-900" style="background:${heldColors.get(h.account_id) || '#9ca3af'}">${esc(H.fmtQty(h.qty))}</span>`).join('')}</td>`;
}

function sideCells(row, type, compact) {
  const order = colsFor(compact)[type];
  const s = row[type];
  if (!s) return `<td colspan="${order.length}"></td>`;
  const sp = T.spreadInfo(s.bid, s.ask);
  const tone = sp ? TONE[T.spreadTone(sp.pct)] : '';
  const sel = tray.legs.find(l => T.legKey(l) === T.legKey({ expiration, strike: row.strike, type }));
  const hit = side => (sel && sel.side === side
    ? (side === 'sell' ? 'bg-red-600 text-white' : 'bg-green-600 text-white')
    : `${compact ? tone : ''} hover:bg-blue-100 dark:hover:bg-blue-900/40`);
  const sym = esc(s.stream_symbol);
  const pad = compact ? 'px-1.5 py-2' : 'px-2 py-1';
  const cells = {
    bid: `<td data-cell="sell" data-type="${type}" data-strike="${row.strike}" data-q="${sym}|bid" title="Sell" class="${pad} text-right cursor-pointer tabular-nums ${hit('sell')}">${fmtP(s.bid)}</td>`,
    ask: `<td data-cell="buy" data-type="${type}" data-strike="${row.strike}" data-q="${sym}|ask" title="Buy" class="${pad} text-right cursor-pointer tabular-nums ${hit('buy')}">${fmtP(s.ask)}</td>`,
    spread: `<td data-q="${sym}|spread" class="${pad} text-right tabular-nums ${tone}">${sp ? `${fmtP(sp.abs)} <span class="text-gray-400">${isFinite(sp.pct) ? Math.round(sp.pct * 100) + '%' : '—'}</span>` : '—'}</td>`,
    delta: `<td class="${pad} text-right tabular-nums text-gray-500">${fmtD(s.delta)}</td>`,
    oi: `<td class="${pad} text-right tabular-nums text-gray-500">${fmtN(s.oi)}</td>`,
  };
  return order.map(k => (k === 'pos' ? posCell(row, type, pad) : cells[k])).join('');
}

function renderTable() {
  if (!rows.length) return;
  const vis = T.visibleRows(rows, spot, showAll);
  let atm = null;
  if (spot != null) atm = vis.reduce((b, r) => (b == null || Math.abs(r.strike - spot) < Math.abs(b - spot) ? r.strike : b), null);
  const compact = narrow();
  const cols = colsFor(compact);
  const pad = compact ? 'px-1.5' : 'px-2';
  // each heading shows on the call and the put side: the side keeps the tip ids unique
  const th = side => k => `<th class="${pad} py-1 text-right font-medium">${HEAD[k]}${tipHtml(`trade-${side}-${k}`, TRADE_TIPS[k], HEAD[k], { end: k === 'ask', more: TRADE_MORE[k] })}</th>`;
  const head = `<tr class="sticky top-0 bg-gray-50 dark:bg-gray-900 text-gray-500 dark:text-gray-400">
      ${cols.call.map(th('call')).join('')}
      <th class="${pad} py-1 text-center font-semibold">Strike</th>
      ${cols.put.map(th('put')).join('')}</tr>
    <tr class="sticky top-[22px] bg-gray-50 dark:bg-gray-900 text-[10px] text-gray-400"><th colspan="${cols.call.length}" class="text-center">CALLS</th><th></th><th colspan="${cols.put.length}" class="text-center">PUTS</th></tr>`;
  const body = vis.map(r => `<tr class="border-t border-gray-100 dark:border-gray-700/60 text-gray-800 dark:text-gray-200 ${r.strike === atm ? 'outline outline-1 outline-blue-400' : ''}"${r.strike === atm ? ' data-atm' : ''}>
      ${sideCells(r, 'call', compact)}
      <td class="${pad} py-1 text-center font-semibold bg-gray-50 dark:bg-gray-900">${fmtK(r.strike)}</td>
      ${sideCells(r, 'put', compact)}</tr>`).join('');
  $('table').innerHTML = `<table class="w-full border-collapse"><thead>${head}</thead><tbody>${body}</tbody></table>`;
}

function connect() {
  if (socket || typeof io === 'undefined') return;
  try {
    socket = io({ transports: ['websocket', 'polling'], forceNew: true });
    socket.on('connect', subscribe);
    socket.on('risk_quotes', p => onQuotes((p && p.quotes) || {}));
  } catch (e) { socket = null; }
}

function subscribe() {
  if (!socket || !socket.connected || !rows.length) return;
  socket.emit('subscribe_risk_quotes', { symbols: T.streamWindow(T.visibleRows(rows, spot, showAll), spot, 64) });
}

function onQuotes(q) {
  let touched = false;
  for (const [sym, v] of Object.entries(q)) {
    if (!v || !(v.ask > 0)) continue;
    quotes.set(sym, { bid: v.bid, ask: v.ask });
    if (tray.legs.some(l => l.stream_symbol === sym)) touched = true;
    for (const row of rows) for (const s of [row.call, row.put]) {
      if (s && s.stream_symbol === sym) { s.bid = v.bid; s.ask = v.ask; }
    }
    const b = root.querySelector(`[data-q="${CSS.escape(sym)}|bid"]`);
    const a = root.querySelector(`[data-q="${CSS.escape(sym)}|ask"]`);
    const sp = root.querySelector(`[data-q="${CSS.escape(sym)}|spread"]`);
    if (b) b.textContent = fmtP(v.bid);
    if (a) a.textContent = fmtP(v.ask);
    if (sp) {
      const info = T.spreadInfo(v.bid, v.ask);
      sp.innerHTML = info ? `${fmtP(info.abs)} <span class="text-gray-400">${isFinite(info.pct) ? Math.round(info.pct * 100) + '%' : '—'}</span>` : '—';
    }
  }
  if (touched) emit();
}
