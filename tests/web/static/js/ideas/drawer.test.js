import { test } from 'node:test';
import assert from 'node:assert/strict';
import { installIdeasDrawer } from '../../../../../src/optionsjourney/web/static/js/ideas/drawer.js';

// A stand-in page: elements that remember listeners, html and classes; just what the drawer asks of them.
function el(extra = {}) {
  const listeners = {};
  const classes = new Set(extra.classes || []);
  return {
    dataset: {}, listeners, hidden: false, textContent: '', innerHTML: '', value: '',
    classList: { add: c => classes.add(c), remove: c => classes.delete(c), contains: c => classes.has(c),
      toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)) },
    addEventListener(t, fn) { (listeners[t] ||= []).push(fn); },
    setAttribute() {}, focus() {}, querySelector: () => null, querySelectorAll: () => [],
    ...extra,
  };
}

const IDEA = {
  id: 7, ticker: 'SPX', label: 'SPX <b>Condor</b>', note: 'wait', status: 'live', settlement_na: false,
  created_at: '2026-10-23T14:00:00+00:00', spot_at_entry: 6612.5, pnl: 120, pnl_pct: 6.2,
  cards: [{ label: 'SPX Condor', qty: 2, entry: 1.9, locked: true, now: 1.3, pnl: 120 }],
};

function page(ideas, { prompt } = {}) {
  const calls = [], intervals = [];
  const backdrop = el(), panel = el(), closeBtn = el();
  const drawer = el({
    classes: ['hidden'],
    dataset: { ideasUrl: '/api/ideas', marketsUrl: '/api/ideas/markets', tradeUrl: '/trade' },
    querySelector: s => ({ '[data-ideas-backdrop]': backdrop, '[data-ideas-panel]': panel, '[data-ideas-close]': closeBtn })[s] || null,
  });
  const opener = el();
  const body = el();
  const els = { 'ideas-drawer': drawer, 'ideas-body': body };
  const doc = {
    getElementById: id => els[id] || null,
    querySelectorAll: s => (s === '[data-ideas-open]' ? [opener] : []),
    addEventListener() {}, visibilityState: 'visible',
  };
  const win = {
    requestAnimationFrame: fn => fn(), setTimeout: () => 0, clearTimeout() {},
    setInterval: (fn, ms) => { intervals.push({ fn, ms, live: true }); return intervals.length; },
    clearInterval: id => { intervals[id - 1].live = false; },
    fetch: async (url, opts = {}) => {
      calls.push({ url, opts });
      const json = url === '/api/ideas?count=1' ? { count: ideas.length }
        : url === '/api/ideas/markets' ? { ideas: {} }
        : url === '/api/ideas' ? { ideas }
        : { status: 'success' };
      return { ok: true, json: async () => json };
    },
    location: { href: '' },
  };
  return { calls, intervals, body, opener, win, doc, drawer, closeBtn };
}

const tick = () => new Promise(r => setImmediate(r));
const open = async p => { installIdeasDrawer(p.doc, p.win); p.opener.listeners.click[0]({ currentTarget: p.opener }); await tick(); await tick(); };
const click = (p, act, id = '7') => p.body.listeners.click.forEach(fn => fn({ target: { closest: s => (s === '[data-act]' ? { dataset: { act, id } } : null) } }));
const writes = p => p.calls.filter(c => c.opts.method);

test('the button shows the count, and nothing when there are no ideas', async () => {
  const p = page([IDEA]);
  installIdeasDrawer(p.doc, p.win);
  await tick(); await tick();
  assert.equal(p.opener.textContent, '💡 Ideas (1)');
  const q = page([]);
  installIdeasDrawer(q.doc, q.win);
  await tick(); await tick();
  assert.equal(q.opener.textContent, '💡 Ideas');
});

test('opening fetches the list; no ideas reads the empty state', async () => {
  const p = page([]);
  await open(p);
  assert.ok(p.calls.some(c => c.url === '/api/ideas'));
  assert.match(p.body.innerHTML, /No ideas yet\. Build a trade on the Trade page and press <strong>Idea ☆<\/strong>\./);
});

test('an idea renders with its lock, chip, P/L and escaped text', async () => {
  const p = page([IDEA]);
  await open(p);
  assert.match(p.body.innerHTML, /🔒/);
  assert.match(p.body.innerHTML, /Live/);
  assert.match(p.body.innerHTML, /\+\$120\.00/);
  assert.match(p.body.innerHTML, /1\.90 cr/);
  assert.match(p.body.innerHTML, /SPX &lt;b&gt;Condor&lt;\/b&gt;/);
  assert.doesNotMatch(p.body.innerHTML, /<b>Condor/);
  assert.match(p.body.innerHTML, /Trade it ↗/);
  assert.match(p.body.innerHTML, /idea-chip--live/);
  assert.match(p.body.innerHTML, /<th>Card<\/th>/);
});

test('the refresh button reloads the list', async () => {
  const p = page([IDEA]);
  const refresh = el();
  const q = p.drawer.querySelector;
  p.drawer.querySelector = s => (s === '[data-ideas-refresh]' ? refresh : q(s));
  await open(p);
  const before = p.calls.filter(c => c.url === '/api/ideas').length;
  refresh.listeners.click[0]();
  await tick(); await tick();
  assert.equal(p.calls.filter(c => c.url === '/api/ideas').length, before + 1);
});

test('Trade it is hidden unless the idea is live', async () => {
  const p = page([{ ...IDEA, status: 'expired' }, { ...IDEA, id: 8, status: 'partly_expired' }]);
  await open(p);
  assert.equal((p.body.innerHTML.match(/Trade it/g) || []).length, 0);
  assert.match(p.body.innerHTML, /Expired/);
});

test('Trade it goes to the Trade page with the idea and ticker', async () => {
  const p = page([IDEA]);
  await open(p);
  click(p, 'trade');
  assert.equal(p.win.location.href, '/trade?idea=7&ticker=SPX');
});

test('Delete needs the inline Confirm, then removes and recounts', async () => {
  const p = page([IDEA]);
  await open(p);
  click(p, 'delete');
  assert.equal(writes(p).length, 0);
  assert.match(p.body.innerHTML, /Confirm delete/);
  click(p, 'delete-keep');
  assert.doesNotMatch(p.body.innerHTML, /Confirm delete/);
  click(p, 'delete');
  click(p, 'delete-confirm');
  await tick(); await tick();
  assert.equal(writes(p).length, 1);
  assert.equal(writes(p)[0].url, '/api/ideas/7');
  assert.equal(writes(p)[0].opts.method, 'DELETE');
  assert.ok(p.calls.filter(c => c.url === '/api/ideas?count=1').length >= 2);
});

test('the note edits inline: Save patches, empty clears, Cancel posts nothing', async () => {
  const p = page([IDEA]);
  await open(p);
  click(p, 'note');
  assert.match(p.body.innerHTML, /<input[^>]*value="wait"[^>]*data-act="note-input"/);
  click(p, 'note-cancel');
  assert.equal(writes(p).length, 0);
  click(p, 'note');
  p.body.listeners.input.forEach(fn => fn({ target: { dataset: { act: 'note-input' }, value: '' } }));
  click(p, 'note-save');
  await tick(); await tick();
  assert.equal(writes(p)[0].url, '/api/ideas/7');
  assert.equal(writes(p)[0].opts.method, 'PATCH');
  assert.deepEqual(JSON.parse(writes(p)[0].opts.body), { note: '' });
});

test('markets poll every 3 s while open, and stop when it closes', async () => {
  const p = page([IDEA]);
  await open(p);
  const poll = p.intervals.find(i => i.ms === 3000 && i.live);
  assert.ok(poll);
  poll.fn();
  await tick();
  const m = p.calls.find(c => c.url === '/api/ideas/markets');
  assert.ok(m && m.opts.signal, 'the request can be aborted');
  p.closeBtn.listeners.click[0]();
  assert.ok(!p.intervals.some(i => i.ms === 3000 && i.live));
});

test('an id the markets answer does not know reloads the list', async () => {
  const p = page([IDEA]);
  const real = p.win.fetch;
  p.win.fetch = async (url, opts) => (url === '/api/ideas/markets'
    ? (p.calls.push({ url, opts }), { ok: true, json: async () => ({ ideas: { 99: { pnl: 1, pnl_pct: 1, cards: [] } } }) })
    : real(url, opts));
  await open(p);
  const before = p.calls.filter(c => c.url === '/api/ideas').length;
  p.intervals.find(i => i.ms === 3000).fn();
  await tick(); await tick();
  assert.equal(p.calls.filter(c => c.url === '/api/ideas').length, before + 1);
});

test('a markets answer rewrites the live cells in place', async () => {
  const p = page([IDEA]);
  const real = p.win.fetch;
  p.win.fetch = async (url, opts) => (url === '/api/ideas/markets'
    ? { ok: true, json: async () => ({ ideas: { 7: { pnl: -30, pnl_pct: -1.5, cards: [{ now: 2.2, pnl: -30 }] } } }) }
    : real(url, opts));
  const cells = { '7': el({ dataset: { live: '7' } }), '7:pct': el({ dataset: { live: '7:pct' } }), '7:0:now': el({ dataset: { live: '7:0:now' } }) };
  p.body.querySelectorAll = () => Object.values(cells);
  await open(p);
  p.intervals.find(i => i.ms === 3000).fn();
  await tick(); await tick();
  assert.equal(cells['7'].textContent, '−$30.00');
  assert.equal(cells['7'].className, 'idea-pnl negative');
  assert.equal(cells['7:pct'].textContent, '−1.5%');
  assert.equal(cells['7:0:now'].textContent, '2.20 cr');
});
