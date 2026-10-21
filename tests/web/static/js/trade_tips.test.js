import test from 'node:test';
import assert from 'node:assert/strict';
import { TRADE_TIPS } from '../../../../src/optionsjourney/web/static/js/trade/trade_tips.js';
import { stripItems } from '../../../../src/optionsjourney/web/static/js/trade/chain_stats.js';
import { tipHtml } from '../../../../src/optionsjourney/web/static/js/tip.js';

const KEYS = ['oi', 'pos', 'delta', 'spread', 'bid', 'ask', 'stripDelta', 'pop', 'p50',
  'maxProfit', 'maxLoss', 'bpEff', 'bpAfter', 'fees', 'gtc', 'gtcOvernight'];

test('every Trade tip key has text', () => {
  for (const k of KEYS) assert.ok(typeof TRADE_TIPS[k] === 'string' && TRADE_TIPS[k].trim(), k);
});

test('every strip item carries a tip that renders as its own id', () => {
  const items = stripItems({ delta: 1, pop: 0.5, p50: 0.3, maxProfit: 100, maxLoss: -200 }, null);
  assert.equal(items.length, 8);
  for (const i of items) {
    assert.ok(i.tip, i.label);
    assert.equal(i.tip, TRADE_TIPS[i.key]);
    assert.match(tipHtml('trade-' + i.key, i.tip, i.label), new RegExp(`id="tip-trade-${i.key}"`));
  }
});

test('a tray note stays as the only title', () => {
  const items = stripItems({ note: 'one expiration only' }, null);
  assert.equal(items.find(i => i.label === 'POP').title, 'one expiration only');
  assert.equal(items.find(i => i.label === 'Fees').title, null);
});

import { tipEngaged } from '../../../../src/optionsjourney/web/static/js/tip.js';
test('tipEngaged is true only while a tip is open, hovered or focused', () => {
  const root = sel => ({ querySelector: s => (s.includes(sel) ? {} : null) });
  assert.equal(tipEngaged(root('.tip--open')), true);
  assert.equal(tipEngaged({ querySelector: () => null }), false);
  assert.equal(tipEngaged(null), false);
});
