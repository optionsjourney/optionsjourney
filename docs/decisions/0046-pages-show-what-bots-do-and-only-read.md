# 0046. The pages that show what bots do only read; the account stream waits

- **Status:** Proposed (built under the author's standing approval for the parity roadmap; waits on review)
- **Date:** 2026-10-14
- **Builds on:** [0028](0028-one-order-path-and-two-pause-switches.md) (one order path), [0030](0030-bots-built-as-thin-slices-in-tracker2s-tables.md) (thin slices, decided by Tracker2)

## Context
Tracker2 shows what its bots do in several places: a run's orders as one timeline (`run_view.order_timeline`), a watch-exit banner refreshed every 2 s, today's schedule (`/api/bots/schedule`), the VIX filters as they read now, P/L % and days on a bot's record, list search and bulk tagging, and journal chips pushed live over Socket.IO (`override_tags.notify_changed`). It also wakes its entry chase and exit passes from its TastyTrade account stream within about a second of a fill (`entry_waiters`, `fill_router`), with a watchdog alert when the stream drops. This app's run page showed the raw chase log, and it had none of the rest. It already runs a Socket.IO server for the chart pages, and it has no account stream: its streams are quotes only.

## Decision
- **Every new page part only reads.** The timeline, the banner and the chips read the database; the schedule reads the database, the stored calendars and the clock's memory; the filter check reads two quotes through the filters' own feed. Bulk tagging writes tags only.
- **Live updates use what the app has:** the journal's chips are pushed over the existing Socket.IO by one listener on the app's database sessions (after any commit that changes a trade's overrides or an exit's state), so no change path can forget to call it; the run page's banner polls a JSON endpoint every 2 s, as Tracker2's does.
- **The account stream waits.** Fill notices are not built in this slice. Building TastyTrade's account streamer is a new authenticated connection, its messages can't be checked here without a live account, and it would change when the order path reads its orders. The entry chase and the monitor keep polling as before.

## Why
- Pages that only read can't put an order at risk, whatever they show.
- A commit listener covers the run page, the watch rules, profit and time actions and restores at once; Tracker2 needs a call after each.
- Tracker2's doorbell is safe by design (a notice only means "read the order now"), but the stream under it must be watched against a live account before the order path relies on it.
- Turned down: socket pushes for the banner (polling one run is simpler and is what Tracker2 does); a new account stream built unseen.

## Consequences
- Existing bots trade exactly as before; no migration.
- A missed push only leaves a chip stale until the next reload.
- Later, with the author and a live account: the account streamer, `EntryWaiters` in the chase (the wait a notice ends early), the router to one bot's exit pass, and the "account stream down" alert.
