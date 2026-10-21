# 0017. Positions follow Tracker2's rules

- **Status:** Accepted, amended by [0020](0020-matching-lessons-from-real-history.md)
- **Date:** 2026-10-09

## Context
Orders have to be put together into positions: what was opened, how it was closed, and the P/L. The author asked that rolls and partial closes work the way they do in Tracker2.

## Decision
**Same as Tracker2:**
- **Opening:** an opening order starts a position.
- **Closing:** a closing order closes legs of **one** position in the same account. It must hold every leg the order closes, in the same direction, with enough quantity left. A closing order that would need two positions is reported as unmatched, not split.
- **Partial closes:** the position stays **one position**. Each leg tracks how much is open and how much is closed. The status goes Open → Partial → Closed, and the realized P/L builds up with each close.
- **Rolls:** a roll is an order that closes **whole legs** of a position and opens new ones, where the greatest common divisor of the order's quantities equals the position's size. The new legs join the **same position**: the roll count goes up, the original entry price is kept, and the roll's net credit per unit is added to the entry price (e.g. 3.25 + 1.83 = 5.08). Anything else is a close plus a new position.
- **0DTE:** a position opened on the day its options expire is never rolled.
- **Prices:** entry and exit prices are per unit (spread), signed: + credit, − debit.

**Where we differ, on purpose:**
1. **Expirations, assignments and exercises come from the broker's own records** (TastyTrade's Receive Deliver, Schwab's RECEIVE_AND_DELIVER), including the cash for an in-the-money cash-settled option. Tracker2 works expirations out from the date and a Yahoo closing price.
2. **Closes match the oldest open position first.** Tracker2's docs say it does this, but its code doesn't sort.
3. **A leg that expires counts toward the position's P/L** even when the rest of the position is closed by trade (e.g. a calendar whose front month expires). Tracker2 drops that leg's P/L.
4. **P/L comes from the brokers' cash amounts,** so contract multipliers (100, /ES 50, …) never have to be looked up. Opening fees are booked in proportion as a position is closed in parts. Tracker2 books them all on the first partial close.
5. **Positions are worked out from the stored transactions every time they're shown,** not saved, so a fix to these rules applies to all history at once.

**Not brought over:**
- **Tracker2's automatic 0DTE iron condor split** into a put spread and a call spread. It depended on the per-strategy configuration that [0004](0004-automation-first-strategy-naming.md) dropped. Splitting becomes the user's choice instead: a bot setting with a per-trade override ([0018](0018-iron-condor-split-is-a-choice.md)).
- **Its bot-specific matching overrides.** These come back with the bots.

## Consequences
- **Unmatched closes are listed.** These are closes with no opening, usually because the trade was opened before the synced history begins. They appear on the Positions page instead of being guessed at. Manual matching can come later if it's needed.
- **Edits will need stable keys.** A position's key is built from its opening order, so later edits (recategorizing, notes) can be stored against it.
