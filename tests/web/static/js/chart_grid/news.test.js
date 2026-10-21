// Tracker2's tests/js/chart_grid_news.test.js, the pure helpers only: its NewsPanel and
// SideColumn cases need jsdom (an npm package), so the panel is checked in the browser.
import { describe, it, expect } from '../vitest_shim.js';
import { timeAgo, hasCompanyNews, clampSplit } from '../../../../../src/optionsjourney/web/static/js/chart_grid/news.js';
import { focusSymbolFor, tradeSymbolFor, tradeUrl } from '../../../../../src/optionsjourney/web/static/js/chart_grid/dashboard.js';

describe('news helpers', () => {
  it('timeAgo', () => {
    const now = 1_000_000_000_000;
    expect(timeAgo(now / 1000 - 30, now)).toBe('now');
    expect(timeAgo(now / 1000 - 600, now)).toBe('10m');
    expect(timeAgo(now / 1000 - 7200, now)).toBe('2h');
    expect(timeAgo(now / 1000 - 3 * 86400, now)).toBe('3d');
  });
  it('hasCompanyNews skips futures and indices', () => {
    expect(hasCompanyNews('DELL')).toBe(true);
    expect(hasCompanyNews('BRK.B')).toBe(true);
    expect(hasCompanyNews('/ES')).toBe(false);
    expect(hasCompanyNews('$SPX')).toBe(false);
    expect(hasCompanyNews(null)).toBe(false);
  });
  it('clampSplit', () => {
    expect(clampSplit(0.01)).toBe(0.15);
    expect(clampSplit(0.99)).toBe(0.85);
    expect(clampSplit(NaN)).toBe(0.5);
  });
  it('focusSymbolFor prefers the linked ticker, else the first pane', () => {
    expect(focusSymbolFor([{ symbol: 'SPY' }, { symbol: 'DELL', linked: true }])).toBe('DELL');
    expect(focusSymbolFor([{ symbol: 'AAPL' }, { symbol: 'MSFT' }])).toBe('AAPL');
    expect(focusSymbolFor([])).toBe(null);
  });
  it('the Trade button follows only a linked pane, onto the Trade page', () => {
    expect(tradeSymbolFor([{ symbol: 'SPY' }])).toBe(null);
    expect(tradeSymbolFor([{ symbol: 'SPY' }, { symbol: 'BRK.B', linked: true }])).toBe('BRK.B');
    expect(tradeUrl('BRK.B')).toBe('/trade?ticker=BRK.B');
  });
});
