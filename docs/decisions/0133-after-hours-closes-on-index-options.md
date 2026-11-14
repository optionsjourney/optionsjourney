# 0133. A close on index options works after hours, a close asked for in a gap is scheduled, and a stop the broker won't take waits for 09:30

- **Status:** Accepted
- **Date:** 2026-11-14
- **Adds to:** 0028 (one order path), 0048 (which exit rests at the broker), 0091 (after-hours marks) and 0131 (the Dynamic stop).

## Context
SPX, SPXW, XSP, VIX, VIXW, RUT and RUTW options also trade in Cboe's Global Trading Hours (GTH), from 20:15 New York time on the evening before a trading day to 09:25 on it. TastyTrade takes limit orders then with the "GTC Ext Overnight" time in force, which this app already had (`TimeInForce.GTC_OVERNIGHT`, refused by the central check for anything but TastyTrade index options). But every close you asked for from the Manage box was refused outside 09:30-16:00, so a trade could only be closed at night from the broker's own app. TastyTrade also refuses every stop order outside the regular session (`tif_session_denied`, probed in Tracker2 on 2026-10-26 and 2026-11-04), so a stop the app had to place at night (a re-arm after the broker dropped one, or a stop put back after a close) was refused and the side left with none. Issue #330, ported from Tracker2's after-hours work of 2026-11-04 and its follow-up fixes.

## Decision
**Which trades.** A trade is after-hours capable when it is at TastyTrade and every leg is a Cboe index option (`bots/after_hours.capable`). Everything else (Schwab, stock and ETF options, futures) keeps the regular-session rules and their message.

**The window** (`bots/after_hours.close_window`, pure, New York time, the NYSE calendar):

| When you click Close | What happens |
|---|---|
| 09:30 to the close | Closes now, as before. |
| 20:15 to 09:25 (GTH) | Closes now with GTC Ext Overnight limits. |
| 16:00 to 20:15, 09:25 to 09:30, a weekend or holiday | Scheduled for the next session's open plus the opening pause: 20:16 or 09:31. |
| Every leg expired before the trading day the next session belongs to | Refused. |
| A year the calendar doesn't cover, off-hours | Refused (never guessed). |

An overnight session belongs to the next trading day. No GTH runs into a holiday or a Saturday: Friday night schedules for Sunday 20:16.

**16:00-16:15 ships off** (`POST_CLOSE_ENABLED = False`), as in Tracker2: two checks come first (a paired close's resting DAY sale must be settled before a 16:00-16:15 close, and TastyTrade must be seen to work a 16:00-16:15 index limit). Until then that quarter hour is part of the scheduled gap.

**An overnight close** (`bot_exits.fire_close(overnight=True)`, `close_group`) is the daytime close with three differences: every order of the chase is a GTC Ext Overnight limit (the replaces keep it); it stops when the overnight session ends at 09:25; and it never goes to market, to the natural price or to a leg-out. Market is refused outside the regular session. A close claimed overnight that starts after 09:25 sends nothing and puts the stop and target back as they were.

**Scheduled closes** live in their own table (`bot_scheduled_closes`, migration 0059), never in the run's timed close and never as an exit row. One a trade. The monitor's pass (every 30 s, and at startup) claims a due one with a conditional update, then calls the same Close as your click, which checks Freeze, the window and what's held again. A trade that closed first is skipped quietly; one Freeze or anything else stops is marked failed and alerted, and isn't tried again (you schedule it again); one that comes due in another gap (the app was off) is marked rescheduled and a new one is scheduled. A partial unique index keeps one waiting close per trade even when two clicks race. A claim cut off by a restart is alerted after 2 minutes. Cancel is on the Manage box.

**Stops held for the open.** Wherever a resting stop would be sent (after a fill, a re-arm, a stop put back after a close, Restore missing exits), a capable trade outside the regular session writes it `pending` with `bot_exits.stop_at_open`, sends nothing and says so. The monitor's usual pending placement leaves it alone; `place_stops_at_open` places it from 09:30:05 on the 30-second monitor pass (so within about a minute of the open), sized to what's held then, never while Freeze is on. Refused at the open, it is tried again from 09:45; refused again, it's left failed with an UNPROTECTED alert. A stop the app watches (a group's P&L, or the short while the target rests) isn't affected: nothing is sent for it.

**Stop changes off-hours are refused** for a capable trade (Change stop, Closing order and the Dynamic switch, switching a stop on) before anything is cancelled, as Tracker2's 0e700c4e. Switching a stop off is only a cancel and stays allowed.

**A resting target placed off-hours** on a capable trade rests GTC Ext Overnight, so it works overnight and in the day (Tracker2's `exit_tif_choice`).

**The Dynamic stop** already moves only in the regular session, as Tracker2's does; nothing changes.

## Why
- Tracker2's behaviour, ported with its fixes: scheduled closes start after the opening pause (142ceef4); only an every-leg-expired trade is refused (7d1e2f67); stop edits are refused rather than half done (0e700c4e); a lost scheduled close is alerted (ce8cbbd9).
- Holding a stop as `pending` reuses a state every reader already treats as "a stop, not at the broker yet": a close's claim pulls it, Restore sees it present, the side view shows it. A separate flag keeps it away from the 30-second pending placement, so the broker isn't asked for a stop it refuses every 30 seconds overnight.
- A scheduled close in its own table can't be counted by the thousands of lines that read exit rows.

## Where this differs from Tracker2 (this app's calls)
- **An overnight close that runs out of tries is cancelled, not left working.** This app's chase always cancels its last order and confirms it; leaving a GTC Ext Overnight close working into the morning would need a new state for every close reader. The stop comes back (held for 09:30) and the target goes back.
- **No profit target is rested in place of a held stop.** In Tracker2, while the stop waits for 09:30 the profit target rests overnight, and the 09:30 job swaps them back. Here a group whose stop rests keeps its target armed (the app fires it in the regular session); a GTC stop at the broker doesn't trigger overnight either, so the trade is protected at the open as before, a few seconds later. Swapping which exit rests twice a night would add a new path to every close and is left out.
- **Market is refused for any scheduled close**, not only one that starts overnight: a 09:31 market close is still a market order at the open's wide quotes.
- **No overnight entries.** Trade now picks strikes from the underlying's price, and this app has no off-hours index price (Tracker2 reads one from /ES and refuses an overnight entry when it can't, 3d902b34): every overnight Trade now would be refused anyway. A Trade page entry sent through a bot would need a cancel-by time before the 09:30 auction in the entry chase, and its trading day moved to the next one inside the central check's daily limits; those are left for #331 rather than changed quietly. A plain GTC overnight limit order from the Trade page already works.
- The 09:30 job is the 30-second monitor (from 09:30:05, and at startup) rather than separate jobs at 09:30:05 and 09:45.

## Consequences
- A trade on index options at TastyTrade can be closed from the Manage box at night, or scheduled for when its options trade again.
- After an off-hours close that doesn't fill, the trade has no stop at the broker until just after 09:30; you're told so in the alert and on the Manage box.
- Migration 0059 adds `bot_exits.stop_at_open` (false everywhere) and the `bot_scheduled_closes` table.
- Turning on 16:00-16:15 is a code change after the two checks above.
