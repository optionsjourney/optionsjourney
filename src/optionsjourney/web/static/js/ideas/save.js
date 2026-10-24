// Trade Ideas (#292): saving the Trade page's ticket as an idea (Idea ☆), and reading back the
// idea the page was opened with (Trade it, /trade?idea=<id>). The server checks and stores it
// (POST /api/ideas); nothing here places an order.
import { legMid, legKey, setQty, toggleCell, emptyTray } from '../trade/chain_tray.js';
import { units, readableSymbol } from '../trade/ticket_core.js';

export const SAVED = 'Saved to Ideas';
export const NO_PRICE = 'Wait for prices, or type a limit price.';

const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const r2 = n => Math.round(n * 100) / 100;

// A chain stream symbol as the OCC symbol ideas keep (".SPXW261023P6600" -> "SPXW  261023P06600000");
// null for anything else (a futures option's "./ESZ26P7000:XCME").
export function occFromStream(stream) {
  const m = /^\.([A-Z0-9]{1,6})(\d{6})([CP])(\d+(?:\.\d+)?)$/.exec(stream || '');
  if (!m) return null;
  const [, root, yymmdd, right, strike] = m;
  return `${root.padEnd(6)}${yymmdd}${right}${String(Math.round(Number(strike) * 1000)).padStart(8, '0')}`;
}

// The ticket as an idea {ticker, label, cards}. A typed limit is the entry, signed by its effect
// (credit +, debit −) and locked; without one the entry is the tray's mid now (sells +, buys −,
// times each ratio), not locked. null when there's no price to save or a leg can't be an idea.
export function ideaFromTicket(tray, ticker, price, effect, mult, structureLabel) {
  if (!tray.legs.length) return null;
  const u = units(tray);
  const legs = tray.legs.map(l => ({ symbol: occFromStream(l.stream_symbol), side: l.side, ratio: l.qty / u }));
  if (legs.some(l => !l.symbol)) return null;
  const typed = Number(price);
  let entry, locked;
  if (price != null && price !== '' && typed > 0) {
    entry = effect === 'debit' ? -typed : typed;
    locked = true;
  } else if (tray.legs.every(l => Number(l.ask) > 0)) {
    entry = tray.legs.reduce((s, l) => s + (l.side === 'sell' ? 1 : -1) * (l.qty / u) * legMid(l), 0);
    locked = false;
  } else return null;
  const label = structureLabel ? `${ticker} ${structureLabel}` : ticker;
  return { ticker, label, cards: [{ label, qty: u, entry_unit: r2(entry), locked, multiplier: mult || 100, legs }] };
}

// The dialog's read-only lines: each leg, then the size and the entry.
function summaryHtml(idea) {
  const card = idea.cards[0];
  const legs = card.legs.map(l => `<div>${l.side === 'sell' ? 'Sell' : 'Buy'} ${card.qty * l.ratio} ${esc(readableSymbol(l.symbol))}</div>`).join('');
  const e = card.entry_unit;
  const entry = `${Math.abs(e).toFixed(2)} ${e < 0 ? 'debit' : 'credit'}`;
  return `${legs}<div class="mt-1 text-gray-400">×${card.qty} at ${entry} ${card.locked ? '(your limit)' : '(the mid now)'}</div>`;
}

// The idea the server put in data-idea on #trade-ticket, or null.
export function pageIdea(el) {
  const raw = el && el.dataset && el.dataset.idea;
  if (!raw) return null;
  try {
    const v = JSON.parse(raw);
    return v && Number.isInteger(v.id) && Array.isArray(v.tray) ? v : null;
  } catch { return null; }
}

// The idea's legs as the chain's tray: the cells in order, each at its quantity.
export function ideaTray(idea) {
  const tray = idea.tray.reduce((t, cell) => toggleCell(t, cell), emptyTray());
  return idea.tray.reduce((t, cell) => setQty(t, legKey(cell), cell.qty), tray);
}

const ET = { timeZone: 'America/New_York', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' };

export function ideaBanner(idea) {
  const when = `${new Date(idea.created_at).toLocaleString('en-US', ET)} ET`;
  return `Idea · ${idea.label} · saved ${when}. Trading it removes it from Ideas.`;
}

// Idea ☆ ([data-act="idea-open"], drawn in the ticket) opens #idea-save with the ticket as an
// idea: its name (editable), an optional note and a summary. getTicket() gives
// {tray, ticker, price (typed, or null), effect, mult, structureLabel}.
export function installIdeaSave(doc, win, getTicket) {
  const box = doc.getElementById('idea-save');
  if (!box) return null;
  const part = name => box.querySelector(`[data-idea-${name}]`);
  const label = part('label'), note = part('note'), summary = part('summary');
  const submit = part('submit'), status = part('status');
  let idea = null, saving = false;

  function open() {
    const t = getTicket();
    idea = ideaFromTicket(t.tray, t.ticker, t.price, t.effect, t.mult, t.structureLabel);
    label.value = idea ? idea.label : (t.ticker || '');
    note.value = '';
    summary.innerHTML = idea ? summaryHtml(idea) : '';
    status.textContent = idea ? '' : NO_PRICE;
    submit.disabled = !idea;
    box.hidden = false;
    label.focus();
  }

  function close() { box.hidden = true; idea = null; }

  async function save() {
    if (!idea || saving) return;
    const name = label.value.trim();
    const body = { ...idea, label: name, note: note.value, cards: [{ ...idea.cards[0], label: name }] };
    saving = true;
    submit.disabled = true;
    status.textContent = 'Saving…';
    let saved = false;
    try {
      const r = await win.fetch('/api/ideas', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      const d = await r.json();
      saved = d.status === 'success';
      status.textContent = saved ? SAVED : (d.message || `Couldn't save it (HTTP ${r.status}).`);
    } catch (err) {
      status.textContent = `Couldn't save it: ${err.message}`;
    }
    saving = false;
    submit.disabled = saved;   // saved once: open it again to save another
  }

  doc.addEventListener('click', e => { if (e.target.closest('[data-act="idea-open"]')) open(); });
  box.addEventListener('click', async e => {
    if (e.target.closest('[data-act="idea-cancel"]')) close();
    else if (e.target.closest('[data-act="idea-submit"]')) await save();
  });
  return { open, close };
}
