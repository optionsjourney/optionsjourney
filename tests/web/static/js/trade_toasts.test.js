import { test } from 'node:test';
import assert from 'node:assert/strict';
import { stillUp, toastView } from '../../../../src/optionsjourney/web/static/js/trade_toasts.js';

const event = {
  id: 7, kind: 'partial_close', label: 'Partly closed', name: 'XSP Put Credit Spread',
  bot_name: 'Put Spreads - testing', account: 'Alpha ••1234', amount: '$34.00 paid',
  link: '/journal/trades/tastytrade:5WX01234:123456789%23put', popup: true,
};

test('a popup names the trade, its bot, account and cash', () => {
  assert.deepEqual(toastView(event), {
    badge: 'Partly closed', tone: 'close', title: 'XSP Put Credit Spread',
    detail: 'Put Spreads - testing · Alpha ••1234', amount: '$34.00 paid', link: event.link, lines: [],
  });
});

test('a closed trade lists its strikes and each roll on lines of their own (#212)', () => {
  const closed = { ...event, kind: 'close', detail: 'Strikes: 5750\nRolled: 2026-09-02 5800 → 5750' };
  assert.deepEqual(toastView(closed).lines, ['Strikes: 5750', 'Rolled: 2026-09-02 5800 → 5750']);
});

test('a trade you placed has no bot; opens and rolls have their own colour', () => {
  assert.equal(toastView({ ...event, bot_name: null }).detail, 'Alpha ••1234');
  assert.equal(toastView({ ...event, kind: 'open' }).tone, 'open');
  assert.equal(toastView({ ...event, kind: 'roll' }).tone, 'roll');
});

test('a reload keeps only the popups whose time is not up', () => {
  const kept = [{ e: { id: 1 }, until: 2000 }, { e: { id: 2 }, until: 900 }, null];
  assert.deepEqual(stillUp(kept, 1000).map(k => k.e.id), [1]);
  assert.deepEqual(stillUp('junk', 1000), []);
});

test('an alert that went off pops up with its own colour and detail (#275)', () => {
  const v = toastView({ id: 'alert:7', kind: 'alert', label: 'Alert', name: 'SPX went above 7,600.00',
                        detail: 'now 7,646.98 · testing', link: '/trade?ticker=SPX' });
  assert.equal(v.tone, 'alert');
  assert.equal(v.title, 'SPX went above 7,600.00');
  assert.equal(v.detail, 'now 7,646.98 · testing');
  assert.deepEqual(v.lines, []);
});
