# 0030. Bots are built as thin, working slices, stored in Tracker2's tables

- **Status:** Accepted
- **Date:** 2026-10-12

## Context
Decision 0027 splits the bot engine into five pieces. Piece 1, the order path, is built. Pieces 2–4 (the link to the journal, trade management, entries) built in order would leave nothing usable until the last one. A bot's definition also needs a home: Tracker2 keeps it in tables (legs, leg groups, entry times, chase settings), while decision 0005 shares bots as JSON files.

## Decision
- **Slices, not layers.** The engine is built one automation at a time, each working end to end:
  - an editor page
  - a clock that fires entries
  - strike selection and the entry order
  - the journal link
  - the exits

  The first slice is an SPX 0DTE iron condor with strikes by premium and offset, a stop per side and an app-watched profit target. Later slices widen it toward everything Tracker2's bots do.
- **Tracker2's table layout** for bot definitions: `bots`, `bot_legs`, `bot_leg_groups`, `bot_entry_times`, `bot_chase`. Runs and exits get their own tables too (`bot_runs`, `bot_exits`). One simplification: **one group per side** of a trade (the short and its wing), where Tracker2 gives each leg its own group.
- **Decide by Tracker2:** where a question comes up, the bot does what Tracker2's bot does, unless that's unsafe or there's a stated reason.

## Why
- The author can use and judge the editor and a real bot from the first slice.
- Tables make each part of a bot easy to query and change one field at a time, and they match Tracker2, which keeps porting simple.
- Turned down:
  - **Layer by layer:** nothing usable for a long time.
  - **One JSON document per bot:** easy to share, but further from Tracker2 and harder to query.
  - **A fixed iron condor form:** would need rewriting for the second strategy shape.

## Consequences
- Sharing bots as files (0005, #33) needs an export and import between the tables and the file format.
- Each slice gets its own spec, plan and build.
