# 0050. A bot sizes by contracts or by buying power, and the account's broker says what one contract needs

- **Status:** Accepted
- **Date:** 2026-10-15
- **Replaces:** the dollar sizing in [0045](0045-fixed-dollar-sizing-and-trade-now.md) (dollars ÷ the worst debit, debits only) and, in [0048](0048-trades-take-either-side-and-each-group-chooses-what-rests.md), the fallback that sized a dollar bot's credit trade by its contracts.

## Context
The bot form's Size and limits showed **Contracts per entry** and **Size each entry by: Dollars** together. Under 0048 a dollar bot whose trade priced as a credit fell back to its contracts, so both boxes had to stay, and it wasn't clear which one a bot used. Tracker2 picks one allocation type and shows only that type's box. The author's ruling: a fixed value is a **buying-power** budget, for a credit or a debit, and the number comes from the account's own broker.

Each broker was checked with a preview that was never placed (gateway mode, trading guard on):
- **TastyTrade's dry run** reports `change-in-buying-power`, which nets against what the account holds. On an account holding nearby SPX positions, a 5-wide debit spread showed buying power *freed*. The same reply reports the trade's `isolated-order-margin-requirement` (500 for a 5-wide credit spread, 0 for a debit spread) and `total-fees`.
- **Schwab's `previewOrder`** has no isolated figure. Its `orderBalance.projectedBuyingPower` moves by margin leverage, not by what the trade ties up. `projectedAvailableFund` falls by exactly the trade's requirement for each added contract ((25 − 0.05) × 100 + fees for a 25-wide credit spread).

## Decision
- **Size each entry by** comes first: **Contracts** (Contracts per entry) or **Buying power** (Most buying power per entry). Only the chosen one's box shows. The limits sit under their own **Limits** heading.
- **A buying-power bot sizes every entry, credit or debit,** by the buying power one contract needs **on its own**, at the worst price the entry chase could reach:
  - TastyTrade: one dry run of one unit; isolated requirement − credit received (+ debit paid) + total fees.
  - Schwab: previews of one and two units; the drop in `projectedAvailableFund` between them.
  - Contracts = ⌊ budget ÷ that ⌋, capped by Most per trade; past Most per day the entry is skipped, as before.
- **Anything unclear skips the entry and alerts:** a refused or unreadable preview (TastyTrade refuses a dry run the account can't afford), a cost of $0 or less, or a budget that doesn't cover one contract.
- The stored values stay `sizing = "dollars"` and `max_per_entry_usd`: no migration; the form calls it Buying power. Contracts per entry is ignored, and not checked against the limits, while sizing by buying power (a hidden count out of range is kept as 1).
- Unchanged: only the bot's own entries are sized this way (the Trade page keeps your size); never a futures bot; the bot file never carries the amount.

## Why
- One way to size at a time is what Tracker2 does and is easier to read.
- Buying power is how the author budgets a trade, for credits as well as debits.
- The trade on its own, not net of holdings: the same bot gets the same size whatever else the account holds, and the cost is never negative.
- The broker's own figure, not the app's arithmetic: it covers every shape the broker can margin, with its fees.

## Consequences
- Every bot sized by buying power makes one (TastyTrade) or two (Schwab) preview calls before each entry. Previews are allowed under the trading guard and are never placed.
- A debit's size is about what 0045 gave (its price), less a little for fees. A credit dollar bot used to take its contracts and now takes what its buying power covers.
- Older runs keep their "Sized by dollars" and "Sized by contracts" records and still show them.
- Not built: a buying-power amount per day.
