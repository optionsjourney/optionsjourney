// Canonical previous-day close from the daily series (NOT the intraday
// last-bar-of-yesterday, which varies by timeframe), for a pane's change readout.
// Cached per symbol for TTL_MS so every pane on the same symbol shares one fetch.
// A futures root ('/ES') takes its prior settlement from Schwab's quote, as the broker and
// the watchlist do; null on any failure, and the pane walks its own bars.
const TTL_MS = 10 * 60 * 1000;

export function prevCloseFetcher() {
  const cache = new Map();
  return (symbol) => {
    const cached = cache.get(symbol);
    if (cached && cached.expiry > Date.now()) return cached.promise;
    const promise = String(symbol).startsWith('/') ? futuresSettle(symbol) : (async () => {
      try {
        const r = await fetch(`/api/chart/pricehistory/${symbol}?frequency=1440&period=5`);
        const data = await r.json();
        if (data.error) return null;
        const candles = data.candles || [];
        if (candles.length < 2) return null;
        // Most recent candle = today (or last trading session if not open yet).
        // Second-to-last = the prior session's official close.
        return candles[candles.length - 2].close;
      } catch (e) {
        console.warn('getPrevClose failed for', symbol, e);
        return null;
      }
    })();
    cache.set(symbol, { promise, expiry: Date.now() + TTL_MS });
    return promise;
  };
}

async function futuresSettle(symbol) {
  try {
    const r = await fetch(`/api/chart/prev_close?symbol=${encodeURIComponent(symbol)}`);
    const data = await r.json();
    return data.error || !(data.prev_close > 0) ? null : data.prev_close;
  } catch (e) {
    console.warn('futures settle failed for', symbol, e);
    return null;
  }
}
