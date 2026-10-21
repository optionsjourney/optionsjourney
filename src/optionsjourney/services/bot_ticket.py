"""A Trade page ticket sent as a bot's entry (spec: bots slice 15; Tracker2's
services/bots/analyzer_entry.py, spec 2026-09-27-analyzer-bot-trade-design.md).

The ticket supplies the options and the size; a live bot whose legs match it
(bots/adopt.py, Tracker2's analyzer_shape.match) supplies the chase and every exit.
choices() reads only the database. enter() checks everything it can without the
broker, then claims the run on a key from the page's token BEFORE any broker call,
so a double click or a retried request lands on the same row and places nothing
twice (run_analyzer_entry). The rest runs in the background: the positions, the
fresh mids, then exactly a scheduled entry's steps (bot_entry.enter_picked): one
opening order through the order path, the bot's entry chase, and each fill's exits."""

import logging
import re
import threading
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select

from optionsjourney.bots import adopt as rules
from optionsjourney.bots import futures as futures_rules
from optionsjourney.bots import timing, underlyings
from optionsjourney.bots.definition import BotDef, largest_ratio
from optionsjourney.bots.strikes import MIN_CREDIT, too_close_to_even
from optionsjourney.core.chain import chain_root
from optionsjourney.core.market import MARKET_TZ
from optionsjourney.core.symbols import normalize_underlying, parse_occ
from optionsjourney.db.models import BotRun
from optionsjourney.services import (
    bot_adopt,
    bot_entry,
    bot_exits,
    bot_futures,
    bot_limit,
    bot_store,
    orders,
    trade_ticket,
)
from optionsjourney.services.trade_ticket import _legs
from optionsjourney.trading.orders import Order, order_effect, plain

logger = logging.getLogger(__name__)
TOKEN = re.compile(r"^[0-9a-f]{12}$")  # analyzer_entry.TOKEN
KEY_PREFIX = "ticket-"  # a Trade page entry's key: ticket-<bot>-<token>
FROM_TICKET = "from the Trade page"  # the run log's first event
COULDNT = "Couldn't check this; nothing was sent."
QUOTE_WAITS = 3  # fresh-quote reads per leg, 1 s apart (bot_exits.QUOTE_WAITS)


def ticket_key(bot_id: int, token: str) -> str:
    return f"{KEY_PREFIX}{bot_id}-{token}"


def is_from_ticket(run) -> bool:
    return (run.key or "").startswith(KEY_PREFIX)


@dataclass(frozen=True)
class Sent:
    ok: bool
    message: str
    run_id: int | None = None
    bot_id: int | None = None


def _today(deps):
    return deps.now().astimezone(MARKET_TZ).date()


def _resolver(deps):
    """How a futures ticket's DXLink names become TastyTrade's (bots slice 17)."""
    return bot_futures.ticket_resolver(deps.chain, deps.clients(), deps.now())


def _held_legs(body, futures=None) -> tuple[list[rules.HeldLeg], str, object] | str:
    """The ticket's legs as the matcher takes them, their underlying and, for a futures
    ticket (bots slice 17), what they resolved to (None otherwise); or what's wrong with
    the ticket (trade_ticket._legs' own words)."""
    found = trade_ticket.ticket_legs(body if isinstance(body, dict) else {}, futures)
    if isinstance(found, str):
        return found
    legs, quantity, resolved = found
    if resolved is not None:  # its root, from the contract it's on (/ESZ6 is /ES)
        out = [
            rules.HeldLeg(leg.symbol, listed.right, listed.strike, listed.expiration, leg.side,
                          leg.ratio * quantity, Decimal(0))
            for leg, listed in zip(legs, resolved.legs, strict=True)
        ]  # fmt: skip
        return out, chain_root(resolved.contract), resolved
    out, underlyings = [], set()
    for leg in legs:
        parsed = parse_occ(leg.symbol)
        if parsed is None:
            return f"{leg.symbol.strip()} isn't an option the ticket knows."
        underlying, option = parsed
        underlyings.add(normalize_underlying(underlying))
        out.append(rules.HeldLeg(
            leg.symbol, option.put_call.lower(), option.strike, option.expiration, leg.side,
            leg.ratio * quantity, Decimal(0),
        ))  # fmt: skip
    if len(underlyings) != 1:
        return "A bot takes a ticket on one underlying."
    return out, underlyings.pop(), None


def choices(deps, broker: str, account: str, body) -> tuple[str | None, list]:
    """(what's wrong with the ticket, or None; [(bot, why it can't take it or None, its
    exit lines)]) for the listed bots on the ticket's account (the Trade window's bot
    picker: "analyzer bots where bot.account_id equals the tray's account and the
    bot's leg shape matches"). Reads only the database."""
    found = _held_legs(body, _resolver(deps))
    if isinstance(found, str):
        return found, []
    legs, underlying, _ = found
    today = _today(deps)
    out = []
    for bot in bot_store.listing(deps.session_factory):
        if (bot.broker, bot.account_number) != (broker, account):
            continue
        why = rules.bot_problem(bot, legs, underlying, today=today)
        # The pairing the bot will use, with each group's exits, for a bot that can take it
        lines = rules.exit_summary(bot) if why else rules.group_lines(bot, rules.pair(bot, legs)[1])
        out.append((bot, why, lines))
    return None, out


def enter(deps, broker: str, account: str, body) -> Sent:
    """Send the ticket as the bot's entry (run_analyzer_entry, Chase). Never raises."""
    try:
        return _enter(deps, broker, account, body if isinstance(body, dict) else {})
    except Exception:  # noqa: BLE001 -- the page always answers
        logger.exception("couldn't send a ticket as a bot's entry")
        return Sent(False, COULDNT)


def _enter(deps, broker: str, account: str, body: dict) -> Sent:
    token = body.get("token")
    if not isinstance(token, str) or not TOKEN.match(token):
        return Sent(False, "This needs a fresh review: Review order again.")
    found = _held_legs(body, _resolver(deps))
    if isinstance(found, str):
        return Sent(False, found)
    legs, underlying, resolved = found
    bot_id = body.get("bot_id")
    bot = bot_store.load(deps.session_factory, bot_id) if type(bot_id) is int else None
    if bot is None or bot_adopt._hidden(deps.session_factory, bot.id):
        return Sent(False, "There's no such bot.")
    if (bot.broker, bot.account_number) != (broker, account):
        return Sent(False, f"{bot.name} trades in another account.", bot_id=bot.id)
    key = ticket_key(bot.id, token)
    taken = _run_of_key(deps, key)
    if taken is not None:
        return Sent(False, f"This order was already sent (run {taken}).", taken, bot.id)
    # A claim a crash left pending sent nothing: end it, so it doesn't hold a place
    bot_entry._settle_stuck_claims(deps, bot.id, _today(deps), None, f"{KEY_PREFIX}{bot.id}-")
    problem = _before_claim(deps, bot, legs, underlying)
    if problem is None and resolved is not None:  # its series' increments, before the claim
        try:
            bot_futures.ensure_ticks(deps.session_factory, deps.clients(), resolved, deps.now())
        except futures_rules.Unresolvable as e:
            problem = str(e)
    if problem is not None:
        return Sent(False, f"{bot.name} can't take it: {problem}", bot_id=bot.id)
    order = _use_review(deps, broker, account, body, _legs_of(body, resolved))
    if isinstance(order, str):
        return Sent(False, order, bot_id=bot.id)
    limit = _limit_problem(bot, order) if resolved is not None else None
    if limit is not None:
        return Sent(False, f"{bot.name} can't take it: {limit}", bot_id=bot.id)
    units, pairs = rules.pair(bot, legs)
    if not _as_reviewed(units, pairs, order):
        return Sent(False, NOT_AS_REVIEWED, bot_id=bot.id)
    run_id = bot_entry._claim(
        deps, bot, key, deps.now().astimezone(MARKET_TZ).strftime("%H:%M"), bot.live
    )
    if run_id is None:
        taken = _run_of_key(deps, key)
        return Sent(False, f"This order was already sent (run {taken}).", taken, bot.id)
    with bot_entry._active_lock:
        bot_entry.ACTIVE.add(run_id)  # until the background owns it
    resting = (order, resolved) if resolved is not None else None
    try:
        deps.background(_send, deps, bot.id, run_id, legs, _signature(units, pairs), resting)
    except Exception:  # noqa: BLE001 -- nothing was sent: finish the claim
        logger.exception("couldn't start the entry for run %s", run_id)
        bot_entry.update_run(deps, run_id, status="error", reason=COULDNT)
        with bot_entry._active_lock:
            bot_entry.ACTIVE.discard(run_id)
        return Sent(False, COULDNT, run_id, bot.id)
    if resolved is not None:
        words = bot_limit._price_words(order.price, order.time_in_force)
        return Sent(
            True,
            f"Sent as {bot.name}'s entry (run {run_id}). Your limit ({words}) rests at "
            "TastyTrade as its entry; when it fills, the bot rests its profit target. "
            f"{bot_exits.FUTURES_NOTE}.",
            run_id,
            bot.id,
        )
    return Sent(
        True,
        f"Sent as {bot.name}'s entry (run {run_id}). It's chased and its exits are placed as "
        "a scheduled entry's.",
        run_id,
        bot.id,
    )


REVIEW_USED = "That review has already been used or has expired. Review again."
REVIEW_CHANGED = "The ticket changed since the review. Review again."


def _legs_of(body: dict, resolved) -> tuple | str:
    """The ticket's order legs and size: a futures ticket's on the symbols it resolved
    to (bots slice 17), any other's from its DXLink names (trade_ticket._legs)."""
    if resolved is None:
        return _legs(body)
    found = trade_ticket.ticket_legs(body, lambda root, names: resolved)
    return found if isinstance(found, str) else found[:2]


def _use_review(deps, broker: str, account: str, body: dict, ticket) -> Order | str:
    """Use up the review's one-time code (trade_ticket._take), so one review places at
    most one order, by Send or by a bot. The reviewed order's legs and size must be the
    ticket's (`ticket`: _legs_of). Returns the reviewed order: a resting entry rests at
    its price and time in force (bots slice 17)."""
    order = trade_ticket._take(body.get("code"), broker, account, deps.now())
    if order is None:
        return REVIEW_USED
    if isinstance(ticket, str):
        return ticket
    legs, quantity = ticket

    def shape(items):
        return sorted((plain(leg.symbol), str(leg.side), leg.ratio) for leg in items)

    if quantity != order.quantity or shape(legs) != shape(order.legs):
        return REVIEW_CHANGED
    return order


NOT_AS_REVIEWED = (
    "The bot would send this ticket in a different size than you reviewed (its legs aren't "
    "in their smallest ratio). Nothing was sent: Review order again."
)


def _as_reviewed(units: int, pairs, order: Order) -> bool:
    """The bot sends `units` x its own leg ratios (enter_picked, bot_limit.entry_order):
    that must be exactly the reviewed order, its quantity and each leg's ratio (final
    review, slice 17: a reviewed 1 x (2/2) must never go out as 2 x (1/1))."""
    reviewed = {plain(leg.symbol): leg.ratio for leg in order.legs}
    return units == order.quantity and all(
        reviewed.get(plain(held.symbol)) == b.ratio for held, b in pairs
    )


def _limit_problem(bot: BotDef, order: Order) -> str | None:
    """A resting entry's own price (bots slice 17): either side (decision 0048), never
    under the smallest price either way (strikes.too_close_to_even), and a debit never
    past the bot's premium maximum when its bounds are on (slice 15's debit cap)."""
    price = order.price
    if price is None or price == 0:
        return "the limit is 0."
    if too_close_to_even(price) is not None:
        side = "credit" if price > 0 else "debit"
        return f"The {side} is below ${MIN_CREDIT:f}; not entered."
    cap = bot.premium_max if bot.use_premium_bounds and price < 0 else None
    if cap is not None and -price > cap:
        return f"the debit {-price:f} is over its premium maximum {cap:f}."
    return None


def _signature(units: int, pairs) -> tuple:
    """What the claim was checked against: the units and each leg's bot leg and group."""
    return units, tuple(sorted((h.symbol, b.position, b.group) for h, b in pairs))


def _run_of_key(deps, key: str) -> int | None:
    with deps.session_factory() as session:
        return session.scalar(select(BotRun.id).where(BotRun.key == key))


def _before_claim(deps, bot: BotDef, legs, underlying: str) -> str | None:
    """Everything that refuses without the broker (spec question 4): the match and
    the held rules, the switches and the session, the bot's limits and a timed close
    that would close the trade at once. A futures bot's resting entry may go at any
    hour (bots slice 17; guards.check_manual(require_open=False) for a Limit)."""
    today = _today(deps)
    problem = rules.bot_problem(bot, legs, underlying, today=today)
    if problem is not None:
        return problem
    if underlyings.futures(bot.underlying):
        problem = _switches(deps)
    else:
        problem = bot_entry._switches_and_session(deps, manual=False)
    if problem is not None:
        return problem
    units, pairs = rules.pair(bot, legs)
    contracts = units * largest_ratio(bot)
    if bot.max_per_day is not None:  # decision 0058: blank holds no Trade page entry back
        used = orders.used_today(deps.session_factory, bot.id, deps.now())
        if used + contracts > bot.max_per_day:
            return (
                f"it has ordered {used} today; {contracts} more is over its limit of "
                f"{bot.max_per_day} a day."
            )
    problem = _cap_problem(deps, bot)
    if problem is not None:
        return problem
    return _timed_close_due(deps, bot, pairs)


def _switches(deps) -> str | None:
    """Freeze and No new trades, with no session: a resting entry rests (bots slice 17)."""
    current = orders.switches(deps.session_factory)
    if current.freeze:
        return "Orders are frozen."
    if current.no_new_trades:
        return "No new trades is on."
    return None


_bot_locks: dict[int, threading.Lock] = {}
_bot_locks_guard = threading.Lock()


def _bot_lock(bot_id: int) -> threading.Lock:
    with _bot_locks_guard:
        return _bot_locks.setdefault(bot_id, threading.Lock())


def _cap_problem(deps, bot: BotDef, run_id: int | None = None) -> str | None:
    """Max open trades, counting the bot's Trade page entries still being entered today
    as open (two tabs, or a re-review, can't both pass). With `run_id` (the re-check
    in _place), only entries claimed before it count: the earlier one goes ahead."""
    if bot.max_open_trades is None:
        return None
    try:
        held = bot_entry._open_trades(deps, bot)
        entering = bot_entry.off_schedule_entering(deps, bot, before=run_id)
    except Exception:  # noqa: BLE001 -- can't count, so don't take it
        logger.exception("couldn't count %s's open trades", bot.name)
        return f"Couldn't count {bot.name}'s open trades. Try again."
    each = bot_entry.entering_weight(bot)
    if held + each * len(entering) < bot.max_open_trades:
        return None
    still = (
        f" and run {', '.join(str(r) for r in entering)} is still being entered"
        if entering else ""
    )  # fmt: skip
    return (
        f"{bot.name} already holds {held} open trade(s){still}, at its Max open trades "
        f"({bot.max_open_trades}). Raise the limit or wait for one to close."
    )


def _timed_close_due(deps, bot: BotDef, pairs) -> str | None:
    """validation._timed_close's entry-time rule for this trade: a group the bot's
    timed close is already due on would be closed as soon as it's in."""
    if bot.close_time is None:
        return None
    local = deps.now().astimezone(MARKET_TZ)
    for group in bot.groups:
        expirations = [held.expiration for held, b in pairs if b.group == group.position]
        if not expirations:
            continue
        if timing.timed_close_due(bot.close_time, bot.close_dte, min(expirations), local):
            return (
                f"its timed close ({bot.close_time}) is already due for {group.name}: the trade "
                "would be closed as soon as it's in."
            )
    return None


def _send(deps, bot_id: int, run_id: int, legs, signature: tuple, resting=None) -> None:
    """The claimed run, in the background, owned until it's settled (bot_entry.own):
    the bot reloaded and matched again, the positions, the fresh mids, then a scheduled
    entry's steps. Whatever happens, the run is never left pending in ACTIVE, and an end
    this module chose (nothing sent) alerts once; the chase's own endings and own()'s
    failures alert themselves."""
    ended: dict[str, str] = {}
    bot = None
    try:
        bot = bot_store.load(deps.session_factory, bot_id)
        if bot is None:  # deleted since the claim
            _end(deps, run_id, "error", "The bot is gone; nothing was sent.", ended)
            return
        try:
            units, pairs = rules.pair(bot, legs)
        except rules.Refused:
            units, pairs = 0, []
        if _signature(units, pairs) != signature:  # edited since the claim
            _end(deps, run_id, "error", CHANGED, ended)
            return

        def work(owned: list, handed: set) -> None:
            if resting is not None:  # bots slice 17: a futures ticket's resting entry
                _place_resting(deps, bot, run_id, pairs, units, resting, ended)
            else:
                _place(deps, bot, run_id, pairs, units, owned, handed, ended)

        bot_entry.own(deps, bot, run_id, work)
    except Exception:  # noqa: BLE001 -- the run is finished, never left pending
        logger.exception("the entry for run %s broke before it was sent", run_id)
        if bot_entry._status(deps, run_id) == "pending":
            _end(deps, run_id, "error", COULDNT, ended)
    finally:
        with bot_entry._active_lock:
            bot_entry.ACTIVE.discard(run_id)
        if ended:
            name = bot.name if bot is not None else "A bot"
            bot_entry.alert(
                deps, f"{name}: entry from the Trade page not sent", ended["reason"],
                f"/automation/runs/{run_id}", run_id=run_id, bot_id=bot_id,
            )  # fmt: skip


CHANGED = "The bot's legs changed since you pressed Send; nothing was sent."


def _end(deps, run_id: int, status: str, reason: str, ended: dict) -> None:
    bot_entry.update_run(deps, run_id, status=status, reason=reason)
    ended["reason"] = reason


def _place(deps, bot: BotDef, run_id: int, pairs, units: int, owned, handed, ended) -> None:
    def skip(reason: str) -> None:
        _end(deps, run_id, "skipped", reason, ended)

    live = bot.live  # the bot's mode now: a bot switched to preview only previews
    if not live:
        bot_entry.update_run(deps, run_id, live=False)
    with _bot_lock(bot.id):
        problem = _cap_problem(deps, bot, run_id)
    if problem is not None:
        _end(deps, run_id, "error", problem, ended)
        return
    problem = _touches_held(deps, bot, pairs)
    if problem is not None:
        _end(deps, run_id, "error", problem, ended)
        return
    prices = {}
    for held, _ in pairs:
        mid = _fresh_mid(deps, held.symbol)
        if mid is None:
            skip(f"No fresh quote for {held.symbol.strip()}; nothing was sent.")
            return
        prices[held.symbol] = mid
    problem = rules.priced_problem(bot, pairs, prices)
    if problem is not None:  # said here, once (enter_picked would end it silently)
        skip(problem)
        return
    # The quotes can take seconds: the switches, the session and the cut-off again
    problem = bot_entry._switches_and_session(deps, manual=False)
    if problem is not None:
        skip(problem)
        return
    today = _today(deps)
    picked = rules.picked_of(pairs, prices)
    dtes = {b.position: (held.expiration - today).days for held, b in pairs}
    lead = {"at": deps.now().isoformat(), "event": FROM_TICKET}
    bot_entry.enter_picked(
        deps, bot, run_id, picked, units, live, owned, handed, skip,
        scheduled=False, dtes=dtes, spot=_spot(deps, bot), lead=lead,
    )  # fmt: skip


def _place_resting(deps, bot: BotDef, run_id: int, pairs, units: int, resting, ended) -> None:
    """A futures ticket, claimed (run_analyzer_entry's Limit path): the open-trade cap
    and the positions again, the switches again, then bot_limit.place rests the reviewed
    limit as the bot's entry."""
    order, resolved = resting
    if not bot.live:
        bot_entry.update_run(deps, run_id, live=False)
    with _bot_lock(bot.id):
        problem = _cap_problem(deps, bot, run_id)
    if problem is not None:
        _end(deps, run_id, "error", problem, ended)
        return
    problem = _touches_held(deps, bot, pairs)
    if problem is not None:
        _end(deps, run_id, "error", problem, ended)
        return
    problem = _switches(deps)
    if problem is not None:
        _end(deps, run_id, "skipped", problem, ended)
        return
    lead = {"at": deps.now().isoformat(), "event": FROM_TICKET}
    bot_limit.place(
        deps, bot, run_id, bot_limit.run_legs(pairs, resolved, _today(deps)),
        bot_limit.entry_order(pairs, units, order.price, order.time_in_force), resolved, lead,
    )  # fmt: skip


def _touches_held(deps, bot: BotDef, pairs) -> str | None:
    """A bot only opens new trades (spec question 10): the ticket must open, by the
    plain ticket's own rule (trading.orders.order_effect). Unreadable refuses."""
    try:
        client = bot_exits._client(deps, bot.broker)
        held = client.option_positions(bot.account_number)
    except Exception:  # noqa: BLE001 -- not knowing what's held refuses
        logger.exception("couldn't read positions for a ticket entry")
        return "Couldn't read your positions at the broker, so nothing was sent."
    if order_effect([(h.symbol, h.side) for h, _ in pairs], held) != "opening":
        return (
            "This ticket would close or roll a position you hold, and a bot only opens new "
            "trades. Nothing was sent."
        )
    return None


def _fresh_mid(deps, symbol: str) -> Decimal | None:
    """The option's mid from a fresh quote (bot_exits._quote: under 30 seconds old),
    waiting briefly for one, by the chain's own rule (strikes.Quote2.mid)."""
    from optionsjourney.bots.strikes import Quote2

    for attempt in range(QUOTE_WAITS):
        quote = bot_exits._quote(deps, symbol)  # subscribes it too
        mid = Quote2(symbol, quote.bid, quote.ask).mid if quote is not None else None
        if mid is not None and mid > 0:
            return mid
        if attempt < QUOTE_WAITS - 1:
            deps.sleep(1)
    return None


def _spot(deps, bot: BotDef):
    """The underlying's price for the watch rule's move (entry_spot.stamp); None when
    it can't be read: the rule then can't run, as for a trade handed over."""
    try:
        return bot_entry.underlying_price(deps, deps.clients(), bot.underlying) or None
    except Exception:  # noqa: BLE001 -- only the watch rule needs it
        logger.exception("couldn't read %s's price for a ticket entry", bot.underlying)
        return None
