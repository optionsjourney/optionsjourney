"""Turning a leg's days to expiration into a listed expiration date (bots slice 14;
Tracker2's chain.resolve_expiration and resolve_leg_expirations). Pure.

Never today + DTE: only the expirations the broker lists. With exact DTE only the
date that many calendar days away will do; without it, the first listed date at or
after it, however far (Tracker2's tolerance None), and never an earlier one: a 1 DTE
leg on a Friday must not trade the same day."""

from collections.abc import Iterable
from datetime import date, datetime

from optionsjourney.bots.definition import MAX_DTE


def _date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError, TypeError:
        return None


def _listed(available: Iterable) -> list[date]:
    return sorted({d for d in (_date(v) for v in available or ()) if d is not None})


def resolve(
    available: Iterable, dte: int, today: date, exact: bool, max_dte: int = MAX_DTE
) -> tuple[date | None, str | None]:
    """(the expiration, None), or (None, why not). Expired dates in the list are never
    taken (a cached chain can still hold them). DTE 0 is always exact: a same-day leg
    never becomes tomorrow's. Nothing more than `max_dte` days out is ever taken (the
    passes that watch held trades look back that far)."""
    exact = exact or dte == 0
    listed = _listed(available)
    if not listed:
        return None, "No expirations listed."
    future = [d for d in listed if d >= today]
    if not future:
        return None, "No expiration listed from today on."
    if exact:
        best = min(future, key=lambda d: (abs((d - today).days - dte), d))
        if (best - today).days != dte:
            return None, (
                f"No expiration at exactly {dte} DTE (the nearest is {best.isoformat()}, "
                f"{(best - today).days} DTE)."
            )
        return best, None
    later = [d for d in future if (d - today).days >= dte]
    if not later:
        last = future[-1]
        return None, (
            f"No expiration at {dte} DTE or later (the last listed is {last.isoformat()}, "
            f"{(last - today).days} DTE)."
        )
    if (later[0] - today).days > max_dte:
        return None, (
            f"The first expiration at {dte} DTE or later is {later[0].isoformat()}, "
            f"{(later[0] - today).days} DTE: more than {max_dte}."
        )
    return later[0], None


def resolve_legs(
    dtes: Iterable[int], available: Iterable, today: date, exact: bool
) -> tuple[dict[int, date], list[str], list[str]]:
    """({dte: expiration}, problems, notes) for every distinct DTE, nearest first.
    Without exact DTE two DTEs can land on one date (2 and 3 DTE on a Tuesday are both
    Friday): the later one moves to the next listed date, keeping the order they were
    set in, with a note; with nothing later listed, that's a problem."""
    listed = _listed(available)
    found: dict[int, date] = {}
    problems: list[str] = []
    notes: list[str] = []
    previous: tuple[int, date] | None = None
    for dte in sorted({int(d) for d in dtes}):
        expiration, reason = resolve(listed, dte, today, exact)
        if expiration is None:
            problems.append(reason)
            continue
        if previous is not None and expiration <= previous[1]:
            moved = next((d for d in listed if d > previous[1]), None)
            if moved is not None and (moved - today).days > MAX_DTE:
                problems.append(
                    f"{dte} DTE resolves to {expiration.isoformat()}, the same as "
                    f"{previous[0]} DTE, and the next expiration is more than {MAX_DTE} DTE."
                )
                continue
            if moved is None:
                problems.append(
                    f"{dte} DTE resolves to {expiration.isoformat()}, the same as "
                    f"{previous[0]} DTE, and no later expiration is listed."
                )
                continue
            notes.append(
                f"{dte} DTE resolved to {expiration.isoformat()}, the same as {previous[0]} "
                f"DTE; moved to the next expiration, {moved.isoformat()} "
                f"({(moved - today).days} DTE)."
            )
            expiration = moved
        found[dte] = expiration
        previous = (dte, expiration)
    return found, problems, notes
