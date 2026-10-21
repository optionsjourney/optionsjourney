# 0090. Chart news works without a key: Yahoo Finance, or Finnhub with one

- **Status:** Accepted
- **Date:** 2026-10-21
- **Replaces:** [0089](0089-chart-news-from-finnhub-and-plex-fonts-in-the-app.md) in part (where headlines come from)

## Context
Decision 0089 took the News panel's headlines from Finnhub only, as Tracker2 does. Finnhub needs a key, so for most users the panel would only say how to get one (#239). Neither broker's API offers news. Yahoo Finance has a per-ticker RSS feed that needs no key and answers with current headlines.

## Decision
- **No key:** headlines come from Yahoo Finance's per-ticker feed (`feeds.finance.yahoo.com/rss/2.0/headline?s=<ticker>`), the last 7 days, labelled "Yahoo Finance".
- **With the optional Finnhub key:** Finnhub's company news, as in 0089 and Tracker2. Saving a key switches at once.
- Both are cached 5 minutes per ticker. The panel itself is unchanged.
- Only web addresses (http/https) are kept as links, whichever the source.

## Why
- News works out of the box, with no sign-up, for people who aren't technical.
- Finnhub stays the better choice: an official service whose headlines are matched to the company. Yahoo's feed sometimes mixes in stories about related companies.

## What it costs
- Yahoo doesn't promise this feed. If it changes or stops, the panel shows the error, and adding a Finnhub key is the way out.
