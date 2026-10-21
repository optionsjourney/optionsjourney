# 0089. Chart news from Finnhub, and IBM Plex stored in the app

- **Status:** Replaced by 0090 (in part: without a key, headlines come from Yahoo Finance)
- **Date:** 2026-10-21

## Context
The author asked for Charts to match Tracker2's in look, style and feel, with ↗ Trade in place of ↗ Analyzer (#238). Charts is Tracker2's own code ([0024](0024-charts-and-analyze-are-tracker2s-pages.md)), so only two things differed:
- Tracker2 added a News panel (its ce22e8ec): Finnhub company headlines beside the watchlist. Finnhub needs an API key.
- Tracker2 loads IBM Plex Sans Condensed and IBM Plex Mono from Google Fonts. Our copy used system fonts instead, so the page works offline, and looked different.

## Decision
- **News is Tracker2's panel, copied as it is.** `/api/news/<symbol>` answers as Tracker2's does, from Finnhub's company news (last 7 days, each ticker at most every 5 minutes).
- **The Finnhub key is an optional setting** (Settings → Charts), like the FRED key ([0032](0032-economic-events-and-the-market-calendar-from-public-calendars.md)). Without one, the panel says how to add it, and nothing else changes. The key goes in a request header, not the address, so it never shows in a log.
- **IBM Plex is stored in the app** (`web/static/vendor/fonts/`, SIL Open Font License): the same faces and weights as Tracker2's, from the `@fontsource` packages, latin only. The page keeps working offline.

## Why
- Copying the panel and the fonts is what makes the page the same; anything else would drift from Tracker2.
- A free key is the only way to get headlines; making it optional keeps setup as short as before.
