# 0073. The Trade page's chain follows the ticket's broker and says where it came from

- **Status:** Accepted
- **Date:** 2026-10-18

## Context
The option chain always asked Schwab first and fell back to TastyTrade without saying so, whichever account the ticket was on. With both brokers connected, a TastyTrade order was priced from Schwab's chain (and its live prices from TastyTrade's stream), and nothing on the page told you which broker you were looking at.

## Decision
- The Trade page asks for the chain from the ticket account's broker first; the other broker is the fallback. Changing the account reloads the chain.
- The chain's header says where it came from: "Chain: Schwab" or "Chain: TastyTrade". A fallback is shown in amber with the reason ("Schwab unavailable").
- Futures options always come from TastyTrade: Schwab's chain endpoint refuses futures (`/ES`, `/ESZ26`: 400 "Check Param Values", checked 2026-10-18), though it quotes the future itself. The header says so.
- Without a preference (Analyze, the bots), the order is unchanged: Schwab first.

## Consequences
- What you see matches where the order goes, including Schwab's Vol and OI on a Schwab ticket. Schwab sends open interest as 0 for SPX (its SPY and stock chains have it).
- The chain is cached per broker, so switching accounts back and forth doesn't reload it each time.
