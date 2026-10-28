# 0121. An entry time keeps trying, then tries a fallback premium

- **Status:** Accepted
- **Date:** 2026-10-28

Issue #314.

## Context
A user's bot aims its sold legs at a premium (say 2.80) and has a **Min short width**. When the sold strikes land too close together they're moved apart, the credit drops, and **Premium bounds** skips the entry. At an entry time that check runs once, so one bad moment costs the day's trade. An entry window already tries again every minute. Tracker2 checks once, as we did, and has no fallback.

## Decision
- **Keep trying (minutes),** a setting on each bot under Schedule, 1 to 30, blank for one try. An entry time skipped because the price is outside **Premium bounds**, or because no strikes are left to reach the **Min short width**, picks again every 10 seconds until that time is up. Every other skip still skips at once. Each new try checks the guards again first (Stop all bots, the session, the limits and the filters).
- **Fallback premium,** a box on each sold leg picked by premium. It's shown only while **Min short width** is set, and then the form won't save without it (it's outlined in red). It must be above $0 and below the leg's target.
- **The last try uses the fallback.** Once the tries are used up (straight after the one try when Keep trying is blank, or on an entry window's last minute), one more try aims those legs at their fallback premium. The premium minimum isn't checked on that try; the maximum and every other check are. The run's Reason says how many tries there were and whether the last one was at the fallback.
- **Preview order** also shows what the fallback try would pick now.

## Why
- The user asked for it, and the two skips it covers can change from one second to the next, as quotes move.
- Taking the smaller credit only on the last try keeps the full premium as the aim for as long as possible.
- A fallback that still had to meet the premium minimum would always fail it: a lower premium is a smaller credit.

## Trade-off
- A bot file with a min width and no fallback still imports and saves, so Tracker2 files and shared bots keep working. It just has no fallback until you open it and fill the red boxes.
- While it keeps trying, the run counts toward **Max entries per day**, as any entry in progress does.
