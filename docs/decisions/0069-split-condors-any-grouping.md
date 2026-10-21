# 0069. Split iron condors whatever their leg grouping

- **Status:** Accepted
- **Date:** 2026-10-17

## Context
The **Split iron condors** switch showed only for an iron condor in exactly two groups (a put spread and a call spread). Tracker2's MEIC-style bots put each leg in its own group (four groups) so each short can have its own stop. Imported, they lost the switch, and their trades couldn't be split in the journal (#165). In Tracker2, `split_ic` is a strategy setting that doesn't depend on grouping (`transaction_processor._should_split_ic`).

## Decision
- A bot is an **iron condor shape** (`definition.is_iron_condor_shape`) when it has four ratio-1 legs, a sold and a bought put and a sold and a bought call, grouped any way in which no group mixes puts and calls. The switch shows, is saved and works for any such bot.
- The journal already splits a condor by type (put spread, call spread), so grouping never mattered there.
- Wherever the app counts or matches a split condor's sides, a side is the put legs or the call legs, not a group: **max open trades** counts each side once (a side is open while any of its groups is), and **Match trades** compares a journal half with the bot's put legs or call legs.
- The old two-group test (`is_iron_condor`) stays for what is about two groups: naming them "Call side" and "Put side".
- Tracker2's bot file doesn't carry `split_ic` (it lives on Tracker2's strategy settings), so an imported bot keeps this app's default: on.

## Why
- **Follow Tracker2**, where splitting doesn't depend on grouping.
- Per-leg stops and split journal sides are separate choices; one shouldn't take away the other.

## Consequences
- A per-leg condor's bought leg with no exit of its own keeps its side open (for max open trades) until it expires, as an unsplit per-leg bot already did.
