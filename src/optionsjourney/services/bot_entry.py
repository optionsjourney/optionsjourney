"""Firing a bot's entry (spec: the first automation, section 2; Tracker2's
orchestrator.run_bot_entry). In order: claim the run, check the guards, find
today's expiration and the chain, pick strikes, price it, send one order
through the order path and chase it, then hand each fill to the exits. A
partly filled order splits the entry into tranches: each fill is its own run,
the later ones children of the first (Tracker2's _tranche_run)."""

import logging
import re
import threading
import time as clock
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_FLOOR, Decimal
from functools import partial

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from optionsjourney.bots import (
    caps,
    exit_plan,
    expirations,
    filters,
    floating,
    schedule,
    sizing,
    strikes,
    underlyings,
)  # fmt: skip
from optionsjourney.bots.definition import contracts_for, largest_ratio, trade_cap
from optionsjourney.bots.definition import limits as entry_limits
from optionsjourney.brokers.credentials import CredentialsUnavailable
from optionsjourney.brokers.http import BrokerError
from optionsjourney.core import black_scholes
from optionsjourney.core.market import MARKET_TZ
from optionsjourney.core.positions import Position, PositionLeg
from optionsjourney.core.risk_graph import graph_legs, graph_symbol
from optionsjourney.core.sessions import session_now
from optionsjourney.core.symbols import occ_symbol
from optionsjourney.core.transactions import InstrumentType, OptionDetails
from optionsjourney.db.models import Bot, BotExit, BotRun
from optionsjourney.services import bot_filters, bot_paper, bot_store, economic_events, orders
from optionsjourney.services.bot_chase import chase_entry
from optionsjourney.services.user_alerts import Fired
from optionsjourney.trading import ticks
from optionsjourney.trading.orders import Leg, Order, Side, filled_units, plain

logger = logging.getLogger(__name__)
ACTIVE: set[int] = set()  # runs being chased by a live thread; recovery leaves them alone
_active_lock = threading.Lock()


def place_protections(deps, run_id: int, **options) -> None:
    """Hand a filled entry to the exits (Task 8's bot_exits). Called through this
    module so tests can replace it. `options`: bot_exits.place_protections' own."""
    from optionsjourney.services import bot_exits

    bot_exits.place_protections(deps, run_id, **options)


NEEDS_YOU = "Needs you"  # a run's reason once we've alerted that it needs you
UNEVEN_DETAIL = "The entry's legs filled unevenly — settle it at your broker."
# How an uneven-legs run's reason starts. Recovery skips only these runs: their
# legs hold no matching whole units, so the app never records or protects them.
UNEVEN = f"{NEEDS_YOU}: The entry's legs filled unevenly"


def hand_to_exits(deps, bot, run_id: int, live_check: bool = False) -> None:
    """Place a fill's exits. If that breaks, the fill stands: say the exits are
    missing (once; the run's reason is marked). `live_check`: each stop is checked
    against its short's fresh ask just before it's sent (a trade handed over, slice 12)."""
    try:
        place_protections(deps, run_id, **({"live_check": True} if live_check else {}))
    except Exception as e:  # noqa: BLE001 -- the fill stands; say the exits are missing
        logger.exception("bot %s: couldn't place exits for run %s", bot.name, run_id)
        detail = f"Exits not placed ({e.__class__.__name__}). Check your broker."
        alert(
            deps,
            f"{bot.name}: exits not placed",
            detail,
            f"/automation/{bot.id}/runs",
            run_id=run_id,
        )
        update_run(deps, run_id, reason=f"{NEEDS_YOU}: {detail}")
    make_default_alerts(deps, run_id)


def make_default_alerts(deps, run_id: int) -> None:
    """The bot's default alerts for this fill (bots slice 9). On its own: a failure
    is logged and never touches the fill or its exits."""
    from optionsjourney.services import bot_alerts

    try:
        bot_alerts.apply_defaults(deps, run_id)
    except Exception:  # noqa: BLE001 -- the fill and its exits stand
        logger.exception("couldn't make the default alerts for run %s", run_id)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def in_background(fn: Callable, *args) -> None:
    """Start `fn(*args)` on a daemon thread (close_jobs.enqueue's thread fallback)."""
    threading.Thread(target=fn, args=args, name=f"bot-{fn.__name__}", daemon=True).start()


@dataclass
class BotDeps:
    session_factory: sessionmaker
    clients: Callable[[], list]
    hub: object
    chain: object
    notify: Callable | None = None
    sleep: Callable[[float], None] = clock.sleep
    now: Callable[[], datetime] = _utcnow
    # Runs a closing chase off the caller's thread (Tracker2's close_jobs.enqueue).
    # Tests replace it to run the job inline.
    background: Callable[..., None] = in_background
    # The market filters' prices (services/bot_filters.Market); None: a filter that's
    # on can't be checked, so it fails closed.
    market: object | None = None


_BOT_LINK = re.compile(r"^/automation/(\d+)(?:/|$)")


def alert(deps, title: str, detail: str, link: str, *, run_id: int | None = None,
          routine: bool = False, bot_id: int | None = None) -> None:  # fmt: skip
    """Raise a bot alert. It carries its bot (read from the link when not given) and
    run, so the message can show the trade; `routine` lets it follow the bot's
    channel choice, never for a safety alert (bots slice 9). With no `notify`, it's
    queued for the background sender: a bot never waits on a send."""
    try:
        if bot_id is None and (match := _BOT_LINK.match(link or "")):
            bot_id = int(match.group(1))
        event = Fired(0, title, detail, link, bot_id, run_id, routine)
        if deps.notify is not None:
            deps.notify(event)
        else:
            from optionsjourney.services import notify

            notify.send_soon(deps.session_factory, event)
    except Exception:  # noqa: BLE001 -- an alert never stops a bot
        logger.exception("couldn't send a bot alert")


def _claim(deps, bot, key: str, hhmm: str, live: bool) -> int | None:
    now = deps.now()
    with deps.session_factory() as session:
        run = BotRun(
            bot_id=bot.id,
            key=key,
            trading_date=now.astimezone(MARKET_TZ).date(),
            entry_time=hhmm,
            status="pending",
            live=live,
            account_number=bot.account_number,
            broker=bot.broker,
            legs=[],
            filled_legs=[],
            chase_log=[],
            contracts=0,
            created_at=now,
            updated_at=now,
        )
        session.add(run)
        try:
            session.commit()
        except IntegrityError:
            return None  # already fired
        return run.id


def update_run(deps, run_id: int, **values) -> None:
    with deps.session_factory() as session:
        run = session.get(BotRun, run_id)
        for name, value in values.items():
            setattr(run, name, value)
        run.updated_at = deps.now()
        session.commit()


def record_skip(deps, bot_id: int, hhmm: str, reason: str) -> int | None:
    bot = bot_store.load(deps.session_factory, bot_id)
    key = schedule.entry_key(bot_id, hhmm, deps.now().astimezone(MARKET_TZ).date())
    run_id = _claim(deps, bot, key, hhmm, bot.live)
    if run_id is not None:
        update_run(deps, run_id, status="skipped", reason=reason)
    return run_id


# Runs that count as an entry today. "pending" is a scheduled run still in
# progress (a preview mode run stays pending until its preview ends), so two
# overlapping entries can't both pass the cap. Manual runs never count (by key).
COUNTED = ("filled", "partial", "running", "preview", "pending")


def _entries_today(deps, bot_id: int, day, run_id: int) -> int:
    """Today's entries for the bot: runs that placed, previewed or are still working on a
    scheduled order, each entry counted once however many tranches it was split into."""
    with deps.session_factory() as session:
        return session.scalar(
            select(func.count(func.distinct(func.coalesce(BotRun.parent_run_id, BotRun.id)))).where(
                BotRun.bot_id == bot_id,
                BotRun.trading_date == day,
                BotRun.status.in_(COUNTED),
                BotRun.id != run_id,
                BotRun.key.not_like("%-now-%"),  # Preview now runs never count
                # nor a trade you opened and handed to the bot (bots slice 12)
                BotRun.key.not_like("adopt-%"),
                # nor an entry sent from the Trade page (bots slice 15)
                BotRun.key.not_like("ticket-%"),
            )
        )


def _open_trades(deps, bot) -> int:
    """The bot's open trades, from its own runs and their exits (Tracker2 counts
    the BotPosition rows it writes at fill). Any failure raises."""
    with deps.session_factory() as session:
        held = list(
            session.scalars(
                select(BotRun).where(BotRun.bot_id == bot.id, BotRun.status.in_(caps.HOLDING))
            )
        )
        exits: dict[int, list] = {}
        if held:
            for row in session.scalars(
                select(BotExit).where(BotExit.run_id.in_([run.id for run in held]))
            ):
                exits.setdefault(row.run_id, []).append(caps.ExitRow.of(row))
        closed = bot_paper.closed_groups(session, held)
        rows = [
            caps.RunRow(
                run.parent_run_id or run.id,
                run.id,
                run.status,
                tuple(run.legs),
                tuple(exits.get(run.id, ())),
                bool(run.filled_legs),
                closed.get(run.id, frozenset()),
            )
            for run in held
        ]
    return caps.open_trades(rows, bot.split_iron_condors, deps.now())


def _bot_limits(deps, bot, run_id: int, day) -> str | None:
    """The bot's own limits for a scheduled entry: entries per day, open trades.
    Counting open trades fails closed."""
    if bot.max_entries_per_day is not None:
        entries = _entries_today(deps, bot.id, day, run_id)
        if entries >= bot.max_entries_per_day:
            return f"already at {entries} entries today"
    if bot.max_open_trades is not None:
        try:
            held = _open_trades(deps, bot)
        except Exception:  # noqa: BLE001 -- can't count, so don't enter
            logger.exception("bot %s: couldn't count open trades", bot.name)
            return "Couldn't count this bot's open trades; not entered."
        entering = off_schedule_entering(deps, bot)
        if held + entering_weight(bot) * len(entering) >= bot.max_open_trades:
            if entering:  # slices 15, 19: Trade page and Trade now entries being entered
                runs = ", ".join(str(r) for r in entering)
                return (
                    f"already holding {held} open trades, and run {runs} (from the Trade page "
                    "or Trade now) is still being entered"
                )
            return f"already holding {held} open trades"
    return None


TICKET_PREFIX = "ticket-"  # a Trade page entry's key (services/bot_ticket.py)
ENTERING = ("pending", "running")
RESTING = "working"  # bots slice 17: a resting entry (services/bot_limit.py), on any day


def off_schedule_entering(deps, bot, before: int | None = None) -> list[int]:
    """The bot's entries off its schedule still being entered today: the Trade page's
    (bots slice 15) and Trade now's (slice 19), oldest first; with `before`, only those
    claimed before that run. They count toward Max open trades for every entry, so a
    scheduled entry and one of these can't pass it together."""
    today = deps.now().astimezone(MARKET_TZ).date()
    with deps.session_factory() as session:
        query = select(BotRun.id).where(
            BotRun.bot_id == bot.id,
            or_(
                BotRun.key.startswith(f"{TICKET_PREFIX}{bot.id}-"),
                BotRun.key.startswith(f"bot-{bot.id}{TRADE_NOW_KEY}"),
            ),
            or_(
                and_(BotRun.trading_date == today, BotRun.status.in_(ENTERING)),
                BotRun.status == RESTING,  # a resting entry, however long it has rested
            ),
        )
        if before is not None:
            query = query.where(BotRun.id < before)
        return list(session.scalars(query.order_by(BotRun.id)))


def entering_weight(bot) -> int:
    """Open trades one entry becomes: 2 for a condor the journal splits (caps.open_trades)."""
    from optionsjourney.bots.definition import is_iron_condor_shape

    return 2 if bot.split_iron_condors and is_iron_condor_shape(bot) else 1


def _switches_and_session(deps, manual: bool, require_open: bool = True) -> str | None:
    """Freeze, No new trades, the regular session and the entry cut-off, as of now.
    require_open=False (Preview order, which never sends) drops the session and cut-off,
    as Tracker2's check_manual does for an order that isn't sent now."""
    current = orders.switches(deps.session_factory)
    if current.freeze:
        return "Orders are frozen."
    if current.no_new_trades and not manual:
        return "No new trades is on."
    if not require_open:
        return None
    local = deps.now().astimezone(MARKET_TZ)
    if session_now("SPY", local) != "regular":
        return "The market isn't open."
    cutoff = schedule.entry_cutoff(local.date())
    if cutoff is not None and local.time() >= cutoff:
        return "Too close to the close: entries stop 2 minutes before."
    return None


FUTURES_ONLY_FROM_TICKET = (
    "A futures bot enters only from the Trade page (Manage with a bot): it has no "
    "scheduled entries and no Preview order."
)


def _guard(deps, bot, manual: bool, contracts: int, run_id: int,
           require_open: bool = True) -> str | None:
    now = deps.now()
    if underlyings.futures(bot.underlying):  # bots slice 17: never on a schedule
        return FUTURES_ONLY_FROM_TICKET
    if bot.archived:  # first: an archived bot never fires, Preview now included
        return "The bot is archived. Unarchive it first."
    current = orders.switches(deps.session_factory)
    if current.freeze:
        return "Orders are frozen."
    if current.no_new_trades and not manual:
        return "No new trades is on."
    if not bot.enabled and not manual:
        return "The bot isn't armed."
    problem = _switches_and_session(deps, manual, require_open)
    if problem:
        return problem
    local = now.astimezone(MARKET_TZ)
    if not manual:
        problem = _blackout(deps, bot, now)  # first among the bot's own guards
        if problem:
            return problem
    if not manual:
        problem = _daily_limit(deps, bot, contracts, by_hand=False)
        if problem:
            return problem
    if manual:
        return None  # Preview now ignores the bot's own guards and filters (check_manual)
    problem = _bot_limits(deps, bot, run_id, local.date())
    if problem:
        return problem
    problem = _market_filters(deps, bot, now)
    if problem:
        return problem
    # The filters' reads can take up to a minute: check the clock and the switches again.
    return _switches_and_session(deps, manual)


def _daily_limit(deps, bot, contracts: int, by_hand: bool) -> str | None:
    """Most contracts per day (decision 0058): the bot's own limit, or when blank the
    worked-out one, counting only the entries it made by itself. An entry you start
    yourself (`by_hand`, Trade now) has no daily cap while the bot's limit is blank."""
    _, per_day = entry_limits(bot, by_hand)
    if per_day is None:
        return None
    own_only = bot.max_per_day is None
    used = orders.used_today(deps.session_factory, bot.id, deps.now(), own_only=own_only)
    if used + contracts > per_day:
        return f"{bot.name} has reached its limit of {per_day} a day."
    return None


def _trade_guard(deps, bot, contracts: int, run_id: int) -> str | None:
    """Trade now's checks (slice 19; guards.check_manual, as the Trade page's entries,
    slice 15): pressing it is the choice to trade now, so arming, the schedule, blackout
    days, the market filters and the premium bounds don't apply. The bot, the broker,
    Freeze, No new trades, the session and the cut-off, Max per day and Max open trades
    do; Max per trade is the contracts' clamp, and the order path checks every send."""
    if underlyings.futures(bot.underlying):
        return FUTURES_ONLY_FROM_TICKET
    if bot.archived:
        return "The bot is archived. Unarchive it first."
    problem = _switches_and_session(deps, manual=False)
    if problem:
        return problem
    problem = _daily_limit(deps, bot, contracts, by_hand=True)
    if problem:
        return problem
    if bot.max_open_trades is not None:
        try:
            held = _open_trades(deps, bot)
        except Exception:  # noqa: BLE001 -- can't count, so don't enter
            logger.exception("bot %s: couldn't count open trades", bot.name)
            return "Couldn't count this bot's open trades; not entered."
        entering = off_schedule_entering(deps, bot, before=run_id)  # never itself
        if held + entering_weight(bot) * len(entering) >= bot.max_open_trades:
            if entering:
                runs = ", ".join(str(r) for r in entering)
                return f"already holding {held} open trades, and run {runs} is still being entered"
            return f"already holding {held} open trades"
    return None


def _blackout(deps, bot, now) -> str | None:
    """A typed blackout day, an Entry day list's day, or a Before expiration list's day
    after today through the trade's expiry estimate (guards.is_trading_day, #207). A
    list that can't say skips the entry: the blackout fails closed. The clock alerts
    about lists unreadable today, once a day."""
    day = now.astimezone(MARKET_TZ).date()
    entry, later = schedule.blackout_entry_lists(bot), schedule.blackout_expiry_lists(bot)
    names = sorted({*entry, *later})
    lists = economic_events.lists_for(deps.session_factory, names, day, now) if names else {}
    window = schedule.expiry_window(bot, day) if later else ()
    ahead = economic_events.lists_ahead(deps.session_factory, later, window, now)
    if schedule.is_blackout(bot, day, lists, ahead):
        hit = schedule.blackout_lists_hit(bot, day, lists, ahead)
        return "today is a blackout day" + (f" ({', '.join(hit)})" if hit else "")
    missing = schedule.unavailable_blackout_lists(bot, day, lists, ahead)
    if missing:
        return f"{', '.join(missing)} dates unavailable today, so the blackout can't be checked."
    return None


def _market_filters(deps, bot, now) -> str | None:
    """The last guards, the only ones that go over the network (guards.check_all).
    A filter that couldn't be checked fails closed and alerts once a day."""
    skip = bot_filters.check(deps.market, bot, now)
    if skip is None:
        return None
    day = now.astimezone(MARKET_TZ).date()
    if skip.unavailable and bot_filters.first_today(bot.id, skip.unavailable, day):
        title = f"{bot.name}: entry skipped, {skip.unavailable} unavailable"
        alert(deps, title, skip.reason, f"/automation/{bot.id}/runs")
    return skip.reason


def fire(deps: BotDeps, bot_id: int, hhmm: str | None, manual: bool = False) -> int | None:
    """Run one entry. Returns the run id, or None if that entry was already taken."""
    bot = bot_store.load(deps.session_factory, bot_id)
    if bot is None:
        return None
    now = deps.now()
    if manual:
        key = schedule.manual_key(bot_id, now)
    else:
        key = schedule.entry_key(bot_id, hhmm, now.astimezone(MARKET_TZ).date())
    live = bot.live and not manual
    run_id = _claim(deps, bot, key, hhmm or now.astimezone(MARKET_TZ).strftime("%H:%M"), live)
    if run_id is None:
        return None
    _run(deps, bot, run_id, manual, live, hhmm)
    return run_id


TRADE_WORD = "TRADE"  # automation_bp.api_run_now's confirm='TRADE'
TRADE_NOW_KEY = "-trade-"


def is_trade_now(run) -> bool:
    """A run started by Trade now (bots slice 19), by its key."""
    return TRADE_NOW_KEY in (run.key or "")


def trade_now(deps: BotDeps, bot_id: int, typed: str) -> tuple[int | None, str | None]:
    """Trade now (bots slice 19; Tracker2's automation_bp.api_run_now): a live entry off
    schedule, once `typed` is exactly TRADE. It's claimed here and run on
    `deps.background` with _trade_guard's checks (question 1: guards.check_manual plus the
    contract and open-trade limits), with the bot's contracts. It counts as one of the
    day's entries.
    Returns (the run id, None), or (None, why not) with nothing claimed."""
    bot = bot_store.load(deps.session_factory, bot_id)
    if bot is None:
        return None, "There's no such bot."
    if bot.per_trade:  # final review I1: a trade's own rules never open a trade
        return None, "This is one trade's rules, not a bot: it never opens a trade."
    if bot.archived:
        return None, "The bot is archived. Unarchive it first."
    if underlyings.futures(bot.underlying):
        return None, FUTURES_ONLY_FROM_TICKET
    if not bot.live:
        return None, "Trade now sends a real order, so it's for a live bot. Use Preview now."
    if typed != TRADE_WORD:
        return None, f"Type {TRADE_WORD} to send a real order."
    now = deps.now()
    hhmm = now.astimezone(MARKET_TZ).strftime("%H:%M")
    with _trade_lock:  # two presses, or two tabs: one Trade now at a time per bot
        run_id = _claim(deps, bot, schedule.trade_key(bot_id, now), hhmm, True)
        if run_id is None:
            return None, ALREADY_TRADING
        earlier = _trading_now(deps, bot, run_id)
        if earlier:
            update_run(deps, run_id, status="skipped", reason=f"{ALREADY_TRADING} (run "
                       f"{earlier[0]}); nothing was sent.")  # fmt: skip
            return None, f"{ALREADY_TRADING} (run {earlier[0]})."
        with _active_lock:
            ACTIVE.add(run_id)  # own() takes it from here; recovery never sees it unowned
    try:
        deps.background(_run, deps, bot, run_id, False, True, None, None, True)
    except Exception:  # noqa: BLE001 -- nothing was sent: end the claim
        logger.exception("bot %s: couldn't start Trade now", bot.name)
        with _active_lock:
            ACTIVE.discard(run_id)
        reason = "Trade now couldn't start; nothing was sent."
        update_run(deps, run_id, status="error", reason=reason)
        return None, reason
    return run_id, None


ALREADY_TRADING = "A Trade now of this bot is still being entered"
_trade_lock = threading.Lock()


def _trading_now(deps, bot, run_id: int) -> list[int]:
    """This bot's other Trade now runs still pending or running today, claimed before
    `run_id` (read under the claim, so of two presses only the first goes on)."""
    today = deps.now().astimezone(MARKET_TZ).date()
    with deps.session_factory() as session:
        return list(session.scalars(select(BotRun.id).where(
            BotRun.bot_id == bot.id, BotRun.trading_date == today,
            BotRun.key.startswith(f"bot-{bot.id}{TRADE_NOW_KEY}"),
            BotRun.status.in_(ENTERING), BotRun.id < run_id,
        ).order_by(BotRun.id)))  # fmt: skip


def _run(deps, bot, run_id: int, manual: bool, live: bool, hhmm, miss=None,
         trade: bool = False) -> None:  # fmt: skip
    """The claimed run's entry, owned in ACTIVE from the claim to the settle. `trade`:
    a Trade now run (slice 19), checked as guards.check_manual and the limits."""

    def work(owned: list, handed: set) -> None:
        _fire(deps, bot, run_id, manual, live, hhmm, owned, handed, miss, trade)

    own(deps, bot, run_id, work)


def own(deps, bot, run_id: int, work: Callable[[list, set], None]) -> None:
    """Run `work(owned, handed)` for the claimed run, owned in ACTIVE from the claim to
    the settle, so recovery leaves the run, and every tranche run made for it, alone
    until the entry is fully settled. A failure finishes every owned run (an order that
    may be out leaves it for recovery) and a fill recorded before it still gets its
    exits. `handed`: the runs whose exits were placed."""
    owned, handed = [run_id], set()
    with _active_lock:
        ACTIVE.add(run_id)
    try:
        try:
            work(owned, handed)
        except Exception as e:  # noqa: BLE001 -- a bot never takes the clock down
            logger.exception("bot %s entry failed", bot.name)
            for owned_id in owned:
                _failed(deps, owned_id, e)
            # A fill recorded before the failure still gets its exits.
            for owned_id in owned:
                if owned_id not in handed and _status(deps, owned_id) == "filled":
                    handed.add(owned_id)
                    hand_to_exits(deps, bot, owned_id)
            alert(deps, f"{bot.name}: entry failed", str(e), f"/automation/{bot.id}/runs")
    finally:
        with _active_lock:
            ACTIVE.difference_update(owned)


# The last check of each floating bot's window, per process (floating.remember in
# Tracker2): {bot id: (New York day, minute, reason)}. Misses write no row, so this is
# the only trace of them until the window's last minute; lost on restart.
_floating_last: dict[int, tuple] = {}
_floating_lock = threading.Lock()


def floating_last(bot_id: int, day) -> tuple[str, str] | None:
    """(minute, reason) of the bot's last floating miss today, or None."""
    with _floating_lock:
        found = _floating_last.get(bot_id)
    if found is None or found[0] != day:
        return None
    return found[1], found[2]


def fire_floating(deps: BotDeps, bot_id: int, hhmm: str) -> int | None:
    """One minute of a floating window (orchestrator.run_bot_floating_entry): claim
    the minute, stop if the day's window is done (floating.STOPS), then a fixed entry's
    steps. A miss (any guard, filter or pick that skips) deletes the claim and is
    remembered; on the window's last minute with nothing tried all day, one skipped
    row says why. Returns the run id, or None when nothing was kept."""
    bot = bot_store.load(deps.session_factory, bot_id)
    if bot is None or not floating.in_window(bot, hhmm):
        return None
    day = deps.now().astimezone(MARKET_TZ).date()
    run_id = _claim(deps, bot, floating.minute_key(bot.id, day, hhmm), hhmm, bot.live)
    if run_id is None:
        return None  # this minute was already claimed
    _settle_stuck_claims(deps, bot.id, day, run_id)
    if _floating_runs(deps, bot.id, day, run_id, floating.STOPS):
        _drop(deps, run_id)  # one entry per window: already done, or one in progress
        return None
    missed: list[str] = []

    def miss(reason: str) -> None:
        missed.append(reason)
        _drop(deps, run_id)
        with _floating_lock:
            _floating_last[bot.id] = (day, hhmm, reason)
        if floating.is_final_minute(bot, hhmm) and not _floating_runs(deps, bot.id, day):
            closed = _claim(deps, bot, floating.close_key(bot.id, day), hhmm, bot.live)
            if closed is not None:
                what = f"Window {floating.label(bot)} closed; last check {hhmm}: {reason}"
                update_run(deps, closed, status="skipped", reason=what)

    _run(deps, bot, run_id, False, bot.live, hhmm, miss)
    return None if missed else run_id


def _floating_runs(deps, bot_id: int, day, leave_out: int | None = None, statuses=None) -> bool:
    """Does the bot have another floating run today (in `statuses`, if given)?"""
    with deps.session_factory() as session:
        query = select(BotRun.id).where(
            BotRun.bot_id == bot_id,
            BotRun.trading_date == day,
            BotRun.key.startswith(floating.prefix(bot_id, day)),
        )
        if leave_out is not None:
            query = query.where(BotRun.id != leave_out)
        if statuses is not None:
            query = query.where(BotRun.status.in_(statuses))
        return session.scalar(query.limit(1)) is not None


STUCK_CLAIM = timedelta(minutes=2)  # a pending floating claim with no thread this long


def _settle_stuck_claims(deps, bot_id: int, day, leave_out: int | None, prefix=None) -> None:
    """A floating claim still `pending` with no thread working it (not in ACTIVE) for
    STUCK_CLAIM was cut off (the app stopped part-way). Nothing was sent while pending
    (a live entry is `running` before its order goes out), so it's ended as skipped and
    the rest of the window carries on instead of waiting on it silently. `prefix`: the
    claims' key prefix (default the floating window's; a Trade page entry's, slice 15)."""
    prefix = prefix or floating.prefix(bot_id, day)
    cutoff = deps.now() - STUCK_CLAIM
    with deps.session_factory() as session:
        stuck = session.scalars(
            select(BotRun).where(
                BotRun.bot_id == bot_id,
                BotRun.trading_date == day,
                BotRun.key.startswith(prefix),
                BotRun.status == "pending",
                BotRun.id != (leave_out or -1),
                BotRun.created_at < cutoff,
            )
        ).all()
        for run in stuck:
            if run.id in ACTIVE:
                continue
            run.status = "skipped"
            run.reason = "This check never finished (the app stopped part-way); nothing was sent."
            run.updated_at = deps.now()
        session.commit()


def _drop(deps, run_id: int) -> None:
    """Delete a claimed floating run nothing was sent for (a filter miss writes no row)."""
    with deps.session_factory() as session:
        run = session.get(BotRun, run_id)
        if run is not None:
            session.delete(run)
            session.commit()


def _status(deps, run_id: int) -> str | None:
    with deps.session_factory() as session:
        run = session.get(BotRun, run_id)
        return run.status if run is not None else None


def _failed(deps, run_id: int, e: Exception) -> None:
    """Record an unexpected failure. If an order may be out there, leave the run
    'running' so recovery settles it; 'error' would hide a live order. A run
    that already holds its fill keeps it."""
    what = f"Something went wrong ({e.__class__.__name__})."
    with deps.session_factory() as session:
        run = session.get(BotRun, run_id)
        if run is None or run.status in ("filled", "partial"):
            return
        order_out = run.status == "running" or bool(run.broker_order_id)
    if order_out:
        update_run(
            deps,
            run_id,
            status="running",
            reason=f"{what} Check your broker; recovery will settle it.",
        )
    else:
        update_run(deps, run_id, status="error", reason=what)


def _tranche_run(deps, root_id: int, index: int, contracts: int, owned: list) -> int:
    """A later order of the same entry, on its own run so its fill, journal
    position and exits stay separate from the root's (Tracker2's _tranche_run).
    It's in ACTIVE before it's committed, so recovery never sees it unowned."""
    now = deps.now()
    with deps.session_factory() as session:
        root = session.get(BotRun, root_id)
        child = BotRun(
            bot_id=root.bot_id,
            key=f"{root.key}#t{index}",
            trading_date=root.trading_date,
            entry_time=root.entry_time,
            status="running",
            live=root.live,
            account_number=root.account_number,
            broker=root.broker,  # the root's broker: its exits trade there too (slice 11)
            legs=list(root.legs),
            target_credit=root.target_credit,
            entry_spot=root.entry_spot,
            filled_legs=[],
            chase_log=[],
            contracts=contracts,
            parent_run_id=root.id,
            created_at=now,
            updated_at=now,
        )
        session.add(child)
        session.flush()
        owned.append(child.id)
        with _active_lock:
            ACTIVE.add(child.id)
        session.commit()
        return child.id


class _Missing(Exception):
    """No expiration, or no chain or price, to pick from (`chain`: the second)."""

    def __init__(self, reason: str, chain: bool = False):
        super().__init__(reason)
        self.reason, self.chain = reason, chain


def _find_legs(deps, bot, day, strike_notes: list) -> tuple[tuple, object, list]:
    """The strikes for today's entry: the listed expirations, a chain for each, the
    underlying's price, then `_pick`. Returns (picked, spot, expiration notes); raises
    _Missing, or strikes.NoTrade from the pickers. The scheduled entry and Preview order
    both pick here."""
    clients = deps.clients()
    # Bots slice 14: each leg's DTE as a listed expiration (resolver.resolve_trade: never
    # today + DTE), and a chain for each.
    listed = deps.chain.expirations(clients, bot.underlying).get("expirations", [])
    dtes = {leg.dte for leg in bot.legs}
    resolved, problems, notes = expirations.resolve_legs(dtes, listed, day, bot.use_exact_dte)
    if problems:
        same_day = dtes == {0} and bot.use_exact_dte
        raise _Missing("No expiration today." if same_day else " ".join(problems))
    rows = {
        dte: deps.chain.option_rows(clients, deps.hub, bot.underlying, when.isoformat())
        for dte, when in resolved.items()
    }
    spot = underlying_price(deps, clients, bot.underlying)
    if not all(rows.values()) or not spot:
        raise _Missing("No option chain or price for the underlying.", chain=True)
    return _pick(deps, bot, rows, spot, resolved, strike_notes), spot, notes


def _fire(
    deps, bot, run_id: int, manual: bool, live: bool, hhmm: str | None, owned: list, handed: set,
    miss=None, trade: bool = False,
) -> None:  # fmt: skip
    """`miss(reason)`, for a floating minute, takes every skip instead of a skipped run."""

    def skip(reason: str) -> None:
        if miss is not None:
            miss(reason)
        else:
            update_run(deps, run_id, status="skipped", reason=reason)

    # A window's minutes never take an entry time's own quantity (floating.py)
    contracts, capped = contracts_for(bot, None if manual or trade or miss is not None else hhmm)
    if bot.sizing == "dollars":  # slice 19: sized once priced; the guards need room for one
        contracts, capped = 1, None
    problem = (
        _trade_guard(deps, bot, contracts, run_id)
        if trade
        else _guard(deps, bot, manual, contracts, run_id)
    )
    if problem:
        skip(problem)
        return
    day = deps.now().astimezone(MARKET_TZ).date()
    strike_notes: list[str] = []  # slice 19: what the pickers moved, for the run's log
    try:
        picked, spot, notes = _find_legs(deps, bot, day, strike_notes)
    except _Missing as e:
        if not e.chain:
            skip(e.reason)
        elif miss is None:
            update_run(deps, run_id, status="error", reason=e.reason)
        else:  # a window's minute: nothing was sent, so the next minute tries again
            _no_data(deps, bot, strikes.NoData(e.reason, kind="option chain"))
            miss(e.reason)
        return
    except strikes.NoTrade as e:
        if isinstance(e, strikes.NoData):
            _no_data(deps, bot, e)
        if isinstance(e, strikes.Unsafe):
            update_run(deps, run_id, status="error", reason=str(e))
        else:
            skip(str(e))
        return
    if live:  # #205: priced, filtered, sized and chased from fresh quotes, not the snapshot
        picked = _requote(deps, bot, picked, strike_notes)
    dte_of = {leg.position: leg.dte for leg in bot.legs}
    enter_picked(
        deps, bot, run_id, picked, contracts, live, owned, handed, skip,
        scheduled=not manual and not trade, dtes=dte_of, spot=spot, capped=capped, notes=notes,
        by_bot=True, strike_notes=strike_notes, limits=not manual,
    )  # fmt: skip


def _requote(deps, bot, picked: tuple, said: list) -> tuple:
    """#205 (Tracker2's entry_requote): a live entry re-reads its legs' quotes just
    before it is priced, since one stale or wide quote in the chain snapshot can start
    the chase further from the market than all its steps reach. The repriced legs
    (strikes.requote: only when every leg has a sane two-sided quote), with a note per
    changed leg in `said`; anything else keeps the chain's prices. Futures are left
    alone: Schwab's prices aren't used for them (decision 0080)."""
    if underlyings.futures(bot.underlying):
        return picked
    try:
        quotes = fresh_option_quotes(deps.clients(), [p.symbol for p in picked])
        fresh, notes = strikes.requote(picked, quotes)
    except Exception:  # noqa: BLE001 -- a quote outage never blocks an entry
        logger.exception("bot %s: entry requote failed; keeping the chain's quotes", bot.name)
        return picked
    if fresh is picked:
        logger.info("bot %s: entry requote found no usable quote for every leg", bot.name)
    said.extend(notes)
    return fresh


def fresh_option_quotes(clients: list, symbols: list[str]) -> dict:
    """{OCC symbol: (bid, ask)} read now from the brokers' REST quotes, never the stream
    or the chain: TastyTrade's market data first, then Schwab's quotes for any symbol
    TastyTrade didn't answer (decision 0080: TastyTrade is the main source, Schwab the
    backup). A broker that fails is logged and skipped; never raises."""
    found: dict[str, tuple] = {}
    tasty = next((c for c in clients if hasattr(c, "market_data")), None)
    schwab = next((c for c in clients if hasattr(c, "quotes")), None)
    if tasty is not None:
        try:
            for item in tasty.market_data({"equity-option": list(symbols)}) or []:
                if item.get("symbol") in symbols:
                    found[item["symbol"]] = (_price(item.get("bid")), _price(item.get("ask")))
        except Exception as e:  # noqa: BLE001 -- Schwab may still answer
            logger.info("entry quotes unavailable from TastyTrade: %s", e)
    missing = [s for s in symbols if not _two_sided(found.get(s))]
    if schwab is not None and missing:
        try:
            got = schwab.quotes(missing) or {}
            for symbol in missing:
                quote = (got.get(symbol) or {}).get("quote") or {}
                found[symbol] = (_price(quote.get("bidPrice")), _price(quote.get("askPrice")))
        except Exception as e:  # noqa: BLE001
            logger.info("entry quotes unavailable from Schwab: %s", e)
    return found


def _price(value) -> Decimal | None:
    try:
        return None if value is None else Decimal(str(value))
    except ArithmeticError:
        return None


def _two_sided(quote) -> bool:
    return quote is not None and quote[0] is not None and quote[1] is not None


def enter_picked(
    deps, bot, run_id: int, picked: tuple, contracts: int, live: bool, owned: list,
    handed: set, skip: Callable[[str], None], *, scheduled: bool, dtes: dict, spot,
    capped: int | None = None, notes=(), lead: dict | None = None, by_bot: bool = False,
    strike_notes=(), limits: bool | None = None,
) -> None:  # fmt: skip
    """From the picked legs to a settled entry (orchestrator._place_trade): the side (the
    mid's sign) and too close to even, the premium bounds by size (a scheduled entry only),
    the inverted check, the credit floor and the debit cap, dollar sizing on a debit, the
    run's legs, one opening order through the order path,
    the chase and its tranches, then every fill's exits (_settle). A bot's own entry
    and one sent from the Trade page (bots slice 15) both come through here. `dtes`:
    each leg's DTE by position, for the run's legs; `spot`: the underlying's price
    at the entry (None: not known); `lead`: a first entry for the run's log. `by_bot`:
    the bot's own entry (scheduled, its window, Preview now, Trade now), which a bot sized
    by dollars sizes here (slice 19); the Trade page's keeps the size you reviewed."""
    problem = tick_problem(deps, bot)
    if problem:
        skip(f"{problem} Not entered.")
        return
    # Decision 0048: the trade takes the side its midpoint prices on; under $0.05 either way
    # is too close to even (strikes.MIN_CREDIT, both sides), Preview now included.
    mid = strikes.entry_price(picked, Decimal(0))
    problem = strikes.too_close_to_even(mid)
    if problem:
        skip(problem)
        return
    if scheduled:
        problem = filters.premium_problem(bot, mid)
        if problem:
            skip(problem)
            return
    credit = mid > 0
    try:  # and each group that works out its stop on its short, on its own
        strikes.inverted_group(bot, picked)
    except strikes.Unsafe as e:
        update_run(deps, run_id, status="error", reason=str(e))
        return
    price, cap, problem = _opening_price(deps, bot, picked, contracts, credit)
    if problem:
        skip(problem)
        return
    sized = None
    if by_bot and bot.sizing == "dollars":
        # Decision 0050: a credit or a debit, sized by the buying power one unit needs
        sized = _size_by_buying_power(deps, bot, picked, price, cap)
        if sized.problem:
            title = ("the budget buys nothing" if sized.too_small  # allocation_too_small
                     else "its buying power couldn't be worked out")  # fmt: skip
            alert(deps, f"{bot.name}: entry skipped, {title}", sized.problem,
                  f"/automation/{bot.id}/runs", run_id=run_id)  # fmt: skip
            skip(f"{sized.problem} Not entered.")
            return
        contracts, capped = sized.contracts, None
        daily = scheduled if limits is None else limits  # Trade now: limits, no bounds
        problem = daily and _daily_limit(deps, bot, contracts * largest_ratio(bot),
                                         by_hand=limits is not None)  # fmt: skip
        if problem:
            skip(problem)
            return
    legs_json = _leg_rows(picked, dtes)
    priced = _priced(bot, legs_json, picked, mid)
    update_run(
        deps, run_id, status="running" if live else "pending", legs=legs_json, target_credit=price,
        # the underlying move's base (slice 7)
        entry_spot=None if spot is None else Decimal(str(spot)), priced=priced,
    )  # fmt: skip
    order = Order(
        tuple(Leg(p.symbol, p.side, p.ratio) for p in picked), contracts, True, price=price
    )
    request = orders.Request(
        bot.broker,
        bot.account_number,
        "bot",
        order,
        bot_id=bot.id,
        bot_run_id=run_id,
        preview_only=not live,  # a Paper bot (or Preview now) never sends: check only
    )
    tranches = {run_id: 1}  # run id -> tranche number

    def on_tranche(index: int, remaining: int) -> int:
        child = _tranche_run(deps, run_id, index, remaining, owned)
        tranches[child] = index
        return child

    def on_fill(fill_run: int, found) -> None:
        record_fill(deps, fill_run, found, "filled")  # its exits go out after the chase
        stamp_spot(deps, bot, fill_run)  # slice 19: the underlying move's base at the fill

    if bot.use_underlying_exit:  # entry_spot.subscribe: so the fill finds a fresh price
        try:
            deps.hub.subscribe({underlyings.watch_symbol(bot.underlying)})
        except Exception:  # noqa: BLE001 -- the chain's spot stays the base
            logger.exception("couldn't subscribe %s for the entry's price", bot.underlying)

    outcome = chase_entry(
        deps,
        request,
        price,
        bot.entry_chase,
        partial(strikes.next_price, debit_max=cap),
        on_tranche,
        on_fill,  # fmt: skip
    )
    if capped is not None:
        outcome.log.insert(0, {"capped": capped, "to": contracts})
    if sized is not None:
        outcome.log.insert(0, sized.record)
    if notes:  # an expiration moved off another's date (resolve_leg_expirations)
        outcome.log.insert(0, {"expirations": notes})
    if strike_notes:  # slice 19: a short widened or a strike moved off a conflict
        outcome.log.insert(0, {"strikes": list(strike_notes)})
    if lead is not None:
        outcome.log.insert(0, lead)
    if scheduled and not live:  # a Paper bot's entry: never chased, never sent
        _paper_entry(deps, run_id, outcome, legs_json, contracts)
        return
    _settle(deps, bot, run_id, outcome, tranches, handed)


def _leg_rows(picked: tuple, dtes: dict) -> list[dict]:
    """The picked legs as a run stores them (and Preview order shows them)."""
    return [
        {
            "position": p.position,
            "symbol": p.symbol,
            "strike": f"{p.strike.normalize():f}",
            "right": p.right,
            "side": str(p.side),
            "ratio": p.ratio,
            "group": p.group,
            "mid": f"{p.mid:f}",
            **({"delta": _delta_text(p.delta)} if p.delta is not None else {}),
            # Slice 14: the leg's expiration and the DTE it was set to (readers go by the
            # OCC symbol; these are for the pages)
            "expiration": p.expiration.isoformat() if p.expiration else None,
            "dte": dtes[p.position],
        }
        for p in picked
    ]


def _opening_price(deps, bot, picked: tuple, contracts: int, credit: bool):
    """The opening order's first price (signed, + a credit) and the debit cap on the tick
    (None without one): (price, cap, problem), the problem being why it isn't entered."""
    price = strikes.entry_price(picked, bot.entry_chase.start_offset)
    cap = None
    if credit:
        if price < strikes.MIN_CREDIT:
            # Never open for less than the smallest credit.
            return price, cap, "The credit is below $0.05; not entered."
    else:
        # A debit has no minimum: the offset pays more, up to the premium maximum.
        cap = bot.premium_max if bot.use_premium_bounds else None
        if cap is not None:
            # The order is rounded onto the tick before it is sent, and rounding a debit
            # pays more: so the cap itself sits on the tick, toward zero. Anything at or
            # under it then rounds to at most the cap.
            probe = Order(
                tuple(Leg(p.symbol, p.side, p.ratio) for p in picked), contracts, True, price=price
            )
            tables = orders.tick_tables(deps.session_factory, deps.clients(), probe, deps.now())
            tick = ticks.order_tick(probe, -cap, tables)
            cap = (cap / tick).to_integral_value(rounding=ROUND_FLOOR) * tick
            if cap <= 0:
                return price, cap, ("The premium maximum is below the smallest price step; "
                                    "not entered.")  # fmt: skip
            price = max(price, -cap)
    return price, cap, None


@dataclass(frozen=True)
class PreviewResult:
    """Preview order: whether the broker accepted the order the bot would send now, and
    why not if it didn't. `legs` are the picked legs (as a run stores them), `price` the
    signed opening price (+ a credit), `graph` the risk graph's trade at the mids, `exits`
    each group's stop and target on that price (exit_plan.group_lines)."""

    ok: bool
    reason: str
    legs: list[dict]
    price: Decimal | None
    buying_power: Decimal | None
    graph: dict | None
    contracts: int = 0
    exits: tuple[str, ...] = ()


def _no_preview(reason: str, legs: list | None = None, price=None) -> PreviewResult:
    return PreviewResult(False, reason, legs or [], price, None, None)


BUYING_POWER = re.compile(r"Buying power change: \$(-?[\d,]*\.?\d+)")


def preview_order(deps: BotDeps, bot_id: int) -> PreviewResult:
    """Preview order: the order the bot would send right now, picked from the live chain
    and checked with the broker, never sent and with no run saved. It ignores the bot's
    schedule, caps, filters and the market's hours (it sends nothing, so it works on the
    last quotes outside them), but the order path's checks (Freeze, size limits) and the
    broker's own apply. Live and Paper bots alike."""
    bot = bot_store.load(deps.session_factory, bot_id)
    if bot is None:
        return _no_preview("That bot isn't there.")
    try:
        return _preview_order(deps, bot)
    except (BrokerError, CredentialsUnavailable) as e:
        logger.exception("bot %s: Preview order couldn't reach the broker", bot.name)
        return _no_preview(f"Couldn't reach the broker: {e}")
    except Exception as e:  # noqa: BLE001 -- a page, not a 500, whatever the read raised
        logger.exception("bot %s: Preview order failed", bot.name)
        return _no_preview(f"Something went wrong ({e.__class__.__name__}). Nothing was sent.")


def _preview_order(deps: BotDeps, bot) -> PreviewResult:
    now = deps.now()
    contracts, _ = contracts_for(bot, None)
    if bot.sizing == "dollars":  # sized once priced; the guards need room for one
        contracts = 1
    problem = _guard(deps, bot, True, contracts, 0, require_open=False)
    if problem:
        return _no_preview(problem)
    day = now.astimezone(MARKET_TZ).date()
    try:
        picked, spot, _notes = _find_legs(deps, bot, day, [])
    except (_Missing, strikes.NoTrade) as e:
        return _no_preview(str(e))
    legs = _leg_rows(picked, {leg.position: leg.dte for leg in bot.legs})
    problem = tick_problem(deps, bot)
    if problem:
        return _no_preview(f"{problem} Not entered.", legs)
    mid = strikes.entry_price(picked, Decimal(0))
    problem = strikes.too_close_to_even(mid)
    if problem:
        return _no_preview(problem, legs)
    try:
        strikes.inverted_group(bot, picked)
    except strikes.Unsafe as e:
        return _no_preview(str(e), legs)
    price, cap, problem = _opening_price(deps, bot, picked, contracts, mid > 0)
    if problem:
        return _no_preview(problem, legs, price)
    if bot.sizing == "dollars":
        sized = _size_by_buying_power(deps, bot, picked, price, cap)
        if sized.problem:
            return _no_preview(f"{sized.problem} Not entered.", legs, price)
        contracts = sized.contracts
    order = Order(
        tuple(Leg(p.symbol, p.side, p.ratio) for p in picked), contracts, True, price=price
    )
    result = orders.submit(
        deps.session_factory,
        deps.clients(),
        orders.Request(bot.broker, bot.account_number, "bot", order, bot_id=bot.id,
                       preview_only=True),
        now,
        deps.notify,
    )  # fmt: skip
    found = BUYING_POWER.search(result.message)
    buying_power = Decimal(found.group(1).replace(",", "")) if found else None
    return PreviewResult(
        result.ok, result.message, legs, price, buying_power,
        _graph(bot, picked, contracts, now, spot) if result.ok else None, contracts,
        _exit_lines(bot, legs, picked, price),
    )  # fmt: skip


def _exit_lines(bot, legs: list, picked: tuple, price: Decimal) -> tuple[str, ...]:
    """What each group's exits would be on this order (exit_plan.group_lines). Never stops
    the preview: none if they can't be worded."""
    try:
        return tuple(exit_plan.group_lines(bot, legs, {p.symbol: p.mid for p in picked}, price))
    except Exception:  # noqa: BLE001 -- a note on the page, never a reason not to preview
        logger.exception("bot %s: couldn't word the preview's exits", bot.name)
        return ()


def _graph(bot, picked: tuple, contracts: int, now: datetime, spot) -> dict:
    """The picked legs as one trade for the risk graph, entered and marked at the mids."""
    legs = []
    for p in picked:
        held = Decimal(contracts * p.ratio)
        legs.append(PositionLeg(
            p.symbol, bot.underlying, InstrumentType.OPTION,
            OptionDetails(p.right.upper(), p.strike, p.expiration),
            1 if p.side is Side.BUY else -1, held,
            open_value=(p.mid if p.side is Side.SELL else -p.mid) * held * 100,
        ))  # fmt: skip
    position = Position(
        f"preview-{bot.id}", bot.broker, bot.account_number, now, legs,
        Decimal(contracts), Decimal(0),
    )  # fmt: skip
    symbol = graph_symbol(position)
    return {
        "key": position.key,
        "label": f"{bot.name} · Preview order",
        "symbol": symbol,
        "legs": graph_legs(position, {}, {p.symbol: p.mid for p in picked}),
        "realized_pl": 0.0,
        "spot": None if spot is None else float(spot),
    }


def _paper_entry(deps, run_id: int, outcome, legs_json: list, contracts: int) -> None:
    """A Paper bot's scheduled entry (decision 0061): its one order check came back as a
    preview, so it pretend-fills at the mid (bot_paper.enter); a check that refused, or
    a leg with no price, skips the run. Its exits are the paper watcher's (not placed)."""
    update_run(deps, run_id, chase_log=outcome.log)
    if outcome.status != "preview":
        update_run(deps, run_id, status="skipped", reason=outcome.message or "Not entered.")
        return
    reason = bot_paper.enter(deps, run_id, legs_json, contracts, deps.now())
    if reason is not None:
        update_run(deps, run_id, status="skipped", reason=reason)


def _priced(bot, legs_json: list, picked: tuple, mid: Decimal) -> str | None:
    """The run's record of its side and what each group would set at the mid (decision 0048,
    exit_plan.preview). Never stops the entry: None if it can't be worded."""
    try:
        return exit_plan.preview(bot, legs_json, {p.symbol: p.mid for p in picked}, mid)
    except Exception:  # noqa: BLE001 -- a record, never a reason not to trade
        logger.exception("bot %s: couldn't word the entry's preview", bot.name)
        return None


SPOT_STAMP_FRESH = timedelta(seconds=30)  # entry_spot.MAX_AGE_SECONDS


def stamp_spot(deps, bot, run_id: int) -> None:
    """Slice 19 (entry_spot.stamp): a bot with the underlying move rule on measures it from
    the underlying's last trade at the fill, in its own points (XSP: SPX ÷ 10), when that
    trade is under 30 s old; otherwise the chain's spot written at the pick stays. Never
    raises: a missing base only pauses that rule (bot_watch says so), never the entry."""
    if not bot.use_underlying_exit:
        return
    try:
        quote = deps.hub.latest(underlyings.watch_symbol(bot.underlying))
        at = getattr(quote, "last_at", None) if quote is not None else None
        if quote is None or quote.last is None or at is None:
            return
        if deps.now() - at >= SPOT_STAMP_FRESH:
            return
        update_run(deps, run_id, entry_spot=underlyings.in_points(bot.underlying, quote.last))
    except Exception:  # noqa: BLE001 -- the chain's spot stays
        logger.exception("bot %s: couldn't stamp the entry's price on run %s", bot.name, run_id)


UNKNOWN_WORST = "Couldn't work out the worst price this entry could reach."


def _size_by_buying_power(deps, bot, picked: tuple, price: Decimal, cap) -> sizing.Sized:
    """Slice 19, decision 0050: the units the bot's budget buys, by the buying power one
    unit needs on its own at the worst price its entry chase could reach (on the order's
    own ticks, the tables the chase reads), as the bot's account's broker reports it.
    Anything that can't be worked out refuses the entry: never a guess."""
    if bot.max_per_entry_usd is None:
        return sizing.Sized(0, problem=UNKNOWN_WORST)
    legs = tuple(Leg(p.symbol, p.side, p.ratio) for p in picked)
    probe = Order(legs, 1, True, price=price)
    try:
        tables = orders.tick_tables(deps.session_factory, deps.clients(), probe, deps.now())
        worst = sizing.worst_price(
            price, bot.entry_chase, partial(strikes.next_price, debit_max=cap),
            lambda at: ticks.order_tick(probe, at, tables),
        )  # fmt: skip
    except Exception:  # noqa: BLE001 -- can't tell the worst price, so don't size a guess
        logger.exception("bot %s: couldn't work out the worst entry price", bot.name)
        return sizing.Sized(0, problem=UNKNOWN_WORST)
    broker = orders.BROKER_NAMES.get(bot.broker, bot.broker)

    def preview(units: int) -> dict:
        order = Order(legs, units, True, price=worst)
        return orders.preview_reply(deps.clients(), bot.broker, bot.account_number, order)

    try:
        if bot.broker == "schwab":  # no isolated figure: what one more unit takes
            cost = sizing.schwab_unit_cost(preview(1), preview(2))
        else:
            cost = sizing.tastytrade_unit_cost(preview(1), worst)
    except (BrokerError, CredentialsUnavailable) as e:  # e.g. not enough for even one
        return sizing.Sized(0, problem=f"{broker}'s preview of one contract was refused: {e}")
    except Exception as e:  # noqa: BLE001 -- an unreadable preview sizes nothing
        logger.exception("bot %s: couldn't read %s's preview", bot.name, broker)
        return sizing.Sized(0, problem=f"{broker}'s preview couldn't be read "
                            f"({e.__class__.__name__}).")  # fmt: skip
    return sizing.buying_power_size(
        bot.max_per_entry_usd, cost, worst, max_per_trade=trade_cap(bot),
        ratio=largest_ratio(bot), broker=broker,
    )  # fmt: skip


def tick_problem(deps, bot) -> str | None:
    """Slice 16 (Tracker2's option_ticks.ensure, refused in
    analyzer_entry.run_analyzer_entry): an underlying whose single legs follow the broker's
    own increments (a stock or ETF; not SPX's tiers, not XSP's flat penny) is traded only
    once its table is loaded, stored or fetched from TastyTrade. orders.tick_tables keeps
    it for the exit math (ticks.learn), which rounds the stops and one-leg closes on it.
    None when it may trade; can't load (no TastyTrade, a failed fetch, an empty table):
    refused."""
    root = underlyings.root(bot.underlying)
    if root in ticks.TIERED_ROOTS | ticks.PENNY_INDEX_ROOTS:
        return None
    if underlyings.futures(bot.underlying):
        return None  # bots slice 17: its series' ticks load when it's resolved (bot_futures)
    refused = f"Couldn't load TastyTrade's option increments for {root}. Try again."
    try:
        day = deps.now().astimezone(MARKET_TZ).date()
        probe = Order(
            (Leg(occ_symbol(root, OptionDetails("CALL", Decimal(1), day)), Side.SELL),),
            1, True, price=Decimal(1),
        )  # fmt: skip
        tables = orders.tick_tables(deps.session_factory, deps.clients(), probe, deps.now())
    except Exception:  # noqa: BLE001 -- can't tell, so don't trade
        logger.exception("couldn't read %s's option increments", root)
        return refused
    return None if tables.get(ticks.equity_key(root)) else refused


def underlying_price(deps, clients, underlying: str):
    """The underlying's price in its own points, as a float like ChainData.underlying_last:
    XSP is SPX ÷ 10 (Tracker2's entry_spot.live_price, technicals.dx_symbol and
    spot_divisor), so a stale XSP index print never holds up an entry SPX can price.
    Strikes, spot offsets and the watch rules' entry price all start from it."""
    key = underlyings.watch_symbol(underlying)
    price = deps.chain.underlying_last(clients, deps.hub, key)
    if not price or key == underlying:
        return price
    return float(underlyings.in_points(underlying, Decimal(str(price))))


SPOT_FRESH = timedelta(seconds=30)  # exit_math.STALE_AFTER, for the price a pick is built on


def _held(deps, bot, notes: list) -> dict | None:
    """The account's option positions (plain symbol -> signed contracts) for moving
    conflicted strikes (slice 19; broker.held_by_strike), read once. Unreadable: None and
    a note, and the held check is skipped (resolver.resolve_trade: "a positions outage
    must degrade to the previous behaviour"; the broker still refuses a real clash)."""
    try:
        client = next(c for c in deps.clients() if c.broker == bot.broker)
        return client.option_positions(bot.account_number)
    except Exception as e:  # noqa: BLE001 -- the held check is skipped, not the entry
        logger.exception("bot %s: couldn't read positions for conflicted strikes", bot.name)
        notes.append(f"Couldn't read your positions ({e.__class__.__name__}), so strikes "
                     "held the other way weren't checked.")  # fmt: skip
        return None


def _pick(deps, bot, rows: dict, spot, resolved: dict, notes: list | None = None) -> tuple:
    """The strikes (strikes.pick_legs), each leg on its own expiration's chain (`rows`
    and `resolved` by DTE; slice 14), only on the underlying's own root (SPXW for SPX;
    slice 16). A bot with a delta or spot leg (slice 10) needs a fresh underlying price,
    and a delta leg reads the chain's deltas, worked out to its own expiration's close; a
    premium bot reads neither, as before."""
    watched = underlyings.watch_symbol(bot.underlying)  # XSP: SPX's quote (slice 16)
    quote = deps.hub.latest(watched)
    at = quote and (quote.last_at or quote.at)
    if bot.market_picks and at is not None and deps.now() - at > SPOT_FRESH:
        when = at.astimezone(MARKET_TZ).strftime("%H:%M:%S")
        raise strikes.NoData(
            f"{bot.underlying}'s price is stale (last {when} New York); not entered.",
            kind=f"{bot.underlying} price",
        )
    # #206: a short placed by an offset may land in the money; its delta is the check
    deltas = any(leg.pick == "delta" or strikes.offset_short(leg) for leg in bot.legs)
    # Streamed rows count while TastyTrade's stream is up and the underlying is updating
    # through it: DXLink sends on change, so a quiet option's quote isn't a stale one.
    status = deps.hub.status() if hasattr(deps.hub, "status") else {}
    tasty = _tasty_quote(deps.hub, watched)
    tasty_at = tasty and (tasty.last_at or tasty.at)
    stream_ok = (
        status.get("tastytrade") == "streaming"
        and tasty_at is not None
        and deps.now() - tasty_at <= SPOT_FRESH
    )
    chains = {
        dte: strikes.chain_from_rows(
            rows[dte],
            spot,
            deltas=deltas,
            now=deps.now(),
            expires_at=black_scholes.expiry_close_utc(when),
            stream_ok=stream_ok,
        )  # fmt: skip
        for dte, when in resolved.items()
    }
    said = notes if notes is not None else []
    held = _held(deps, bot, said) if bot.move_conflicted_strikes else None
    return strikes.pick_legs(
        bot.legs, chains, root=underlyings.root(bot.underlying), underlying=bot.underlying,
        min_width=bot.min_short_width, held=held, move_conflicted=bot.move_conflicted_strikes,
        notes=said,
    )  # fmt: skip


def _tasty_quote(hub, key: str):
    """The underlying's quote from TastyTrade's own stream, which the streamed option
    rows came through: a Schwab quote says nothing about that stream's health."""
    if hasattr(hub, "get"):
        return hub.get("tastytrade", key)
    quote = hub.latest(key)
    return quote if quote is not None and quote.source == "tastytrade" else None


def _no_data(deps, bot, e: Exception) -> None:
    """Missing market data skips the entry and alerts once a day per bot and kind, like
    a filter that couldn't be read (bot_filters.first_today)."""
    what = getattr(e, "kind", "deltas")
    day = deps.now().astimezone(MARKET_TZ).date()
    if bot_filters.first_today(bot.id, what, day):
        alert(deps, f"{bot.name}: entry skipped, {what} unavailable", str(e),
              f"/automation/{bot.id}/runs")  # fmt: skip


def _delta_text(delta: Decimal) -> str:
    return f"{delta.quantize(Decimal('0.0001')).normalize():f}"


def _settle(deps, bot, root_id: int, outcome, tranches: dict, handed: set) -> None:
    """Place every filled run's exits first, whatever the ending (Tracker2's
    orchestrator protects each tranche after the chase), then end each run the
    chase left without a fill; the fills are already recorded."""
    status = outcome.status
    link = f"/automation/{bot.id}/runs"
    filled_runs = [fill_run for fill_run, _ in outcome.fills]
    for fill_run in filled_runs:
        if fill_run not in handed:
            handed.add(fill_run)
            hand_to_exits(deps, bot, fill_run)
    update_run(deps, root_id, chase_log=outcome.log)  # the whole entry's log, on the root
    for child in tranches:
        if child != root_id:
            update_run(deps, child, chase_log=outcome.log_of(child))  # its own part
    if root_id not in filled_runs and outcome.run_id != root_id and filled_runs:
        n = tranches.get(filled_runs[0], 2)
        update_run(
            deps,
            root_id,
            status="missed",
            reason=f"Nothing filled on this order; the entry filled in tranche {n}.",
        )
    if outcome.run_id is not None:
        _end_run(deps, bot, outcome.run_id, outcome)
    if status == "missed":
        alert(
            deps,
            f"{bot.name}: entry missed",
            outcome.message or "Not filled after the last try.",
            link,
        )
    elif status == "short":
        detail = f"Entry short: {outcome.filled} of {outcome.requested} filled — rest given up"
        if outcome.message:
            detail = f"{detail}. {outcome.message}"
        alert(deps, f"{bot.name}: entry short", detail, link)


def _end_run(deps, bot, run_id: int, outcome) -> None:
    """The run whose order ended without a fill: preview, refused, unclear or missed."""
    status, link = outcome.status, f"/automation/{bot.id}/runs"
    log = outcome.log_of(run_id)
    if status == "preview":
        update_run(deps, run_id, status="preview", reason=outcome.message, chase_log=log)
    elif status == "refused":
        update_run(deps, run_id, status="error", reason=outcome.message, chase_log=log)
        alert(deps, f"{bot.name}: entry refused", outcome.message, link, run_id=run_id)
    elif status == "uneven":
        # Legs held without a whole unit: never recorded or protected by the app.
        # Left "running" and marked, so recovery skips it; you settle it.
        detail = f"{NEEDS_YOU}: {UNEVEN_DETAIL}"  # starts with UNEVEN
        update_run(
            deps,
            run_id,
            status="running",
            broker_order_id=outcome.order.broker_order_id if outcome.order else None,
            chase_log=log,
            reason=detail,
        )
        alert(deps, f"{bot.name}: a run needs you", UNEVEN_DETAIL, link, run_id=run_id)
    elif status == "unclear":
        # The order may still be working (or its fill unknown): recovery settles it.
        broker_id = outcome.order.broker_order_id if outcome.order else None
        detail = f"{outcome.message} Check your broker; recovery will settle it."
        update_run(
            deps,
            run_id,
            status="running",
            broker_order_id=broker_id,
            chase_log=log,
            reason=detail,
        )
        alert(deps, f"{bot.name}: entry unclear", detail, link, run_id=run_id)
    else:  # missed, or short: its order filled nothing
        update_run(
            deps,
            run_id,
            status="missed",
            reason=outcome.message or "Not filled after the last try.",
            broker_order_id=outcome.order.broker_order_id if outcome.order else None,
            chase_log=log,
        )


def record_fill(deps, run_id: int, found, status: str, log: list | None = None) -> None:
    """Freeze the fill once: credit, each leg's fill, contracts (whole units, the
    smallest across the legs, as the chase counts them). Contracts a leg filled
    beyond those whole units are alerted: the app doesn't track them."""
    legs = [
        {
            "symbol": leg.symbol,
            "side": str(leg.side),
            "fill_price": f"{leg.fill_price:f}",
            "contracts": leg.quantity - leg.remaining,
        }
        for leg in found.legs
        if leg.fill_price is not None
    ]
    with deps.session_factory() as session:
        run = session.get(BotRun, run_id)
        ratios = {plain(leg["symbol"]): leg["ratio"] for leg in run.legs}
        bot_id = run.bot_id
        bot = session.get(Bot, bot_id)
        name = bot.name if bot is not None else "A bot"
    units, leftover = filled_units(found, ratios)
    credit = sum(
        (
            Decimal(leg["fill_price"])
            * (1 if leg["side"] == "sell" else -1)
            * ratios.get(plain(leg["symbol"]), 1)
            for leg in legs
        ),
        Decimal(0),
    )
    if leftover:
        alert(
            deps,
            f"{name}: legs filled unevenly",
            f"The entry's legs filled unevenly: the app recorded {units} whole "
            "unit(s); the extra contracts aren't tracked or protected. Settle them at "
            "your broker.",
            f"/automation/{bot_id}/runs",
            run_id=run_id,
        )
    update_run(
        deps,
        run_id,
        status=status,
        broker_order_id=found.broker_order_id,
        filled_legs=legs,
        fill_credit=credit,
        contracts=units,
        filled_at=deps.now(),
        **({"chase_log": log} if log is not None else {}),
    )
