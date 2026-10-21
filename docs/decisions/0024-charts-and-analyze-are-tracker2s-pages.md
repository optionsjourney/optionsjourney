# 0024. Charts and Analyze are Tracker2's own pages

- **Status:** Accepted
- **Date:** 2026-10-10

## Context
The first Charts page and Analyze (#25, #26) were our own versions: simpler pages in this app's stylesheet, with Analyze's math moved from the browser into Python. After using them, the author asked for both to be **exactly** like Tracker2's.

Tracker2's chart grid and analyzer are large, well-tested browser apps (about 60 JavaScript modules between them) that talk to the server through a small, clear set of REST calls and Socket.IO events. Rewriting them would never come out exactly the same.

## Decision
- **Copy Tracker2's JavaScript and CSS for these two pages as they are** (`web/static/js/chart_grid/`, `js/charts/`, `js/analyzer/`, `css/chart_grid.css`), and build the server side they expect: the same addresses, the same answers, the same Socket.IO events.
- **Keep the copies close to Tracker2's.** Changes are limited to what this app doesn't have: personal strategies, bots, trade ideas, buying-power previews, and Tracker2's addresses that differ from ours. Each change is listed in the developer doc.
- **Their libraries are stored in the repo** (`web/static/vendor/`): Lightweight Charts 5.0.0, the Socket.IO 4.7.4 client and the Tailwind 3.4 browser build. The pages work without the internet once installed.
- **These pages use Tracker2's look** (Tailwind, dark) with this app's top bar, through `tracker_base.html`. The rest of the app keeps its own stylesheet.
- **Socket.IO is added** (Flask-SocketIO in threading mode, simple-websocket), because the pages are built around it. Its default same-origin check keeps other sites out, like [0013](0013-local-only-web-server.md).
- **Data comes from the user's brokers, TastyTrade first** ([0021](0021-product-scope.md)):
  - Live candles come from TastyTrade's DXLink, on a connection of its own.
  - Price history comes from Schwab, as in Tracker2, or from TastyTrade when Schwab isn't connected.
  - Watchlist prices come from the quote hub, measured against the brokers' previous close.

## Why
- "Exactly like Tracker2" can only be met by running Tracker2's own code.
- Keeping the server contract identical means later Tracker2 fixes can be copied over file by file.
- This replaces part of the journal's 2026-10-10 note that Analyze's math would live in Python. With the analyzer copied as it is, its math runs in the browser, as in Tracker2.
