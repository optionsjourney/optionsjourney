# 0130. A 09:00 check that today's entries can be placed, and a note when Schwab lists the expirations

- **Status:** Accepted
- **Date:** 2026-11-14
- **Adds to:** 0040 (the 09:20 check of trades held overnight) and 0080 (TastyTrade first, Schwab the backup).

## Context
A bot's entry reads two things from the brokers: the listed expirations (TastyTrade's list, with Schwab's as the backup) and an option chain for the expiration it trades (Schwab's snapshot, with TastyTrade's live quotes as the backup), with deltas when a leg picks by delta. When the brokers can't give those, the entry fails at its entry time, and that's when you find out.

That happened in Tracker2 on 2026-11-09: TastyTrade added a root to the SPX chain that Tracker2 couldn't read, the same error repeated every minute from 08:16, and the first anyone heard of it was two entries failing at 09:47. Tracker2 answered with a Schwab backup for expirations, a note in the trade's warnings when it's used, and a weekday 09:00 check that alerts only on a problem. Issue #327.

This app already had the Schwab backup for expirations (`services/chain.py`), but only for a broker outage (`BrokerError` and the like), not for a reply that couldn't be read, and the cached answer didn't say where it came from.

## Decision
- **The backup covers any failure.** TastyTrade's expiration list falls back to Schwab's on any error or an empty list, not only an outage. The answer is cached for an hour together with where it came from.
- **The note.** When Schwab lists the expirations because a connected TastyTrade couldn't, the entry's log gets its own step, **Expirations from Schwab** ("TastyTrade couldn't list them; Schwab's list was used"), shown on the run page. It isn't added to the existing "Expirations moved" step, which means something else.
- **The check.** At 09:00 New York on each trading day, for each underlying an armed bot (Live or Paper) trades today:
  - it asks **each** broker for its expiration list, fresh, not from the cache;
  - for **SPX and XSP**, whose options quote overnight, it reads one chain (the nearest expiration), and checks its deltas when a bot on that underlying picks by delta (or places a short by an offset, which reads deltas too).
- **Alert only on a problem**, one alert for all underlyings:
  - **"Market data check: SPX entries will NOT be placed"** (a safety alert) when no broker lists expirations, there's no chain, or a delta bot's chain has no deltas;
  - **"Market data degraded: entries will use a backup source"** when TastyTrade couldn't list expirations and Schwab could, when Schwab's chain failed and TastyTrade's quotes were used, or when Schwab couldn't list expirations (no backup today).
  - A quiet morning means every check passed.
- **Scheduling.** It's a step in the bot monitor next to the 09:20 held-trades check, with the same pattern: weekends and market holidays (`core.sessions`) skipped, once a New York day, remembered in the `_market_data_checked` setting before it runs.

Choices Tracker2 doesn't settle (it always has both brokers, runs one job at 09:00 and has no demo):
- **A broker that isn't connected is never a problem.** A friend may connect only TastyTrade or only Schwab. Tracker2's "Schwab lists no expirations: there is no fallback today" would then alert every morning. Only a connected broker that fails, or lists nothing, counts. With no broker connected at all, the entries can't be placed, and it says so.
- **The chain's backup is the other way round.** Tracker2 reads DXLink first and Schwab second; this app reads Schwab's snapshot first. "Degraded" for a chain means Schwab is connected but its chain failed. A TastyTrade-only user reading TastyTrade's quotes is normal.
- **Catch-up until the close, for entries still to come.** Tracker2's job runs once at 09:00 (10 minutes' grace). Here, as with the 09:20 check, an app started later in the day runs it then, but only for bots with an entry time, or an entry window, still ahead. After 09:30 every underlying's options quote, so a check that runs then reads the chain for stocks and ETFs too.
- **Deltas only where a bot needs them.** Tracker2 says "NOT placed" whenever the chain has no deltas. Here, if no bot on that underlying reads deltas, missing deltas don't stop anything, so they aren't checked. The test is the one a delta leg uses (`strikes.chain_has_deltas`: 95% of each side's out-of-the-money priced strikes).
- **Futures are left out.** Their options come from TastyTrade only (0080), with no backup to check, and an entry already alerts when their data is missing.
- **Not in the demo.** The demo's pretend brokers can't fail, and its pretend Schwab has no expiration list. The check is skipped in demo mode, and a broker without an expiration list counts as not connected anyway.
- **A blackout day** (typed, or an entry-day list) leaves that bot out, as Tracker2's `is_trading_day` does.

## Why
- Finding out at 09:00 leaves time to reconnect a broker or press Refresh before the first entry, instead of reading a failed run at 09:47.
- Alerting only on a problem keeps the alert meaningful: a daily "all fine" would be noise next to the 09:20 check's own message.
- Treating a broker that isn't connected as fine is what makes the check usable by friends with one broker, and keeps the demo quiet.
- The pure decision (`bots/market_check.py`) is separate from the broker calls (`services/bot_market_check.py`), so every answer is tested without a broker.

## Consequences
- One more read of each broker's expiration list per underlying each trading morning, and one chain for SPX or XSP. With brokers timing out (10 seconds a call) and TastyTrade's 4-second wait for quotes, one underlying can take about 40 seconds, so the check runs on its own thread and never holds up the exit monitor.
- An expired Schwab login still counts as connected (the app reports it rather than hiding it), so until you log in again each morning also brings "degraded: Schwab couldn't list expirations, no backup today", next to the login reminder.
- The check reads only: it sends, cancels and changes nothing.
- A problem the check can't see (a chain that breaks after 09:00) still shows when the entry fails, as before.
