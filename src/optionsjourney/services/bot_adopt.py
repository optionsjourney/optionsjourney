"""Handing a trade you opened to a bot (spec: bots slice 12; Tracker2's
services/bots/adopt.py, spec 2026-09-27-adopt-trade-into-bot-design.md).

The journal trade's legs are matched onto a live bot's sides (bots/adopt.py), so
the run recorded is a filled run of that bot whose "fill" is the journal's entry
prices, and its exits go on through the same bot_entry.hand_to_exits a bot's own
fill uses. check() reads only: the journal, the broker's positions and working
orders, and the quotes. adopt() checks everything again under one lock, records
the run in one commit and hands it to the exits; it never sends, cancels or
replaces an order of its own (Tracker2 cancels working orders; here they refuse).

Every refusal records and sends nothing. Each stop is checked against its
short's fresh ask before anything is recorded (slice 4's live-ask check,
management._group_stop_live_check): a stop at or below it would fire at once."""

import logging
import threading
from dataclasses import dataclass, field, replace
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from optionsjourney.bots import adopt as rules
from optionsjourney.bots import futures as futures_rules
from optionsjourney.bots import underlyings
from optionsjourney.bots.definition import BotDef
from optionsjourney.core.chain import chain_root
from optionsjourney.core.market import MARKET_TZ
from optionsjourney.core.positions import Position, Status
from optionsjourney.core.sessions import session_now
from optionsjourney.core.symbols import normalize_underlying
from optionsjourney.db.models import Bot, BotExit, BotRun
from optionsjourney.services import bot_entry, bot_exits, bot_futures, bot_store, journal, orders
from optionsjourney.trading.orders import Side, plain

logger = logging.getLogger(__name__)
KEY_PREFIX = "adopt-"  # an adopted run's key: adopt-<bot>-<the trade's opening order id>
HANDED_OVER = "a trade you opened, handed to this bot"  # the run log's first event
COULDNT = "Couldn't check this trade; nothing was done."
CHANGED = "Something changed since the check (a price, the bot or the trade). Check again."
BROKERS = ("tastytrade", "schwab")
# A run whose rules were removed from its trade (manual bots, #158): ended, never managed
HANDED_BACK = journal.HANDED_BACK
# One hand-over at a time, across every bot (adopt._adopt_lock): two confirms of
# the same trade into two bots can't both pass the check.
_LOCK = threading.Lock()


def adopt_key(bot_id: int, order_id: str) -> str:
    return f"{KEY_PREFIX}{bot_id}-{order_id}"


def is_adopted(run) -> bool:
    return (run.key or "").startswith(KEY_PREFIX)


@dataclass(frozen=True)
class Trade:
    """A journal trade as the hand-over sees it: the whole condor, never a split half."""

    key: str
    name: str
    broker: str
    account: str
    order_id: str | None
    opened_at: datetime
    underlying: str
    legs: list[rules.HeldLeg]
    problem: str | None  # why no bot can take it, or None
    futures: object = None  # bots slice 17: a futures trade's series (futures.Resolved)


@dataclass
class Check:
    """What the check found: a refusal, or the exits the bot would place."""

    ok: bool
    message: str = ""
    trade: Trade | None = None
    bot: BotDef | None = None
    matched: rules.Matched | None = None
    sides: list[rules.SidePlan] = field(default_factory=list)
    asks: dict[str, Decimal] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    fingerprint: str | None = None


@dataclass(frozen=True)
class Outcome:
    ok: bool
    message: str
    run_id: int | None = None
    warning: bool = False  # handed over, but a side has no stop or the exits weren't placed


def _today(deps):
    return deps.now().astimezone(MARKET_TZ).date()


def whole_key(key: str) -> str:
    """A split condor's half (…#put, …#call) stands for the condor it was split from."""
    return key.split("#", 1)[0]


def _fill_price(position: Position, symbol: str) -> Decimal | None:
    """The leg's average fill in the opening order (Tracker2 reads leg.entry_price)."""
    fills = [
        f for event in position.events[:1] for order in event.orders for f in order.fills
        if f.symbol == symbol
    ]  # fmt: skip
    if not fills or any(f.price is None for f in fills):
        return None
    total = sum((f.quantity for f in fills), Decimal(0))
    return sum((f.price * f.quantity for f in fills), Decimal(0)) / total if total else None


def _held_legs(position: Position) -> tuple[list[rules.HeldLeg], str | None]:
    legs, problem = [], None
    for leg in position.live_legs:
        if leg.option is None:
            return [], f"It holds more than options. {rules.WHICH_TRADES}"
        price = _fill_price(position, leg.symbol)
        if price is None or leg.opened != int(leg.opened):
            problem = "Its opening fills can't be read."
            continue
        legs.append(
            rules.HeldLeg(
                leg.symbol,
                leg.option.put_call.lower(),
                leg.option.strike,
                leg.option.expiration,
                Side.SELL if leg.sign < 0 else Side.BUY,
                int(leg.opened),
                price,
            )  # fmt: skip
        )
    return legs, problem


def _owner(session_factory, order_id: str | None) -> str | None:
    """The bot whose active run carries this order id (adopt.check_position's C1). A run
    whose rules were removed (manual bots, #158: HANDED_BACK) no longer owns the trade,
    so it can take rules again. Called under _LOCK: the one guard against two owners."""
    if order_id is None:
        return None
    with session_factory() as session:
        found = session.execute(
            select(BotRun, Bot).join(Bot, Bot.id == BotRun.bot_id)
            .where(BotRun.broker_order_id == order_id, BotRun.status != HANDED_BACK)
            .limit(1)
        ).first()  # fmt: skip
        return None if found is None else found[1].name


def find(deps, key: str) -> Trade | None:
    """The journal trade at `key` (a split half: its whole condor), or None. Built
    without any split, from the stored transactions, as the journal builds it."""
    _, book = journal.build_unsplit(deps.session_factory)
    position = next((p for p in book.positions if p.key == whole_key(key)), None)
    if position is None:
        return None
    opening = position.events[0].orders[0] if position.events else None
    order_id = opening.order_id if opening is not None else None
    legs, problem = _held_legs(position)
    if position.status is not Status.OPEN:
        problem = "It isn't open with nothing closed."
    elif position.roll_count:
        problem = "It was rolled."
    elif position.broker not in BROKERS:
        problem = "Only a trade at TastyTrade or Schwab can be handed to a bot."
    elif order_id is None:
        problem = "It has no broker order id to link a bot to."
    elif (owner := _owner(deps.session_factory, order_id)) is not None:
        problem = f"It's already {owner}'s trade."
    problem = problem or rules.trade_problem(
        legs, position.opened_at.astimezone(MARKET_TZ).date(), _today(deps)
    )
    underlying, resolved = normalize_underlying(position.underlying), None
    if _is_futures(underlying, legs):  # bots slice 17: its series, its root
        if problem is None:
            legs, resolved, problem = _futures_trade(deps, underlying, legs, position.broker)
        underlying = chain_root(underlying.removeprefix("."))
    return Trade(
        position.key, position.name, position.broker, position.account_number, order_id,
        position.opened_at, underlying, legs, problem, resolved,
    )  # fmt: skip


def _is_futures(underlying: str, legs) -> bool:
    return underlying.lstrip(".").startswith("/") or any(
        futures_rules.is_futures_symbol(leg.symbol) for leg in legs
    )


def _futures_trade(deps, underlying: str, legs, broker: str):
    """(legs, resolved, problem) for a futures trade (adopt.preflight's futures branch):
    each leg looked up in TastyTrade's listed series by the journal's own symbol
    (futures_legs.resolve_held), on the journal's contract, and carried from here on by
    TastyTrade's symbol."""
    if broker != "tastytrade":
        return legs, None, "Futures options trade through TastyTrade only."
    if not all(futures_rules.is_futures_symbol(leg.symbol) for leg in legs):
        return legs, None, "It mixes futures options with other options."
    journal = "/" + underlying.lstrip("./")
    root = chain_root(journal)
    try:
        resolved = bot_futures.resolve_held(
            deps.chain, deps.clients(), root, [leg.symbol for leg in legs], deps.now()
        )
    except futures_rules.Unresolvable as e:
        return legs, None, str(e)
    if resolved.contract != journal:
        return (
            legs,
            None,
            (
                f"TastyTrade lists these options on {resolved.contract}, not the journal's "
                f"{journal}."
            ),
        )
    legs = [
        replace(leg, symbol=listed.symbol) for leg, listed in zip(legs, resolved.legs, strict=True)
    ]
    return legs, resolved, None


def _hidden(session_factory, bot_id: int) -> bool:
    with session_factory() as session:
        row = session.get(Bot, bot_id)
        return row is None or row.hidden


def candidates(deps, trade: Trade) -> list[tuple[BotDef, str | None]]:
    """The bots on the trade's account, each with why it can't take the trade (None:
    it can). Tracker2 lists only the account's bots (adopt.candidates)."""
    return [
        (
            bot,
            rules.fits(bot, trade.legs, trade.underlying, today=_today(deps)),
        )
        for bot in bot_store.listing(deps.session_factory)
        if (bot.broker, bot.account_number) == (trade.broker, trade.account)
    ]


def _refused(message: str, **found) -> Check:
    return Check(False, message, **found)


def check(deps, key: str, bot_id: int) -> Check:
    """Tracker2's preview: everything the hand-over refuses on, and the exits it would
    place. Reads only. Never raises."""
    try:
        return _check(deps, key, bot_id)
    except Exception:  # noqa: BLE001 -- the page always answers; nothing was done
        logger.exception("couldn't check handing %s to bot %s", key, bot_id)
        return _refused(COULDNT)


def _check(deps, key: str, bot_id: int) -> Check:
    trade = find(deps, key)
    if trade is None:
        return _refused("There's no such trade in the journal.")
    if trade.problem:
        return _refused(trade.problem, trade=trade)
    bot = bot_store.load(deps.session_factory, bot_id)
    if bot is None or _hidden(deps.session_factory, bot_id):
        return _refused("There's no such bot.", trade=trade)
    if (bot.broker, bot.account_number) != (trade.broker, trade.account):
        return _refused(f"{bot.name} trades in another account.", trade=trade, bot=bot)
    problem = rules.fits(bot, trade.legs, trade.underlying, today=_today(deps))
    if problem:
        return _refused(f"{bot.name} can't take it: {problem}", trade=trade, bot=bot)
    found = {"trade": trade, "bot": bot}
    problem = (
        _at_cap(deps, bot)
        or _switches_and_session(deps, futures=trade.futures is not None)
        or bot_entry.tick_problem(deps, bot)  # slice 16, question 9
        or _futures_ticks(deps, trade)  # slice 17: ensure_ticks
        or _unsettled(deps, trade)
    )
    if problem:
        return _refused(problem, **found)
    try:
        matched = rules.match(bot, trade.legs, today=_today(deps))
    except rules.Refused as e:  # priced on the wrong side, or a side inverted
        return _refused(f"{bot.name} can't take it: {e}", **found)
    sides = rules.plan_exits(bot, matched)
    found |= {"matched": matched, "sides": sides}
    for side in sides:
        if side.stop is not None and side.stop.problem is not None:
            return _refused(
                f"{side.name}: {side.stop.problem}. A bot never takes a side it can't protect.",
                **found,
            )
    problem = _broker_problem(deps, trade)
    if problem:
        return _refused(problem, **found)
    asks, problem = _live_check(deps, sides)
    if problem:
        return _refused(problem, **found, asks=asks)
    return Check(
        True, "", trade=trade, bot=bot, matched=matched, sides=sides, asks=asks,
        warnings=_warnings(deps, bot, sides),
        fingerprint=rules.fingerprint(bot.id, matched, sides),
    )  # fmt: skip


def _at_cap(deps, bot: BotDef) -> str | None:
    """The bot's Max open trades (guards.under_position_cap): a trade handed over is one
    more open trade, so a bot already at its cap can't take it. Can't count: refused."""
    if bot.max_open_trades is None:
        return None
    try:
        held = bot_entry._open_trades(deps, bot)
    except Exception:  # noqa: BLE001 -- can't count, so don't take it
        logger.exception("couldn't count %s's open trades", bot.name)
        return f"Couldn't count {bot.name}'s open trades. Try again."
    if held >= bot.max_open_trades:
        return (
            f"{bot.name} already holds {held} open trade(s), its Max open trades. Raise the "
            "limit or wait for one to close."
        )
    return None


def _futures_ticks(deps, trade: Trade) -> str | None:
    """A futures trade's series increments (futures_legs.ensure_ticks): its target can't
    be priced on its tick without them."""
    if trade.futures is None:
        return None
    try:
        bot_futures.ensure_ticks(deps.session_factory, deps.clients(), trade.futures, deps.now())
    except futures_rules.Unresolvable as e:
        return str(e)
    return None


def _switches_and_session(deps, futures: bool = False) -> str | None:
    """Both switches (adopt.preflight's kill switch is our No new trades; Freeze would
    leave the stops unsent) and the regular session (fresh quotes for the stop check).
    A futures trade has no stop to check and is handed over at any hour (preflight has
    no session check; bots slice 17)."""
    current = orders.switches(deps.session_factory)
    if current.freeze:
        return "Orders are frozen, so no stop could be placed. Turn Freeze off first."
    if current.no_new_trades:
        return "No new trades is on: bots take on nothing new while it's on."
    if futures:
        return None
    if session_now("SPY", deps.now().astimezone(MARKET_TZ)) != "regular":
        return "A trade can be handed to a bot only while the market is open."
    return None


def _unsettled(deps, trade: Trade) -> str | None:
    """An earlier order on these options with an unclear outcome, or a bot entry on
    this account still being settled: either may be this very trade."""
    symbols = {plain(leg.symbol) for leg in trade.legs}
    unclear = orders.unclear_symbols(deps.session_factory, trade.broker, trade.account)
    if symbols & unclear:
        return (
            "An earlier order on these options has an unclear outcome. Check your broker, "
            "then clear it on the order log."
        )
    with deps.session_factory() as session:
        settling = session.scalar(
            select(BotRun.id).where(
                # a resting entry (bots slice 17) may be filling as this very trade
                ((BotRun.status == "running") & (BotRun.trading_date == _today(deps)))
                | (BotRun.status == "working"),
                BotRun.broker == trade.broker, BotRun.account_number == trade.account,
            ).limit(1)
        )  # fmt: skip
    if settling is not None:
        return (
            "A bot's entry on this account is still being settled (or needs you: see the "
            "Automation page). Try again once it's settled."
        )
    return None


def _broker_problem(deps, trade: Trade) -> str | None:
    """The broker must show every leg held the right way round, at least the trade's
    contracts of it, and no working order on any of them (adopt.working_orders: not
    knowing what is working refuses)."""
    try:
        client = bot_exits._client(deps, trade.broker)
    except LookupError:
        return f"{orders.BROKER_NAMES.get(trade.broker, trade.broker)} isn't connected."
    try:
        held = {plain(s): q for s, q in client.option_positions(trade.account).items()}
    except Exception:  # noqa: BLE001 -- unreadable refuses
        logger.exception("couldn't read positions to hand a trade to a bot")
        return "Couldn't read your positions at the broker. Try again."
    for leg in trade.legs:
        have = held.get(plain(leg.symbol), 0)
        want = leg.quantity  # units x the leg's ratio
        right_way = have <= -want if leg.side is Side.SELL else have >= want
        if not right_way:
            return f"The broker doesn't show {leg.symbol.strip()} held as this trade holds it."
    try:
        working = list(client.working_orders(trade.account))
    except Exception:  # noqa: BLE001 -- not knowing what is working refuses
        logger.exception("couldn't read working orders to hand a trade to a bot")
        return "Couldn't read your working orders at the broker. Try again."
    symbols = {plain(leg.symbol) for leg in trade.legs}
    touching = [
        o.broker_order_id for o in working if any(plain(leg.symbol) in symbols for leg in o.legs)
    ]
    if touching:
        return (
            f"Working orders on this trade: {', '.join(touching)}. Cancel them at your broker "
            "first; the bot places its own exits."
        )
    return None


def _live_check(deps, sides) -> tuple[dict[int, Decimal], str | None]:
    """Each stop must not fire at once (management._group_stop_live_check): a resting
    stop above its short's fresh ask, a watched level below the group's live P&L
    (bot_exits._live_pnl, the watched stop's own reading). No fresh price refuses: no
    telling. Returns what was read per group (the ask, or the P&L) and the refusal."""
    read = {}
    for side in sides:
        if side.stop is None:
            continue
        if side.watched:
            pnl = bot_exits._live_pnl(deps, _group(side))
            if pnl is not None:
                read[side.position] = pnl
            problem = bot_exits.fires_at_once(side.stop.trigger, pnl)
            if problem is not None:
                return read, f"{side.name}: {problem}. Nothing was done."
            continue
        ask = bot_exits._fresh_ask(deps, side.short)
        if ask is None:
            return read, (
                f"{side.name}: no fresh quote for {side.short.strip()}, so there's no telling "
                f"a stop at {side.stop.trigger} wouldn't fire at once. Try again."
            )
        read[side.position] = ask
        if side.stop.trigger <= ask:
            return read, (
                f"{side.name}: a stop at {side.stop.trigger} is at or below the short's ask "
                f"{ask}, so it would fire at once. Nothing was done."
            )
    return read, None


def _group(side) -> bot_exits._Group:
    """The group as bot_exits' live reading takes it (no fill time: any fresh quote)."""
    return bot_exits._Group(side.legs, None, side.contracts, {}, side.entry, None)


def _warnings(deps, bot: BotDef, sides) -> list[str]:
    """What will happen at once, or won't run (adopt._warnings)."""
    out = []
    hhmm = deps.now().astimezone(MARKET_TZ).strftime("%H:%M")
    if underlyings.futures(bot.underlying):  # adopt._warnings' FUTURES_NOTE
        out.append(f"{bot_exits.FUTURES_NOTE}: the bot rests its profit target at TastyTrade "
                   "and places no stop.")  # fmt: skip
    groups = {g.position: g for g in bot.groups}
    for side in sides:
        if side.stop is None and not underlyings.futures(bot.underlying):
            group = groups.get(side.position)
            if group is not None and group.stop_value is not None and side.notes:
                # final review I2: this fill drops the bot's stop; its note says why
                out.append(f"{side.name} gets no stop: {' '.join(side.notes)}")
            else:
                out.append(f"{side.name} gets no stop: the bot sets none.")
        if side.target_problem:
            out.append(f"{side.name} gets no profit target: {side.target_problem}.")
        elif side.target is not None and side.entry is not None:
            # The group's value at its mids (bot_exits._live_pnl): at or below the value
            # the target closes at, it's reached.
            pnl = bot_exits._live_pnl(deps, _group(side))
            value = None if pnl is None else side.entry - pnl
            if value is not None and value <= side.target:
                out.append(
                    f"{side.name}'s profit target ({side.target}) is already reached "
                    f"(≈ {value.quantize(Decimal('0.01'))} to close): it closes that "
                    "side within the next checks."
                )
    if bot.use_underlying_exit and (
        bot.underlying_up_pct is not None or bot.underlying_down_pct is not None
    ):
        out.append(
            f"The bot's {bot.underlying} move watch rule can't run on this trade: no "
            f"{bot.underlying} price was recorded when it was opened."
        )
    if bot.close_time and bot.close_time <= hhmm:
        out.append(f"The bot's timed close ({bot.close_time}) has passed: it starts at once.")
    if bot.use_time_actions and any(a.days == 0 and a.at <= hhmm for a in bot.time_actions):
        out.append("The bot's time actions due earlier today act at once.")
    return out


def adopt(deps, key: str, bot_id: int, fingerprint: str) -> Outcome:
    """Tracker2's adopt: check everything again under the lock, record the run in one
    commit, then place its exits through the bot's own machinery. Never raises."""
    with _LOCK:
        found = check(deps, key, bot_id)
        if not found.ok:
            return Outcome(False, found.message)
        if found.fingerprint != fingerprint:
            return Outcome(False, CHANGED)
        try:
            run_id = _record(deps, found)
        except Exception:  # noqa: BLE001 -- nothing was recorded or sent
            logger.exception("couldn't record handing %s to bot %s", key, bot_id)
            return Outcome(False, COULDNT)
        if run_id is None:
            return Outcome(False, "This trade was already handed to a bot.")
        try:
            # Alerts any failure itself; each stop's ask is checked again just before it's sent
            bot_entry.hand_to_exits(deps, found.bot, run_id, live_check=True)
        finally:
            with bot_entry._active_lock:
                bot_entry.ACTIVE.discard(run_id)
    message, warning = _placed_words(deps, found.bot, run_id)
    return Outcome(True, message, run_id, warning)


def record_run(deps, trade: Trade, bot: BotDef, matched: rules.Matched) -> int | None:
    """The run and nothing else, in one commit (adopt._record): a filled, live run of
    the bot whose fills are the journal's. In ACTIVE before it's committed, so the
    startup and monitor checks never see it without its exits. None: already taken.
    Also a manual bot's per-trade bot's run (services/manual_attach.py, #158)."""
    now = deps.now()
    with deps.session_factory() as session:
        run = BotRun(
            bot_id=bot.id,
            key=adopt_key(bot.id, trade.order_id),
            trading_date=_today(deps),
            entry_time=trade.opened_at.astimezone(MARKET_TZ).strftime("%H:%M"),
            status="filled",
            live=True,
            account_number=trade.account,
            broker=trade.broker,
            legs=matched.legs,
            filled_legs=matched.filled_legs,
            target_credit=matched.credit,
            fill_credit=matched.credit,
            contracts=matched.contracts,
            broker_order_id=trade.order_id,
            chase_log=[{"at": now.isoformat(), "event": HANDED_OVER, "trade": trade.key}],
            filled_at=trade.opened_at,
            created_at=now,
            updated_at=now,
        )
        if trade.futures is not None:  # adopt._record: PT only, the contract, x multiplier
            run.multiplier, run.contract = trade.futures.multiplier, trade.futures.contract
            run.pt_only = True
        session.add(run)
        try:
            session.flush()
        except IntegrityError:
            session.rollback()
            return None
        with bot_entry._active_lock:
            bot_entry.ACTIVE.add(run.id)
        try:
            session.commit()
        except Exception:
            with bot_entry._active_lock:
                bot_entry.ACTIVE.discard(run.id)
            raise
        return run.id


def _record(deps, found: Check) -> int | None:
    """record_run for a check's trade, bot and match (the hand-over's own call)."""
    return record_run(deps, found.trade, found.bot, found.matched)


def _placed_words(deps, bot: BotDef, run_id: int) -> tuple[str, bool]:
    """What the exits did, for the page, and whether it needs a warning."""
    names = {g.position: g.name for g in bot.groups}
    with deps.session_factory() as session:
        rows = list(session.scalars(select(BotExit).where(BotExit.run_id == run_id)))
        reason = session.get(BotRun, run_id).reason or ""
    needs_you = reason.startswith(bot_entry.NEEDS_YOU)
    sets_exits = any(g.stop_value is not None or g.target_value is not None for g in bot.groups)
    if not rows and (sets_exits or needs_you):
        return f"Handed to {bot.name}, but NO EXITS PLACED — see the alert and your broker.", True
    parts = []
    for row in sorted(rows, key=lambda r: (r.group_position, r.kind)):
        name = names.get(row.group_position, f"Group {row.group_position + 1}")
        if row.kind == "stop":
            if row.state in ("working", bot_exits.WATCHING):
                parts.append(f"{name}: stop at {row.trigger}")
            else:
                parts.append(f"{name}: NO STOP ({row.state}; see the alert)")
        elif row.kind == "target" and row.state == "armed":
            parts.append(f"{name}: target at {row.limit}")
        elif row.kind == "target" and row.state in ("working", "pending"):  # slice 17
            parts.append(f"{name}: target rests at {row.limit}")
        elif row.kind == "target":
            parts.append(f"{name}: NO PROFIT TARGET ({row.state}; see the alert)")
    done = "; ".join(parts) if parts else "no exits (the bot sets none)"
    warning = needs_you or any("NO STOP" in part or "NO PROFIT" in part for part in parts)
    return f"Handed to {bot.name}. {done}.", warning
