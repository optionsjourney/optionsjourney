# 0078. P/L at the close from either broker, corrected to the official close

- **Status:** Accepted
- **Date:** 2026-10-19
- **Replaces:** the "settle once, at the brokers' daily closes" parts of [0062](0062-settle-expirations-at-the-close.md). The rest of 0062 stands (what is settled or left to the broker, the switch, the broker's record replacing the app's).

## Context
Decision 0062 settled expiring trades once, at 4:15 PM, at the daily close, which only Schwab provided in time. TastyTrade's market data had no close for SPX or XSP that evening, only a last price. So a TastyTrade-only user, or anyone whose Schwab login had run out, saw 0DTE trades stay open until the broker's record arrived the next day. The app has to work whichever broker is down. Issue #186.

## Decision
- **At 4:15 PM, settle at the best price either broker gives.** An official close if one is already out (Schwab's daily close; TastyTrade's close when it is marked Final for that day), otherwise the last price now: an index's last price (indexes don't trade after hours), or a stock or ETF's regular-session price. Each broker is asked on its own, and one failing or timing out moves on to the other. A settlement from an official close is **final**; one from a last price is **prelim**, and records its source and price.
- **Correct prelims to the official close.** A correction pass runs on the same 5-minute loop until 8 PM New York on the next trading day, whether or not the switch is still on (the switch controls new settlements only). Sources: Schwab's daily close, TastyTrade's Final close, or TastyTrade's previous close dated that day. A prelim never corrected stays prelim, and the broker's record replaces it when it arrives.
- **Unread alerts when a shown P/L changes.** A summary when a run settles ("Today's expirations settled: P/L ... (prelim)"), a correction when a trade's P/L moves by $1.00 or more, and a broker-record alert when the broker's own record differs by $1.00 or more (fees left out on both sides). They are stored on the server (`app_alerts`, one row per event key, so nothing alerts twice), so read and unread are the same on every device, and they are counted in the existing Alerts badge. The Alerts page lists them under Unread, and opening it marks them read. Each is also sent through the notification channels.
- **Per-account waiting stays** (#185): an account waits only for its own sync, and never holds up another account.
- **Settlements raise no trade popups** (0077: expirations aren't alerts). Only these alerts.
- **Schwab in-the-money broker records aren't compared yet** (#187): the synced Schwab history only has $0 "Removed due to Expiration" rows, so comparing would wrongly read as a difference.
- The journal says **Settled at close (prelim)** until the official close replaces the price, and the note under the switch lists the prelims still waiting.

## Why
- Works with TastyTrade only, Schwab only, or both, and with either one down.
- The final P/L matches the official close, and the user is told whenever a number they saw changed, rather than it changing silently.
- Server-side unread state, unlike Tracker2's browser-side state, follows the user across devices.

## Consequences
- A trade can show a prelim P/L for hours, and up to a day or so for indexes (TastyTrade's previous close arrives the next morning). Most trades expire worthless and correct with no change and no alert.
- The ETF regular-session price fields are still to be verified against live data at 4:15 on a trading day.
- Prelim prices are never cached as closes, and the "Expired, waiting on broker" estimate keeps using official closes only.
- Existing settlements are migrated as final and as already checked against the broker, so upgrading raises no alerts about old trades.
