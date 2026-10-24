"""Manual bots (#158): a manual bot's rules attached to one open trade.

Attaching copies the rules into a hidden per-trade bot (Bot.per_trade, never listed,
armed or counted) with one group, "Whole trade", holding every open leg. The trade is
then handed over as bot_adopt hands a trade to a bot: one filled run whose fills are
the journal's opening prices of the legs still open (bot_adopt.record_run), and its
exits placed by bot_entry.hand_to_exits, so every order goes through orders.submit,
the one central check (preview, size limits, Freeze, which replaced Stop all bots).
Unlike a hand-over to a bot, any open trade can take rules: opened any day, partly
closed, on any underlying; the switches are left to the central check.

% rules read the trade's net credit or debit per unit: the stop as a % of the entry
lost (stop_basis "group_entry"), the target as a bot group's (% of the credit kept).
Futures place a profit target only (decision 0043): the stop, exit time and alerts are
dropped. The exit time counts from the nearest expiration (bot_timed reads each
group's nearest)."""

import logging
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from math import gcd

from sqlalchemy import select

from optionsjourney.bots import adopt as rules_of
from optionsjourney.bots import timing, underlyings
from optionsjourney.bots.definition import (
    DEFAULT_EXIT_CHASE,
    MAX_CONTRACTS,
    MAX_DTE,
    MAX_LEGS,
    BotDef,
    GroupDef,
    LegDef,
    lone_shorts_rest_stops,
)
from optionsjourney.bots.manual import ManualRules
from optionsjourney.bots.validation import validate
from optionsjourney.core.chain import chain_root
from optionsjourney.core.market import MARKET_TZ
from optionsjourney.core.positions import Status
from optionsjourney.core.symbols import normalize_underlying, parse_occ
from optionsjourney.db.models import Bot, BotExit, BotRun, PendingManual, Tag, TradeTag
from optionsjourney.services import (
    bot_adopt,
    bot_entry,
    bot_exits,
    bot_manage,
    bot_store,
    journal,
    manual_bots,
)
from optionsjourney.trading.orders import Side, filled_units, plain

logger = logging.getLogger(__name__)
GROUP_NAME = "Whole trade"
DEFAULT_TAG = "Manual"
MANAGED = "This trade is already managed by a bot."
ONE_UNDERLYING = "Manage this trade works on one underlying."
NO_TRADE = "There's no such trade in the journal."
CLOSED = "This trade is closed."
NO_OPTIONS = "Manage this trade works on an options trade."
WAIT = "A close is working on this trade. You can change its rules once it finishes."
HANDED_BACK = bot_adopt.HANDED_BACK
NOT_MANUAL = "These rules can't be changed here: the trade isn't managed by a manual bot."
FUTURES_TARGET = "A futures trade takes a profit target only: add one to these rules."


def _today(deps) -> date:
    return deps.now().astimezone(MARKET_TZ).date()


def read_trade(deps, key: str, managed_ok: bool = False
               ) -> tuple[bot_adopt.Trade | None, str | None]:  # fmt: skip
    """(the open trade at `key` as a run would hold its open legs, why rules can't be
    attached to it or None). A split half stands for its whole trade. `managed_ok`: a
    trade already managed isn't refused for it (checking new rules before a change)."""
    _, book = journal.build_unsplit(deps.session_factory)
    position = next((p for p in book.positions if p.key == bot_adopt.whole_key(key)), None)
    if position is None:
        return None, NO_TRADE
    if position.status is Status.CLOSED:
        return None, CLOSED
    held = [leg for leg in position.live_legs if leg.remaining > 0]
    if not held or any(leg.option is None for leg in held):
        return None, NO_OPTIONS
    if len({normalize_underlying(leg.underlying) for leg in held}) != 1:
        return None, ONE_UNDERLYING
    opening = position.events[0].orders[0] if position.events else None
    order_id = opening.order_id if opening is not None else None
    legs = []
    for leg in held:
        price = bot_adopt._fill_price(position, leg.symbol)
        if price is None or leg.remaining != int(leg.remaining):
            return None, "Its opening fills can't be read."
        legs.append(rules_of.HeldLeg(
            leg.symbol, leg.option.put_call.lower(), leg.option.strike, leg.option.expiration,
            Side.SELL if leg.sign < 0 else Side.BUY, int(leg.remaining), price,
        ))  # fmt: skip
    problem = None
    if position.broker not in bot_adopt.BROKERS:
        problem = "Only a trade at TastyTrade or Schwab can be managed by the app."
    elif order_id is None:
        problem = "It has no broker order id to link its rules to."
    elif not managed_ok and bot_adopt.owner(deps.session_factory, order_id) is not None:
        problem = MANAGED
    underlying, resolved = normalize_underlying(position.underlying), None
    if bot_adopt._is_futures(underlying, legs):
        if problem is None:
            legs, resolved, problem = bot_adopt._futures_trade(
                deps, underlying, legs, position.broker
            )
        underlying = chain_root(underlying.removeprefix("."))
    trade = bot_adopt.Trade(
        position.key, position.name, position.broker, position.account_number, order_id,
        position.opened_at, underlying, legs, problem, resolved,
    )  # fmt: skip
    return trade, problem


def nearest_expiry(trade: bot_adopt.Trade) -> date:
    return min(leg.expiration for leg in trade.legs)


def assignable(trade: bot_adopt.Trade) -> str | None:
    """The first leg's underlying that settles in shares (can be assigned), else None."""
    for leg in trade.legs:
        parsed = parse_occ(leg.symbol)
        symbol = parsed[0] if parsed is not None else trade.underlying
        found = underlyings.get(symbol)
        if found is not None and found.settles == "shares":
            return found.symbol
    return None


def _units(trade: bot_adopt.Trade) -> int:
    units = 0
    for leg in trade.legs:
        units = gcd(units, leg.quantity)
    return units or 1


def matched(trade: bot_adopt.Trade, today: date | None = None) -> rules_of.Matched:
    """The open legs as a filled run of the per-trade bot holds them, in one group, at
    the journal's opening prices: its credit is the net opening credit per unit (+ a
    credit) of the legs still open."""
    units = _units(trade)
    run_legs, filled, credit = [], [], Decimal(0)
    for n, leg in enumerate(trade.legs):
        ratio = leg.quantity // units
        credit += (leg.price if leg.side is Side.SELL else -leg.price) * ratio
        run_legs.append({
            "position": n, "symbol": leg.symbol, "strike": f"{leg.strike.normalize():f}",
            "right": leg.right, "side": str(leg.side), "ratio": ratio, "group": 0,
            "mid": f"{leg.price:f}", "expiration": leg.expiration.isoformat(),
            "dte": 0 if today is None else (leg.expiration - today).days,
        })  # fmt: skip
        filled.append({"symbol": leg.symbol, "side": str(leg.side),
                       "fill_price": f"{leg.price:f}", "contracts": leg.quantity})  # fmt: skip
    return rules_of.Matched(run_legs, filled, credit, units)


def bot_for(rules: ManualRules, trade: bot_adopt.Trade, manual_bot_id: int | None,
            today: date | None = None, tag_id: int | None = None) -> BotDef:  # fmt: skip
    """The per-trade bot the rules make for this trade: one group, "Whole trade", with
    every open leg; live, never armed, on the trade's broker, account and underlying."""
    units = _units(trade)
    futures = trade.futures is not None or underlyings.futures(trade.underlying)
    pct_stop = rules.stop_unit != "usd"
    pct_target = rules.target_unit != "usd"
    group = GroupDef(
        0, GROUP_NAME,
        stop_pct=None if futures or not pct_stop else rules.stop_value,
        stop_usd=None if futures or pct_stop else rules.stop_value,
        stop_unit=rules.stop_unit, stop_basis="group_entry",
        target_pct=rules.target_value if pct_target else None,
        target_usd=None if pct_target else rules.target_value, target_unit=rules.target_unit,
    )  # fmt: skip
    day = today or nearest_expiry(trade)
    legs = tuple(
        LegDef(
            n,
            leg.side,
            leg.right,
            ratio=leg.quantity // units,
            dte=min(max((leg.expiration - day).days, 0), MAX_DTE),
            target=max(leg.price, Decimal("0.01")),
            group=0,
        )  # fmt: skip
        for n, leg in enumerate(trade.legs)
    )
    largest = max(leg.ratio for leg in legs)
    bot = BotDef(
        id=None, name=rules.name, tag_id=tag_id, broker=trade.broker,
        account_number=trade.account, underlying=trade.underlying, contracts=units,
        live=True, enabled=False, max_per_trade=units * largest, max_per_day=units * largest,
        split_iron_condors=False, per_trade=True, legs=legs, groups=(group,),
        exit_chase=replace(DEFAULT_EXIT_CHASE, ignore_bidless_longs=False),
        close_time=None if futures else rules.close_time,
        close_dte=0 if futures else rules.close_dte,
        use_default_alerts=bool(rules.alerts) and not futures,
        default_alerts=() if futures else tuple(rules.alerts),
    )  # fmt: skip
    return lone_shorts_rest_stops(bot)


def _tag_id(deps, trade: bot_adopt.Trade) -> int:
    """The tag the per-trade bot files under: the trade's own first tag by name, else
    the "Manual" tag (made if missing). Decision 0072: a bot files under a tag."""
    with deps.session_factory() as session:
        own = session.scalars(
            select(Tag)
            .join(TradeTag, TradeTag.tag_id == Tag.id)
            .where(TradeTag.position_key == trade.key)
        ).all()
        if own:
            return min(own, key=lambda t: t.name.lower()).id
    return journal.tag_for(deps.session_factory, DEFAULT_TAG).id


def _save_hidden(deps, bot: BotDef, manual_bot_id: int | None) -> tuple[int | None, str | None]:
    """The per-trade bot, hidden in the same commit that creates it (bot_store.save)."""
    bot_id, problems = bot_store.save(deps.session_factory, bot, deps.now(),
                                      manual_bot_id=manual_bot_id)  # fmt: skip
    if problems:
        return None, _plain(problems, bot)
    return bot_id, None


def _plain(problems: dict[str, str], bot: BotDef) -> str:
    """What bot_store.save refused, said for a trade rather than a bot's form."""
    keys = set(problems)
    if "underlying" in keys:
        why = f"the app can't manage {bot.underlying} options"
    elif "legs" in keys:
        why = f"it has more than {MAX_LEGS} legs"
    elif keys & {"contracts", "limits"}:
        why = f"it's more than {MAX_CONTRACTS} contracts"
    elif any(k.startswith("leg-") for k in keys):
        why = "a sold leg expires after a bought leg (a reverse calendar)"
    else:
        why = next(iter(problems.values()))
    return f"A manual bot can't manage this trade: {why}."


def _discard(deps, bot_id: int) -> None:
    """A per-trade bot whose run wasn't recorded: left hidden, disarmed and archived, so
    nothing can ever fire or show it. Logged if even that fails."""
    try:
        with deps.session_factory() as session:
            row = session.get(Bot, bot_id)
            if row is not None:
                row.hidden, row.enabled, row.archived_at = True, False, deps.now()
                session.commit()
    except Exception:  # noqa: BLE001 -- it stays hidden and disarmed from its save
        logger.exception("couldn't archive the unused per-trade bot %s", bot_id)


def _prepare(deps, key: str, rules: ManualRules, manual_bot_id: int | None,
             managed_ok: bool = False):  # fmt: skip
    """(trade, the per-trade bot, why the rules can't be attached or None): every check
    that needs no save and sends nothing, shared by attach and change (which runs it
    before removing the old rules, so a refusal leaves them in place)."""
    trade, problem = read_trade(deps, key, managed_ok)
    if problem:
        return trade, None, problem
    if trade.futures is not None and rules.target_value is None:
        return trade, None, FUTURES_TARGET
    if trade.futures is not None:
        problem = bot_adopt._futures_ticks(deps, trade)
        if problem:
            return trade, None, problem
    bot = bot_for(rules, trade, manual_bot_id, _today(deps), _tag_id(deps, trade))
    with deps.session_factory() as session:
        tag_ids = set(session.scalars(select(Tag.id)))
    problems = validate(bot, tag_ids)
    if problems:
        return trade, None, _plain(problems, bot)
    return trade, bot, None


def attach(deps, key: str, rules: ManualRules, manual_bot_id: int | None) -> bot_adopt.Outcome:
    """Attach the rules to the trade at `key`: a hidden per-trade bot, its filled run,
    and its exits placed through the central check. Never raises."""
    with bot_adopt._LOCK:
        try:
            trade, bot, problem = _prepare(deps, key, rules, manual_bot_id)
            if problem:
                return bot_adopt.Outcome(False, problem)
            bot_id, problem = _save_hidden(deps, bot, manual_bot_id)
            if problem:
                return bot_adopt.Outcome(False, problem)
        except Exception:  # noqa: BLE001 -- nothing was recorded or sent
            logger.exception("couldn't attach manual rules to %s", key)
            return bot_adopt.Outcome(False, bot_adopt.COULDNT)
        try:
            bot = bot_store.load(deps.session_factory, bot_id)
            run_id = bot_adopt.record_run(deps, trade, bot, matched(trade, _today(deps)))
        except Exception:  # noqa: BLE001 -- nothing was recorded or sent
            logger.exception("couldn't record attaching manual rules to %s", key)
            _discard(deps, bot_id)
            return bot_adopt.Outcome(False, bot_adopt.COULDNT)
        if run_id is None:
            _discard(deps, bot_id)
            return bot_adopt.Outcome(False, MANAGED)
        try:
            # Alerts any failure itself; each stop's ask is checked again just before it's sent
            bot_entry.hand_to_exits(deps, bot, run_id, live_check=True)
        except Exception:  # noqa: BLE001 -- the run stands; hand_to_exits alerts its own
            logger.exception("couldn't place the exits of run %s", run_id)
        finally:
            with bot_entry._active_lock:
                bot_entry.ACTIVE.discard(run_id)
    try:
        message, warning = bot_adopt._placed_words(deps, bot, run_id)
    except Exception:  # noqa: BLE001 -- attached; only the words are missing
        logger.exception("couldn't read the exits of run %s", run_id)
        message, warning = f"{rules.name} manages this trade. See its run for the exits.", True
    dropped = _futures_dropped(rules) if trade.futures is not None else None
    if dropped:
        message = f"{message} Futures take a profit target only: {dropped} not placed."
    return bot_adopt.Outcome(True, message, run_id, warning)


def _futures_dropped(rules: ManualRules) -> str | None:
    """What a futures trade leaves out of the rules (decision 0043), in words."""
    parts = [name for name, there in (
        ("the stop loss", rules.stop_value is not None), ("the exit time", bool(rules.close_time)),
        ("the alerts", bool(rules.alerts)),
    ) if there]  # fmt: skip
    if not parts:
        return None
    return parts[0] if len(parts) == 1 else f"{', '.join(parts[:-1])} and {parts[-1]}"


# --- Changing and removing the rules (Task 4) ------------------------------------------


def closing(deps, run_id: int) -> bool:
    """Whether a close is working for the run: a close, target, timed close or stop
    running or chasing (timing.BUSY), or one the app can't yet vouch is over
    (bot_exits.held_back)."""
    with deps.session_factory() as session:
        rows = list(session.scalars(select(BotExit).where(BotExit.run_id == run_id)))
    if any(row.state in timing.BUSY for row in rows):
        return True
    groups = {row.group_position for row in rows}
    return any(
        bot_exits.held_back([r for r in rows if r.group_position == g]) == "close" for g in groups
    )


def _per_trade_run(deps, run_id: int):
    """(the run, its per-trade bot row), or (None, None) when it isn't one."""
    with deps.session_factory() as session:
        run = session.get(BotRun, run_id)
        bot = session.get(Bot, run.bot_id) if run is not None else None
        if run is None or bot is None or not bot.per_trade:
            return None, None
        session.expunge_all()
        return run, bot


def _trade_key(run) -> str | None:
    for entry in run.chase_log or []:
        if entry.get("trade"):
            return entry["trade"]
    return None


def remove(deps, run_id: int) -> bot_adopt.Outcome:
    """Take the rules off a trade: the run's resting exits cancelled through the
    exit-cancel path (bot_manage's exits off, or a futures target's own cancel), then
    the run ended as handed back. The trade is unmanaged and can take rules again.
    Refused, touching nothing, while a close is working. Never raises."""
    try:
        run, _ = _per_trade_run(deps, run_id)
        if run is None:
            return bot_adopt.Outcome(False, NOT_MANUAL)
        if run.status == HANDED_BACK:
            return bot_adopt.Outcome(True, "This trade has no rules now.", run_id)
        if closing(deps, run_id):
            return bot_adopt.Outcome(False, WAIT)
        if run.status in bot_manage.HOLDING:
            positions = sorted({leg["group"] for leg in run.legs or []}) or [0]
            futures = bool(run.pt_only)
            for position in positions:
                problem = _exits_off(deps, run_id, position, futures)
                if problem:
                    return bot_adopt.Outcome(False, f"{problem} The rules stay on.")
        with bot_adopt._LOCK, deps.session_factory() as session:
            row = session.get(BotRun, run_id)
            row.status, row.updated_at = HANDED_BACK, deps.now()
            row.chase_log = [*(row.chase_log or []),
                             {"at": deps.now().isoformat(), "event": "rules removed"}]  # fmt: skip
            session.commit()
    except Exception:  # noqa: BLE001 -- the page always answers
        logger.exception("couldn't remove the rules of run %s", run_id)
        return bot_adopt.Outcome(False, bot_manage.CHECK_BROKER)
    return bot_adopt.Outcome(True, "Rules removed. Nothing manages this trade now.", run_id)


def _exits_off(deps, run_id: int, position: int, futures: bool) -> str | None:
    """Switch one group's exits off for good; None once nothing of it rests or fires."""
    with deps.session_factory() as session:
        side = bot_manage._read(session, run_id, position)
    if side is None or side.state == "closed":
        return None
    if futures:
        if side.target_off:
            return None
        done = bot_manage.target_off(deps, run_id, position)
    else:
        if side.off:
            return None
        done = bot_manage.exits_off(deps, run_id, position)
    return None if done.ok else done.message


def change(deps, run_id: int, rules: ManualRules,
           manual_bot_id: int | None = None) -> bot_adopt.Outcome:  # fmt: skip
    """New rules for a managed trade: the old rules removed (their exits cancelled, the
    run handed back) and the new ones attached as a new per-trade bot and run, its exits
    placed through the central check. Refused, touching nothing, while a close works.
    `manual_bot_id`: the manual bot the new rules come from (None: custom rules)."""
    run, _ = _per_trade_run(deps, run_id)
    if run is None or run.status == HANDED_BACK:
        return bot_adopt.Outcome(False, NOT_MANUAL)
    if closing(deps, run_id):
        return bot_adopt.Outcome(False, WAIT)
    key = _trade_key(run)
    if key is None:
        return bot_adopt.Outcome(False, bot_adopt.COULDNT)
    try:
        _, _, problem = _prepare(deps, key, rules, manual_bot_id, managed_ok=True)
    except Exception:  # noqa: BLE001 -- nothing was changed
        logger.exception("couldn't check new rules for run %s", run_id)
        return bot_adopt.Outcome(False, bot_adopt.COULDNT)
    if problem:
        return bot_adopt.Outcome(False, f"{problem} Its rules are unchanged.")
    removed = remove(deps, run_id)
    if not removed.ok:
        return removed
    done = attach(deps, key, rules, manual_bot_id)
    if not done.ok:
        return bot_adopt.Outcome(
            False, f"The old rules were removed, but the new ones weren't attached: "
            f"{done.message} Nothing manages this trade now.", None, True,
        )  # fmt: skip
    return done


def rules_of_run(deps, run_id: int) -> ManualRules | None:
    """The rules a per-trade run manages its trade by, read back from its hidden bot."""
    run, _ = _per_trade_run(deps, run_id)
    if run is None:
        return None
    bot = bot_store.load(deps.session_factory, run.bot_id)
    group = bot.groups[0]
    return ManualRules(
        bot.name, group.target_value, group.target_unit or "pct", group.stop_value,
        group.stop_unit or "pct", bot.close_time, bot.close_dte or 0,
        tuple(bot.default_alerts) if bot.use_default_alerts else (),
    )  # fmt: skip


# --- Manage with on the Trade ticket: attached once the order fills (Task 5) -----------

WAITING, ATTACHED, ENDED = "waiting", "attached", "ended"
NOT_ATTACHED = "Manage with: the rules weren't attached"
READ_MISSES = 3  # passes in a row the order can't be read before the row ends
NOT_SEEN = timedelta(days=1)  # an order over, its fill still missing from the journal


def snapshot(rules: ManualRules) -> dict:
    """The rules as stored in a pending attachment (a copy, so later edits don't count)."""

    def text(value):
        return None if value is None else f"{value:f}"

    return {
        "name": rules.name, "target_value": text(rules.target_value),
        "target_unit": rules.target_unit, "stop_value": text(rules.stop_value),
        "stop_unit": rules.stop_unit, "close_time": rules.close_time,
        "close_dte": rules.close_dte,
        "alerts": [bot_store._alert_rule_row(rule) for rule in rules.alerts],
    }  # fmt: skip


def from_snapshot(raw: dict) -> ManualRules:
    def amount(value):
        return None if value is None else Decimal(value)

    return ManualRules(
        name=raw["name"], target_value=amount(raw.get("target_value")),
        target_unit=raw.get("target_unit", "pct"), stop_value=amount(raw.get("stop_value")),
        stop_unit=raw.get("stop_unit", "pct"), close_time=raw.get("close_time"),
        close_dte=raw.get("close_dte", 0),
        alerts=tuple(bot_store._alert_rule_of(a) for a in raw.get("alerts") or ()),
    )  # fmt: skip


def remember(deps, broker: str, account: str, order_id: str, manual_bot_id: int) -> None:
    """Remember to attach the manual bot's rules (as they are now) to the trade the
    ticket's order opens, once it fills. Called by the ticket's confirm only after
    orders.submit returned an order id; sends nothing. Raises LookupError when the
    manual bot is gone."""
    rules = manual_bots.load(deps.session_factory, manual_bot_id)
    if rules is None:
        raise LookupError("no such manual bot")
    now = deps.now()
    with deps.session_factory() as session:
        session.add(PendingManual(
            broker=broker, account_number=account, order_id=order_id,
            manual_bot_id=manual_bot_id, rules=snapshot(rules), run_id=None,
            attached_contracts=0, status=WAITING, created_at=now, updated_at=now,
        ))  # fmt: skip
        session.commit()


def pending_pass(deps) -> None:
    """Each waiting or attached pending attachment, on its own (the monitor's step):
    the first fill seen attaches the rules to the filled part; once the order is no
    longer working, a bigger trade gets them once more for its whole size (change: old
    exits cancelled, new ones placed); an order that ends with no fill attaches nothing.
    The journal is read once per pass."""
    with deps.session_factory() as session:
        ids = list(session.scalars(
            select(PendingManual.id).where(PendingManual.status.in_((WAITING, ATTACHED)))
        ))  # fmt: skip
    if not ids:
        return
    _, book = journal.build_unsplit(deps.session_factory)
    for row_id in ids:
        try:
            _pending_one(deps, row_id, book)
        except Exception:  # noqa: BLE001 -- one never stops the others; tried next pass
            logger.exception("couldn't work the pending manual rules %s", row_id)


def _set(deps, row_id: int, **fields) -> None:
    with deps.session_factory() as session:
        row = session.get(PendingManual, row_id)
        for name, value in fields.items():
            setattr(row, name, value)
        row.updated_at = deps.now()
        session.commit()


def _order_units(found) -> int:
    """Whole units the broker says the order filled."""
    size = 0
    for leg in found.legs:
        size = gcd(size, leg.quantity)
    ratios = {plain(leg.symbol): leg.quantity // (size or 1) for leg in found.legs}
    return filled_units(found, ratios)[0] if found.legs else 0


def _opened_by(book, broker: str, account: str, order_id: str) -> tuple[str | None, int]:
    """(the journal key of the open trade this order opened, its whole units held now),
    or (None, 0) while the journal hasn't it."""
    for position in book.positions:
        opening = position.events[0].orders[0] if position.events else None
        if (opening is None or opening.order_id != order_id or position.broker != broker
                or position.account_number != account):  # fmt: skip
            continue
        if position.status is Status.CLOSED:
            return None, 0
        units = 0
        for leg in position.live_legs:
            if leg.remaining > 0 and leg.remaining == int(leg.remaining):
                units = gcd(units, int(leg.remaining))
        return position.key, units
    return None, 0


def _not_attached(deps, row_id: int, name: str, why: str) -> None:
    _set(deps, row_id, status=ENDED)
    bot_entry.alert(deps, f"{NOT_ATTACHED} ({name})",
                    f"{why} Your trade is open without them; use Manage this trade in "
                    "the Journal.", "/journal")  # fmt: skip


def _read_order(deps, row_id: int, name: str, broker: str, account: str, order_id: str,
                misses: int):  # fmt: skip
    """The broker's order, or None: READ_MISSES failures in a row end the row, said."""
    try:
        found = bot_exits._client(deps, broker).get_order(account, order_id)
    except Exception:  # noqa: BLE001 -- counted; tried again next pass
        logger.exception("couldn't read order %s for its manual rules", order_id)
        if misses + 1 >= READ_MISSES:
            _not_attached(deps, row_id, name, "The app couldn't read the order at your broker.")
        else:
            _set(deps, row_id, read_misses=misses + 1)
        return None
    if misses:
        _set(deps, row_id, read_misses=0)
    return found


def _overdue(deps, row_id: int, name: str, done_at) -> None:
    """The order is over but the journal hasn't (all of) its fill: waited NOT_SEEN, then
    the row ends, said."""
    if done_at is None:
        _set(deps, row_id, done_at=deps.now())
    elif deps.now() - done_at > NOT_SEEN:
        _not_attached(deps, row_id, name,
                      "The app couldn't find all of the order's fill in your Journal.")  # fmt: skip


def _pending_one(deps, row_id: int, book) -> None:
    with deps.session_factory() as session:
        row = session.get(PendingManual, row_id)
        broker, account, order_id = row.broker, row.account_number, row.order_id
        rules, manual_bot_id = from_snapshot(row.rules), row.manual_bot_id
        run_id, attached = row.run_id, row.attached_contracts
        misses, done_at = row.read_misses, row.done_at
    found = _read_order(deps, row_id, rules.name, broker, account, order_id, misses)
    if found is None:
        return
    done = not found.may_be_working
    ordered = _order_units(found)
    key, units = _opened_by(book, broker, account, order_id)
    if run_id is None:
        if not units:
            if done and not ordered:
                _set(deps, row_id, status=ENDED)  # cancelled, rejected or expired unfilled
            elif done:
                _overdue(deps, row_id, rules.name, done_at)
            return  # not filled yet, or the journal hasn't the fill yet
        attached_now = attach(deps, key, rules, manual_bot_id)
        if not attached_now.ok:
            _not_attached(deps, row_id, rules.name, attached_now.message)
            return
        _set(deps, row_id, run_id=attached_now.run_id, attached_contracts=units, status=ATTACHED)
        if done and units >= ordered:
            _set(deps, row_id, status=ENDED)
        return  # a part: grown once, when the order is over
    with deps.session_factory() as session:
        run = session.get(BotRun, run_id)
        removed = run is None or run.status == HANDED_BACK
    if removed:  # you took the rules off: they aren't put back
        _set(deps, row_id, status=ENDED)
        return
    if not done:
        return  # the rest is protected once the order is over: one change, not one a fill
    if units > attached:
        grown = change(deps, run_id, rules, manual_bot_id)
        if not grown.ok:
            if grown.message != WAIT:
                _not_attached(deps, row_id, rules.name, grown.message)
            return  # a close is working: tried again next pass
        _set(deps, row_id, run_id=grown.run_id, attached_contracts=units, status=ENDED)
    elif attached >= ordered:
        _set(deps, row_id, status=ENDED)
    else:
        _overdue(deps, row_id, rules.name, done_at)
