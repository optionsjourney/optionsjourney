import test from 'node:test';
import assert from 'node:assert/strict';
import { CHART_TIPS, CHART_TIPS_MORE, chartTipHtml } from '../../../../src/optionsjourney/web/static/js/chart_grid/chart_tips.js';
import { INDICATORS } from '../../../../src/optionsjourney/web/static/js/chart_grid/indicators/index.js';

const KEYS = ['vwap', 'orb', 'kc', 'macd', 'rsi', 'exp_move'];

test('every tipped indicator has text and is a real indicator', () => {
  for (const k of KEYS) {
    assert.ok(typeof CHART_TIPS[k] === 'string' && CHART_TIPS[k].trim(), k);
    assert.ok(INDICATORS[k], k);
  }
});

test('chartTipHtml renders the tip with its own id, and nothing for an untipped indicator', () => {
  for (const k of KEYS) {
    assert.match(chartTipHtml(k, INDICATORS[k].label), new RegExp(`id="tip-chart-${k}"`));
  }
  assert.equal(chartTipHtml('ema', 'EMA'), '');
});

test('the tips state the periods the code uses', () => {
  assert.match(CHART_TIPS.kc, /20/);
  assert.match(CHART_TIPS.kc, /10/);
  assert.match(CHART_TIPS.macd, /12/);
  assert.match(CHART_TIPS.macd, /26/);
  assert.match(CHART_TIPS.rsi, /14/);
  assert.match(CHART_TIPS.orb, /30/);
  assert.match(CHART_TIPS.exp_move, /VIX1D/);
});

test('Learn more links point at the charts guide', () => {
  for (const v of Object.values(CHART_TIPS_MORE)) assert.match(v, /^\/help\/charts#/);
});
