import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buttonLabel, filterOrders, chipCounts, rowView, legView, asOfText, statusText, moneyParts } from '../../../../../src/optionsjourney/web/static/js/pending/model.js';

const leg = { symbol: 'SPXW  261023C07645000', side: 'sell', opening: true, quantity: 1, remaining: 1 };
const OUTSIDE = 'Placed outside the app. Change or cancel it at your broker.';
const order = (id, kind, category, extra = {}) => ({
  order_id: id, status: 'WORKING', legs: [leg], entered_at: '2026-10-23T13:47:00+00:00', yours: kind === 'you',
  price: 1.25, owner: { kind, name: null, link: null }, can_change: kind === 'you',
  can_cancel: kind === 'you', reason: kind === 'outside' ? OUTSIDE : null, category, ...extra,
});
const ACCOUNTS = [
  { account_id: 1, broker: 'SCHWAB', label: 'A', error: null,
    orders: [order('a', 'you', '0dte'), order('b', 'outside', 'calendars'), order('c', 'bot', 'options')] },
  { account_id: 2, broker: 'TASTYTRADE', label: 'B', error: 'Down', orders: [order('d', 'outside', '0dte')] },
];

test('buttonLabel: hidden when nothing, hourglass for orders, warning for errors', () => {
  assert.equal(buttonLabel(0, 0), '');
  assert.equal(buttonLabel(3, 0), '⏳ 3 pending');
  assert.equal(buttonLabel(3, 1), '⚠ 3 pending');
  assert.equal(buttonLabel(0, 2), '⚠ 0 pending');
});

test('chips filter by category, and outside-only keeps the outside orders', () => {
  const ids = f => filterOrders(ACCOUNTS, f, false).flatMap(a => a.orders.map(o => o.order_id));
  assert.deepEqual(ids('all'), ['a', 'b', 'c', 'd']);
  assert.deepEqual(ids('0dte'), ['a', 'd']);
  assert.deepEqual(ids('calendars'), ['b']);
  const out = filterOrders(ACCOUNTS, 'all', true).flatMap(a => a.orders.map(o => o.order_id));
  assert.deepEqual(out, ['b', 'd']);
});

test('an account with an error stays even with no matching orders', () => {
  const r = filterOrders(ACCOUNTS, 'calendars', false);
  assert.deepEqual(r.map(a => a.account_id), [1, 2]);
  assert.equal(r[1].orders.length, 0);
});

test('chipCounts counts every chip, honouring outside-only', () => {
  assert.deepEqual(chipCounts(ACCOUNTS, false), { all: 4, '0dte': 2, calendars: 1, options: 1 });
  assert.deepEqual(chipCounts(ACCOUNTS, true), { all: 2, '0dte': 1, calendars: 1, options: 0 });
});

test('rowView for your own order has both actions and the leg lines', () => {
  const v = rowView(order('a', 'you', '0dte'), -0.5);
  assert.equal(v.from, 'You');
  assert.equal(v.symbol, 'SPXW');
  assert.equal(v.status, 'WORKING');
  assert.equal(v.price, '1.25 cr');
  assert.equal(v.market, '0.50 db');
  assert.deepEqual(v.description, ['Sell 1 SPXW 23 Oct 7645C']);
  assert.match(v.time, /\d/);
  assert.deepEqual(v.actions, { change: true, cancel: true, reason: null });
});

test('rowView for a bot order has no actions and a reason; no mark is a dash', () => {
  const v = rowView(order('c', 'bot', 'options', { reason: 'Managed by Bot, use its controls.',
    owner: { kind: 'bot', name: 'Bot', link: '/automation/3' } }), null);
  assert.equal(v.market, '—');
  assert.equal(v.from, 'Bot');
  assert.equal(v.link, '/automation/3');
  assert.deepEqual(v.actions, { change: false, cancel: false, reason: 'Managed by Bot, use its controls.' });
});

test('rowView for an order placed outside the app is read-only with the reason', () => {
  const v = rowView(order('b', 'outside', 'calendars'), null);
  assert.equal(v.from, 'Outside the app');
  assert.deepEqual(v.actions, { change: false, cancel: false, reason: OUTSIDE });
});

test('a debit order reads db and an unpriced one a dash', () => {
  assert.equal(rowView(order('x', 'you', '0dte', { price: -2 }), 1.1).price, '2.00 db');
  assert.equal(rowView(order('x', 'you', '0dte', { price: null }), 1.1).price, '—');
  assert.equal(rowView(order('x', 'you', '0dte', { price: null }), 1.1).market, '1.10 cr');
});

test('legView: signed quantity, expiry, strike, type and the open/close badge', () => {
  assert.deepEqual(legView(leg), { qty: -1, left: null, side: 'sell', action: 'STO', expiry: 'Oct 23', strike: '7645', type: 'C', symbol: null });
  const buy = legView({ ...leg, side: 'buy', opening: false, quantity: 2, remaining: 1 });
  assert.equal(buy.qty, 2);
  assert.equal(buy.action, 'BTC');
  assert.equal(buy.left, 1);
  assert.equal(legView({ ...leg, opening: false }).action, 'STC');
  assert.equal(legView({ ...leg, side: 'buy' }).action, 'BTO');
  assert.equal(legView({ ...leg, opening: null }).action, 'Sell');
  const stock = legView({ symbol: 'SPY', side: 'buy', opening: true, quantity: 100, remaining: 100 });
  assert.equal(stock.symbol, 'SPY');
  assert.equal(stock.expiry, null);
});

test('rowView carries the legs, the status text, the owner kind and the price parts', () => {
  const v = rowView(order('a', 'outside', '0dte', { status: 'PENDING_CHANGE', price: -2 }), 0.4);
  assert.equal(v.legs.length, 1);
  assert.equal(v.statusText, 'Pending change');
  assert.equal(v.ownerKind, 'outside');
  assert.deepEqual(v.priceParts, { value: '2.00', side: 'db' });
  assert.deepEqual(v.marketParts, { value: '0.40', side: 'cr' });
  assert.equal(moneyParts(null), null);
  assert.equal(statusText('WORKING'), 'Working');
});

test('asOfText reads the listing time in New York', () => {
  assert.equal(asOfText('2026-10-23T14:42:10+00:00'), 'as of 10:42');
  assert.equal(asOfText('nonsense'), '');
  assert.equal(asOfText(null), '');
});
