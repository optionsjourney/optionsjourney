# 0025. Strategies also tag their trades, and stocks get their own tag and tab

- **Status:** Accepted
- **Date:** 2026-10-11

## Context
[0019](0019-journal-strategies-tags-dashboards.md) gave each trade one strategy and any number of tags. The author asked for each strategy to be a tag too, and for stocks to have their own tag. Stock trades were in the journal but hard to find: 24 among about 5,600 option trades.

We considered three ways to make strategies tags:
1. **Merge** strategies into tags.
2. **Show** the strategy among the tags.
3. **Also add a tag** when a strategy is chosen.

The author chose the third.

## Decision
- **Strategies stay** (one per trade, and later the parent of bots, #30).
- **Choosing a strategy for a trade, or a different one, also tags the trade with the strategy's name.** It reuses a tag of that name if one exists. The tag can be removed afterwards; the strategy stays. Migration 0008 does the same once for trades that already had a strategy.
- **Stock trades always carry a Stocks tag.** It isn't stored per trade: the journal adds it to every trade made only of shares, as long as a tag called Stocks exists. Migration 0008 creates it.
- **A Stocks tab** sits next to **All trades** and shows only stock trades, with their own summary, P/L and metrics. **All trades** still shows everything.

## Why
- Adding the tag keeps one place to filter by (tags), without losing the strategy that bots will hang off.
- A computed Stocks tag can't drift: new stock trades get it with no extra step.
