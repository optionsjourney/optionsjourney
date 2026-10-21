"""A stop the app watches instead of the broker holding it (bots slice 11; decision
0037): Tracker2's engine-watched stop (watch_rules.evaluate_armed) and its pass's
rules (watch_loop._armed_exit_pass, _track_stale). Slice 13 adds a group's whole P&L
(watch_rules._usd_stop_outcome) for a group with no single short to watch. Pure."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from optionsjourney.bots import exit_math
from optionsjourney.core.market import MARKET_TZ
from optionsjourney.core.sessions import session_close
from optionsjourney.trading.orders import Side

CONFIRM_PASSES = exit_math.CONFIRM_PASSES  # 2 passes in a row, 2 s apart
FIRES_PER_DAY = exit_math.REARMS_PER_DAY  # a side's stop fires at most 3 times a day
STALE_ALERT_AFTER = timedelta(seconds=60)  # watch_loop.STALE_ALERT_SECONDS
LAPSE_AFTER = timedelta(seconds=60)  # no watch for this long in the session: said
HEARTBEAT_EVERY = timedelta(seconds=15)  # how often the last watch is written down
# watch_loop.QUOTE_QUIET_MAX_SECONDS: a stream sends on change, so while it's healthy a
# quiet quote this old is still current (review M-4; the watch rules' too, #202).
QUIET_MAX = exit_math.QUIET_MAX


@dataclass(frozen=True)
class Reading:
    """One pass over one watched stop: fired, or why it couldn't tell (skipped)."""

    fired: bool
    skipped: str | None  # "stale", "bad_quote" or "incomplete": never fired
    mid: Decimal | None  # the short's mid; for a group, its value at the mids
    detail: str
    pnl: Decimal | None = None  # a group's P&L per unit at the mids


def evaluate(
    label: str, bid, ask, quote_at, now: datetime, filled_at, trigger: Decimal,
    quiet_ok: bool = False,
) -> Reading:  # fmt: skip
    """watch_rules.evaluate_armed for a stop: the short's mid at or above the trigger
    fires. A quote that isn't fresh (newer than the fill, and under 30 s old, or under
    QUIET_MAX while its stream is healthy: `quiet_ok`, watch_loop._quote_fresh) is
    stale; one without a usable bid and ask (bid 0 or more, ask above 0, not crossed)
    is bad. Neither ever fires."""
    limit = QUIET_MAX if quiet_ok else exit_math.STALE_AFTER
    if not _fresh_at(quote_at, now, filled_at, limit):
        return Reading(False, "stale", None, f"{label}: no fresh price")
    mid = exit_math.leg_mid(bid, ask, sell=True)
    if mid is None:
        return Reading(False, "bad_quote", None, f"{label}: no usable quote")
    mid = mid.quantize(Decimal("0.000001"))
    fired = mid >= trigger
    return Reading(
        fired, None, mid, f"{label} mid {mid:.2f} {'≥' if fired else '<'} stop {trigger}"
    )


def signed(amount: Decimal) -> str:
    """An amount to 2 places with a real minus sign: "−1.62"."""
    return f"{amount:.2f}".replace("-", "−")


def quote_fresh(quote, now: datetime, filled_at, quiet_ok: bool = False) -> bool:
    """One quote (.at) fresh enough to act on, by evaluate's rule: newer than the fill and
    under STALE_AFTER old (QUIET_MAX while its stream is healthy: `quiet_ok`)."""
    limit = QUIET_MAX if quiet_ok else exit_math.STALE_AFTER
    return quote is not None and _fresh_at(getattr(quote, "at", None), now, filled_at, limit)


def _fresh_at(quote_at, now: datetime, filled_at, limit: timedelta) -> bool:
    return not (
        quote_at is None or filled_at is None or quote_at <= filled_at or now - quote_at > limit
    )


def evaluate_group(
    label: str, legs: Sequence[tuple[exit_math.GroupLeg, object | None]], entry: Decimal | None,
    level: Decimal, now: datetime, filled_at, quiet_ok: bool = False,
) -> Reading:  # fmt: skip
    """watch_rules._usd_stop_outcome for a group the app watches: the group's P&L at
    its legs' mids (entry less exit_math.group_value) at or below the level fires.
    Every leg needs a fresh quote (evaluate's rule, leg by leg) or the whole group is
    stale: one leg with no quote, one older than the fill or past STALE_AFTER (QUIET_MAX
    while its streams are healthy) is enough. A leg without a usable bid and ask makes
    it bad (exit_math.leg_mid: a sold leg at 0/0 has no mid, one at 0 bid under a
    positive ask is ask / 2; a bought leg at 0/0 counts 0); no entry price yet makes it
    incomplete. None of those ever fires. `legs` pairs each leg with its quote (.bid,
    .ask, .at) or None."""
    if entry is None:
        return Reading(False, "incomplete", None, f"{label}: no entry price yet")
    limit = QUIET_MAX if quiet_ok else exit_math.STALE_AFTER
    for leg, quote in legs:
        if quote is None or not _fresh_at(getattr(quote, "at", None), now, filled_at, limit):
            detail = f"{label}: no fresh price for {leg.symbol.strip()}"
            return Reading(False, "stale", None, detail)
    for leg, quote in legs:
        if exit_math.leg_mid(quote.bid, quote.ask, leg.side is Side.SELL) is None:
            return Reading(
                False, "bad_quote", None, f"{label}: no usable quote for {leg.symbol.strip()}"
            )
    value = exit_math.group_value([leg for leg, _ in legs], {leg.symbol: q for leg, q in legs})
    if value is None:
        return Reading(False, "bad_quote", None, f"{label}: no usable quote")
    value = value.quantize(Decimal("0.000001"))
    pnl = exit_math.group_pnl(entry, value)
    fired = pnl <= level
    detail = (
        f"{label} P&L {signed(pnl)} {'≤' if fired else '>'} stop {signed(level)} "
        "(watched by the app)"
    )
    return Reading(fired, None, value, detail, pnl)


def lapse_since(last, now: datetime, session_open: datetime, earliest_stop: datetime):
    """When watching last happened, if that's more than LAPSE_AFTER ago; else None.
    The stops have needed watching since the later of the session's open and the
    earliest of them (`earliest_stop`: when the longest-watched one last changed); the
    last watch counts only if it came after that and today (New York)."""
    start = max(session_open, earliest_stop)
    today = now.astimezone(MARKET_TZ).date()
    if last is not None and last.astimezone(MARKET_TZ).date() == today and last > start:
        start = last
    return start if now - start > LAPSE_AFTER else None


def missed_sessions(last, earliest_stop: datetime, today: date) -> list[date]:
    """Bots slice 14: earlier sessions (before `today`, New York) a held trade's watched
    stops went wholly unwatched: each session that closed after the earliest stop was
    there, with no watch since its open (`last`: the last watch written down; None: none).
    Oldest first, at most the last 10 days."""
    missed = []
    day = today
    for _ in range(10):
        day -= timedelta(days=1)
        close = session_close(day)
        if close is None:
            continue
        opened = datetime.combine(day, time(9, 30), MARKET_TZ)
        if datetime.combine(day, close, MARKET_TZ) <= earliest_stop:
            break
        if last is not None and last >= opened:
            break
        missed.append(day)
    return list(reversed(missed))
