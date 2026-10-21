# Charts

**Charts** in the top bar is a grid of charts: live candles for any stock, ETF, index or futures product your broker has.

## The grid
The grid fills the page under the top bar, with the charts separated by thin lines. One bar runs along the top: your tabs on the left, and the layout, shape, Watchlist, News and pop-out buttons on the right. On a phone the bar wraps onto two lines and the charts stack one under another.

- **Layouts:** the **Layout** menu holds your saved layouts. Make new ones, copy, rename or delete them. The grid opens with the last one you used.
- **Shape:** the grid button picks how many charts to show, from 1 to 10, including one big chart with smaller ones beside it. Drag the lines between charts to resize them.
- **Tabs:** each tab is a layout with its own symbols. The open tab has a green line along its top. **+** opens another.
- **Pop out grid** opens the whole grid in a window of its own, without the top bar, for a second monitor.
- **↗ Trade** shows when a chart is linked (🔗). It opens the [Trade](trade.md) page on the linked symbol in its own window, ready to build an order from the option chain.

## Each chart
Each chart's header shows the symbol, price and change. Its smaller buttons fade until you point at them. Rising candles are green and falling ones red.

- **Symbol:** type one (SPX, SPY, AAPL, /ES …) and press Return. A futures root like /ES shows its front month.
- **Link (🔗):** linked charts change symbol together.
- **Timeframe:** 1, 5, 15 and 30 minutes, 1 and 4 hours, or daily, weekly and monthly.
- **Indicators (the line button):**
  - EMA, SMA, MA cross arrows, Bollinger and Keltner bands, the expected move, VWAP, the previous day's levels, the opening range (ORB) and volume profiles.
  - Volume, RSI and MACD go in panels below the chart.
- **Drawing:** horizontal lines, trend lines, boxes and Fibonacci levels. They're saved for that symbol and timeframe and show on every chart of it, including pop-outs.
- **⧉** opens one chart in its own window, which stays in step with the grid.

## Indicators
Open the line button on a chart and tick what you want. The ⓘ beside a name says the same as this list.
- **VWAP:** the volume-weighted average price, starting again each New York date, with a dashed line one standard deviation either side. It needs volume.
- **ORB:** the high and low of the first 30 minutes after the 9:30 open, with a midpoint, held for the rest of the day, and an arrow on the first close above or below. **+** adds other lengths in minutes.
- **Keltner Channels:** the 20-bar EMA of the close, with bands 2 times the 10-bar ATR either side.
- **MACD:** in a panel below. The 12-bar EMA minus the 26-bar EMA, a 9-bar EMA of that line, and bars for the gap between them.
- **RSI:** in a panel below. A 14-bar RSI from 0 to 100, with the 35 to 65 band shaded; the line turns orange above 65 and red below 35.
- **Expected move:** lines one and two standard deviations either side of the 9:30 open. The size is open x volatility index / 100 / the square root of 365. The index is VIX1D (else VIX9D, else VIX) for S&P symbols, VXN for Nasdaq and RVX for Russell. Other symbols, and daily or longer charts, show nothing.

## Watchlist
**Watchlist** opens a side list (the button turns green while it's open):
- **Trading** shows the underlyings of your open trades. It updates by itself.
- Add your own groups with **+ Group**, then type symbols into them.
- Each row shows the last price, the change since the previous close, and the mark. The badge at the top shows whether prices are live or refreshed every 20 seconds.

## News
**📰 News** opens the latest headlines for the symbol on the chart, from the last 7 days (the button turns green while it's open).
- It follows the linked chart's symbol, or the first chart's. Clicking a watchlist row switches it to that symbol.
- Click a headline to read the story in your browser. Hover over it for a short summary.
- It refreshes every 5 minutes; **↻** refreshes it now.
- With the watchlist open too, the watchlist sits on top and news below. Drag the line between them to share the space.
- Futures and indexes have no company news, so the panel says so.
- Headlines come from Yahoo Finance, with nothing to set up. Yahoo sometimes mixes in stories about related companies. For better-matched headlines, get a free key at [finnhub.io](https://finnhub.io) and paste it in **Settings → Charts**: News then comes from Finnhub.

## Where the prices come from
- Live candles come from TastyTrade. Without TastyTrade connected, the charts load Schwab's history after a few seconds instead, without live updates.
- Futures charts need TastyTrade.
- Stocks and indexes show the regular session, 9:30 to 4:00 New York time. Futures show every hour they trade.
