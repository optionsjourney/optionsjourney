# 0080. Schwab answers when TastyTrade is down or stale

- **Status:** Accepted
- **Date:** 2026-10-20
- **Refines:** the "Both" bullet of [0021](0021-product-scope.md) (TastyTrade is the main source and Schwab the backup). 0021's text is unchanged; this says what "backup" means in practice.

## Context
With both brokers connected, the app preferred TastyTrade for everything and treated Schwab as a spare, but only when TastyTrade was fully down. Two real gaps showed up. A TastyTrade stream can stay connected and quietly stop sending, which hid fresh Schwab quotes behind an old TastyTrade one. And a bot whose entry needs a market check (for example the price or the volatility index) skipped its entry when TastyTrade couldn't answer, even though Schwab could. The app has to keep working when either broker doesn't. Issue #188.

## Decision
- **Live quotes: a stale TastyTrade quote gives way to a fresher Schwab one.** TastyTrade's quote is used while its stream is up, unless Schwab's quote for the same symbol is more than 10 seconds newer. Then Schwab's is used. If TastyTrade is down or has no quote, Schwab's is used; if only TastyTrade has one, a last-known TastyTrade quote still beats none. A symbol that simply trades rarely is only a few seconds behind, so it stays on TastyTrade. A quote's time is when it arrived, on the app's own clock, not a time the broker stamps on it.
- **Bot market filters fall back to Schwab's prices.** When TastyTrade can't give a price a bot's entry filter needs, the filter asks Schwab's REST prices instead of skipping the entry. If neither answers, the bot still waits.
- **Futures filters never fall back.** Schwab's prices aren't used for futures (such as /ES), so a filter on a futures symbol fails closed while TastyTrade can't answer.
- **The opening price is the 09:30 bar's or nothing.** The VIX overnight move and the intraday move use the open of the 09:30 one-minute bar, from either broker. If that bar is missing, a later minute's open is never used instead: the filter fails closed and the bot doesn't enter.
- **Tick sizes stay TastyTrade-only.** Schwab has no tick-size endpoint. After a failed fetch the app waits 5 minutes before asking TastyTrade again, so a broker that is down isn't hit on every order or screen.

## Why
- A connected-but-silent stream looks healthy, so "is it up" alone isn't enough; comparing the time of each quote catches it.
- Ten seconds is long enough that a quiet symbol doesn't flip between brokers, and short enough that a stuck stream is noticed within one screen refresh or bot check.
- Bots and screens keep working with one broker down, without changing any order rules: every order still goes through the one central check.

## Consequences
- Two brokers can briefly disagree on a price; the user sees whichever is fresher, and the quote says which broker it came from.
- An option that trades rarely can go more than 10 seconds without a TastyTrade update and so briefly switch to Schwab, for example right after Schwab reconnects and sends a fresh snapshot of every symbol. It goes back to TastyTrade with TastyTrade's next update.
- A bot's stops, exits and paper fills read the same chosen quote as the screens. So the broker a position's prices come from can change while it is open, but only after TastyTrade has gone quiet on that symbol for more than 10 seconds.
- Tick sizes can be unavailable while TastyTrade is down; orders needing one wait until it is back.
