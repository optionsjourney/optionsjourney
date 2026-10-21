# 0067. Legs up to 1,100 days out, chases up to 60 tries

- **Status:** Accepted
- **Date:** 2026-10-17

## Context
Decision 0040 capped a leg at 60 days to expiration, and the chase bounds capped tries at 20. Tracker2 has neither cap. A test import of a full Tracker2 export found bots these caps refused: a 90 DTE short put, and FOMC bots whose entry chase takes 26 tries. LEAPS run past a year (#160, #161).

## Decision
- **A leg can be 0 to 1,100 days to expiration** (`MAX_DTE`, about three years). The same cap applies to a trade handed to a bot and to the next-listed expiration with Exact DTE off.
- **A chase can take 1 to 60 tries.**
- This replaces 0040's 60-day cap; the rest of 0040 stands.

## Why
- **Follow Tracker2.** Real bots need more; a cap still stops a mistyped value.
- **The look-backs stay cheap.** The passes that watch held trades look back `MAX_DTE + 10` days, but only at runs still marked as holding, and expired ones are dropped first.

## Consequences
- The held-trade passes (`bot_watch`, `bot_integrity`) now look back 1,110 days.
