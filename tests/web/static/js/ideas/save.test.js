import test from 'node:test';
import assert from 'node:assert/strict';
import { ideaFromTicket, occFromStream, pageIdea, ideaBanner, ideaTray, installIdeaSave, NO_PRICE }
  from '../../../../../src/optionsjourney/web/static/js/ideas/save.js';

const leg = over => ({ expiration: '2026-10-23', strike: 6600, type: 'put', side: 'sell', qty: 2,
  bid: 3.0, ask: 3.2, stream_symbol: '.SPXW261023P6600', delta: -0.2, ...over });
const spread = { legs: [leg({}), leg({ strike: 6550, side: 'buy', qty: 4, bid: 0.9, ask: 1.1, stream_symbol: '.SPXW261023P6550' })] };

test('occFromStream pads the root and writes the strike in thousandths', () => {
  assert.equal(occFromStream('.SPXW261023P6600'), 'SPXW  261023P06600000');
  assert.equal(occFromStream('.SPY261023C512.5'), 'SPY   261023C00512500');
  assert.equal(occFromStream('./ESZ26P7000:XCME'), null);
  assert.equal(occFromStream(''), null);
});

test('ideaFromTicket with a typed price: that price, signed by effect, locked', () => {
  const idea = ideaFromTicket(spread, 'SPX', 1.25, 'debit', 100, null);
  assert.deepEqual(idea, { ticker: 'SPX', label: 'SPX', cards: [{
    label: 'SPX', qty: 2, entry_unit: -1.25, locked: true, multiplier: 100,
    legs: [{ symbol: 'SPXW  261023P06600000', side: 'sell', ratio: 1 },
      { symbol: 'SPXW  261023P06550000', side: 'buy', ratio: 2 }],
  }] });
  assert.equal(ideaFromTicket(spread, 'SPX', 1.25, 'credit', 100, null).cards[0].entry_unit, 1.25);
});

test('ideaFromTicket without a price: the tray mid, unlocked; the structure name in the label', () => {
  const idea = ideaFromTicket(spread, 'SPX', null, 'credit', 100, 'Put ratio');
  assert.equal(idea.label, 'SPX Put ratio');
  assert.equal(idea.cards[0].entry_unit, 1.1);   // 3.10 x 1 - 1.00 x 2
  assert.equal(idea.cards[0].locked, false);
});

test('ideaFromTicket gives null with no price to save, or a leg that cannot be an idea', () => {
  const unquoted = { legs: [leg({ ask: null, bid: null })] };
  assert.equal(ideaFromTicket(unquoted, 'SPX', null, 'credit', 100, null), null);
  assert.equal(ideaFromTicket(unquoted, 'SPX', 0, 'credit', 100, null), null);
  assert.ok(ideaFromTicket(unquoted, 'SPX', 2, 'credit', 100, null));
  assert.equal(ideaFromTicket({ legs: [] }, 'SPX', 2, 'credit', 100, null), null);
  const future = { legs: [leg({ stream_symbol: './ESZ26P7000:XCME' })] };
  assert.equal(ideaFromTicket(future, '/ES', 2, 'credit', 50, null), null);
  assert.equal(NO_PRICE, 'Wait for prices, or type a limit price.');
});

test('pageIdea reads data-idea and ignores junk', () => {
  assert.deepEqual(pageIdea({ dataset: { idea: '{"id":4,"tray":[]}' } }), { id: 4, tray: [] });
  assert.equal(pageIdea({ dataset: { idea: 'nope' } }), null);
  assert.equal(pageIdea({ dataset: {} }), null);
  assert.equal(pageIdea(null), null);
});

test('ideaTray puts the cells in the tray at their quantities', () => {
  const cell = (strike, side, qty) => ({ expiration: '2030-12-20', strike, type: 'put', side, qty, bid: null, ask: null,
    stream_symbol: `.SPXW301220P${strike}`, delta: null });
  const tray = ideaTray({ tray: [cell(5800, 'sell', 2), cell(5790, 'buy', 4)] });
  assert.deepEqual(tray.legs.map(l => [l.strike, l.side, l.qty]), [[5800, 'sell', 2], [5790, 'buy', 4]]);
});

test('the banner says what trading it does, with the saved time in New York', () => {
  const text = ideaBanner({ label: 'Fly', created_at: '2026-10-23T18:05:00+00:00' });
  assert.equal(text, 'Idea · Fly · saved Oct 23, 2:05 PM ET. Trading it removes it from Ideas.');
});

// A stand-in page: elements that remember listeners and what was written to them.
function el(extra = {}) {
  const listeners = {};
  return { dataset: {}, listeners, hidden: true, disabled: false, textContent: '', innerHTML: '', value: '',
    addEventListener(t, fn) { (listeners[t] ||= []).push(fn); }, focus() {}, ...extra };
}

function page(ticket, answer = { status: 201, json: { status: 'success', idea: { id: 9 } } }) {
  const parts = { label: el(), note: el(), summary: el(), submit: el(), cancel: el(), status: el() };
  const box = el({ querySelector: s => parts[/data-idea-(\w+)/.exec(s)[1]] || null });
  const docListeners = {};
  const doc = { getElementById: id => (id === 'idea-save' ? box : null),
    addEventListener(t, fn) { (docListeners[t] ||= []).push(fn); } };
  const calls = [];
  const win = { fetch: async (url, opts) => { calls.push({ url, opts }); return { ok: answer.status < 300, status: answer.status, json: async () => answer.json }; } };
  installIdeaSave(doc, win, () => ticket);
  const target = act => ({ closest: s => (s === `[data-act="${act}"]` ? {} : null) });
  const click = async act => {
    for (const fn of docListeners.click || []) await fn({ target: target(act) });
    for (const fn of box.listeners.click || []) await fn({ target: target(act) });
  };
  return { parts, box, calls, click };
}

const TICKET = { tray: spread, ticker: 'SPX', price: 1.25, effect: 'debit', mult: 100, structureLabel: null };

test('Idea ☆ opens the dialog prefilled; Save posts it and says Saved to Ideas', async () => {
  const p = page(TICKET);
  await p.click('idea-open');
  assert.equal(p.box.hidden, false);
  assert.equal(p.parts.label.value, 'SPX');
  assert.match(p.parts.summary.innerHTML, /Sell 2 SPXW 23 Oct 6600P/);
  assert.match(p.parts.summary.innerHTML, /1\.25 debit/);
  assert.equal(p.parts.submit.disabled, false);
  p.parts.label.value = 'My fly'; p.parts.note.value = 'check Friday';
  await p.click('idea-submit');
  const { url, opts } = p.calls[0];
  assert.equal(url, '/api/ideas');
  assert.equal(opts.method, 'POST');
  const body = JSON.parse(opts.body);
  assert.equal(body.label, 'My fly');
  assert.equal(body.note, 'check Friday');
  assert.equal(body.cards[0].label, 'My fly');
  assert.equal(body.cards[0].entry_unit, -1.25);
  assert.equal(p.parts.status.textContent, 'Saved to Ideas');
});

test('the server refusing it shows its message', async () => {
  const p = page(TICKET, { status: 400, json: { status: 'error', message: "VIX options can't be saved as ideas." } });
  await p.click('idea-open');
  await p.click('idea-submit');
  assert.equal(p.parts.status.textContent, "VIX options can't be saved as ideas.");
});

test('with no price to save, Save is off and says why', async () => {
  const p = page({ ...TICKET, tray: { legs: [leg({ ask: null, bid: null })] }, price: null });
  await p.click('idea-open');
  assert.equal(p.parts.submit.disabled, true);
  assert.equal(p.parts.status.textContent, 'Wait for prices, or type a limit price.');
  await p.click('idea-submit');
  assert.equal(p.calls.length, 0);
});

test('Cancel closes the dialog', async () => {
  const p = page(TICKET);
  await p.click('idea-open');
  await p.click('idea-cancel');
  assert.equal(p.box.hidden, true);
});
