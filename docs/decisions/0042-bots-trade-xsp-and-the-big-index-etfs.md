# 0042. Bots trade XSP, ETFs and stocks, and never hold an American-style option into expiry unannounced

- **Status:** Accepted (the author ruled on its questions, 2026-10-13)
- **Date:** 2026-10-13

## Context
Until now every bot traded SPX. The form's underlying was fixed, and the chain's root, the ticks, the chase grid, the filters' prices and the alerts all assumed it (0030). Tracker2 lets a bot trade any ticker typed in as free text.

Other underlyings differ in ways that matter to a bot's safety:
- **XSP** is a tenth of SPX and trades in pennies.
- **Stock and ETF options** are American-style: they settle in shares and can be assigned at any time.
- **Most stocks** quote single options in pennies under $3 and in nickels or dimes above.

## Decision
- **Free text, as in Tracker2** (the author's ruling, 2026-10-13). A bot can trade SPX, XSP, or any stock or ETF the broker lists options on, checked when it's saved.
  - The form suggests SPX, XSP, SPY, QQQ and IWM.
  - Other indexes, their option roots (SPXW, NDXP…), dotted share classes (BRK.B) and futures are refused.
  - `bots/underlyings.py` describes each underlying: its option root (SPXW for SPX, else the symbol), how it settles, the price its live reads use, and its chase grid ($0.05 for SPX, a cent otherwise).
- **XSP reads SPX ÷ 10** wherever a price is read live, as Tracker2 does.
- **Standard root only.** A bot trades only its underlying's standard root: never an AM-settled monthly SPX, never an adjusted contract.
- **Stock and ETF price steps** are loaded from TastyTrade before a bot enters or takes a trade on one, or the entry is refused (Tracker2's `option_ticks.ensure`). This blocks stock and ETF bots for Schwab-only users. The table is kept for the exit math, so a stop and a one-leg close round on the stock's own price steps.
- **No timed close is required** for a stock or ETF bot (the author's ruling, as in Tracker2). Instead:
  - any of its options still held from 10 minutes before the close on expiration day raises one safety alert, recorded in the run's log;
  - the 09:20 check of held trades says a missing short may have been assigned.

  The app tells you and doesn't act, as Tracker2 leaves assignment to you. It doesn't know earnings or dividends, as Tracker2's bots don't.
- **The underlying is fixed while a trade is open.** It can't change while the bot has anything open, and a change switches the bot off and back to preview.
- **SPX bots are unchanged.** There is no migration.

## Why
- **Free text** is what Tracker2 does and what the author asked for. Checking the chain at save catches a mistyped ticker before the bot is armed.
- **Loading a stock's price steps** is the one thing that makes its stops right: a stop rounded on a penny for a nickel stock would rest off its tick. Refusing without the table follows Tracker2.
- **The expiry alert** stands in for a timed-close rule the author didn't want.
- **The assignment hint sits in the 09:20 check** because an early assignment is processed overnight.
- **Acting on an assignment was turned down:** cancelling the orphaned stop, closing the rest, selling shares. Tracker2 doesn't do it, and each is a judgement about a position the bot no longer understands.
- **Refusing an underlying change under an open trade** keeps every run reading the underlying it opened on.

## Consequences
- Stocks, ETFs and XSP go through the same order path, central check and exits as SPX.
- On a penny underlying a stop at 4.875 rests at 4.87. On a stock that steps in nickels above $3 it rests at 4.85.
- Without TastyTrade connected, only SPX and XSP bots can trade.
- RUT, NDX, futures options, earnings and dividend calendars, and acting on an assignment are later work.
