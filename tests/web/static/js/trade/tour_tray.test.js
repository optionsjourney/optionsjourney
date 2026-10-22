import test from 'node:test';
import assert from 'node:assert/strict';
import { tourTray } from '../../../../../src/optionsjourney/web/static/js/trade/tour_tray.js';

const el = v => ({ dataset: v === undefined ? {} : { tourTray: v } });

test('tourTray parses a good attribute', () => {
  const t = tourTray(el('{"ticker":"SPX","cells":[{"strike":1}]}'));
  assert.equal(t.ticker, 'SPX');
  assert.equal(t.cells.length, 1);
});

test('tourTray is null when missing, broken, or without cells', () => {
  assert.equal(tourTray(el()), null);
  assert.equal(tourTray(el('{nope')), null);
  assert.equal(tourTray(el('{"ticker":"SPX"}')), null);
  assert.equal(tourTray(null), null);
});

import { tourTrayFor } from '../../../../../src/optionsjourney/web/static/js/trade/tour_tray.js';

test('tourTrayFor builds a two-leg tray with quantity 1', () => {
  const c = (strike, side) => ({ expiration: '2026-10-21', strike, type: 'put', side, bid: 1, ask: 2, stream_symbol: 'x', delta: -0.1 });
  const tray = tourTrayFor({ cells: [c(6700, 'sell'), c(6675, 'buy')] });
  assert.deepEqual(tray.legs.map(l => [l.strike, l.side, l.qty]), [[6700, 'sell', 1], [6675, 'buy', 1]]);
});
