import { test } from 'node:test';
import assert from 'node:assert/strict';
import { chartSvg } from '../../../../src/optionsjourney/web/static/js/stock_drawer.js';

const DATA = {
  ticker: 'AAA', quantity: 10, cost_basis: 100, note: '',
  closes: [{ day: '2026-09-01', close: 100 }, { day: '2026-09-02', close: 110 }, { day: '2026-09-03', close: 105 }],
  marks: [{ day: '2026-09-01', side: 'buy', quantity: 10, price: 100 },
          { day: '2026-09-03', side: 'sell', quantity: 2, price: 105 }],
};

test('draws the line, the cost line and the marks', () => {
  const svg = chartSvg(DATA, 560, 280);
  assert.equal(svg.match(/<polyline[^>]*points="([^"]+)"/)[1].split(' ').length, 3);
  assert.match(svg, /your cost 100\.00/);
  assert.equal((svg.match(/stock-chart__buy/g) || []).length, 1);
  assert.equal((svg.match(/stock-chart__sell/g) || []).length, 1);
});

test('no closes gives the message', () => {
  assert.match(chartSvg({ ...DATA, closes: [], note: "Prices aren't available right now." }, 560, 280),
    /Prices aren&#39;t|Prices aren't/);
  assert.match(chartSvg({ ...DATA, closes: [], note: '' }, 560, 280), /No prices/);
});

test('a mark on a day with no close is skipped', () => {
  const svg = chartSvg({ ...DATA, marks: [{ day: '2026-01-01', side: 'buy', quantity: 1, price: 1 }] }, 560, 280);
  assert.doesNotMatch(svg, /stock-chart__buy/);
});

test('the cost label sits inside the viewBox and clear of the axis labels', () => {
  for (const cost of [100, 110, 105]) {   // the min, the max and the middle
    const svg = chartSvg({ ...DATA, cost_basis: cost }, 560, 280);
    const m = svg.match(/stock-chart__label" x="([\d.]+)" y="([\d.]+)">your cost/);
    assert.ok(m);
    assert.ok(Number(m[1]) + 120 <= 560 - 52);   // generous text width, left of the axis labels
    assert.ok(Number(m[2]) > 0 && Number(m[2]) < 280);
  }
});
