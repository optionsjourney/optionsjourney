# 0084. Close group counts single contracts of a ×N group, with no split close

- **Status:** Accepted
- **Date:** 2026-10-20

## Context
Tracker2 (commit cc60dcf9) found that its journal's Close group counted in bot units, so a group of 3 short calls per unit, held at 1 unit, could only close all 3. It added a **split close**: for a group holding one untouched unit of a ×N ratio, the close box runs 1 to N contracts; a sub-unit close cancels the group's stop and target, chases the contracts, then **rebases the run** (each leg's per-unit quantity drops by what filled, the original kept as `entry_quantity`) so every later step sizes to what's held, puts the stop back, and closes the same share of the paired long wing. Groups with a $ stop or target stay whole-unit only, because the rebase would re-price a per-unit $ level per contract and loosen it. Issue #209 asked for the same here.

## Decision
- **No split close and no rebase.** Here a group's unit has always been its legs' ratios over their gcd (slice 13, decision 0039; `exit_math.group_legs`), and the units held are the run's contracts × that gcd, less what your closes closed (`bot_exits.group_units`). One bot contract of a ×3 one-leg group is therefore 3 units of one contract each. Close group (`bot_manage.close_part`) already offers 1 to 3, cancels the stop and target, chases, records what filled as `reduced`, and puts the stop and target back on what's left (`_protect_rest`). It does this on every path: filled, part filled, nothing filled, cancelled, and cut off by a restart. The tests for #209 lock that in for ×N groups.
- **The Close box says "contracts" on a one-leg group** ("Close 3 of 3 contracts"; `SideView.unit`), since one unit is one contract there. A group of two or more different options keeps counting whole units (sets of its reduced ratio), as Tracker2 does: its lot is the same gcd.
- **A $ stop needs no exception.** The level each contract stops at doesn't change when part of the group closes, because the unit is one contract before and after.

## Why
- Tracker2's split close and rebase exist because its unit is the unreduced ratio. Copying them would add a second way to count what's held, and every exit path would have to read it, for a case the existing unit already covers.
- Reusing `close_part` keeps one close path, one central order check and one re-protection rule, all already tested.

## Consequences
- Nothing changes in the database; no migration.
- Not followed from Tracker2: a close you ask for doesn't close the same share of the group's **paired** group (the "When its stop fills, also close" wing). That is true of whole-unit closes here too; only a stop fill closes the paired group. A separate issue if wanted.
- A $ stop on a ×N one-leg group is a dollar amount per contract here ("$2.00 a contract"), where Tracker2 reads it per unit of N contracts. Bots copied from Tracker2 with such a stop would sit looser here; that is a separate question, unchanged by this decision.
