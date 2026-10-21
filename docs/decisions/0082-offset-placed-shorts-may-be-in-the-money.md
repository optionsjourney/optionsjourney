# 0082. A short placed by an offset may be in the money

- **Status:** Accepted
- **Date:** 2026-10-20
- **Refines:** question 7 of the bots slice 10 spec ("the run-time check still refuses a short that lands in the money"). The spec's form rule for spot offsets is unchanged.

## Context
The strike check (`bots/strikes.py`, `_sanity`) refused every sold option that landed in the money. That check exists to catch a premium or delta target that ends up deep in the money because the chain is stale or the target can't be met today. A short placed **by an offset** isn't searching for a target: it sits exactly where it was set. So an in-the-money strike there is the setup, not a mistake. The case that showed it is an in-the-money diagonal: a call bought 50 points below SPX and a call sold 10 points above that one. Our check refused it every time. Tracker2 changed its own check for this (commit c670b556, its run #340). Issue #206.

## Decision
- **A sold leg picked by spot offset or by offset (from another leg) is not refused for being in the money.** Shorts picked by premium or delta still are. Those already look only out of the money, so the check remains as a backstop.
- **The stale-chain check still applies to every short:** a short whose delta is 0.90 or more is refused as an error, nothing sent.
- **So a bot with an offset-placed short reads the chain's deltas**, as a bot with a delta leg already does. Without deltas the 0.90 check would do nothing for exactly the shorts it now protects.
- **An offset-placed short that is in the money with no delta is skipped, never guessed.** The app says so ("... is in the money and has no delta to check it against a stale chain; not entered.") and sends the once-a-day "deltas unavailable" alert. A short that is out of the money needs no delta, as before.
- **The form doesn't change.** A short put's spot offset is still a negative number and a short call's a positive one (slice 10, question 7), and `_by_spot` still rounds a short outward. So in practice the in-the-money short is a leg sold by offset from another leg, such as the diagonal above. Only a spot offset of 0 (no sign to check) can now leave a lone short a point or two in the money, as Tracker2 allows.

## Why
- This follows Tracker2, which exempts both its offset modes (`spot_offset`, `parent_offset`) and keeps the 0.90 delta check for all shorts.
- Requiring a delta for an in-the-money offset short is stricter than Tracker2, which skips the check when the delta is missing. Tracker2's chains nearly always carry deltas from its stream. Ours read them only when asked, and a deep in-the-money 0DTE option priced at its intrinsic value has no delta the app can work out from its price. A missing delta there is a warning sign, so we skip.
- We turned down loosening the form so a short's spot offset can point into the money (Tracker2 allows it). A wrong sign is an easy slip, and the in-the-money diagonal doesn't need it. It can be its own decision if someone asks for it.

## Consequences
- In-the-money diagonals and similar offset setups can be traded, with the 0.90 delta check still in front of them.
- Bots with a sold spot or offset leg now work out deltas at each entry and record them on the run's legs, like a delta bot. This is a little more work per entry. Such an entry can now be skipped for missing deltas, but only when that short is in the money.
- A premium or delta short in the money is still an error, as before.
