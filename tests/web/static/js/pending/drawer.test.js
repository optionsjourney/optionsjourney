import { test } from 'node:test';
import assert from 'node:assert/strict';
import { installPendingDrawer } from '../../../../../src/optionsjourney/web/static/js/pending/drawer.js';

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

const ORDER = {
  order_id: 'o1', status: 'WORKING', legs: [{ symbol: 'SPXW  261023C07645000', side: 'sell', opening: true, quantity: 1, remaining: 1 }],
  entered_at: '2026-10-23T13:47:00+00:00', yours: true, price: 1.25,
  owner: { kind: 'you', name: null, link: null }, can_change: true, can_cancel: true, reason: null, category: '0dte',
};

function page(listing) {
  const calls = [];
  const backdrop = el(), panel = el(), closeBtn = el(), refreshBtn = el();
  const drawer = el({
    classes: ['hidden'],
    dataset: { pendingUrl: '/api/pending', marketsUrl: '/api/pending/markets', replaceUrl: '/api/trade/replace', cancelUrl: '/api/trade/cancel' },
    querySelector: s => ({ '[data-pending-backdrop]': backdrop, '[data-pending-panel]': panel, '[data-pending-close]': closeBtn, '[data-pending-refresh]': refreshBtn })[s] || null,
    querySelectorAll: () => [],
  });
  const opener = el();
  const body = el(), chips = el(), outside = el(), sub = el();
  const els = { 'pending-drawer': drawer, 'pending-body': body, 'pending-chips': chips, 'pending-outside': outside, 'pending-subtitle': sub };
  const doc = {
    getElementById: id => els[id] || null,
    querySelectorAll: s => (s === '[data-pending-open]' ? [opener] : []),
    addEventListener() {}, visibilityState: 'visible',
  };
  const win = {
    localStorage: { getItem: () => null, setItem() {} },
    requestAnimationFrame: fn => fn(), setTimeout: fn => 0, clearTimeout() {}, setInterval: () => 1, clearInterval() {},
    fetch: async (url, opts) => {
      calls.push({ url, opts });
      const json = url.startsWith('/api/pending?count') ? { count: 1, errors: 0 }
        : url.startsWith('/api/pending/markets') ? { marks: {} }
        : url.startsWith('/api/pending') ? (typeof listing === 'function' ? listing() : listing)
        : { status: 'success', ok: true, message: 'done' };
      return { ok: true, json: async () => json };
    },
  };
  return { calls, body, opener, win, doc, drawer, refreshBtn };
}

const tick = () => new Promise(r => setImmediate(r));
const click = (target, matcher) => target.listeners.click.forEach(fn => fn({ target: { closest: s => matcher(s) } }));

test('opening fetches the list; nothing pending reads Nothing pending.', async () => {
  const p = page({ accounts: [], count: 0, errors: 0, as_of: 'x' });
  installPendingDrawer(p.doc, p.win);
  p.opener.listeners.click[0]({ currentTarget: p.opener });
  await tick(); await tick();
  assert.ok(p.calls.some(c => c.url === '/api/pending'));
  assert.match(p.body.innerHTML, /Nothing pending\./);
});

test('a failing account shows its error line', async () => {
  const p = page({ accounts: [{ account_id: 1, broker: 'SCHWAB', label: 'Main', orders: [], error: "Couldn't reach Schwab" }], count: 0, errors: 1, as_of: 'x' });
  installPendingDrawer(p.doc, p.win);
  p.opener.listeners.click[0]({ currentTarget: p.opener });
  await tick(); await tick();
  assert.match(p.body.innerHTML, /Couldn&#39;t reach Schwab|Couldn't reach Schwab/);
  assert.doesNotMatch(p.body.innerHTML, /Nothing pending\./);
});

test('Cancel needs the inline Confirm before anything is posted', async () => {
  const p = page({ accounts: [{ account_id: 1, broker: 'SCHWAB', label: 'Main', orders: [ORDER], error: null }], count: 1, errors: 0, as_of: 'x' });
  installPendingDrawer(p.doc, p.win);
  p.opener.listeners.click[0]({ currentTarget: p.opener });
  await tick(); await tick();
  const posts = () => p.calls.filter(c => c.opts && c.opts.method === 'POST');
  const btn = (act) => ({ dataset: { act, id: 'o1', account: '1' } });
  click(p.body, s => (s === '[data-act]' ? btn('cancel') : null));
  assert.equal(posts().length, 0);
  assert.match(p.body.innerHTML, /This cancels the order at your broker\./);
  click(p.body, s => (s === '[data-act]' ? btn('cancel-keep') : null));
  assert.equal(posts().length, 0);
  assert.doesNotMatch(p.body.innerHTML, /This cancels the order/);
  click(p.body, s => (s === '[data-act]' ? btn('cancel') : null));
  click(p.body, s => (s === '[data-act]' ? btn('cancel-confirm') : null));
  await tick(); await tick();
  assert.equal(posts().length, 1);
  assert.equal(posts()[0].url, '/api/trade/cancel');
  assert.deepEqual(JSON.parse(posts()[0].opts.body), { account_id: 1, order_id: 'o1' });
  assert.match(p.body.innerHTML, /updating…/);
});

const opened = async listing => {
  const p = page({ accounts: [{ account_id: 1, broker: 'SCHWAB', label: 'Main', orders: listing, error: null }], count: 1, errors: 0, as_of: 'x' });
  installPendingDrawer(p.doc, p.win);
  p.opener.listeners.click[0]({ currentTarget: p.opener });
  await tick(); await tick();
  return p;
};
const btn = act => s => (s === '[data-act]' ? { dataset: { act, id: 'o1', account: '1' } } : null);
const type = (p, value) => p.body.listeners.input.forEach(fn => fn({ target: { dataset: { act: 'price' }, value } }));
const posts = p => p.calls.filter(c => c.opts && c.opts.method === 'POST');

test('Change price posts a positive price for a credit order', async () => {
  const p = await opened([ORDER]);
  click(p.body, btn('change'));
  type(p, '1.50');
  click(p.body, btn('change-confirm'));
  await tick(); await tick();
  assert.equal(posts(p)[0].url, '/api/trade/replace');
  assert.deepEqual(JSON.parse(posts(p)[0].opts.body), { account_id: 1, order_id: 'o1', price: 1.5 });
});

test('Change price posts a negative price for a debit order', async () => {
  const p = await opened([{ ...ORDER, price: -2 }]);
  click(p.body, btn('change'));
  type(p, '1.75');
  click(p.body, btn('change-confirm'));
  await tick(); await tick();
  assert.equal(JSON.parse(posts(p)[0].opts.body).price, -1.75);
});

test('a bad price shows the note and posts nothing', async () => {
  const p = await opened([ORDER]);
  click(p.body, btn('change'));
  type(p, '0');
  click(p.body, btn('change-confirm'));
  await tick();
  assert.equal(posts(p).length, 0);
  assert.match(p.body.innerHTML, /Set a limit price above 0\./);
});

test('an order with no price offers no Change price', async () => {
  const p = await opened([{ ...ORDER, price: null }]);
  assert.doesNotMatch(p.body.innerHTML, /Change price/);
});

test('an owner link that is not a local path is not a link', async () => {
  const p = await opened([{ ...ORDER, owner: { kind: 'bot', name: 'Bot', link: '//evil.example' }, can_change: false, can_cancel: false }]);
  assert.doesNotMatch(p.body.innerHTML, /href="\/\/evil/);
});

test('no markets fetch while the tab is hidden', async () => {
  const p = page({ accounts: [{ account_id: 1, broker: 'SCHWAB', label: 'Main', orders: [ORDER], error: null }], count: 1, errors: 0, as_of: 'x' });
  p.doc.visibilityState = 'hidden';
  installPendingDrawer(p.doc, p.win);
  p.opener.listeners.click[0]({ currentTarget: p.opener });
  await tick(); await tick();
  assert.ok(p.calls.some(c => c.url === '/api/pending'));
  assert.ok(!p.calls.some(c => c.url.startsWith('/api/pending/markets')));
});

test('the refresh button reloads the list now', async () => {
  const p = await opened([ORDER]);
  const lists = () => p.calls.filter(c => c.url === '/api/pending').length;
  const before = lists();
  p.refreshBtn.listeners.click.forEach(fn => fn({}));
  await tick(); await tick();
  assert.equal(lists(), before + 1);
});

test('confirming on an order that went away after a reload posts nothing', async () => {
  let orders = [ORDER];
  const p = page(() => ({ accounts: [{ account_id: 1, broker: 'SCHWAB', label: 'Main', orders, error: null }], count: orders.length, errors: 0, as_of: 'x' }));
  installPendingDrawer(p.doc, p.win);
  p.opener.listeners.click[0]({ currentTarget: p.opener });
  await tick(); await tick();
  click(p.body, btn('change'));
  orders = [];
  p.refreshBtn.listeners.click.forEach(fn => fn({}));
  await tick(); await tick();
  click(p.body, btn('change-confirm'));
  await tick();
  assert.equal(posts(p).length, 0);
});

test('rows read like the order: badge, coloured sides, outlined actions; the header says as of', async () => {
  const p = page({ accounts: [{ account_id: 1, broker: 'TASTYTRADE', label: 'Main', error: null,
    orders: [ORDER, { ...ORDER, order_id: 'o2', owner: { kind: 'outside', name: null, link: null }, can_change: false, can_cancel: false,
      reason: 'Placed outside the app.' }] }], count: 2, errors: 0, as_of: '2026-10-23T14:42:00+00:00' });
  const status = el();
  const get = p.doc.getElementById;
  p.doc.getElementById = id => (id === 'pending-status' ? status : get(id));
  installPendingDrawer(p.doc, p.win);
  p.opener.listeners.click[0]({ currentTarget: p.opener });
  await tick(); await tick();
  const html = p.body.innerHTML;
  assert.equal(status.textContent, 'as of 10:42');
  assert.match(html, /Main · TastyTrade/);
  assert.match(html, /pending-badge--sell">STO</);
  assert.match(html, />Oct 23</);
  assert.match(html, /pending-side--cr">cr</);
  assert.match(html, /mini-btn--blue[^>]*>Change price/);
  assert.match(html, /mini-btn--red[^>]*>Cancel/);
  assert.match(html, /pending-tag">Outside the app/);
  assert.match(html, /Placed outside the app\./);
});

test('the toolbar button turns red when an account failed', async () => {
  const p = page({ accounts: [], count: 0, errors: 0, as_of: 'x' });
  p.win.fetch = async url => ({ ok: true, json: async () => ({ count: 2, errors: 1 }) });
  installPendingDrawer(p.doc, p.win);
  await tick(); await tick();
  assert.equal(p.opener.textContent, '⚠ 2 pending');
  assert.ok(p.opener.classList.contains('toolbar-pill--warn'));
});
