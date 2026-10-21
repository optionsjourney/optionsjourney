"""A group whose nearer legs expired (spec: bots slice 14, section 6; decision 0040).

A calendar or diagonal sells its near option and buys a far one. When the near legs
expire at the close, the group's stop (on the near short, or watched on the whole
group's price) ends with them, and its target can no longer be priced. As in Tracker2,
which keeps the rest in its journal position with nothing working on it, the far legs
are left to you: the bot never sells them (the author's choice, 2026-10-13). Right away
a `settle` exit row (state "left") records what's left, the group's armed target is
cancelled and a watched stop ended, and you're told. The integrity check
(bots/integrity.far_legs_held) keeps it on the Automation page. Validation keeps every
sold leg of a group expiring no later than its bought legs, so what's left is only
bought options.

Nothing here sends, cancels or replaces an order."""

import logging

from sqlalchemy import select

from optionsjourney.bots import exit_math
from optionsjourney.core.market import MARKET_TZ
from optionsjourney.db.models import BotExit, BotRun
from optionsjourney.services import bot_entry, bot_exits, bot_store
from optionsjourney.services.bot_integrity import LOOKBACK
from optionsjourney.trading.orders import Side, plain

logger = logging.getLogger(__name__)
HOLDING = ("filled", "partial")
SETTLE = "settle"
ANNOUNCED = "the nearer legs expired"
LEFT = "left"  # the far legs are yours to sell or keep
BUSY = ("placing", "firing", "chasing")


def settle_pass(deps) -> None:
    """Each monitor pass, after bot_exits.monitor_pass: record and announce, once, every
    group whose nearer legs expired with far legs still held. Never raises."""
    try:
        now = deps.now()
        with bot_exits.EXITS_LOCK:
            for run_id, positions in _split_runs(deps, now):
                for position in positions:
                    try:
                        _settle(deps, run_id, position)
                    except Exception:  # noqa: BLE001 -- one group never stops the others
                        logger.exception("couldn't settle run %s group %s", run_id, position)
    except Exception:  # noqa: BLE001 -- the monitor's other steps go on
        logger.exception("the settle pass failed")


def _split_runs(deps, now) -> list[tuple[int, list[int]]]:
    """(run id, its split groups) for each run holding fills, not being entered, filled
    within the look-back, with a group whose nearer legs expired and far legs didn't."""
    since = (now.astimezone(MARKET_TZ) - LOOKBACK).date()
    with bot_entry._active_lock:
        active = set(bot_entry.ACTIVE)
    out = []
    with deps.session_factory() as session:
        for run in session.scalars(
            select(BotRun).where(
                BotRun.status.in_(HOLDING), BotRun.trading_date >= since, BotRun.paper.is_(False)
            )
        ):
            if not run.filled_legs or not run.legs or run.id in active:
                continue
            groups: dict[int, list[str]] = {}
            for leg in run.legs:
                groups.setdefault(leg["group"], []).append(leg["symbol"])
            split = [p for p, symbols in sorted(groups.items()) if exit_math.is_split(symbols, now)]
            if split:
                out.append((run.id, split))
    return out


def _settle(deps, run_id: int, position: int) -> None:
    with deps.session_factory() as session:
        rows = list(
            session.scalars(
                select(BotExit).where(BotExit.run_id == run_id, BotExit.group_position == position)
            )
        )
        if any(r.kind == SETTLE for r in rows):
            return  # said once
        if exit_math.closed_whole((r.kind, r.state) for r in rows):
            return  # a close took the whole group before its nearer legs expired
        if any(r.state in BUSY for r in rows) or bot_exits.held_back(rows):
            return  # a close or a placing is still going on: next pass
        _announce(deps, session, run_id, position, rows)


def _announce(deps, session, run_id: int, position: int, rows) -> None:
    """Write the group's settle row ("left") and say so. The group's armed target is
    cancelled and a watched stop ended; nothing is sent."""
    run = session.get(BotRun, run_id)
    now = deps.now()
    own, _ = exit_math.group_legs(run.legs, position)
    expired, live = exit_math.split_legs([leg.symbol for leg in own], now)
    # A leg a wing row already took (sold, being sold, or left open after a stop, as the
    # group is set to) is that row's, not this one's.
    taken = {plain(bot_exits._wing_leg(r)[1].symbol) for r in rows if r.kind == "wing"}
    far = [leg for leg in own if leg.symbol in live and plain(leg.symbol) not in taken]
    if not far:
        return
    units = bot_exits.group_units(session, run, position)
    for row in rows:
        if row.kind == "target" and row.state == "armed":
            row.state = "cancelled"
            bot_exits._log(row, deps, event=ANNOUNCED)
        elif row.kind == "stop" and row.state == bot_exits.WATCHING:
            row.state = "expired"
            bot_exits._log(row, deps, event=f"{ANNOUNCED}; no longer watched")
    session.add(BotExit(
        run_id=run_id, group_position=position, kind=SETTLE, state=LEFT,
        short_symbol="", long_symbol=far[0].symbol, anchor_symbol=None,
        legs=[{"symbol": g.symbol, "side": g.side.value, "ratio": g.ratio} for g in far],
        watched=False, contracts=units, rearms=0, created_at=now, updated_at=now,
        log=[{"at": now.isoformat(), "event": ANNOUNCED, "expired": list(expired),
              "live": list(live), "units": units}],
    ))  # fmt: skip
    session.commit()
    bot = bot_store.load(deps.session_factory, run.bot_id)
    group = next((g.name for g in bot.groups if g.position == position), f"Group {position + 1}")
    them = "it" if len(far) == 1 else "them"
    detail = (
        f"{bot_exits._listed(expired)} expired at the close. {bot_exits._names(far)} "
        f"{bot_exits._is(far)} still held, with no stop or target now. The bot leaves "
        f"{them} to you: sell {them} or keep {them} at your broker."
    )
    sold = [leg for leg in far if leg.side is Side.SELL]
    if sold:  # validation refuses this; a row written some other way says so
        detail += f" {bot_exits._names(sold)} {bot_exits._is(sold)} sold."
    # This should never happen: a calendar must have a timed close (validation). So it's
    # a safety alert, to every channel (decision 0035).
    detail = (
        "The timed close didn't close it before its near legs expired. "
        + detail
        + " Check your broker."
    )
    bot_exits._alert(deps, f"{bot.name}: URGENT: {group}'s near legs expired, far legs held",
                     detail, bot.id, run_id=run_id)  # fmt: skip
