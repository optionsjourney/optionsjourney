# 0065. Keep conceding and Ignore bid-less longs, as Tracker2

- **Status:** Accepted
- **Date:** 2026-10-17

## Context
Moving the author's Tracker2 bots here showed two exit-chase settings this app left out. **Keep conceding** is an ending for a close that hasn't filled after its last try; until now it imported as "give up" with a note. **Ignore bid-less longs** leaves a bought leg with no bid out of a close; until now it was dropped with a note (#157).

## Decision
Both follow Tracker2 (`exit_chase.keep_conceding`, `close_jobs.select_close_legs`).

- **Keep conceding** (`keep_conceding`, shown as "Keep lowering the price until it fills") is a third exit-chase ending, beside Give up and Natural price, then leg out. After the last try the close keeps stepping one step further each wait, with no try limit, but never past the current natural price, re-read each step. It ends on a fill, Cancel, or the market's close (then the stop goes back, as Give up). A one-leg group's switch to market runs first; a watched stop's close still goes natural, then legs out.
- **Ignore bid-less longs** is an exit-chase option, off by default: a close leaves out any bought leg whose bid is missing or zero, so it stays open to expire worthless; sold legs are always closed. It is forced off for a close you start by hand and for partial closes, as in Tracker2, and the run's timeline says which legs were left out and why. (Built after Keep conceding.)
- A Tracker2 file carries both settings as they are, with no note.

## Why
- **The same bot behaves the same here.** Bots moved from Tracker2 keep the ending and option they were tuned with.
- **Never past the natural.** Past it, a close only pays more for nothing.

## Consequences
- Defaults don't change: existing bots keep their ending, and new bots still start with Natural price, then leg out, and Ignore bid-less longs off.
- A Keep conceding close can work for the rest of the session; Cancel stops it.
