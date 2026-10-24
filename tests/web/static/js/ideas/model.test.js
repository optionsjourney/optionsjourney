import { test } from 'node:test';
import assert from 'node:assert/strict';
import { statusChip, priceText, ageText, cardView } from '../../../../../src/optionsjourney/web/static/js/ideas/model.js';

const IDEA = {
  id: 7, ticker: 'SPX', label: 'SPX Iron Condor', note: 'wait for CPI', status: 'live', settlement_na: false,
  created_at: '2026-10-23T14:00:00+00:00', spot_at_entry: 6612.5, pnl: 120, pnl_pct: 6.2,
  cards: [{ label: 'SPX Iron Condor', qty: 2, entry: 1.9, locked: true, now: 1.3, pnl: 120 }],
};

test('statusChip names each state', () => {
  assert.equal(statusChip({ status: 'live' }), 'Live');
  assert.equal(statusChip({ status: 'partly_expired' }), 'Partly expired');
  assert.equal(statusChip({ status: 'expired', settlement_na: false }), 'Expired');
  assert.equal(statusChip({ status: 'expired', settlement_na: true }), 'Expired · settlement n/a');
});

test('priceText: credit, debit and no price', () => {
  assert.equal(priceText(1.9), '1.90 cr');
  assert.equal(priceText(-2), '2.00 db');
  assert.equal(priceText(null), '—');
});

test('ageText counts minutes, hours and days', () => {
  const now = new Date('2026-10-23T14:00:00Z');
  assert.equal(ageText('2026-10-23T13:59:40Z', now), 'just now');
  assert.equal(ageText('2026-10-23T13:55:00Z', now), '5 min ago');
  assert.equal(ageText('2026-10-23T11:00:00Z', now), '3 h ago');
  assert.equal(ageText('2026-10-20T14:00:00Z', now), '3 d ago');
  assert.equal(ageText('nonsense', now), '');
});

test('cardView builds the header and the table rows', () => {
  const v = cardView(IDEA, new Date('2026-10-23T15:00:00Z'));
  assert.equal(v.chip, 'Live');
  assert.equal(v.canTrade, true);
  assert.equal(v.pnl, '+$120.00');
  assert.equal(v.pct, '+6.2%');
  assert.equal(v.tone, 'positive');
  assert.match(v.saved, /Oct 23/);
  assert.match(v.saved, /ET$/);
  assert.equal(v.age, '1 h ago');
  assert.equal(v.spot, 'at 6612.50');
  assert.deepEqual(v.cards[0], { label: 'SPX Iron Condor', qty: 2, entry: '1.90 cr', locked: true, now: '1.30 cr', pnl: '+$120.00', tone: 'positive' });
});

test('cardView: losses, unknowns, expired ideas can not be traded', () => {
  const v = cardView({ ...IDEA, status: 'expired', pnl: -50, pnl_pct: -2.5, spot_at_entry: null,
    cards: [{ ...IDEA.cards[0], now: null, pnl: null, locked: false }] }, new Date('2026-10-23T15:00:00Z'));
  assert.equal(v.canTrade, false);
  assert.equal(v.pnl, '−$50.00', 'a real minus sign');
  assert.equal(v.pct, '−2.5%');
  assert.equal(v.tone, 'negative');
  assert.equal(v.spot, '');
  assert.equal(v.cards[0].now, '—');
  assert.equal(v.cards[0].pnl, '—');
  assert.equal(v.cards[0].tone, '');
});

test('cardView with no P/L yet shows dashes', () => {
  const v = cardView({ ...IDEA, pnl: null, pnl_pct: null }, new Date('2026-10-23T15:00:00Z'));
  assert.equal(v.pnl, '—');
  assert.equal(v.pct, '');
});
