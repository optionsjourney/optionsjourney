# 0009. Bots start in preview mode

- **Status:** Proposed
- **Date:** 2026-10-09

## Context
Bots place real orders with real money, on behalf of people who can't debug them if something goes wrong.

## Decision
- **Preview mode by default:** a new bot starts in preview mode. It builds each order and shows it, using the broker's own preview or dry-run check, but doesn't send it.
- **Going live is deliberate:** switching a bot to live is a separate step that the user takes on purpose, with a confirmation.
- **Size limits:** every bot has a maximum size per trade and per day that the user sets. Orders over the limit are refused.
- **A "Stop all bots" switch** sits in the app and in the menu bar. It stops all new orders immediately.

## Why
- **Mistakes cost money,** and preview mode lets people watch a bot work for a while before trusting it.
- **Limits contain the damage** from a bad bot file, a typo in a size, or a bug.

## Consequences
- Every order a bot places must go through one central check, covering mode, limits and the stop switch, so none of these protections can be skipped.
