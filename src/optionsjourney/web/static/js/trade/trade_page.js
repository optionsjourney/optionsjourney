// The Trade page: the chain (chain_panel.js) on the left, the order ticket on the right, and the
// risk graph (slice 1's panel, risk_graph.js) under them. Review order previews through the one
// order path (POST /api/trade/review); Confirm then sends exactly the reviewed order, once
// (POST /api/trade/confirm). Your working orders sit under the ticket, with Cancel and Replace.
// Bots slice 15: beside Send, Manage with a bot sends the reviewed ticket as a live bot's entry
// instead (POST /api/trade/bot-entry), claimed on a one-time token so a second press sends nothing.
// Trade Ideas (#292): Idea ☆ saves the ticket as an idea (ideas/save.js); /trade?idea=<id> opens
// one in the ticket, and Send or the bot's Send then carries its id so the server retires it.
import { installChainPanel } from './chain_panel.js';
import { tourTray, tourTrayFor } from './tour_tray.js';
import { installIdeaSave, pageIdea, ideaBanner, ideaTray } from '../ideas/save.js';
import * as T from './chain_tray.js';
import { trayStats, stripItems } from './chain_stats.js';
import { tipHtml, tipEngaged } from '../tip.js';
import { TRADE_TIPS, TRADE_MORE } from './trade_tips.js';
import { overnightOffered, sendWarning, acceptedWords } from './trade_ticket.js';
import { defaultTicket, reviewRequest, reviewBp, graphLegs, alertContracts, confirmLabel, confirmParts, costRows, workingRows, replacePrice,
  newToken, botRequest, pickerRequest, botLists, botSendLabel, futuresBotNote, manageOptions, confirmRequest } from './ticket_core.js';

const ACCOUNT_KEY = 'trade_account';
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
// What Send will send: the terms in bold, then each leg on its own smaller line.
const sendSummary = summary => {
  const { legs, terms } = confirmParts(summary);
  return (terms ? `<div class="font-semibold">${esc(terms)}</div>` : '')
    + legs.map(l => `<div class="text-xs text-gray-300">${esc(l)}</div>`).join('');
};
const fmtP = n => (n == null || !isFinite(n) ? '—' : Number(n).toFixed(2));
const fmtK = k => (Math.round(k * 1000) / 1000).toString();

const doc = document;
const ticketEl = document.getElementById('trade-ticket');
const DEMO = ticketEl?.dataset.demo === '1';   // the demo's Trade page: its words never say real
const graphEl = document.getElementById('trade-graph');
const workingEl = document.getElementById('trade-working');
let accounts = [];
try { accounts = JSON.parse(document.getElementById('trade-accounts').textContent || '[]'); } catch (e) { accounts = []; }
let accountId = null;
try { accountId = Number(localStorage.getItem(ACCOUNT_KEY)) || null; } catch (e) { /* private mode */ }
if (!accounts.some(a => a.id === accountId)) accountId = (accounts.find(a => a.broker === 'TASTYTRADE') || accounts[0] || {}).id ?? null;

// The ticket's state. price/effect are null until you set them: then the defaults follow the mids.
let ctx = { tray: T.emptyTray(), ticker: null, spot: null, mult: 100, message: '' };
let price = null, effect = null, tif = 'day', graphOn = false, graphShown = false;
let autoEffect = null;   // the default effect, locked once every leg is quoted, until the legs change
let result = null;   // {tone, text} from Review or Alert
let inFlight = false;   // a Review is out: the button stays disabled across renders
let autoPrice = false;   // price was frozen from the default by Review, not typed
let reviewed = null;   // {code, summary, sig} from a review your broker accepted: Confirm shows while sig holds
let reviewCosts = null;   // {sig, costs} from the last review: the strip shows them while sig holds (#211)
let sending = false;   // Send or the bot's Send is out: both buttons stay disabled, one review sends once
// Manage with a bot: {sig, token, bots, error, chosen, inFlight} for the reviewed ticket; null otherwise.
let botBox = null;
// Manual bots (#158): Manage with under Send. '' is Place only (the plain send); a manual bot's
// rules are attached to the trade once the order fills. The list loads once with the page.
let manualBots = [], manageWith = '';
let working = { rows: [], error: null, note: null }, workingSeq = 0;
// Working orders for every account, filtered by one (or all) and shown a page at a time.
const WORKING_PAGE = 10;
let workingFilter = 'all', workingShown = WORKING_PAGE;
const accountName = a => `${a.label} (${a.broker === 'SCHWAB' ? 'Schwab' : 'TT'})`;
const busy = new Set();   // working order ids with a Cancel or Replace out
let stats = { sig: '', spot: null, at: 0, value: null };
// The idea the ticket was opened from: {id, sig}, until its legs change (then it's your own ticket).
let idea = null;
const ideaBannerEl = document.getElementById('idea-banner');
const ideaId = () => (idea ? idea.id : null);
function dropIdea() {
  idea = null;
  if (ideaBannerEl) ideaBannerEl.hidden = true;
}
const futuresTicket = () => String(ctx.ticker || '').startsWith('/');

const brokerOf = id => ({ SCHWAB: 'schwab', TASTYTRADE: 'tastytrade' })[(accounts.find(a => a.id === id) || {}).broker] || null;
const panel = installChainPanel(document.getElementById('trade-chain'), {
  broker: brokerOf(accountId),
  accounts,   // the Pos column's account names (#210)
  onChange(next) {
    const structural = legSig(next.tray) !== legSig(ctx.tray) || next.ticker !== ctx.ticker || next.message !== ctx.message;
    if (structural) { result = null; ticketChanged(); autoEffect = null; }
    if (idea && legSig(next.tray) !== idea.sig) dropIdea();
    if (!next.tray.legs.length) { price = null; effect = null; }
    ctx = next;
    if (structural) render(); else refresh();
  },
});

// The leg keys, sides and quantities: what a quote push does not change.
const legSig = tray => tray.legs.map(l => `${T.legKey(l)}|${l.side}|${l.qty}`).join(',');

function limit() { return price != null ? price : defaultTicket(ctx.tray).limit; }
// The default effect follows the mids until every leg is quoted, then holds: a quote push must
// not flip the select away from what Review sends.
function side() {
  if (effect) return effect;
  const d = defaultTicket(ctx.tray);
  if (autoEffect == null && d.limit != null) autoEffect = d.effect;
  return autoEffect || d.effect;
}
// What a Review request depends on: a reply for a different ticket is dropped.
const ticketSig = () => [legSig(ctx.tray), limit(), side(), tif, accountId].join('#');
function account() { return accounts.find(a => a.id === accountId) || null; }
const reviewHolds = () => !!reviewed && reviewed.sig === ticketSig();
// Bots slice 17: a futures review can only go to a bot (#93): no plain Send for it.
const canConfirm = () => reviewHolds() && !reviewed.futures;
const canBot = () => reviewHolds() && !!botBox && botBox.sig === reviewed.sig;

// The ticket changed: a stored review no longer fits it, and a limit Review froze from the
// default follows the mids again rather than sticking to the old legs.
function ticketChanged() {
  reviewed = null;
  botBox = null;
  if (autoPrice) { price = null; effect = null; autoEffect = null; autoPrice = false; }
}

// The P50 stats are costly: recompute when the legs change, the spot moves over 0.1%, or every 5 s.
function updateStats() {
  const legs = ctx.tray.legs;
  if (!legs.length) return false;
  const sig = legSig(ctx.tray);
  const moved = stats.spot == null || ctx.spot == null || Math.abs(ctx.spot - stats.spot) > Math.abs(stats.spot) * 0.001;
  if (sig === stats.sig && !moved && Date.now() - stats.at < 5000) return false;
  stats = { sig, spot: ctx.spot, at: Date.now(), value: trayStats(legs, { spot: ctx.spot, mult: ctx.mult }) };
  return true;
}

// The strip (#211, Tracker2's ac941c41): the net price per unit large (credit green, debit red)
// with Nat and Opp, then Delta, POP, P50, Max Profit/Loss, and BP Eff., BP After and Fees from
// the last review of this same ticket (blank until you review).
const STRIP_TONE = { pos: 'text-green-500', neg: 'text-red-500' };
function stripHtml() {
  if (!ctx.tray.legs.length) return '';
  const costs = reviewCosts && reviewCosts.sig === ticketSig() ? reviewCosts.costs : null;
  const items = stats.value ? stripItems(stats.value, reviewBp(costs)) : [];
  return priceHtml() + items.map(i => `<span${i.title ? ` title="${esc(i.title)}"` : ''}><span class="text-gray-500">${esc(i.label)}</span>${tipHtml('trade-' + i.key, i.tip, i.label, { more: TRADE_MORE[i.key], end: i.key === 'bpAfter' || i.key === 'fees' })} <b class="tabular-nums ${STRIP_TONE[i.tone] || ''}">${esc(i.value)}</b></span>`).join(' ');
}

// The ticket's net price per unit, TastyTrade-style: quantities divided by their common
// factor (×N shown), Nat = sells at the bid and buys at the ask, Opp the reverse.
function priceHtml() {
  const p = T.netPrices(ctx.tray);
  if (!p) return '';
  const side = x => `${fmtP(Math.abs(x))} ${x >= 0 ? 'cr' : 'db'}`;
  return `<span data-price class="w-full sm:w-auto flex items-baseline gap-2 sm:pr-3 sm:border-r border-gray-700">
    <span class="text-xl font-bold tabular-nums ${p.mid >= 0 ? 'text-green-500' : 'text-red-500'}" title="Net mid per unit${p.unit > 1 ? ` (×${p.unit})` : ''}">${side(p.mid)}</span>
    ${p.unit > 1 ? `<span class="text-gray-400 font-semibold">×${p.unit}</span>` : ''}
    <span class="text-gray-400 tabular-nums whitespace-nowrap">Nat ${side(p.nat)} · Opp ${side(p.opp)}</span></span>`;
}

// What the user is typing survives a structural render: the alert box, and the focused input.
function saveUi() {
  const q = sel => ticketEl.querySelector(sel);
  const f = doc.activeElement;
  const focus = f && ticketEl.contains(f) && f.dataset.act
    ? { act: f.dataset.act, key: f.dataset.key, value: f.value, start: f.selectionStart, end: f.selectionEnd } : null;
  const dir = q('[data-act="alert-dir"]'), level = q('[data-act="alert-level"]'), det = q('details');
  return { focus, dir: dir && dir.value, level: level && level.value, open: !!(det && det.open) };
}

function restoreUi(u) {
  const q = sel => ticketEl.querySelector(sel);
  const dir = q('[data-act="alert-dir"]'), level = q('[data-act="alert-level"]'), det = q('details');
  if (dir && u.dir) dir.value = u.dir;
  if (level && u.level != null) level.value = u.level;
  if (det && u.open) det.open = true;
  if (!u.focus) return;
  const els = [...ticketEl.querySelectorAll(`[data-act="${u.focus.act}"]`)];
  const el = els.find(e => (e.dataset.key || '') === (u.focus.key || '')) || els[0];
  if (!el) return;
  const text = el.tagName === 'INPUT';
  if (text && u.focus.value != null) el.value = u.focus.value;
  el.focus();
  if (text && u.focus.start != null) { try { el.setSelectionRange(u.focus.start, u.focus.end); } catch (e) { /* number inputs */ } }
}

// A quote-only push: patch the numbers in place, leave the inputs alone.
function refresh() {
  const legs = ctx.tray.legs;
  if (!legs.length) return;
  for (const l of legs) {
    const el = ticketEl.querySelector(`[data-mid="${CSS.escape(T.legKey(l))}"]`);
    if (el) el.textContent = `mid ${fmtP(T.legMid(l))}`;
  }
  updateStats();
  const strip = ticketEl.querySelector('[data-strip]');
  // The price follows every quote; not while the user is on a strip tip (the rebuild would close it).
  if (strip && !tipEngaged(strip)) strip.innerHTML = stripHtml();
  const inp = ticketEl.querySelector('[data-act="price"]');
  if (inp && price == null && doc.activeElement !== inp) inp.value = limit() == null ? '' : limit();
  const sel = ticketEl.querySelector('[data-act="effect"]');
  if (sel) sel.value = side();
  if (ticketEl.querySelector('[data-act="confirm"]') && !canConfirm()) render();   // never a stale Confirm
}

function render() {
  const legs = ctx.tray.legs;
  const acct = account();
  const overnight = overnightOffered(ctx.ticker, acct && acct.broker);
  if (tif === 'gtc_overnight' && !overnight) tif = 'gtc';
  const saved = saveUi();
  updateStats();
  const strip = stripHtml();
  const rows = legs.map(l => {
    const k = esc(T.legKey(l));
    return `<div class="flex items-center gap-2 py-0.5">
      <span class="w-10 font-semibold ${l.side === 'sell' ? 'text-red-500' : 'text-green-500'}">${l.side === 'sell' ? 'SELL' : 'BUY'}</span>
      <button type="button" data-act="dec" data-key="${k}" class="px-1.5 rounded border border-gray-600">−</button>
      <input type="number" min="1" max="1000" value="${l.qty}" data-act="qty" data-key="${k}" class="w-14 px-1 rounded border border-gray-600 bg-transparent text-right">
      <button type="button" data-act="inc" data-key="${k}" class="px-1.5 rounded border border-gray-600">+</button>
      <span class="tabular-nums">${esc(fmtK(l.strike))}${l.type === 'put' ? 'P' : 'C'} ${esc(l.expiration)}</span>
      <span data-mid="${k}" class="text-gray-500 tabular-nums">mid ${fmtP(T.legMid(l))}</span>
      <button type="button" data-act="rm" data-key="${k}" title="Remove" class="ml-auto text-gray-400 hover:text-red-500">×</button>
    </div>`;
  }).join('');
  const opts = accounts.map(a => `<option value="${a.id}" ${a.id === accountId ? 'selected' : ''}>${esc(a.label)} (${a.broker === 'SCHWAB' ? 'Schwab' : 'TT'})</option>`).join('');
  const lim = limit();
  ticketEl.innerHTML = `
    <div class="font-semibold mb-2">Order ticket ${ctx.ticker ? `· ${esc(ctx.ticker)}` : ''}</div>
    ${legs.length ? '' : '<p class="text-gray-500">Pick a bid (sell) or ask (buy) in the chain to start.</p>'}
    <div data-strip class="flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] ${strip ? 'pb-2 mb-2 border-b border-gray-700' : ''}">${strip}</div>
    <div>${rows}</div>
    ${ctx.message ? `<div class="text-amber-500 mt-1">${esc(ctx.message)}</div>` : ''}
    <div class="grid grid-cols-2 gap-2 mt-3">
      <label class="flex flex-col text-xs text-gray-500">Limit (per unit)
        <input type="number" min="0" step="0.05" data-act="price" value="${lim == null ? '' : lim}" class="mt-0.5 px-1 py-1 rounded border border-gray-600 bg-transparent text-right text-gray-100"></label>
      <label class="flex flex-col text-xs text-gray-500">Credit or debit
        <select data-act="effect" class="mt-0.5 px-1 py-1 rounded border border-gray-600 bg-transparent text-gray-100">
          <option value="credit" ${side() === 'credit' ? 'selected' : ''}>Credit</option>
          <option value="debit" ${side() === 'debit' ? 'selected' : ''}>Debit</option></select></label>
      <label class="flex flex-col text-xs text-gray-500">Account
        <select data-act="account" class="mt-0.5 px-1 py-1 rounded border border-gray-600 bg-transparent text-gray-100">${opts}</select></label>
      <div class="flex flex-col text-xs text-gray-500"><span><label for="trade-tif">Time in force</label>${tipHtml('trade-gtc', TRADE_TIPS.gtc + (overnight ? ' ' + TRADE_TIPS.gtcOvernight : ''), 'Time in force', { end: true })}</span>
        <select id="trade-tif" data-act="tif" class="mt-0.5 px-1 py-1 rounded border border-gray-600 bg-transparent text-gray-100">
          <option value="day" ${tif === 'day' ? 'selected' : ''}>Day</option>
          <option value="gtc" ${tif === 'gtc' ? 'selected' : ''}>GTC</option>
          ${overnight ? `<option value="gtc_overnight" ${tif === 'gtc_overnight' ? 'selected' : ''}>GTC overnight</option>` : ''}</select></div>
    </div>
    <div class="flex flex-wrap gap-2 mt-3">
      <button type="button" data-act="review" class="px-3 py-1 rounded bg-blue-600 text-white disabled:opacity-40" ${legs.length && !inFlight ? '' : 'disabled'}>Review order</button>
      <button type="button" data-act="graph" class="px-3 py-1 rounded border border-gray-600 ${graphOn ? 'bg-gray-700' : ''}" ${legs.length ? '' : 'disabled'}>Risk graph</button>
      <button type="button" data-act="clear" class="px-3 py-1 rounded border border-gray-600" ${legs.length ? '' : 'disabled'}>Clear</button>
      ${futuresTicket() ? '' : `<button type="button" data-act="idea-open" title="Save this ticket to Ideas, to follow it without trading" class="px-3 py-1 rounded border border-gray-600 disabled:opacity-40" ${legs.length ? '' : 'disabled'}>Idea ☆</button>`}
    </div>
    <details class="mt-3 text-xs" ${legs.length ? '' : 'hidden'}>
      <summary class="cursor-pointer text-gray-400">Alert me on this spread</summary>
      <div class="flex items-center gap-2 mt-2">
        <select data-act="alert-dir" class="px-1 py-1 rounded border border-gray-600 bg-transparent"><option value="above">above</option><option value="below">below</option></select>
        <input type="number" min="0" step="0.05" data-act="alert-level" placeholder="price" class="w-24 px-1 py-1 rounded border border-gray-600 bg-transparent text-right">
        <button type="button" data-act="alert" class="px-2 py-1 rounded border border-gray-600">Add alert</button>
      </div>
    </details>
    ${result ? `<div class="mt-3 p-2 rounded ${result.tone === 'ok' ? 'bg-green-900/30 text-green-300' : result.tone === 'error' ? 'bg-red-900/30 text-red-300' : 'bg-gray-700 text-gray-200'}">${esc(result.text)}${result.link ? ` <a class="underline" href="${esc(result.link)}">See the run</a>` : ''}</div>` : ''}
    ${result && costRows(result.costs).length ? `<table class="mt-2 w-full text-xs tabular-nums">${costRows(result.costs).map(([label, text, tone]) =>
      `<tr class="${tone === 'strong' ? 'font-semibold text-gray-100' : 'text-gray-300'}"><td class="py-0.5 text-gray-400">${esc(label)}</td><td class="py-0.5 text-right ${tone === 'bad' ? 'text-red-400' : ''}">${esc(text)}</td></tr>`).join('')}</table>` : ''}
    ${canConfirm() ? `<div class="mt-2">
      <div class="p-2 rounded border border-amber-500/60 bg-amber-500/10 text-gray-100">${sendSummary(reviewed.summary)}</div>
      <button type="button" data-act="confirm" class="mt-2 px-5 py-2 rounded-md bg-amber-500 hover:bg-amber-400 active:bg-amber-600 text-gray-900 font-semibold shadow-md cursor-pointer disabled:opacity-40" ${sending ? 'disabled' : ''} aria-label="${esc(confirmLabel(reviewed.summary))}">Send order</button>
      <p class="mt-1 text-[11px] text-amber-400">${esc(sendWarning(DEMO))}</p>
      <label class="flex flex-col text-xs text-gray-500 mt-2">Manage with
        <select data-act="manage-with" class="mt-0.5 px-1 py-1 rounded border border-gray-600 bg-transparent text-gray-100">${manageOptions(manualBots).map(o => `<option value="${esc(o.value)}" ${o.value === manageWith ? 'selected' : ''}>${esc(o.label)}</option>`).join('')}</select></label>
      ${manageWith ? '<p class="mt-1 text-[11px] text-gray-400">Once the order fills, this manual bot\'s rules (as they are now) are attached to the trade. A partial fill gets them too, and they grow as the rest fills.</p>' : ''}</div>` : ''}
    ${canBot() ? botHtml() : ''}
    <p class="mt-3 text-[11px] text-gray-500">Review order asks your broker to check the order. Nothing is sent until you press Send.</p>`;
  restoreUi(saved);
  drawGraph();
}

// The Manage with a bot box: the bots that can take the reviewed ticket, the chosen one's exits,
// and its Send button; the others under a fold with why not.
function botHtml() {
  const head = '<div class="font-semibold">Or manage it with a bot</div>';
  const wrap = body => `<div class="mt-3 p-2 rounded border border-gray-700">${head}${body}</div>`;
  if (botBox.error) return wrap(`<p class="text-xs text-red-400 mt-1">${esc(botBox.error)}</p>`);
  if (botBox.bots == null) return wrap('<p class="text-xs text-gray-500 mt-1">Finding the bots that can take it…</p>');
  const { offered, refused } = botLists(botBox.bots);
  const why = refused.length ? `<details class="mt-2 text-[11px]"><summary class="cursor-pointer text-gray-400">Bots that can't take it, and why</summary>
    <ul class="mt-1">${refused.map(b => `<li>${esc(b.name)}: ${esc(b.problem)}</li>`).join('')}</ul></details>` : '';
  if (!offered.length) return wrap(`<p class="text-xs text-gray-500 mt-1">No live bot on this account matches this ticket.</p>${why}`);
  const chosen = offered.find(b => b.id === botBox.chosen) || offered[0];
  const opts = offered.map(b => `<option value="${b.id}" ${b.id === chosen.id ? 'selected' : ''}>${esc(b.name)}</option>`).join('');
  if (reviewed.futures) return wrap(`
    <p class="text-[11px] text-gray-500 mt-1">A futures ticket can only be sent as a bot's entry for now.</p>
    <label class="flex flex-col text-xs text-gray-500 mt-2">Bot
      <select data-act="bot-pick" class="mt-0.5 px-1 py-1 rounded border border-gray-600 bg-transparent text-gray-100">${opts}</select></label>
    <ul class="text-[11px] text-gray-400 mt-1">${chosen.exits.map(line => `<li>${esc(line)}</li>`).join('')}</ul>
    <button type="button" data-act="bot-send" class="w-full mt-2 px-3 py-1.5 rounded bg-amber-500 hover:bg-amber-600 text-gray-900 font-semibold text-left disabled:opacity-40" ${sending ? 'disabled' : ''}>${esc(botSendLabel(chosen.name, true))}</button>
    <p class="mt-1 text-[11px] text-red-400">${esc(futuresBotNote(reviewed.summary))}</p>${why}`);
  return wrap(`
    <p class="text-[11px] text-gray-500 mt-1">The bot sends this ticket as its own entry: it chases the price with its entry settings, within its limits, and places its stops and targets on the fill.</p>
    <label class="flex flex-col text-xs text-gray-500 mt-2">Bot
      <select data-act="bot-pick" class="mt-0.5 px-1 py-1 rounded border border-gray-600 bg-transparent text-gray-100">${opts}</select></label>
    <ul class="text-[11px] text-gray-400 mt-1">${chosen.exits.map(line => `<li>${esc(line)}</li>`).join('')}</ul>
    <button type="button" data-act="bot-send" class="w-full mt-2 px-3 py-1.5 rounded bg-amber-500 hover:bg-amber-600 text-gray-900 font-semibold text-left disabled:opacity-40" ${sending ? 'disabled' : ''}>${esc(botSendLabel(chosen.name))}</button>
    <p class="mt-1 text-[11px] text-red-400">Instead of Send: the bot sends this ticket to your broker as its own entry order, moving the price with its entry chase until it fills or runs out of tries, then places its stops and targets. It uses up this review.</p>${why}`);
}

// _riskLive is a top-level const of the classic risk_graph.js: not on window, but in scope.
function stopGraph() {
  if (graphShown && typeof _riskLive !== 'undefined') _riskLive.untrack('modal');
  graphShown = false;
}

function drawGraph() {
  const on = graphOn && ctx.tray.legs.length > 0;
  graphEl.classList.toggle('hidden', !on);
  if (!on) {
    stopGraph();
    return;
  }
  const { legs, realizedPl } = graphLegs(ctx.tray, { limit: limit(), effect: side(), mult: ctx.mult });
  if (!graphShown) {
    window._populatePlChartModal(legs, realizedPl, `${ctx.ticker} · your ticket`, null, ctx.spot, ctx.ticker);
    graphShown = true;
  } else {
    window._setPlLegs(legs, realizedPl);
  }
}

// The drawer closes by its × or Escape, the same as pressing Risk graph again.
graphEl.addEventListener('click', e => { if (e.target.closest('[data-act="graph-close"]')) { graphOn = false; render(); } });
doc.addEventListener('keydown', e => { if (e.key === 'Escape' && graphOn) { graphOn = false; render(); } });

ticketEl.addEventListener('click', async e => {
  const b = e.target.closest('[data-act]');
  if (!b) return;
  const key = b.dataset.key;
  const leg = key && ctx.tray.legs.find(l => T.legKey(l) === key);
  if (b.dataset.act === 'inc' && leg) panel.setTray(T.setQty(ctx.tray, key, leg.qty + 1));
  else if (b.dataset.act === 'dec' && leg) panel.setTray(T.setQty(ctx.tray, key, leg.qty - 1));
  else if (b.dataset.act === 'rm') panel.setTray(T.removeLeg(ctx.tray, key));
  else if (b.dataset.act === 'clear') { panel.setTray(T.emptyTray()); result = null; }
  else if (b.dataset.act === 'graph') { graphOn = !graphOn; render(); }
  else if (b.dataset.act === 'review') await review(b);
  else if (b.dataset.act === 'confirm') await confirmOrder();
  else if (b.dataset.act === 'bot-send') await sendAsBot();
  else if (b.dataset.act === 'alert') await addAlert();
});

ticketEl.addEventListener('change', e => {
  const t = e.target;
  if (t.dataset.act === 'manage-with') { manageWith = t.value; render(); }
  else if (t.dataset.act === 'bot-pick') { if (botBox) botBox.chosen = Number(t.value); render(); }
  else if (t.dataset.act === 'qty') panel.setTray(T.setQty(ctx.tray, t.dataset.key, t.value));
  // ticketChanged() first, then the new value: resetting a frozen default must not undo this edit.
  else if (t.dataset.act === 'price') { ticketChanged(); price = t.value === '' ? null : Number(t.value); result = null; render(); }
  else if (t.dataset.act === 'effect') { ticketChanged(); effect = t.value; result = null; render(); }
  else if (t.dataset.act === 'tif') { ticketChanged(); tif = t.value; result = null; render(); }
  else if (t.dataset.act === 'account') {
    ticketChanged();
    accountId = Number(t.value);
    try { localStorage.setItem(ACCOUNT_KEY, String(accountId)); } catch (err) { /* private mode */ }
    panel.setBroker(brokerOf(accountId));
    result = null; render();
  }
});

async function review(btn) {
  const CHANGED = { tone: 'muted', text: 'The ticket changed while your broker was checking it. Review again.' };
  const sent = limit();
  const body = reviewRequest(ctx.tray, { accountId, price: sent, effect: side(), tif, ticker: ctx.ticker });
  if (body.error) { result = { tone: 'error', text: body.error }; render(); return; }
  // Freeze what is being reviewed: a default limit follows the mids, so a quote push mid-request
  // would change the signature and drop the answer. Now only a real edit changes it.
  if (price == null) { price = sent; autoPrice = true; }
  if (effect == null) effect = side();
  const sig = ticketSig();
  reviewed = null;
  inFlight = true;
  result = { tone: 'muted', text: 'Asking your broker to check it…' };
  render();
  try {
    const r = await fetch('/api/trade/review', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const d = await r.json();
    if (d.status !== 'success') throw new Error(d.message || `HTTP ${r.status}`);
    if (ticketSig() !== sig) { inFlight = false; result = CHANGED; render(); return; }   // the ticket changed: not its answer
    const accepted = d.ok && d.action === 'preview';
    // The costs table says the buying power change, so the message doesn't repeat it.
    const said = d.costs ? d.message.replace(/\s*Buying power change: \$[-\d,.]*\d\.?/, '') : d.message;
    result = { tone: accepted ? 'ok' : 'error', text: accepted ? `${acceptedWords(DEMO)} ${said}` : said, costs: d.costs };
    reviewCosts = d.costs ? { sig, costs: d.costs } : null;   // the strip's BP Eff., BP After and Fees
    if (accepted && d.code) { reviewed = { code: d.code, summary: d.summary, sig, futures: !!d.futures }; openBotBox(sig); }
  } catch (err) {
    if (ticketSig() !== sig) { inFlight = false; result = CHANGED; render(); return; }
    result = { tone: 'error', text: `Couldn't review the order: ${err.message}` };
  }
  inFlight = false;
  render();
}

// A reviewed ticket gets a fresh token and the list of bots that can take it (read only).
async function openBotBox(sig) {
  let token;
  try { token = newToken(crypto.getRandomValues(new Uint8Array(6))); } catch (e) { return; }
  botBox = { sig, token, bots: null, error: null, chosen: null };
  const box = botBox;
  const body = pickerRequest(ctx.tray, accountId, ctx.ticker);
  try {
    const r = await fetch('/api/trade/bots', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const d = await r.json();
    if (d.status !== 'success') throw new Error(d.message || `HTTP ${r.status}`);
    box.bots = d.problem ? [] : d.bots;
    if (d.problem) box.error = d.problem;
    const first = botLists(box.bots).offered[0];
    box.chosen = first ? first.id : null;
  } catch (err) {
    box.error = `Couldn't list the bots: ${err.message}`;
  }
  if (botBox === box) render();
}

// Sends the reviewed ticket as the chosen bot's entry, once: the token is claimed before any
// broker call, and whatever the answer the review and the box go away.
async function sendAsBot() {
  if (!canBot() || sending) return;
  const { offered } = botLists(botBox.bots);
  const chosen = offered.find(b => b.id === botBox.chosen) || offered[0];
  if (!chosen) return;
  const body = botRequest(ctx.tray, { accountId, botId: chosen.id, token: botBox.token, code: reviewed.code, ticker: ctx.ticker, ideaId: ideaId() });
  if (body.error) { result = { tone: 'error', text: body.error }; render(); return; }
  const ask = reviewed.futures
    ? `Let ${chosen.name} rest this limit at TastyTrade as its entry? It stays there until it fills or you cancel it from the run page; when it fills the bot rests its profit target. Futures: profit target only, no stop.`
    : `Let ${chosen.name} send this ticket as its entry? It sends the order to your broker, chases the price with its entry settings, and places its stops and targets when it fills. Send for this review goes away.`;
  if (!window.confirm(ask)) return;
  if (!canBot() || sending) return;
  sending = true;
  render();
  try {
    const r = await fetch('/api/trade/bot-entry', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const d = await r.json();
    if (d.status !== 'success') throw new Error(d.message || `HTTP ${r.status}`);
    result = { tone: d.ok ? 'ok' : 'error', text: d.message + (d.idea_removed ? ' Removed from Ideas.' : ''), link: d.run_url };
    if (d.idea_removed) dropIdea();
  } catch (err) {
    result = { tone: 'error', text: `Couldn't send it: ${err.message}. Check the bot's runs before trying again.` };
  }
  reviewed = null;   // the review's code is used up either way
  botBox = null;
  sending = false;
  render();
  loadWorking();
}

// Sends the reviewed order. The code is used once: whatever the answer, Confirm goes away.
async function confirmOrder() {
  if (sending) return;
  if (!canConfirm()) { reviewed = null; render(); return; }
  const code = reviewed.code;
  sending = true;
  render();
  try {
    const r = await fetch('/api/trade/confirm', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(confirmRequest(accountId, code, manageWith, ideaId())) });
    const d = await r.json();
    if (d.status !== 'success') throw new Error(d.message || `HTTP ${r.status}`);
    if (d.ok && d.action === 'send') {
      result = { tone: 'ok', text: (d.order_id ? `Sent: order #${d.order_id}.` : 'Sent.') + (d.manage_note ? ` ${d.manage_note}` : '')
        + (d.idea_removed ? ' Removed from Ideas.' : '') };
      if (d.idea_removed) dropIdea();
      manageWith = '';   // back to Place only after a send
    }
    else result = { tone: d.ok ? 'muted' : 'error', text: d.message };
  } catch (err) {
    result = { tone: 'error', text: `Couldn't send the order: ${err.message}. Check Working orders before trying again.` };
  }
  reviewed = null;
  botBox = null;
  sending = false;
  render();
  loadWorking();
}

// The working list lives in its own container: ticket renders and quote pushes never touch it,
// so a price typed into Replace is safe. A reload keeps what you typed and where you were.
async function loadManualBots() {
  try {
    const r = await fetch('/api/trade/manual-bots');
    const d = await r.json();
    if (d.status === 'success') { manualBots = d.bots || []; if (!manualBots.some(b => String(b.id) === manageWith)) manageWith = ''; render(); }
  } catch (e) { /* Place only stays */ }
}
loadManualBots();

async function loadWorking() {
  const seq = ++workingSeq;
  const got = await Promise.allSettled(accounts.map(async a => {
    const r = await fetch(`/api/trade/working?account_id=${encodeURIComponent(a.id)}`);
    const d = await r.json();
    if (d.status !== 'success') throw new Error(`${accountName(a)}: ${d.message || `HTTP ${r.status}`}`);
    return workingRows(d.orders || []).map(w => ({ ...w, key: `${a.id}:${w.id}`, accountId: a.id, account: accountName(a) }));
  }));
  if (seq !== workingSeq) return;
  const failed = got.filter(g => g.status === 'rejected').map(g => g.reason.message);
  working = { rows: got.flatMap(g => g.status === 'fulfilled' ? g.value : []),
    error: failed.length ? failed.join(' · ') : null, note: working.note };
  renderWorking();
}

function renderWorking() {
  const typed = {};
  for (const inp of workingEl.querySelectorAll('[data-act="w-price"][data-edited]')) typed[inp.dataset.id] = inp.value;
  const f = doc.activeElement;
  const focus = f && workingEl.contains(f) && f.dataset.act ? { act: f.dataset.act, id: f.dataset.id } : null;
  const counts = new Map();
  for (const w of working.rows) counts.set(w.accountId, (counts.get(w.accountId) || 0) + 1);
  if (workingFilter !== 'all' && !counts.has(workingFilter)) workingFilter = 'all';
  const matching = working.rows.filter(w => workingFilter === 'all' || w.accountId === workingFilter);
  const filter = counts.size > 1 || workingFilter !== 'all'
    ? `<select data-act="w-filter" aria-label="Account" class="ml-2 px-1 py-0.5 rounded border border-gray-600 bg-transparent text-xs text-gray-200">
        <option value="all">All accounts (${working.rows.length})</option>
        ${accounts.filter(a => counts.has(a.id)).map(a => `<option value="${a.id}" ${a.id === workingFilter ? 'selected' : ''}>${esc(accountName(a))} (${counts.get(a.id)})</option>`).join('')}
      </select>`
    : (working.rows.length ? `<span class="ml-2 text-xs text-gray-400">${esc(working.rows[0].account)}</span>` : '');
  const more = matching.length - workingShown;
  const rows = matching.slice(0, workingShown).map(w => {
    const id = esc(w.key);
    const off = busy.has(w.key) ? 'disabled' : '';
    const change = w.canChange
      ? `<input type="number" min="0" step="0.05" data-act="w-price" data-id="${id}" value="${w.price ?? ''}" aria-label="New limit" class="w-20 px-1 py-0.5 rounded border border-gray-600 bg-transparent text-right">
          <span class="text-xs text-gray-500">${esc(w.effect || '')}</span>
          <button type="button" data-act="w-replace" data-id="${id}" class="px-2 py-0.5 rounded border border-gray-600 disabled:opacity-40" ${off}>Replace</button>`
      : `<span class="text-xs text-gray-500">${esc(w.reason || '')}</span>`;
    const controls = w.canChange || w.canCancel
      ? `<div class="flex items-center gap-2 mt-1">
          ${change}
          ${w.canCancel ? `<button type="button" data-act="w-cancel" data-id="${id}" class="px-2 py-0.5 rounded border border-gray-600 text-red-400 disabled:opacity-40" ${off}>Cancel</button>` : ''}
        </div>`
      : `<div class="text-xs text-gray-500 mt-1">${esc(w.reason || '')}</div>`;
    return `<div class="py-1.5 border-b border-gray-700 last:border-0">
      <div class="flex gap-2"><span class="font-semibold">${w.price != null ? `$${w.price.toFixed(2)} ${esc(w.effect)}` : ''}</span><span class="ml-auto text-xs text-gray-500">${esc(w.status)}</span></div>
      <div class="text-xs text-gray-500">${esc(w.from)}${counts.size > 1 && workingFilter === 'all' ? ` · ${esc(w.account)}` : ''}</div>
      ${w.lines.map(l => `<div class="text-xs tabular-nums text-gray-300">${esc(l)}</div>`).join('')}
      ${controls}</div>`;
  }).join('');
  const note = working.note;
  workingEl.innerHTML = `
    <div class="flex items-center mb-1"><span class="font-semibold">Working orders</span>${filter}
      <button type="button" data-act="w-reload" class="ml-auto text-xs text-gray-400 hover:text-gray-200">Refresh</button></div>
    ${note ? `<div class="mb-1 text-xs ${note.tone === 'ok' ? 'text-green-400' : note.tone === 'error' ? 'text-red-400' : 'text-gray-400'}">${esc(note.text)}</div>` : ''}
    ${working.error ? `<p class="text-xs text-gray-500">${esc(working.error)}</p>`
      : ''}
    ${rows || '<p class="text-xs text-gray-500">None.</p>'}
    ${more > 0 ? `<button type="button" data-act="w-more" class="mt-1 text-xs text-blue-400 hover:underline">Show ${Math.min(more, WORKING_PAGE)} more (${more} not shown)</button>` : ''}`;
  for (const [id, value] of Object.entries(typed)) {
    const inp = [...workingEl.querySelectorAll('[data-act="w-price"]')].find(e => e.dataset.id === id);
    if (inp) { inp.value = value; inp.dataset.edited = '1'; }
  }
  if (focus) {
    const el = [...workingEl.querySelectorAll(`[data-act="${focus.act}"]`)].find(e => e.dataset.id === focus.id);
    if (el) el.focus();
  }
}

async function changeWorking(act, id) {
  const row = working.rows.find(w => w.key === id);
  if (!row || busy.has(id)) return;
  const body = { account_id: row.accountId, order_id: row.id };
  if (act === 'w-replace') {
    const inp = [...workingEl.querySelectorAll('[data-act="w-price"]')].find(e => e.dataset.id === id);
    body.price = replacePrice(row, inp ? inp.value : '');
    if (body.price == null) { working.note = { tone: 'error', text: 'Set a limit price above 0.' }; renderWorking(); return; }
  }
  const verb = act === 'w-replace' ? 'replace' : 'cancel';
  busy.add(id);
  working.note = { tone: 'muted', text: verb === 'replace' ? 'Replacing…' : 'Cancelling…' };
  renderWorking();
  try {
    const r = await fetch(`/api/trade/${verb}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const d = await r.json();
    if (d.status !== 'success') throw new Error(d.message || `HTTP ${r.status}`);
    working.note = { tone: d.ok ? 'ok' : 'error', text: d.message };
  } catch (err) {
    working.note = { tone: 'error', text: `Couldn't ${verb} the order: ${err.message}` };
  }
  busy.delete(id);
  const inp = [...workingEl.querySelectorAll('[data-act="w-price"]')].find(e => e.dataset.id === id);
  if (inp) delete inp.dataset.edited;   // the list reloads with the price as it now stands
  await loadWorking();
}

workingEl.addEventListener('change', e => {
  if (e.target.dataset.act !== 'w-filter') return;
  workingFilter = e.target.value === 'all' ? 'all' : Number(e.target.value);
  workingShown = WORKING_PAGE;
  renderWorking();
});
workingEl.addEventListener('input', e => { if (e.target.dataset.act === 'w-price') e.target.dataset.edited = '1'; });
workingEl.addEventListener('click', async e => {
  const b = e.target.closest('[data-act]');
  if (!b) return;
  if (b.dataset.act === 'w-reload') await loadWorking();
  else if (b.dataset.act === 'w-more') { workingShown += WORKING_PAGE; renderWorking(); }
  else if (b.dataset.act === 'w-replace' || b.dataset.act === 'w-cancel') await changeWorking(b.dataset.act, b.dataset.id);
});

async function addAlert() {
  const dir = ticketEl.querySelector('[data-act="alert-dir"]').value;
  const level = Number(ticketEl.querySelector('[data-act="alert-level"]').value);
  if (!(level > 0)) { result = { tone: 'error', text: 'Set the price for the alert.' }; render(); return; }
  try {
    const r = await fetch('/api/alerts', { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ kind: 'spread', symbol: ctx.ticker, contracts: alertContracts(ctx.tray), direction: dir, threshold: level, source: 'trade' }) });
    const d = await r.json();
    if (d.status !== 'success') throw new Error(d.message || `HTTP ${r.status}`);
    result = { tone: 'ok', text: `Alert set: this spread ${dir} ${fmtP(level)}. See Alerts.` };
  } catch (err) {
    result = { tone: 'error', text: `Couldn't set the alert: ${err.message}` };
  }
  render();
}

document.getElementById('trade-symbol').addEventListener('submit', e => {
  e.preventDefault();
  const t = new FormData(e.target).get('ticker').toString().trim().toUpperCase();
  if (!t) return;
  history.replaceState(null, '', `/trade?ticker=${encodeURIComponent(t)}`);
  stopGraph();
  panel.setTicker(t);
});

// Idea ☆: the ticket as it stands. A limit you typed is the idea's entry; one Review froze from
// the mids isn't (the idea takes the mid then).
installIdeaSave(doc, window, () => ({ tray: ctx.tray, ticker: ctx.ticker, price: autoPrice ? null : price,
  effect: side(), mult: ctx.mult, structureLabel: null }));

// Trade it: the idea's legs go in the way the tour's do, its entry in the limit, and the banner says so.
function openIdea(saved) {
  panel.setTray(ideaTray(saved));
  idea = { id: saved.id, sig: legSig(ctx.tray) };
  price = saved.price;
  effect = saved.effect;
  if (ideaBannerEl) { ideaBannerEl.textContent = ideaBanner(saved); ideaBannerEl.hidden = false; }
  render();
}

render();
renderWorking();
loadWorking();
setInterval(() => { if (!doc.hidden) loadWorking(); }, 30000);
{
  const first = document.querySelector('#trade-symbol [name=ticker]').value.trim().toUpperCase();
  const tour = tourTray(document.getElementById('trade-ticket'));   // demo tour, step 4 only
  const saved = pageIdea(ticketEl);   // Trade it from the Journal's Ideas
  const opened = panel.setTicker(first);
  // setTicker clears the tray and resolves once the expirations are in, so the spread goes in after.
  if (tour && first === tour.ticker) opened.then(() => panel.setTray(tourTrayFor(tour)));
  else if (saved && first === saved.ticker) opened.then(() => openIdea(saved));
}
