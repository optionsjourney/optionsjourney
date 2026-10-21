# 0081. Delta and ratio rules hold 30 seconds; a quiet quote on a live stream stays fresh

- **Status:** Accepted
- **Date:** 2026-10-20
- **Builds on:** [0034](0034-watch-rules-read-delta-from-the-quote-stream.md) (watch rules read delta from the quote stream; a price is trusted for 30 seconds) and [0080](0080-schwab-answers-when-tastytrade-is-down-or-stale.md) (which broker's quote is used). 0034's "a price for 30" becomes "30, or up to 5 minutes while the stream is live"; its text is unchanged.

## Context
Two problems with the watch rules, both fixed in Tracker2 during its 10–20 October 2026 review (issues #201 and #202, parent #197):

- **Too quick to close.** Every rule closed the trade after two checks in a row, two seconds apart. The delta and short/long ratio rules read single option quotes and deltas, so a few seconds of one bad quote (for example the wide quotes just after the open) could close a whole trade. Tracker2 saw exactly that.
- **Too quick to pause.** A leg's quote counted as stale 30 seconds after it arrived. The broker's stream sends an option's quote only when its bid or ask changes, so a quiet, far-out option on a perfectly healthy feed went stale, the rules paused, and you got "watch rules paused" and "resumed" alerts for nothing. The app's watched stops already had the fix; the watch rules, profit actions and app-watched targets didn't.

## Decision
- **Hold:** the delta exit, each leg's delta exit and the S/L ratio exit close the trade only when their breach has held on every check for 30 seconds (and at least two checks). One check back inside the level starts the 30 seconds again. The underlying move, the tested-short rules, profit and loss actions, targets and watched stops still need only two checks in a row. Paper trades follow the same rule.
- **Quiet quotes:** a leg's quote is fresh if it arrived after the fill and is under 30 seconds old, or, while the stream it came through is live, under 5 minutes old. "Live" is the same test the watched stops use: that broker's stream is connected and the bot's underlying (SPX for an SPX bot) has updated through it in the last 30 seconds. This applies to the watch rules, profit actions and targets the app watches. Deltas (150 seconds) and the underlying's own price (30 seconds) keep their limits.

## Why
- Both are Tracker2's behaviour (its `HOLD_SECONDS`/`HOLD_KINDS` and `QUOTE_QUIET_MAX_SECONDS`), and this app follows Tracker2 on bots.
- 30 seconds of a breach on every check is far harder for one bad quote to fake, and still quick next to how fast a delta or ratio level is usually reached. Dollar-style levels (stops, targets, profit actions, the underlying) are left fast because a stop that waits costs money.
- A connected stream whose underlying is still updating proves the feed itself is working, so an unchanged option quote is the current one. The 5-minute cap still catches a single symbol the stream has silently dropped.
- Turned down: **making every rule hold 30 seconds** (slows down the protective rules for no gain), and **refreshing a quote's time on every stream heartbeat** (it would hide a dropped symbol for ever).

## Consequences
- A delta or ratio exit now closes about 30 seconds later than before. A genuine move that reverses within those 30 seconds no longer closes the trade.
- Fewer false "watch rules paused" alerts on quiet options. If the stream disconnects or the underlying stops updating, the 30-second limit is back at once.
- A quote can be up to 5 minutes old when a rule acts on it, but only while the feed is demonstrably delivering; a stale feed still pauses the rules.
