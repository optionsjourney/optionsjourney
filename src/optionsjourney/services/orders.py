"""The one path an order takes to a broker (spec: the order path; decision 0028).

submit(), replace() and cancel() are the only code that sends, replaces or
cancels an order, for bots, managed trades and buttons alike. In order:

0. Refuses at once if Freeze all orders is on: nothing is read from the broker.
1. Clears up earlier unclear outcomes on the account, if there are any.
2. Gathers the facts: the switches, ticks, the bot's limits, and (for closing
   orders) what's open and what working orders already cover.
3. Runs trading/check.py. A refusal stops here.
4. Asks the broker's preview (TastyTrade dry run, Schwab previewOrder). A
   preview ends there; a live order whose preview fails isn't sent.
5. Sends once. A failed send is never retried. An unclear one is logged as
   "Unclear: …", blocks its legs until it's cleared up, and raises an alert.

submit(), replace() and cancel() never raise: every outcome, even a fact the
app couldn't gather, is a Result and a row in order_log. A fact that can't be
gathered refuses the order (for a closing order, with an alert).

One lock per broker account is held from step 1 until the log row is written,
so two orders on one account (a double click, a stop and a Close button) can't
both pass the check on the same facts. And because the broker's working list
can lag right after a send, this app's own closing sends from the last few
minutes count as covering their contracts until the list shows them.

In gateway mode the trading guard refuses place, replace and cancel underneath
all of this."""

import logging
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import sessionmaker

from optionsjourney.bots import schedule
from optionsjourney.bots.definition import limits
from optionsjourney.brokers.credentials import CredentialsUnavailable
from optionsjourney.brokers.http import BrokerError, RequestUnclear
from optionsjourney.core.accounts import mask_number
from optionsjourney.core.market import MARKET_TZ
from optionsjourney.db.models import Bot, BotRun, OrderLog, Setting, TickSize
from optionsjourney.services import bot_store
from optionsjourney.services import settings as settings_service
from optionsjourney.services.user_alerts import Fired
from optionsjourney.trading import schwab as schwab_format
from optionsjourney.trading import tastytrade as tastytrade_format
from optionsjourney.trading import ticks
from optionsjourney.trading.check import (
    COULDNT_CHECK,
    BotFacts,
    Decision,
    Facts,
    Switches,
    check,
    check_cancel,
)
from optionsjourney.trading.orders import Order, OrderError, plain, same_legs

logger = logging.getLogger(__name__)

SWITCH_KEYS = {"no_new_trades": "_no_new_trades", "freeze": "_freeze"}
TICKS_STALE_AFTER = timedelta(days=7)
TICKS_RETRY_AFTER = timedelta(minutes=5)

# When each key's fetch last failed, so a TastyTrade that's down isn't asked again
# by every entry and chase step. Guarded by its lock; never held across a broker call.
_tick_failed: dict[str, datetime] = {}
_tick_failed_lock = threading.Lock()


def forget_tick_failures() -> None:
    with _tick_failed_lock:
        _tick_failed.clear()


BROKER_NAMES = {"tastytrade": "TastyTrade", "schwab": "Schwab"}
OWN_SENDS_COUNT_FOR = timedelta(minutes=5)  # while the broker's working list may lag

_account_locks: dict[tuple[str, str], threading.Lock] = {}
_account_locks_guard = threading.Lock()


def _account_lock(broker: str, account_number: str) -> threading.Lock:
    """The one lock for this broker account; the app serves requests on threads."""
    with _account_locks_guard:
        return _account_locks.setdefault((broker, account_number), threading.Lock())


def switches(session_factory: sessionmaker) -> Switches:
    """The two switches. Only "on" is on; if they can't be read, Freeze is on."""
    try:
        with session_factory() as session:
            rows = {
                row.key: row.value
                for row in session.scalars(
                    select(Setting).where(Setting.key.in_(SWITCH_KEYS.values()))
                )
            }
    except Exception:  # noqa: BLE001 -- fail closed
        logger.exception("couldn't read the trading switches; treating Freeze as on")
        return Switches(no_new_trades=True, freeze=True)
    return Switches(
        no_new_trades=rows.get(SWITCH_KEYS["no_new_trades"]) == "on",
        freeze=rows.get(SWITCH_KEYS["freeze"]) == "on",
    )


def set_switch(
    session_factory: sessionmaker, name: str, on: bool, now: datetime | None = None
) -> None:
    key = SWITCH_KEYS[name]  # KeyError for an unknown switch
    with session_factory() as session:
        row = session.get(Setting, key) or Setting(key=key)
        row.value = "on" if on else "off"
        row.updated_at = now or datetime.now(UTC)
        session.add(row)
        session.commit()


def _client(clients, broker: str):
    return next((c for c in clients if getattr(c, "broker", "") == broker), None)


def tick_tables(session_factory: sessionmaker, clients, order: Order, now: datetime) -> dict:
    """The tick tables this order needs, from tick_sizes, fetched from
    TastyTrade when missing or a week old. A failed fetch is logged and the
    stored table (even an old one) or the default tick is used; that key isn't
    asked for again for TICKS_RETRY_AFTER."""
    keys = ticks.keys_for(order)
    if not keys:
        return {}
    with session_factory() as session:
        rows = {r.key: r for r in session.scalars(select(TickSize).where(TickSize.key.in_(keys)))}
    tables = {key: ticks.table_from_rows(row.sizes) for key, row in rows.items()}
    stale = {k for k in keys if k not in rows or now - rows[k].fetched_at > TICKS_STALE_AFTER}
    tasty = _client(clients, "tastytrade")
    if not stale or tasty is None:
        return _learned(tables)
    with _tick_failed_lock:
        stale = {k for k in stale if not _failed_recently(k, now)}
    if not stale:
        return _learned(tables)
    fetched, failed = _fetch_ticks(tasty, stale)
    with _tick_failed_lock:
        for key in stale:
            if key in fetched:
                _tick_failed.pop(key, None)
            elif failed:
                _tick_failed[key] = now
    if fetched:
        with session_factory() as session:
            for key, sizes in fetched.items():
                row = session.get(TickSize, key) or TickSize(key=key)
                row.sizes, row.fetched_at = sizes, now
                session.add(row)
            session.commit()
        tables.update({k: ticks.table_from_rows(v) for k, v in fetched.items() if k in keys})
    return _learned(tables)


def _failed_recently(key: str, now: datetime) -> bool:
    """Call with _tick_failed_lock held."""
    failed_at = _tick_failed.get(key)
    return failed_at is not None and now - failed_at < TICKS_RETRY_AFTER


def _learned(tables: dict) -> dict:
    """Keep a stock's single-leg table for the bots' exit math (ticks.learn; Tracker2's
    option_ticks.ensure sets pricing._OPTION_TICKS the same way)."""
    for key, table in tables.items():
        if not key.startswith(("FOP:", "FUT:")):
            ticks.learn(key, table)
    return tables


def load_learned(session_factory: sessionmaker) -> int:
    """At startup, every stored stock table (option_ticks.load_all): an open trade's exits
    round on its stock's own increments after a restart. Returns how many were learned;
    a row that can't be read is skipped and logged (that stock's next entry fetches it
    again, or refuses)."""
    with session_factory() as session:
        found = [(r.key, r.sizes) for r in session.scalars(select(TickSize))]
    learned = 0
    for key, sizes in found:
        if key.startswith(("FOP:", "FUT:")):
            continue
        try:
            table = ticks.table_from_rows(sizes) if isinstance(sizes, list) else ()
        except AttributeError, TypeError, ValueError:
            table = ()
        if table:
            ticks.learn(key, table)
            learned += 1
        else:
            logger.warning("the stored option increments for %s can't be read", key)
    return learned


def _fetch_ticks(tasty, keys: set[str]) -> tuple[dict[str, list[dict]], bool]:
    """The tables found, and whether TastyTrade failed (BrokerError or
    CredentialsUnavailable). Any other exception raises, so the order is refused."""
    failed = False
    found: dict[str, list[dict]] = {}
    products = {key.split(":")[1] for key in keys if key.startswith("FOP:")}
    try:
        for product in products:
            for series, sizes in tasty.futures_option_tick_sizes(product).items():
                if sizes:
                    found[ticks.series_key(product, series)] = sizes
        for key in keys:
            if key.startswith("FUT:"):
                sizes = tasty.future_tick_sizes(key[4:])
            elif key.startswith("FOP:"):
                continue
            else:
                sizes = tasty.equity_tick_sizes(key)
            if sizes:
                found[key] = sizes
    except (BrokerError, CredentialsUnavailable) as e:
        failed = True
        logger.warning("couldn't fetch tick sizes for %s: %s", sorted(keys), e)
    return found, failed


UNCLEAR = "Unclear:"


@dataclass(frozen=True)
class Request:
    broker: str  # "tastytrade" or "schwab"
    account_number: str
    origin: str  # "bot", "managed" or "you"
    order: Order
    bot_id: int | None = None
    managed_live: bool = False  # a managed trade's own preview setting (piece 3)
    bot_run_id: int | None = None  # the bot run this order belongs to
    preview_only: bool = False  # check and preview it, even for a live bot (Preview now)
    # A bot's exit orders follow their run's live/preview, not the bot's current
    # setting (Tracker2's BotRun.mode). None: use the bot's setting.
    run_live: bool | None = None


@dataclass(frozen=True)
class Result:
    action: str  # "preview", "send", "replace", "cancel" or "refuse"
    ok: bool
    message: str
    broker_order_id: str | None = None
    costs: dict | None = None  # a preview's costs and buying power (preview_costs), refused or not


def used_today(
    session_factory: sessionmaker, bot_id: int, now: datetime, own_only: bool = False
) -> int:
    """Opening contracts in the largest leg the bot sent today (New York day), unclear
    sends included: each send counts its quantity times its biggest ratio. Its own
    stops, re-arms and closes don't count, and neither does a tranche's fresh order
    for the rest of an entry: the entry's first send counted it. `own_only`: only the
    entries the bot made by itself, for a worked-out daily limit (decision 0058)."""
    start = datetime.combine(now.astimezone(MARKET_TZ).date(), time(), MARKET_TZ)
    query = (
        select(OrderLog.quantity, OrderLog.order)
        .select_from(OrderLog)
        .outerjoin(BotRun, BotRun.id == OrderLog.bot_run_id)
        .where(
            BotRun.parent_run_id.is_(None),
            OrderLog.bot_id == bot_id,
            OrderLog.action == "send",
            OrderLog.opening.is_(True),
            OrderLog.created_at >= start.astimezone(UTC),
            (OrderLog.ok.is_(True)) | (OrderLog.result.like(f"{UNCLEAR}%")),
        )
    )
    if own_only:
        query = query.where(
            or_(
                BotRun.key.is_(None),
                and_(
                    *(~BotRun.key.startswith(prefix) for prefix in schedule.BY_HAND_PREFIXES),
                    ~BotRun.key.contains(schedule.TRADE_NOW),
                ),
            )
        )
    with session_factory() as session:
        rows = session.execute(query).all()
        bot = session.get(Bot, bot_id)
        paper = bot is not None and not bot.live
    used = sum(quantity * _largest_ratio(logged) for quantity, logged in rows)
    if paper:  # a Paper bot's own caps also count its pretend entries (decision 0061)
        from optionsjourney.services import bot_paper

        used += bot_paper.used_today(session_factory, bot_id, now)
    return used


def _largest_ratio(logged: dict | None) -> int:
    """The biggest leg ratio in a logged order; 1 for a row with no legs."""
    try:
        return max(int(leg.get("ratio", 1)) for leg in logged["order"]["legs"])
    except KeyError, TypeError, ValueError, AttributeError:
        return 1


def _alert(session_factory, notify, title: str, detail: str) -> None:
    event = Fired(0, title, detail, "/orders")
    try:
        if notify is not None:
            notify(event)
        else:
            from optionsjourney.services import notify as notify_service

            notify_service.send(session_factory, event)
    except Exception:  # noqa: BLE001 -- an alert must never stop the order path
        logger.exception("couldn't send the order alert %r", title)


def _record(
    session_factory,
    now,
    *,
    broker,
    account,
    origin,
    bot_id,
    action,
    ok,
    result,
    order=None,
    body=None,
    broker_order_id=None,
    replaces_id=None,
    tag=None,
    bot_run_id=None,
) -> None:
    with session_factory() as session:
        session.add(
            OrderLog(
                created_at=now,
                broker=broker,
                account_number=account,
                origin=origin,
                bot_id=bot_id,
                action=action,
                opening=order.opening if order else False,
                quantity=order.quantity if order else 0,
                price=order.price if order else None,
                order_type=str(order.order_type) if order else "",
                order={"order": order.to_dict(), "body": body} if order else {},
                result=result[:2000],
                ok=ok,
                broker_order_id=broker_order_id,
                replaces_id=replaces_id,
                tag=tag,
                bot_run_id=bot_run_id,
            )
        )
        session.commit()


def _body(broker: str, order: Order, tag: str | None) -> dict:
    if broker == "tastytrade":
        return tastytrade_format.order_body(order, tag)
    return schwab_format.order_body(order)


def preview_reply(clients, broker: str, account_number: str, order: Order) -> dict:
    """The broker's own check of an order, never placed (a dry run; the trading guard
    allows it): for sizing by buying power (decision 0050). Nothing is logged, as nothing
    is sent. Raises BrokerError when the broker refuses it or isn't connected."""
    client = _client(clients, broker)
    if client is None:
        raise BrokerError(f"{BROKER_NAMES.get(broker, broker)} isn't connected.")
    return client.preview_order(account_number, _body(broker, order, None))


class _NoSuchBot(LookupError):
    """The order names a bot that isn't there (deleted, or a wrong id)."""


def _bot_facts(
    session_factory, request: Request, now, replaces=None
) -> tuple[BotFacts | None, str | None]:
    with session_factory() as session:
        bot = session.get(Bot, request.bot_id)
        if bot is None:
            raise _NoSuchBot(f"There's no bot {request.bot_id}.")
        # A run trades in the account it started in, even if the bot is edited since.
        run = session.get(BotRun, request.bot_run_id) if request.bot_run_id else None
        account, broker = bot.account_number, bot.broker
        if run is not None and run.bot_id == bot.id and run.account_number:
            account = run.account_number
        if run is not None and run.bot_id == bot.id and run.broker:
            broker = run.broker  # and at the broker it fired at (bots slice 11)
        if (broker, account) != (request.broker, request.account_number):
            return None, f"{bot.name} trades in another account."
        # A run's exits follow how the run was opened (Tracker2's BotRun.mode), read
        # from the run itself: only for closing orders of one of this bot's runs.
        own_run = run is not None and run.bot_id == bot.id
        tranche = own_run and run.parent_run_id is not None
        follows_run = request.run_live is not None and own_run and not request.order.opening
        live = run.live if follows_run else bot.live
        # Decision 0058: a blank limit is worked out, and an entry you start yourself
        # isn't held to the bot's own size or day (tranches follow their first run)
        definition = bot_store.as_def(session, bot)
        per_trade, per_day = limits(definition, by_hand=own_run and schedule.by_hand(run.key))
        own_only = definition.max_per_day is None
        facts = BotFacts(bot.name, live, per_trade, per_day, 0)
    used = used_today(session_factory, request.bot_id, now, own_only=own_only)
    if request.order.opening and (replaces or tranche):
        # It swaps an order already counted, or it's a tranche's rest of an entry
        # whose first send counted it: either way it doesn't count again.
        used = max(0, used - request.order.largest_leg)
    return BotFacts(facts.name, facts.live, facts.max_per_trade, facts.max_per_day, used), None


def _positions_and_working(client, account: str, skip_id: str | None):
    """(open contracts, contracts already covered by working closing orders,
    the working orders' ids), by plain symbol; the first two are None if the
    broker couldn't say. Other errors are raised and refuse the order."""
    ids: set[str] = set()
    try:
        held = {plain(s): q for s, q in client.option_positions(account).items()}
    except (BrokerError, CredentialsUnavailable) as e:
        logger.warning("couldn't read positions for %s: %s", account[-4:], e)
        held = None
    try:
        covered: dict[str, int] = {}
        for found in client.working_orders(account):
            ids.add(found.broker_order_id)
            if found.broker_order_id == skip_id:
                continue
            for leg in found.legs:
                if not leg.opening:
                    key = plain(leg.symbol)
                    covered[key] = covered.get(key, 0) + leg.remaining
    except (BrokerError, CredentialsUnavailable) as e:
        logger.warning("couldn't read working orders for %s: %s", account[-4:], e)
        covered = None
    return held, covered, ids


def _own_recent_closes(session_factory, broker, account, now, listed: set[str],
                       skip_id: str | None) -> dict[str, int]:  # fmt: skip
    """Contracts, by plain symbol, that this app's own closing sends and
    replaces from the last few minutes cover, if the broker's working list
    doesn't show them yet. Orders that were later replaced, or that this
    replace is replacing, don't count; neither do orders the broker says have
    finished (in `listed`) or that this app cancelled."""
    with session_factory() as session:
        rows = list(
            session.scalars(
                select(OrderLog).where(
                    OrderLog.broker == broker,
                    OrderLog.account_number == account,
                    OrderLog.action.in_(("send", "replace", "cancel")),
                    OrderLog.ok.is_(True),
                    OrderLog.created_at >= (now - OWN_SENDS_COUNT_FOR).astimezone(UTC),
                )
            )
        )
    cancelled = {r.broker_order_id for r in rows if r.action == "cancel"}
    rows = [r for r in rows if r.action != "cancel" and not r.opening]
    gone = {r.replaces_id for r in rows if r.replaces_id} | cancelled | {skip_id}
    covered: dict[str, int] = {}
    for row in rows:
        if row.broker_order_id and (row.broker_order_id in listed or row.broker_order_id in gone):
            continue
        if not row.order.get("order"):
            continue
        for symbol, count in Order.from_dict(row.order["order"]).contracts.items():
            covered[plain(symbol)] = covered.get(plain(symbol), 0) + count
    return covered


def unclear_symbols(session_factory, broker: str, account: str) -> frozenset[str]:
    """Plain symbols that an unclear, uncleared attempt on this account may cover."""
    with session_factory() as session:
        rows = session.scalars(
            select(OrderLog).where(
                OrderLog.broker == broker,
                OrderLog.account_number == account,
                OrderLog.result.like(f"{UNCLEAR}%"),
                OrderLog.cleared_at.is_(None),
            )
        )
        symbols = set()
        for row in rows:
            if row.order.get("order"):
                symbols |= {plain(s) for s in Order.from_dict(row.order["order"]).contracts}
    return frozenset(symbols)


def _num(value) -> float | None:
    try:
        return None if value is None else round(float(value), 2)
    except TypeError, ValueError:
        return None


def _signed(value, effect) -> float | None:
    """TastyTrade's amount with its effect: a debit is negative."""
    n = _num(value)
    return None if n is None else -n if str(effect).lower() == "debit" else n


def preview_costs(broker: str, preview: dict | None, credit: bool | None = None) -> dict | None:
    """What a preview says the order costs, as the Trade page shows it: the trade's value
    (+ credit, - debit), commission and fees (each a cost), the change in buying power
    (- uses it) and buying power after. None for what the broker doesn't give. Schwab
    doesn't say which way the money goes, so `credit` (the order's) does."""
    if not preview:
        return None
    if broker == "tastytrade":
        bp = preview.get("buying-power-effect") or {}
        fees = preview.get("fee-calculation") or {}
        trade = preview.get("order-costs") or {}
        commission = _num(fees.get("commission"))
        total = _num(fees.get("total-fees"))
        return {
            "value": _signed(
                trade.get("estimated-trade-cost"), trade.get("estimated-trade-cost-effect")
            ),
            "commission": commission,
            "fees": None if total is None else round(total - (commission or 0), 2),
            "bp_change": _signed(
                bp.get("change-in-buying-power"), bp.get("change-in-buying-power-effect")
            ),
            "bp_after": _signed(bp.get("new-buying-power"), bp.get("new-buying-power-effect")),
        }
    if broker == "schwab":
        balance = (preview.get("orderStrategy") or {}).get("orderBalance") or {}
        charges = preview.get("commissionAndFee") or {}

        def total(part, legs, values):
            items = ((charges.get(part) or {}).get(legs)) or []
            found = [_num(v.get("value")) for leg in items for v in leg.get(values) or []]
            found = [n for n in found if n is not None]
            return round(sum(found), 2) if found else None

        value = _num(balance.get("orderValue"))
        if value is not None and credit is not None:
            value = abs(value) if credit else -abs(value)
        return {
            "value": value,
            "commission": total("commission", "commissionLegs", "commissionValues"),
            "fees": total("fee", "feeLegs", "feeValues"),
            "bp_change": None,
            "bp_after": _num(balance.get("projectedBuyingPower")),
        }
    return None


def _summary(broker: str, preview: dict) -> str:
    if broker == "tastytrade":
        change = (preview.get("buying-power-effect") or {}).get("change-in-buying-power")
        return f"Buying power change: ${change}." if change else ""
    return "Schwab's preview passed."


def submit(session_factory, clients, request: Request, now=None, notify=None) -> Result:
    return _attempt(session_factory, clients, request, now or datetime.now(UTC), notify, "send")


def replace(session_factory, clients, request: Request, broker_order_id: str, now=None,
            notify=None) -> Result:  # fmt: skip
    return _attempt(
        session_factory, clients, request, now or datetime.now(UTC), notify, "replace",
        broker_order_id,
    )  # fmt: skip


def _checked(session_factory, clients, client, request, now, current: Switches, replaces):
    """Steps 1-3: clear up unclear outcomes, gather the facts, run the check.
    Returns (the order rounded to the tick, the decision). May raise; _attempt
    turns that into a refusal."""
    order = request.order
    resolve_unclear(session_factory, clients, request.broker, request.account_number, now)
    tables = tick_tables(session_factory, clients, order, now)
    try:
        order = ticks.rounded(order, tables)
    except OrderError as e:
        return order, Decision("refuse", f"{e} (after rounding to the tick)")
    bot = None
    if request.origin == "bot":
        bot, problem = _bot_facts(session_factory, request, now, replaces)
        if problem:
            return order, Decision("refuse", problem)
    held = covered = None
    if not order.opening:
        # Recent orders first: a close that fills after this read still counts as
        # covering, so it can't fill between the reads and leave positions held
        # while no longer covering them.
        try:
            finished = {
                o.broker_order_id
                for o in client.recent_orders(request.account_number, now - OWN_SENDS_COUNT_FOR)
                if not o.may_be_working
            }
        except (BrokerError, CredentialsUnavailable) as e:
            logger.warning("couldn't read recent orders for %s: %s", request.account_number[-4:], e)
            finished = set()
        held, covered, listed = _positions_and_working(client, request.account_number, replaces)
        if covered is not None:
            own = _own_recent_closes(
                session_factory, request.broker, request.account_number, now,
                listed | finished, replaces,
            )  # fmt: skip
            for key, count in own.items():
                covered[key] = covered.get(key, 0) + count
    decision = check(
        Facts(
            order=order,
            broker=request.broker,
            origin=request.origin,
            switches=current,
            price_tick=None
            if order.price is None
            else ticks.order_tick(order, order.price, tables),
            trigger_tick=None
            if order.trigger is None
            else ticks.order_tick(order, order.trigger, tables),
            bot=bot,
            managed_live=request.managed_live,
            open_contracts=held,
            working_closing=covered,
            unclear_symbols=unclear_symbols(
                session_factory, request.broker, request.account_number
            ),
            max_your_contracts=_max_your_contracts(session_factory)
            if request.origin == "you"
            else 0,
        )
    )
    return order, decision


def _max_your_contracts(session_factory) -> int:
    """Settings → Your orders. Unreadable: 0, which refuses (fail closed)."""
    try:
        return int(settings_service.get(session_factory, "max_order_contracts"))
    except Exception:  # noqa: BLE001 -- a setting we can't read refuses, never allows
        logger.exception("couldn't read the order size setting")
        return 0


def _attempt(session_factory, clients, request, now, notify, action, replaces=None) -> Result:
    order, body, tag, bot_id = request.order, None, None, request.bot_id

    def done(logged_action, ok, message, order_id=None, costs=None):
        message = mask_number(message, request.account_number)  # broker text can quote it
        _record(
            session_factory, now, broker=request.broker, account=request.account_number,
            origin=request.origin, bot_id=bot_id, action=logged_action, ok=ok,
            result=message, order=order, body=body, broker_order_id=order_id,
            replaces_id=replaces, tag=tag, bot_run_id=request.bot_run_id,
        )  # fmt: skip
        return Result(logged_action, ok, message, order_id, costs)

    current = switches(session_factory)
    frozen = check_cancel(current)  # Freeze first: nothing is read from the broker
    if frozen.action == "refuse":
        return done("refuse", False, frozen.reason)
    name = BROKER_NAMES.get(request.broker, request.broker)
    client = _client(clients, request.broker)
    if client is None:
        return done("refuse", False, f"{name} isn't connected.")
    with _account_lock(request.broker, request.account_number):
        # Read again: a queued order must see a Freeze turned on while it waited.
        current = switches(session_factory)
        frozen = check_cancel(current)
        if frozen.action == "refuse":
            return done("refuse", False, frozen.reason)
        try:
            order, decision = _checked(
                session_factory, clients, client, request, now, current, replaces
            )
        except _NoSuchBot as e:
            bot_id = None  # the log can't point at a bot that isn't there
            decision = Decision("refuse", str(e))
        except Exception as e:  # noqa: BLE001 -- never raise; a fact we couldn't gather refuses
            logger.exception("couldn't check an order on %s", name)
            # Only closing refusals start with COULDNT_CHECK: the alerts call them closing orders.
            start = "The app couldn't check" if request.order.opening else COULDNT_CHECK
            decision = Decision(
                "refuse", f"{start} this order ({e.__class__.__name__}), so it wasn't sent."
            )
        if decision.action == "send" and request.preview_only:
            checked = "Checked by your broker, not sent."  # a person's own preview
            decision = Decision(
                "preview",
                checked if request.origin == "you" else "Preview order: checked, not sent.",
            )
        if decision.action == "refuse":
            if decision.reason.startswith(COULDNT_CHECK) and not order.opening:
                _alert(session_factory, notify, "A closing order wasn't sent", decision.reason)
            return done("refuse", False, decision.reason)
        tag = uuid.uuid4().hex[:20] if request.broker == "tastytrade" else None
        try:
            body = _body(request.broker, order, tag)
        except OrderError as e:
            return done("refuse", False, str(e))
        except Exception as e:  # noqa: BLE001 -- never raise; an order we can't format isn't sent
            logger.exception("couldn't put an order in %s's format", name)
            return done("refuse", False, f"The order couldn't be formatted for {name} "
                        f"({e.__class__.__name__}), so it wasn't sent.")  # fmt: skip
        logged = "preview" if decision.action == "preview" else action
        try:
            preview = client.preview_order(request.account_number, body)
        except (BrokerError, CredentialsUnavailable) as e:
            credit = order.price > 0 if order.price is not None else None
            costs = preview_costs(request.broker, getattr(e, "detail", None), credit)
            return done(logged, False, f"The broker's check refused it: {e}", costs=costs)
        except Exception as e:  # noqa: BLE001 -- an unreadable preview is a failed one
            logger.exception("couldn't read %s's preview", name)
            return done(logged, False, f"The broker's check couldn't be read "
                        f"({e.__class__.__name__}), so it wasn't sent.")  # fmt: skip
        if decision.action == "preview":
            return done(
                "preview",
                True,
                f"{decision.reason} {_summary(request.broker, preview)}".strip(),
                costs=preview_costs(
                    request.broker, preview, order.price > 0 if order.price is not None else None
                ),
            )
        try:
            if action == "replace":
                order_id = client.replace_order(request.account_number, replaces, body)
            else:
                order_id = client.place_order(request.account_number, body)
        except RequestUnclear as e:
            logger.error("order outcome unclear on %s: %s", name, e)
            message = mask_number(
                f"{UNCLEAR} {e} Check your broker before anything else.", request.account_number
            )
            _alert(session_factory, notify, "An order's outcome is unclear", message)
            return done(action, False, message)
        except (BrokerError, CredentialsUnavailable) as e:
            return done(action, False, f"Not sent: {e}")
        except Exception as e:  # noqa: BLE001 -- the order may be live; treat it as unclear
            logger.exception("couldn't read %s's answer to an order", name)
            message = (
                f"{UNCLEAR} The broker's answer couldn't be read ({e.__class__.__name__}). "
                "Check your broker before anything else."
            )
            _alert(session_factory, notify, "An order's outcome is unclear", message)
            return done(action, False, message)
        result = done(action, True, "Sent." if action == "send" else "Replaced.", order_id or None)
    if request.broker == "schwab" and order_id:
        try:
            _learn_schwab_tag(session_factory, client, request.account_number, order_id)
        except Exception:  # noqa: BLE001 -- the send is already logged; learning is a bonus
            logger.exception("couldn't learn Schwab's app tag")
    return result


def cancel(session_factory, clients, broker: str, account_number: str, broker_order_id: str,
           origin: str, bot_id: int | None = None, now=None, notify=None) -> Result:  # fmt: skip
    now = now or datetime.now(UTC)

    def done(logged_action, ok, message):
        message = mask_number(message, account_number)  # broker text can quote it
        _record(
            session_factory, now, broker=broker, account=account_number, origin=origin,
            bot_id=bot_id, action=logged_action, ok=ok, result=message,
            broker_order_id=broker_order_id,
        )  # fmt: skip
        return Result(logged_action, ok, message, broker_order_id)

    decision = check_cancel(switches(session_factory))
    if decision.action == "refuse":
        return done("refuse", False, decision.reason)
    client = _client(clients, broker)
    if client is None:
        return done("refuse", False, f"{BROKER_NAMES.get(broker, broker)} isn't connected.")
    with _account_lock(broker, account_number):
        frozen = check_cancel(switches(session_factory))  # again: a Freeze may have come on
        if frozen.action == "refuse":
            return done("refuse", False, frozen.reason)
        try:
            client.cancel_order(account_number, broker_order_id)
        except RequestUnclear as e:
            message = mask_number(
                f"{UNCLEAR} {e} The order may still be working; check your broker.", account_number
            )
            _alert(session_factory, notify, "A cancel's outcome is unclear", message)
            return done("cancel", False, message)
        except (BrokerError, CredentialsUnavailable) as e:
            return done("cancel", False, f"Not cancelled: {e}")
        except Exception as e:  # noqa: BLE001 -- the cancel may have gone through; treat as unclear
            logger.exception(
                "couldn't read %s's answer to a cancel", BROKER_NAMES.get(broker, broker)
            )
            message = (
                f"{UNCLEAR} The broker's answer couldn't be read ({e.__class__.__name__}). "
                "Check your broker before anything else."
            )
            _alert(session_factory, notify, "A cancel's outcome is unclear", message)
            return done("cancel", False, message)
        return done("cancel", True, "Cancel sent.")


NOT_FOUND_AFTER = timedelta(minutes=1)
MATCH_WINDOW = (timedelta(minutes=2), timedelta(minutes=5))  # before, after the attempt
SCHWAB_TAG_KEY = "_schwab_tag:{}"


def _schwab_tag(session_factory, account_number: str) -> str | None:
    with session_factory() as session:
        row = session.get(Setting, SCHWAB_TAG_KEY.format(account_number))
    return row.value if row else None


def _learn_schwab_tag(session_factory, client, account_number: str, order_id: str) -> None:
    """Schwab stamps each order with a tag for the app that placed it (it
    takes no custom tag). Remember it from the first order, as Tracker2 does,
    so unclear outcomes can be matched to this app's orders."""
    key = SCHWAB_TAG_KEY.format(account_number)
    with session_factory() as session:
        if session.get(Setting, key):
            return
    try:
        found = client.get_order(account_number, order_id)
    except BrokerError, CredentialsUnavailable, StopIteration:
        return
    if found.tag:
        with session_factory() as session:
            session.add(Setting(key=key, value=found.tag[:64], updated_at=datetime.now(UTC)))
            session.commit()


def _matches(row: OrderLog, order: Order, found, schwab_tag: str | None) -> bool:
    if row.broker == "tastytrade" and row.tag:
        return found.tag == row.tag
    if schwab_tag and found.tag and found.tag != schwab_tag:
        return False
    before, after = MATCH_WINDOW
    if found.entered_at is None or not (
        row.created_at - before <= found.entered_at <= row.created_at + after
    ):
        return False
    return same_legs(order, found)


def _same_day(then: datetime, now: datetime) -> bool:
    """The broker's list covers today's orders; later than that, "not there"
    proves nothing, so the row stays unclear until found or cleared by you."""
    return then.astimezone(MARKET_TZ).date() == now.astimezone(MARKET_TZ).date()


def _note(row: OrderLog, now: datetime, note: str) -> None:
    row.cleared_at = now
    row.result = f"{row.result} {note}"[:2000]


def resolve_unclear(session_factory, clients, broker: str, account_number: str, now=None) -> int:
    """Look for each unclear attempt on this account in the broker's orders.

    - Found: confirmed, with the broker's id.
    - Not there a minute later: it wasn't placed.
    - A cancel: cleared once the broker says how the order stands.

    Returns how many are still unclear."""
    now = now or datetime.now(UTC)
    query = select(OrderLog).where(
        OrderLog.broker == broker,
        OrderLog.account_number == account_number,
        OrderLog.result.like(f"{UNCLEAR}%"),
        OrderLog.cleared_at.is_(None),
    )
    with session_factory() as session:
        rows = list(session.scalars(query))
    client = _client(clients, broker)
    if not rows or client is None:
        return len(rows)
    since = min(r.created_at for r in rows) - MATCH_WINDOW[0]
    try:
        recent = client.recent_orders(account_number, since)
    except (BrokerError, CredentialsUnavailable) as e:
        logger.warning("couldn't read recent orders to clear up unclear ones: %s", e)
        return len(rows)
    except Exception:  # noqa: BLE001 -- an odd answer leaves them unclear, as a failed read does
        logger.exception("couldn't read recent orders to clear up unclear ones")
        return len(rows)
    schwab_tag = _schwab_tag(session_factory, account_number) if broker == "schwab" else None
    with session_factory() as session:
        # Broker orders another attempt already owns (or that a replace is
        # replacing) can't be the answer to this one.
        claimed = set()
        for taken in session.execute(
            select(OrderLog.broker_order_id, OrderLog.replaces_id).where(
                OrderLog.broker == broker, OrderLog.account_number == account_number
            )
        ):
            claimed |= {i for i in taken if i}
        for row in session.scalars(query):
            try:
                _resolve_row(row, client, account_number, recent, claimed, schwab_tag, now)
            except Exception:  # noqa: BLE001 -- one odd row stays unclear; the rest go on
                logger.exception("couldn't clear up unclear order %s", row.id)
        session.commit()
        return len(list(session.scalars(query)))


def _resolve_row(row, client, account_number, recent, claimed, schwab_tag, now) -> None:
    if row.action == "cancel":
        try:
            found = client.get_order(account_number, row.broker_order_id)
        except BrokerError, CredentialsUnavailable, StopIteration:
            return
        _note(row, now, f"The broker shows the order as {found.status}.")
        return
    if not (row.order or {}).get("order"):
        return  # an older row the app can't read back: left for you to clear
    order = Order.from_dict(row.order["order"])
    match = next(
        (
            o
            for o in recent
            if o.broker_order_id not in claimed and _matches(row, order, o, schwab_tag)
        ),
        None,
    )
    if match is not None:
        claimed.add(match.broker_order_id)
        row.ok, row.broker_order_id = True, match.broker_order_id
        _note(row, now, f"Confirmed at the broker: order {match.broker_order_id}.")
    elif now - row.created_at >= NOT_FOUND_AFTER and _same_day(row.created_at, now):
        _note(row, now, "Not found at the broker a minute later: it wasn't placed.")


def clear(session_factory, log_id: int, now=None) -> bool:
    """The user checked their broker and clears an unclear attempt."""
    with session_factory() as session:
        row = session.get(OrderLog, log_id)
        if row is None or row.cleared_at or not row.result.startswith(UNCLEAR):
            return False
        _note(row, now or datetime.now(UTC), "Cleared by you.")
        session.commit()
        return True
