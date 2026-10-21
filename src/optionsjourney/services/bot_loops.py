"""The bots' background loops (spec: the first automation, sections 2, 4, 5):
the clock (every 10 s) fires due entry times; the watch loop (every 2 s)
fires profit targets; the monitor (every 30 s) settles runs left running
after a crash (Tracker2's reconciler), then works exits, then timed closes and
time actions (bots slice 4, services/bot_timed.py), then the integrity checks
(slice 8); the schedule check (every minute, slice 8) says when one has stopped. Like the app's
other loops (backups, P/L Day), each is a daemon thread that never raises."""

import logging
import threading
import time as clock
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from optionsjourney.bots import floating, schedule
from optionsjourney.core import sessions
from optionsjourney.core.market import MARKET_TZ
from optionsjourney.core.sessions import session_close
from optionsjourney.db.models import Bot, BotExit, BotRun, OrderLog
from optionsjourney.services import (
    bot_audit,
    bot_entry,
    bot_exits,
    bot_expiry,
    bot_filters,
    bot_integrity,
    bot_limit,
    bot_manage,
    bot_overnight,
    bot_paper_exits,
    bot_settle,
    bot_store,
    bot_timed,
    bot_watch,
    bot_watched_stops,
    economic_events,
    manual_attach,
    market_feed,
    notify,
    orders,
    schwab_login,
)
from optionsjourney.trading.orders import OrderStatus, filled_units, plain

logger = logging.getLogger(__name__)
STALE_RUN = timedelta(days=5)
NEEDS_YOU = bot_entry.NEEDS_YOU  # a run's reason once we've alerted about it
MAX_CANCELS = 2  # cancels of one orphaned entry order before it's left to you
PLACING_STALE = timedelta(minutes=2)  # exit rows still "placing" this long: a crash
CALENDAR_EVERY = 600  # seconds between checks whether a calendar refresh is due
LOGIN_EVERY = 600  # seconds between checks of Schwab's refresh login (bots slice 11)


_calendar_alerted = None  # the New York day the holiday-table alert last went out


def _check_calendar(deps, today) -> None:
    """Once a day, say so if the holiday table has run out of the current year."""
    global _calendar_alerted  # noqa: PLW0603 -- one in-memory date per process
    if _calendar_alerted == today or sessions.calendar_covers(today):
        return
    _calendar_alerted = today
    bot_entry.alert(
        deps,
        "Holiday calendar out of date",
        f"The market holiday table doesn't cover {today.year}. "
        "Bots may try to trade on a holiday; update the app.",
        "/automation",
    )


def deps_for(services) -> bot_entry.BotDeps:
    return bot_entry.BotDeps(
        services.session_factory,
        services.broker_clients,
        services.quote_hub,
        services.chain,
        # Every bot alert, the order path's included, is queued (bots slice 9)
        notify=notify.sender(services.session_factory),
        # The filters' prices: TastyTrade's stream, else Schwab's REST market data (#194)
        market=bot_filters.Market(
            market_feed.FallbackFeed(
                [
                    market_feed.TastyFeed(services.broker_clients),
                    market_feed.SchwabFeed(services.broker_clients),
                ]
            )
        ),
    )


def clock_pass(deps, spawn=threading.Thread) -> None:
    """Fire every entry time that's due; record the ones that were missed."""
    local = deps.now().astimezone(MARKET_TZ)
    _check_calendar(deps, local.date())
    if session_close(local.date()) is None:
        return  # weekends and holidays: no runs at all
    with deps.session_factory() as session:
        done = set(session.scalars(select(BotRun.key).where(BotRun.trading_date == local.date())))
    bots = bot_store.armed(deps.session_factory)
    lists = _day_lists(deps, bots, local)
    for bot in bots:
        try:
            _clock_bot(deps, bot, local, done, spawn, lists)
        except Exception:  # noqa: BLE001 -- one bot never delays the others
            logger.exception("clock couldn't run bot %s", bot.id)


_lists_alerted: set[tuple[str, object]] = set()  # (list, New York day) already alerted


def _day_lists(deps, bots, local) -> dict:
    """Today's answer for every list an armed bot relies on, and one alert a day per
    list that can't tell (guards.alert_unavailable_sets; here the clock checks every
    armed bot, so a specific-dates bot's missing list alerts too)."""
    names = sorted({name for bot in bots for name in schedule.lists_in_use(bot)})
    if not names:
        return {}
    lists = economic_events.lists_for(deps.session_factory, names, local.date(), deps.now())
    if not economic_events.FIRST_CHECK_DONE.is_set():
        return lists  # still fetching at startup: decide (fail closed), but don't alert yet
    for name, day in economic_events.ff_disagreements(deps.session_factory, names, deps.now()):
        if (f"ff:{name}", day) in _lists_alerted:
            continue
        _lists_alerted.add((f"ff:{name}", day))
        bot_entry.alert(
            deps,
            f"ForexFactory is missing {'an' if name == 'FOMC' else 'a'} {name} day",
            f"The {'Fed' if name == 'FOMC' else 'FRED'} has {name} on {day:%a %m/%d}, but "
            "ForexFactory's calendar for this week doesn't: its titles may have changed. "
            "The day still counts as a list day; check the app's ForexFactory reading.",
            "/automation",
        )
    for name in names:
        if lists[name] is not None or (name, local.date()) in _lists_alerted:
            continue
        _lists_alerted.add((name, local.date()))
        users = ", ".join(b.name for b in bots if name in schedule.lists_in_use(b))
        bot_entry.alert(
            deps,
            f"{name} dates unavailable today",
            f"The app can't tell whether today is in the {name} list ({users}). A bot that "
            "blacks out those days skips its entries today; one that trades only on them "
            "doesn't trade. Press Refresh now in Market calendar on the Automation page.",
            "/automation",
        )
    return lists


def _clock_bot(deps, bot, local, done, spawn, lists=None) -> None:
    if not schedule.runs_on(bot, local.date(), lists):
        return  # not one of this bot's days: no run at all
    if bot.use_floating_entry:
        _clock_floating(deps, bot, local, spawn)
        return  # a window replaces the entry times
    for hhmm, action in schedule.due(bot.entry_times, local.replace(tzinfo=None), done, bot.id):
        if action == "skip":
            reason = "Missed its time: the app was closed or busy."
            bot_entry.record_skip(deps, bot.id, hhmm, reason)
        else:
            spawn(
                target=bot_entry.fire,
                args=(deps, bot.id, hhmm),
                name=f"bot-{bot.id}-{hhmm}",
                daemon=True,
            ).start()


# The minute each floating bot was last started (New York day, HH:MM), so the 10-second
# clock checks a minute once (Tracker2's once-a-minute cron job for the window).
_floating_started: dict[int, tuple] = {}


def _clock_floating(deps, bot, local, spawn) -> None:
    """Start the bot's floating check for this minute, once, inside its window
    (registry's bot_float job; bot_entry.fire_floating decides the rest)."""
    hhmm = local.strftime("%H:%M")
    if not floating.in_window(bot, hhmm) or _floating_started.get(bot.id) == (local.date(), hhmm):
        return
    _floating_started[bot.id] = (local.date(), hhmm)
    spawn(
        target=bot_entry.fire_floating,
        args=(deps, bot.id, hhmm),
        name=f"bot-{bot.id}-float-{hhmm}",
        daemon=True,
    ).start()


def reconcile(deps) -> None:
    """Settle runs left "running" with no thread chasing them (a crash or restart).
    Does nothing while Freeze is on: a fill found then couldn't get its stops."""
    if orders.switches(deps.session_factory).freeze:
        return
    now = deps.now()
    with deps.session_factory() as session:
        running = [
            (r.id, r.bot_id, r.broker_order_id, r.created_at)
            for r in session.scalars(select(BotRun).where(BotRun.status == "running"))
        ]
    for run_id, bot_id, order_id, created in running:
        if run_id in bot_entry.ACTIVE:
            continue
        try:
            _settle(deps, run_id, bot_id, order_id, created, now)
        except Exception:  # noqa: BLE001 -- one run never stops the others
            logger.exception("couldn't settle bot run %s", run_id)


def _needs_you(deps, run_id, flagged, detail, bot_id, name="A bot") -> None:
    """Alert once: the run's reason is marked, and marked runs aren't alerted again."""
    if flagged:
        return
    bot_entry.update_run(deps, run_id, reason=f"{NEEDS_YOU}: {detail} Check your broker.")
    bot_entry.alert(
        deps,
        f"{name}: a run needs you",
        f"{detail} Check your broker.",
        f"/automation/{bot_id}/runs",
        run_id=run_id,
    )


def _cancels_sent(deps, account: str, order_id: str) -> int:
    with deps.session_factory() as session:
        return session.scalar(
            select(func.count(OrderLog.id)).where(
                OrderLog.account_number == account,
                OrderLog.action == "cancel",
                OrderLog.broker_order_id == order_id,
            )
        )


def _settle(deps, run_id, bot_id, order_id, created, now) -> None:
    with deps.session_factory() as session:
        run = session.get(BotRun, run_id)
        # Re-read: the entry thread may have settled it since the list was taken.
        if run is None or run.status != "running" or run_id in bot_entry.ACTIVE:
            return
        if (run.reason or "").startswith(bot_entry.UNEVEN):
            return  # legs without matching whole units: never recorded or protected
        # Other runs that need you are still read every pass (an order still working
        # after its cancels may fill yet, and then gets recorded and protected); the
        # mark only stops a second alert.
        flagged = (run.reason or "").startswith(NEEDS_YOU)
        run_account, symbols = run.account_number, {plain(leg["symbol"]) for leg in run.legs}
        run_broker = run.broker
        resting = run.entry_mode == "limit"  # bots slice 17: a resting entry, not an orphan
        ratios = {plain(leg["symbol"]): leg["ratio"] for leg in run.legs}
    bot = bot_store.load(deps.session_factory, bot_id)
    if bot is None:
        # We can't know its account, so leave the run alone and say so once.
        _needs_you(deps, run_id, flagged, "A run's bot was deleted before it settled.", bot_id)
        return
    if now - created > STALE_RUN:
        detail = "A run from more than 5 days ago never settled."
        _needs_you(deps, run_id, flagged, detail, bot_id, bot.name)
        return
    broker = run_broker or bot.broker  # the broker the run fired at (bots slice 11)
    tasty = next(c for c in deps.clients() if c.broker == broker)
    account = run_account or bot.account_number
    # Wait while any of the run's attempts is still unclear: a replace that timed
    # out may be working at the broker under an id the app doesn't have yet.
    orders.resolve_unclear(deps.session_factory, deps.clients(), broker, account, now)
    with deps.session_factory() as session:
        unclear = session.scalar(
            select(OrderLog.id)
            .where(
                OrderLog.bot_run_id == run_id,
                OrderLog.result.like(f"{orders.UNCLEAR}%"),
                OrderLog.cleared_at.is_(None),
            )
            .limit(1)
        )
    if unclear is not None or symbols & orders.unclear_symbols(
        deps.session_factory, broker, account
    ):
        return  # the order log blocks its legs until it's found or cleared
    if order_id is None:
        with deps.session_factory() as session:
            order_id = session.scalar(
                select(OrderLog.broker_order_id)
                .where(OrderLog.bot_run_id == run_id, OrderLog.broker_order_id.is_not(None))
                .order_by(OrderLog.id.desc())
            )
        if order_id is None:
            return  # nothing was placed that the app knows of; leave it to the log
    if resting:
        # A resting entry whose send was unclear, found at the broker (or a crash between
        # its send and its status): it's the bot's entry, never an orphan to cancel. The
        # resting entries' pass reads it from here: a fill, a partial or its end.
        found_it = "Found at your broker after an unclear send: resting."
        bot_entry.update_run(
            deps, run_id, status=bot_limit.WORKING, broker_order_id=order_id, reason=found_it
        )
        return
    found = tasty.get_order(account, order_id)
    if found.may_be_working:
        # Never leave an orphaned order working, and never chase it. Cancel it at
        # most twice in all (the chase's own cancel counts), never every pass.
        if _cancels_sent(deps, account, order_id) < MAX_CANCELS:
            orders.cancel(
                deps.session_factory,
                deps.clients(),
                broker,
                account,
                order_id,
                "bot",
                bot.id,
                now=now,
                notify=deps.notify,
            )
            found = tasty.get_order(account, order_id)
        if found.may_be_working:
            if _cancels_sent(deps, account, order_id) >= MAX_CANCELS:
                detail = "The entry order is still working after the app cancelled it."
                _needs_you(deps, run_id, flagged, detail, bot_id, bot.name)
            return  # read it again next pass
    if found.status is OrderStatus.FILLED or any(
        leg.remaining < leg.quantity for leg in found.legs
    ):
        units, uneven = filled_units(found, ratios)
        if not units or uneven:
            # Legs held without matching whole units: recording them would place
            # stops on a trade missing legs (a stop on an unheld short opens one).
            # Marked even if it already needed you: the UNEVEN mark makes recovery skip it.
            _needs_you(deps, run_id, False, bot_entry.UNEVEN_DETAIL, bot_id, bot.name)
            return
        status = "filled" if found.status is OrderStatus.FILLED else "partial"
        bot_entry.record_fill(deps, run_id, found, status)
        bot_entry.hand_to_exits(deps, bot, run_id)
    else:
        bot_entry.update_run(
            deps,
            run_id,
            status="missed",
            broker_order_id=order_id,
            reason="Settled after a restart: not filled.",
        )


def check_exits(deps) -> None:
    """Today's filled runs whose exits never got placed: a crash between the
    fill and the exits, or rows left "placing". Alert once and mark the rows
    failed. Never re-place: a "placing" row may have a live stop the app
    doesn't know about."""
    now = deps.now()
    local = now.astimezone(MARKET_TZ)
    day_start = datetime.combine(local.date(), datetime.min.time(), MARKET_TZ)
    with deps.session_factory() as session:
        runs = [
            (r.id, r.bot_id, (r.reason or "").startswith(NEEDS_YOU))
            for r in session.scalars(
                select(BotRun).where(
                    # bots slice 17: a resting (GTC) entry may fill on a later day
                    (BotRun.trading_date == local.date())
                    | ((BotRun.entry_mode == "limit") & (BotRun.filled_at >= day_start)),
                    BotRun.status.in_(("filled", "partial")),
                    BotRun.paper.is_(False),  # a paper run's exits are the paper watcher's
                )
            )
        ]
    for run_id, bot_id, flagged in runs:
        if run_id in bot_entry.ACTIVE:
            continue  # its entry thread is still placing them
        try:
            _check_run_exits(deps, run_id, bot_id, flagged, now)
        except Exception:  # noqa: BLE001 -- one run never stops the others
            logger.exception("couldn't check bot run %s's exits", run_id)


def _check_run_exits(deps, run_id, bot_id, flagged, now) -> None:
    with deps.session_factory() as session:
        rows = list(session.scalars(select(BotExit).where(BotExit.run_id == run_id)))
        stale = [r for r in rows if r.state == "placing" and now - r.updated_at > PLACING_STALE]
        futures = bot_exits.pt_only(session, run_id)
        resting = False  # a resting target cut off in its send: it may rest at TastyTrade
        for row in stale:
            row.state = "failed"
            if row.kind == "target" and (futures or bot_exits.rested(row)):
                # held as an unclear change is (target_may_rest): no close is sent on it
                resting = True
                event = {"event": bot_exits.TARGET_UNCLEAR, "why": "still placing; a crash?"}
            else:
                event = {"event": "still placing; a crash?"}
            row.log = [*row.log, {"at": now.isoformat(), **event}]
        session.commit()
    if rows and not stale:
        return
    bot = bot_store.load(deps.session_factory, bot_id)
    if not rows:
        if flagged:
            return
        if bot is not None and not any(
            g.stop_value is not None or g.target_value is not None for g in bot.groups
        ):
            return  # the bot has no exits to place
    name = bot.name if bot is not None else "A bot"
    detail = f"{NEEDS_YOU}: exits may be missing — check your broker."
    if resting:
        detail += (
            " A profit target may rest at TastyTrade that the app can't vouch for, so no "
            "close is sent on that group until you've checked."
        )
    bot_entry.update_run(deps, run_id, reason=detail)
    bot_entry.alert(
        deps, f"{name}: a run needs you", detail, f"/automation/{bot_id}/runs", run_id=run_id
    )


def send_notices(deps) -> int:
    """Decision 0054: alert once for each bot a migration switched off (Bot.notice), at
    startup; the notice stays on the bot's page until it's armed again."""
    with deps.session_factory() as session:
        rows = session.scalars(
            select(Bot).where(Bot.notice.is_not(None), Bot.notice_sent.is_(False))
        ).all()
        for row in rows:
            bot_entry.alert(deps, f"{row.name} was switched off", row.notice,
                            f"/automation/{row.id}", bot_id=row.id)  # fmt: skip
            row.notice_sent = True
        session.commit()
        return len(rows)


def recover(deps) -> None:
    """At startup, before the loops: settle runs, flag missing exits, and
    settle profit targets a crash left firing. Never raises."""
    for name, step in (
        ("runs", reconcile),
        ("exits", check_exits),
        ("manage with", manual_attach.pending_pass),  # #158: ticket rules once it fills
        ("targets", bot_exits.recover_targets),
        ("exits off", bot_manage.recover),
        # Slices 11, 13: stops the app should have watched while it was off or asleep
        ("watched stops", bot_watched_stops.recover),
        # Slice 14: every trade held overnight against the broker (problems only)
        ("held trades", lambda d: bot_overnight.check(d, startup=True)),
        # Decision 0054: bots migration 0034 switched off
        ("notices", send_notices),
    ):
        try:
            step(deps)
        except Exception:  # noqa: BLE001 -- startup recovery never stops the app
            logger.exception("startup bot recovery (%s) failed", name)


def login_pass(services, deps) -> None:
    """Schwab's refresh-login reminders (bots slice 11), as safety alerts."""
    with services.session_factory() as session:
        some = session.scalar(
            select(Bot.id).where(Bot.broker == "schwab", Bot.hidden.is_(False)).limit(1)
        )
    schwab_login.check(
        services.session_factory, getattr(services, "credentials", None), deps.now(),
        deps.notify, bots=some is not None,
    )  # fmt: skip


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _pass(name: str, body) -> None:
    """One pass of a loop: it never raises, and only a pass that finished is
    recorded for the schedule check (bot_audit)."""
    try:
        body()
    except Exception:  # noqa: BLE001 -- a loop never dies
        logger.exception("%s pass failed", name)
        return
    bot_audit.beat(name, _utcnow())


def _loop(name: str, seconds: float, body) -> None:
    def run():
        while True:
            _pass(name, body)
            clock.sleep(seconds)

    bot_audit.started(name, _utcnow())
    threading.Thread(target=run, name=name, daemon=True).start()


# Tracker2's registry._tick_clock and SLOW_TICK_SECONDS (#204): a pass this long or longer
# logs each step's time, so a slow loop says which step was slow.
_clock = clock.monotonic
SLOW_PASS = 4.0


def _run_steps(kind: str, steps, deps) -> None:
    """Each step on its own (one failing never skips the rest), timed: a pass of SLOW_PASS
    seconds or more logs a WARNING with every step's duration (registry._log_slow_tick)."""
    marks = [("start", _clock())]
    for name, step in steps:
        try:
            step(deps)
        except Exception:  # noqa: BLE001 -- the next step still runs
            logger.exception("bot %s step (%s) failed", kind, name)
        marks.append((name, _clock()))
    total = marks[-1][1] - marks[0][1]
    if total >= SLOW_PASS:
        parts = ", ".join(
            f"{name}={t - prev:.1f}s"
            for (_, prev), (name, t) in zip(marks, marks[1:], strict=False)
        )
        logger.warning("bot %s pass took %.1fs: %s", kind, total, parts)


def _monitor(deps) -> None:
    """Each step on its own: one failing never skips the rest of the pass."""
    _run_steps("monitor", (
        ("runs", reconcile),
        ("resting entries", bot_limit.check_pass),  # slice 17: at every hour
        ("exits", check_exits),
        ("manage with", manual_attach.pending_pass),  # #158: ticket rules once it fills
        ("monitor", bot_exits.monitor_pass),
        ("exits off", bot_manage.recover),  # a cut-off "turn exits off" (slice 6)
        ("settle", bot_settle.settle_pass),  # slice 14: far legs after the near ones expired
        ("expiry", bot_expiry.warn_pass),  # slice 16: American-style legs held into expiry
        ("timed close", bot_timed.timed_pass),
        ("time actions", bot_timed.actions_pass),
        ("pre-open", bot_overnight.preopen_pass),  # slice 14: held trades, from 09:20
        ("integrity", bot_integrity.check),  # last: it only reads and alerts
    ), deps)  # fmt: skip


def _watch(deps) -> None:
    """Every 2 s: the armed targets, the stops the app watches (slice 11), then the
    watch rules and profit actions (slice 7), then the paper trades' exits, each on its
    own."""
    _run_steps("watch", (
        ("targets", bot_exits.watch_pass),
        ("watched stops", bot_watched_stops.watch_pass),
        ("watch rules", bot_watch.watch_pass),
        ("paper", bot_paper_exits.watch_pass),  # Paper bots' pretend exits (decision 0061)
    ), deps)  # fmt: skip


def load_learned(deps) -> None:
    """The stored stock increments for the exit math (orders.load_learned). A failure is
    logged and alerted, and never stops the bots starting: a stock's entry then loads its
    table again, or refuses (bot_entry.tick_problem)."""
    try:
        orders.load_learned(deps.session_factory)
    except Exception:  # noqa: BLE001 -- the loops and recovery must still start
        logger.exception("couldn't load the stored option increments")
        bot_entry.alert(
            deps, "Stock option increments not loaded",
            "The app couldn't read the stored option price steps for stocks and ETFs. Stock "
            "and ETF bots won't enter until they load again from TastyTrade, and a held "
            "stock trade's stops may be priced on a penny. Check your broker.",
            "/automation",
        )  # fmt: skip


def start(services) -> None:
    """Load the stored market calendar and recover once, then start the clock,
    watch, monitor and market calendar threads."""
    deps = deps_for(services)
    economic_events.load_calendar(services.session_factory)
    load_learned(deps)  # bots slice 16: stocks' increments; never stops the start
    recover(deps)
    _loop("bot-clock", 10, lambda: clock_pass(deps))
    _loop("bot-watch", 2, lambda: _watch(deps))
    _loop("bot-monitor", 30, lambda: _monitor(deps))
    # The schedule check: alerts once a day when one of the loops above has stopped.
    _loop("bot-audit", bot_audit.AUDIT_EVERY, lambda: bot_audit.audit_pass(deps))
    # Fetches the calendars when due (never on the clock's thread): ForexFactory each
    # new week, the Fed, FRED and NYSE weekly, a failing source again after an hour.
    _loop(
        "market-calendar",
        CALENDAR_EVERY,
        lambda: economic_events.refresh_if_due(services.session_factory, deps.now()),
    )
    # Schwab's 7-day login: reminded 48 and 12 hours ahead and when it has run out.
    _loop("schwab-login", LOGIN_EVERY, lambda: login_pass(services, deps))
