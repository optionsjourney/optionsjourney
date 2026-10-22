"""The pre-open check of trades held overnight (spec: bots slice 14, section 7;
decision 0040).

Tracker2 syncs its journal at 09:20 so its session-gated exits never act on a position
the broker closed overnight (overnight_sync.run_catchup). This app's exits read the
broker, never the journal, so there's nothing to sync; what's worth doing before the
open is the check. At 09:20 New York on each trading day (once, remembered in the
`_preopen_checked` setting), and at startup, every held trade is compared with the
broker: each leg still held the trade's way at its size, each stop in place (a resting
stop's order still working at the broker). One alert says what doesn't match, or, at
09:20, that all is well.

Alert only, never repair (Tracker2's integrity rule): nothing here sends, cancels or
changes an order or a run. The monitor re-arms a stop the broker dropped; the rest
is yours."""

import logging
from dataclasses import dataclass, field
from datetime import time

from sqlalchemy import select

from optionsjourney.bots import exit_math, timing, underlyings
from optionsjourney.core.market import MARKET_TZ
from optionsjourney.core.sessions import session_close
from optionsjourney.db.models import Bot, BotExit, BotLegGroup, BotRun, Setting
from optionsjourney.services import bot_entry, bot_exits
from optionsjourney.services.bot_integrity import LOOKBACK
from optionsjourney.trading.orders import OrderStatus, Side, plain

logger = logging.getLogger(__name__)
HOLDING = ("filled", "partial")
PREOPEN = time(9, 20)  # Tracker2's catch-up time
CHECKED_KEY = "_preopen_checked"  # the New York date the 09:20 check last ran


@dataclass
class _Group:
    run_id: int
    bot_name: str
    name: str
    legs: list  # (symbol, signed contracts the run should hold)
    stop: BotExit | None  # its latest stop row, if it ever had one
    watched: bool
    split: bool
    closed: bool
    off: bool
    american: bool = False  # SPY, QQQ, IWM: a short can be assigned early (slice 16)
    target: BotExit | None = None  # bots slice 17: a futures run's resting profit target
    pt_only: bool = False  # a futures run: its profit target rests, it has no stop


@dataclass
class _Account:
    broker: str
    account: str
    groups: list = field(default_factory=list)


def held_runs(deps, now) -> list[int]:
    """Runs holding fills from before today (New York), not being entered, with a leg not
    past its expiry: the trades held overnight."""
    today = now.astimezone(MARKET_TZ).date()
    since = today - LOOKBACK
    with bot_entry._active_lock:
        active = set(bot_entry.ACTIVE)
    with deps.session_factory() as session:
        return [
            run.id
            for run in session.scalars(
                select(BotRun)
                .where(
                    BotRun.status.in_(HOLDING),
                    BotRun.trading_date >= since,
                    BotRun.paper.is_(False),
                )
                .order_by(BotRun.id)
            )
            if run.filled_legs
            and run.legs
            and run.id not in active
            and run.filled_at is not None
            and run.filled_at.astimezone(MARKET_TZ).date() < today
            and any(not exit_math.after_expiry(leg["symbol"], now) for leg in run.legs)
        ]


def _accounts(deps, run_ids: list[int], now) -> dict[tuple[str, str], _Account]:
    out: dict[tuple[str, str], _Account] = {}
    with deps.session_factory() as session:
        for run_id in run_ids:
            run = session.get(BotRun, run_id)
            bot = session.get(Bot, run.bot_id)
            names = {
                g.position: g.name
                for g in session.scalars(select(BotLegGroup).where(BotLegGroup.bot_id == bot.id))
            }
            broker, account = run.broker or bot.broker, run.account_number or bot.account_number
            entry = out.setdefault((broker, account), _Account(broker, account))
            rows = list(session.scalars(select(BotExit).where(BotExit.run_id == run_id)))
            for position in sorted({leg["group"] for leg in run.legs}):
                own, _ = exit_math.group_legs(run.legs, position)
                mine = [r for r in rows if r.group_position == position]
                stops = [r for r in mine if r.kind == "stop"]
                targets = [r for r in mine if r.kind == "target"]
                units = bot_exits.group_units(session, run, position)
                entry.groups.append(_Group(
                    run_id, bot.name, names.get(position) or f"Group {position + 1}",
                    [(leg.symbol, units * leg.ratio * (-1 if leg.side is Side.SELL else 1))
                     for leg in own],
                    max(stops, key=lambda r: r.id) if stops else None,
                    any(r.watched or r.state == bot_exits.WATCHING for r in stops),
                    exit_math.is_split([leg.symbol for leg in own], now),
                    timing.side_state((r.kind, r.state) for r in mine) == "closed",
                    bot_exits.stop_off(session, run_id, position),
                    underlyings.american(bot.underlying),
                    max(targets, key=lambda r: r.id) if targets else None,
                    bool(run.pt_only),
                ))  # fmt: skip
    return out


def check(deps, startup: bool = False) -> list[str]:
    """Compare every held trade with the broker and alert once: the problems (a safety
    alert), or, unless `startup`, that all is well (routine). Returns the problems.
    Sends, cancels and changes nothing. Never raises."""
    try:
        return _check(deps, startup)
    except Exception:  # noqa: BLE001 -- a check never stops the app
        logger.exception("the held trades' check failed")
        bot_entry.alert(
            deps, "Held trades not checked",
            "Something went wrong checking the trades held overnight against your broker. "
            "Check your broker.", "/automation",
        )  # fmt: skip
        return ["the check failed"]


def _check(deps, startup: bool) -> list[str]:
    now = deps.now()
    run_ids = held_runs(deps, now)
    if not run_ids:
        return []
    accounts = _accounts(deps, run_ids, now)
    problems: list[str] = []
    watched: list[str] = []
    for account in accounts.values():
        problems += _check_account(deps, account, watched)
    trades = len(run_ids)
    futures = any(g.pt_only for a in accounts.values() for g in a.groups)
    if problems:
        title = f"Held trades: {len(problems)} need{'s' if len(problems) == 1 else ''} a look"
        detail = "; ".join(problems) + ". The app changed nothing. Check your broker."
        bot_entry.alert(deps, title, detail, "/automation")
    elif not startup:
        detail = (
            f"{trades} trade{'s' if trades != 1 else ''} held overnight, every leg held and "
            "every stop in place."
        )
        if futures:  # bots slice 17: a futures trade has no stop, its target rests
            detail = detail[:-1] + (
                "; each futures trade's profit target resting at TastyTrade (futures: profit "
                "target only, no stop)."
            )
        if watched:
            detail += (
                f" Stops the app watches ({', '.join(watched)}) need the app running from 09:30."
            )
        bot_entry.alert(deps, "Held trades checked", detail, "/automation", routine=True)
    return problems


def _check_account(deps, account: _Account, watched: list) -> list[str]:
    problems = []
    try:
        client = bot_exits._client(deps, account.broker)
        held = {plain(k): v for k, v in client.option_positions(account.account).items()}
    except Exception:  # noqa: BLE001 -- unreadable: said, never guessed
        logger.exception("couldn't read positions for the held trades' check")
        count = len({g.run_id for g in account.groups})
        return [
            f"couldn't read your positions in account ••{account.account[-4:]}, so "
            f"{count} held trade{'s weren' if count != 1 else ' wasn'}'t checked"
        ]
    expected: dict[str, int] = {}
    owners: dict[str, list[_Group]] = {}
    for group in account.groups:
        label = f"{group.bot_name} {group.name}"
        if group.closed:  # #266: first, so a calendar its exit closed is never held
            continue
        if group.split:  # a timed close should have closed it before its near legs expired
            problems.append(
                f"{label}: its near legs expired and its far legs are still held, with no stop "
                "or target: yours to sell or keep"
            )
            continue
        if group.off:
            continue
        for symbol, signed in group.legs:
            expected[plain(symbol)] = expected.get(plain(symbol), 0) + signed
            owners.setdefault(plain(symbol), []).append(group)
        if group.watched:
            watched.append(label)
        if group.pt_only:  # bots slice 17: no stop to check; its resting target instead
            problem = _target_problem(client, account, label, group.target)
            if problem:
                problems.append(problem)
            continue
        stop = group.stop
        if stop is None:
            continue
        if stop.state not in bot_exits.STOP_LIVE:
            problems.append(f"{label} has no stop in place (its stop is {stop.state})")
        elif stop.state == "pending":  # review 5: written, not at the broker yet
            problems.append(f"{label}: its stop isn't at your broker yet (waiting to be placed)")
        elif stop.state == "working":
            try:
                found = client.get_order(account.account, stop.broker_order_id)
            except Exception:  # noqa: BLE001
                logger.exception("couldn't read stop %s for the held trades' check", stop.id)
                problems.append(f"{label}: its stop couldn't be read at your broker")
                continue
            if not found.may_be_working and found.status is not OrderStatus.FILLED:
                problems.append(
                    f"{label}: its stop shows {found.status.value} at your broker (the app "
                    "re-places a stop the broker dropped)"
                )
    for symbol, want in sorted(expected.items()):
        have = held.get(symbol, 0)
        short = (want < 0 and have > want) or (want > 0 and have < want)
        if short:
            who = ", ".join(dict.fromkeys(f"{g.bot_name} {g.name}" for g in owners[symbol]))
            problem = f"{who}: {symbol} — your broker shows {have}, the trade should hold {want}"
            if want < 0 and any(g.american for g in owners[symbol]):
                # Slice 16, question 4: an early assignment is processed overnight
                problem += (
                    ". It's an American-style option: it may have been assigned, so you may "
                    "hold shares, and its stop may still be resting at your broker"
                )
            problems.append(problem)
    return problems


def _target_problem(client, account: _Account, label: str, target) -> str | None:
    """A futures group's resting profit target (bots slice 17): it must be at the broker
    and still working there, or the trade has no exit at all."""
    if target is None or target.state not in ("working", "pending"):
        state = target.state if target is not None else "missing"
        return f"{label} has no profit target in place (it's {state}): it has no exit at all"
    if target.state == "pending":
        return f"{label}: its profit target isn't at your broker yet (waiting to be placed)"
    try:
        found = client.get_order(account.account, target.broker_order_id)
    except Exception:  # noqa: BLE001
        logger.exception("couldn't read target %s for the held trades' check", target.id)
        return f"{label}: its profit target couldn't be read at your broker"
    if not found.may_be_working and found.status is not OrderStatus.FILLED:
        return f"{label}: its profit target shows {found.status.value} at your broker"
    return None


def preopen_pass(deps) -> None:
    """From 09:20 New York on a trading day until its close, once a day: check the held
    trades (Tracker2's 09:20 catch-up time). Never raises."""
    try:
        now = deps.now()
        local = now.astimezone(MARKET_TZ)
        close = session_close(local.date())
        if close is None or not PREOPEN <= local.time() < close:
            return
        today = local.date().isoformat()
        with deps.session_factory() as session:
            row = session.get(Setting, CHECKED_KEY)
            if row is not None and row.value == today:
                return
            row = row or Setting(key=CHECKED_KEY)
            row.value, row.updated_at = today, now
            session.add(row)
            session.commit()
        check(deps)
    except Exception:  # noqa: BLE001 -- the monitor's other steps go on
        logger.exception("the pre-open pass failed")
