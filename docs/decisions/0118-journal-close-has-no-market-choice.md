# 0118. The journal's Close has no Limit/Market choice

- **Status:** Accepted
- **Date:** 2026-10-25

Issue #300. Replaces the Limit/Market part of [0117](0117-journal-alerts-and-close-from-the-select-bar.md).

## Context
0117 gave the select bar's Close a Limit or Market choice, Market for 0DTE trades only. But a Market close only goes to market for a group with one leg; any spread is still closed by the bot's exit chase. A calendar held to its last day, or a 0DTE spread, can't be closed at market either. So the choice did nothing for most trades and read as if it did.

## Decision
- **Close has no order type.** Every ticked trade closes from the mid, chasing toward a fill, by its own exit chase settings. Those settings already say whether a last leg goes to market after its final try.
- **Market stays where it means something:** closing one side of a trade from its own panel or run page, still for 0DTE only.

## Why
- The app can tell from each trade how it should close; asking the user for a choice that most trades ignore is misleading.
