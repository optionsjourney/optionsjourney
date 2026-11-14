# 0123. The entry chase sends an entry again when the broker cancels it on its own

- **Status:** Accepted
- **Date:** 2026-10-30
- **Refines:** [0085](0085-exit-chase-retries-a-close-the-broker-cancels-at-once.md), which retries only closes. The exit chase is unchanged.

## Context
Tracker2 saw TastyTrade cancel an FOMC entry 166 ms after it arrived, with no reason and no cancelling user, while the same bot's entry in another account filled. Its chase took the cancel as final and the bot missed the trade (Tracker2 commit 9b49dfea). This app's entry chase did the same: an order found gone with nothing filled ended the chase as "missed". Issue #317.

## Decision
- **Up to two sends again, as Tracker2.** An entry read back as cancelled by the broker on its own, with nothing filled, is sent again for the same size. TastyTrade marks this as *Cancelled* with no reject reason, no replacing order and no cancelling user. The send waits until the market is open and the entry window hasn't run out. It is never sent more than twice. These sends don't use up the chase's tries.
- **From fresh quotes.** Each send re-reads the legs' quotes (the entry requote, #205) and starts again from the fair price less the starting offset, still held to the premium maximum. If the quotes can't be read, it goes at the entry's first price.
- **Through the central check.** Each send goes through `orders.submit` like any order (preview vs live, size limits, Stop all bots). A refusal ends the chase.
- **TastyTrade only.** Schwab orders never carry the flag.

## Why
- Unlike a close (0085), an entry can be read cancelled at any read, so the app can't assume the cancel was the broker's. TastyTrade says who cancelled it: one with a cancelling user is yours (or the app's) and is never sent again.
- Fresh quotes, as Tracker2 does: the market may have moved while the first order was out, so the old price could start the chase too far from it.
- Two sends, not one: Tracker2's limit. A broker that keeps cancelling for a reason it doesn't say is left alone after that.

## Consequences
- A bot can send up to three opening orders for one entry, one at a time; only one is ever working.
- The run's order log notes each extra send.
