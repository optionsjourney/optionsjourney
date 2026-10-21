"""Paper exits (decision 0061): the app watches each open paper trade on the stream and
records pretend exits at the mid, and settles expired legs at intrinsic value."""

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select

from optionsjourney.bots import (
    caps,
    exit_math,
    exit_plan,
    futures,
    overrides,
    stops,
    timing,
    underlyings,
    watch,
    watched_stops,
)
from optionsjourney.bots.definition import BotDef, GroupDef
from optionsjourney.core.chain import chain_root
from optionsjourney.core.market import MARKET_TZ
from optionsjourney.core.quotes import DXLINK_KEY
from optionsjourney.core.sessions import naive_et, session_now
from optionsjourney.core.symbols import parse_occ
from optionsjourney.db.models import BotRun, BotTradeOverride, PaperFill
from optionsjourney.services import (
    bot_entry,
    bot_exits,
    bot_store,
    bot_timed,
    bot_trade_settings,
    bot_watch,
)
from optionsjourney.trading.orders import Side

logger = logging.getLogger(__name__)

# --- The paper exit watcher (Task 4) ---------------------------------------------------
#
# Every 2 s (the bot-watch loop) each open paper trade is read from the stream and its
# bot's exits are acted on as pretend fills at the mid: its stops and targets, every one
# watched by the app here (one the live path would rest at the broker too), its watch
# rules, profit actions, timed close (with close DTE) and time actions. The rules are the
# live path's own pure ones (bots/exit_plan, stops, watched_stops, watch, timing); nothing
# here sends, previews or cancels an order. A trigger must hold CONFIRM_PASSES passes in
# a row (a delta or S/L ratio rule 30 s too, as live: #201), and a close needs a fresh,
# usable mid on every leg of the group: until then it waits. Expired legs settle at
# intrinsic once the session has closed.

EXIT, EXPIRY, ACTION = "exit", "expiry", "action"
STOP, TARGET, TIMED, EXPIRED = "stop", "target", "timed close", "expired"
CLOSING = (EXIT, EXPIRY)

_lock = threading.Lock()
# (run, rule key) -> (passes in a row the rule held, the first of those passes' time)
_streaks: dict[tuple, tuple[int, datetime | None]] = {}
_last: datetime | None = None  # the last paper watch pass in the session
_said: set[tuple] = set()  # one-off alerts already sent


def reset() -> None:
    """For tests, and what a restart does: nothing in memory."""
    global _last
    with _lock:
        _streaks.clear()
        _said.clear()
        _last = None


def _first_time(*key) -> bool:
    with _lock:
        if key in _said:
            return False
        _said.add(key)
        return True


def _held(run: BotRun, rows) -> dict[int, int]:
    """Each group's open units (caps.paper_held, the rule the caps count by)."""
    return caps.paper_held(run.legs, run.contracts, ((r.kind, r.group, r.contracts) for r in rows))


def open_runs(session_factory, bot_id: int | None = None) -> list[BotRun]:
    """Paper runs filled and not yet closed in full by their paper exits and expiries."""
    with session_factory() as session:
        query = select(BotRun).where(BotRun.paper.is_(True), BotRun.status == "filled")
        if bot_id is not None:
            query = query.where(BotRun.bot_id == bot_id)
        runs = list(session.scalars(query.order_by(BotRun.id)))
        rows = _fills_of(session, [r.id for r in runs])
        session.expunge_all()
    return [r for r in runs if any(_held(r, rows.get(r.id, [])).values())]


def _fills_of(session, run_ids) -> dict[int, list[PaperFill]]:
    out: dict[int, list[PaperFill]] = {}
    if not run_ids:
        return out
    for row in session.scalars(
        select(PaperFill).where(PaperFill.run_id.in_(run_ids)).order_by(PaperFill.id)
    ):
        out.setdefault(row.run_id, []).append(row)
    return out


@dataclass
class _Paper:
    """One open paper trade, as a pass needs it."""

    id: int
    bot: BotDef
    legs: list
    fills: dict  # symbol -> its pretend fill price
    contracts: int
    filled_at: datetime
    entry_credit: Decimal | None
    entry_spot: Decimal | None
    held: dict[int, int]  # group -> open units (contracts x the gcd of its ratios)
    groups: dict[int, GroupDef]  # each group as this trade runs it
    stops: dict[int, dict] = field(default_factory=dict)  # group -> a moved stop
    targets: dict[int, Decimal] = field(default_factory=dict)  # group -> a moved target
    done: set[tuple[int, str]] = field(default_factory=set)  # (group, action sig)
    blocked: set[int] = field(default_factory=set)  # groups whose profit actions stopped
    stop_off: set[int] = field(default_factory=set)  # groups whose stop you switched off
    target_off: set[int] = field(default_factory=set)  # groups whose target you switched off
    own_watch: object = None  # the trade's own watch rules (watch.TradeWatch), if any
    own_timed: object = None  # the trade's own timed close (overrides.TradeTimed), if any
    rule: str | None = None  # a watch rule that fired: its name, closing what's left

    def open_groups(self) -> list[int]:
        return [p for p, n in self.held.items() if n > 0]


def _load(deps, bot_id: int | None = None) -> list[_Paper]:
    runs = open_runs(deps.session_factory, bot_id)
    bots: dict[int, BotDef | None] = {}
    out = []
    if not runs:
        return out
    own_watch = bot_trade_settings.all_watch(deps.session_factory)
    own_timed = bot_trade_settings.all_timed(deps.session_factory)
    with deps.session_factory() as session:
        rows = _fills_of(session, [r.id for r in runs])
        for run in runs:
            if not run.legs or (run.filled_at is None and bot_id is None):
                continue
            if run.bot_id not in bots:
                bots[run.bot_id] = bot_store.load(deps.session_factory, run.bot_id)
            bot = bots[run.bot_id]
            if bot is None or (bot.live and bot_id is None):
                continue  # a Live bot's paper trades are over (going Live closes them)
            fills = {
                f["symbol"]: Decimal(f["fill_price"])
                for f in run.filled_legs or ()
                if f.get("fill_price") is not None
            }
            paper = _Paper(
                run.id, bot, list(run.legs), fills, run.contracts,
                run.filled_at or run.created_at,
                run.fill_credit, run.entry_spot, _held(run, rows.get(run.id, [])),
                {g.position: bot_exits.effective_group(session, run.id, bot, g.position)
                 for g in bot.groups},
            )  # fmt: skip
            for row in session.scalars(
                select(BotTradeOverride).where(BotTradeOverride.run_id == run.id)
            ):
                if row.stop_off:
                    paper.stop_off.add(row.group_position)
                if row.target_off:
                    paper.target_off.add(row.group_position)
            paper.own_watch, paper.own_timed = own_watch.get(run.id), own_timed.get(run.id)
            for row in rows.get(run.id, []):
                if row.kind != ACTION or not row.legs:
                    continue
                said = row.legs[0]
                paper.done.add((row.group, said.get("sig")))
                if said.get("blocked"):
                    paper.blocked.add(row.group)
                if said.get("stop"):
                    paper.stops[row.group] = said["stop"]
                if said.get("target") is not None:
                    paper.targets[row.group] = Decimal(said["target"])
                if said.get("rule"):
                    paper.rule = said["rule"]
            out.append(paper)
    return out


def watch_pass(deps, now: datetime | None = None) -> None:
    """One pass over every open paper trade (the bot-watch loop, every 2 s). In the
    regular session its exits are read; once a session has closed, legs past their
    expiry settle at intrinsic. A gap in watching longer than LAPSE_AFTER is said once,
    and nothing is filled for it: a rule counts its passes again from after it. Never
    raises."""
    global _last
    try:
        now = now or deps.now()
        papers = _load(deps)
        for paper in papers:
            try:
                _settle_expired(deps, paper, now)
            except Exception:  # noqa: BLE001 -- one trade never stops the others
                logger.exception("couldn't settle paper run %s", paper.id)
        local = now.astimezone(MARKET_TZ)
        papers = [p for p in papers if p.open_groups()]
        if session_now("SPY", local) != "regular" or not papers:
            with _lock:
                _streaks.clear()
            return
        _check_lapse(deps, papers, now)
        _last = now
        opening = watch.in_opening_pause(local)
        for paper in papers:
            try:
                _watch_run(deps, paper, now, opening)
            except Exception:  # noqa: BLE001 -- one trade never stops the others
                logger.exception("couldn't watch paper run %s", paper.id)
        ids = {p.id for p in papers}
        with _lock:
            for key in [k for k in _streaks if k[0] not in ids]:
                del _streaks[key]
    except Exception:  # noqa: BLE001 -- the loop goes on
        logger.exception("the paper watch pass failed")


def _check_lapse(deps, papers: list[_Paper], now: datetime) -> None:
    """watched_stops.lapse_since over the paper trades: said once per gap, per bot, and
    every rule's count starts again (a price seen before the gap never confirms one after)."""
    global _last
    local = now.astimezone(MARKET_TZ)
    opened = datetime.combine(local.date(), watch.SESSION_OPEN, MARKET_TZ)
    since = watched_stops.lapse_since(_last, now, opened, min(p.filled_at for p in papers))
    if since is None:
        return
    with _lock:
        _streaks.clear()
    _last = now
    start = since.astimezone(MARKET_TZ).strftime("%H:%M")
    end = local.strftime("%H:%M")
    for bot_id, paper in {p.bot.id: p for p in papers}.items():
        bot_entry.alert(
            deps, f"Paper: {paper.bot.name} wasn't watched",
            f"The app didn't watch this bot's paper trades from {start} to {end} New York "
            "(it was off, asleep or busy). Nothing was filled for that time: a price that "
            "went past an exit and came back then wasn't acted on.",
            f"/automation/{bot_id}/runs", run_id=paper.id, routine=True,
        )  # fmt: skip


def _confirmed(key: tuple, fired: bool, opening: bool = False, rule: str | None = None,
               now: datetime | None = None) -> bool:  # fmt: skip
    """Whether a rule has now held CONFIRM_PASSES passes in a row (none count in the
    session's first minute for the rules that pause then). A watch rule (`rule`, read at
    `now`) confirms as the live path's does (watch.confirmed: a delta or S/L ratio rule
    must also hold 30 s, #201)."""
    with _lock:
        if fired and not opening:
            passes, since = _streaks.get(key, (0, now))
            _streaks[key] = (passes + 1, since)
            if rule is not None and now is not None and since is not None:
                return watch.confirmed(rule, passes + 1, since, now)
            return passes + 1 >= watched_stops.CONFIRM_PASSES
        _streaks.pop(key, None)
        return False


def _done(key: tuple) -> None:
    with _lock:
        _streaks.pop(key, None)


def _quotes(deps, symbols) -> dict:
    deps.hub.subscribe(set(symbols))
    return {symbol: deps.hub.latest(symbol) for symbol in symbols}


def _own(paper: _Paper, position: int):
    return exit_math.group_legs(paper.legs, position)


def _plan(paper: _Paper, position: int):
    """The group's exit plan on this fill, as place_protections reads it."""
    group = paper.groups.get(position)
    if group is None:
        return None
    whole = [exit_math.GroupLeg(leg["symbol"], Side(leg["side"]), int(leg.get("ratio", 1)))
             for leg in paper.legs]  # fmt: skip
    credit = exit_plan.credit_fill(paper.entry_credit, exit_math.group_entry(whole, paper.fills))
    own, _ = _own(paper, position)
    return exit_plan.plan_group(group, (leg.side for leg in own), credit, paper.bot.broker,
                                paper.bot.stop_co_mode)  # fmt: skip


def _close(
    deps, paper: _Paper, position: int, contracts: int, reason: str, now, last_price: bool = False
) -> bool:
    """A pretend close of `contracts` of the group at its mid now; False (nothing written)
    while a leg has no fresh, usable quote. With `last_price` (going Live ends the trade
    now), a stale quote's last mid is used instead, or the entry fill where the stream
    never gave one, and the reason says so."""
    own, _ = _own(paper, position)
    quotes = _quotes(deps, [leg.symbol for leg in own])
    fresh = all(watched_stops.quote_fresh(quotes[leg.symbol], now, paper.filled_at, True)
                for leg in own)  # fmt: skip
    if not fresh and not last_price:
        return False
    value = exit_math.group_value(own, quotes)
    if value is None and not last_price:
        return False
    used = []
    price = Decimal(0)
    if value is not None:
        price = -value  # per unit of the group (as live counts), signed like a fill: + a credit
        for leg in own:
            q = quotes[leg.symbol]
            mid = exit_math.leg_mid(q.bid, q.ask, leg.side is Side.SELL)
            used.append({"symbol": leg.symbol, "side": leg.side.value, "ratio": leg.ratio,
                         "mid": f"{mid:f}"})  # fmt: skip
    else:
        # No price ever seen (only when `last_price`): closed where it opened, no gain or
        # loss, or at 0 where even that is unknowable (a leg with no recorded fill)
        entry = exit_math.group_entry(own, paper.fills)
        price = -entry if entry is not None else Decimal(0)
        used = [{"symbol": leg.symbol, "side": leg.side.value, "ratio": leg.ratio,
                 "mid": f"{paper.fills.get(leg.symbol, Decimal(0)):f}"} for leg in own]  # fmt: skip
    if not fresh:
        reason = f"{reason} (last price)"
    with deps.session_factory() as session:
        # Held now, as the other pass (the watcher, or a switch to Live) may have closed it
        rows = session.scalars(select(PaperFill).where(PaperFill.run_id == paper.id))
        held = caps.paper_held(paper.legs, paper.contracts,
                               ((r.kind, r.group, r.contracts) for r in rows))  # fmt: skip
        paper.held[position] = held.get(position, 0)
        contracts = min(contracts, paper.held[position])
        if contracts <= 0:
            return False
        session.add(PaperFill(
            run_id=paper.id, kind=EXIT, group=position, price=price, contracts=contracts,
            reason=reason[:60], legs=used, at=now,
        ))  # fmt: skip
        session.commit()
    paper.held[position] -= contracts
    _alert_fill(deps, paper, position, reason, price, contracts)
    return True


def _alert_fill(deps, paper: _Paper, position: int, reason: str, price: Decimal, n: int) -> None:
    name = _group_name(paper, position)
    left = paper.held[position]
    bot_entry.alert(
        deps, f"Paper: {paper.bot.name} {reason} at {abs(price):f} (estimated at mid)",
        f"{name}: {n} contract(s) closed, {left} open. Paper trades are pretend: nothing was "
        "sent to your broker.",
        f"/automation/{paper.bot.id}/runs", run_id=paper.id, routine=True,
    )  # fmt: skip


def _group_name(paper: _Paper, position: int) -> str:
    return next((g.name for g in paper.bot.groups if g.position == position),
                f"Group {position + 1}")  # fmt: skip


def _record(deps, paper: _Paper, position: int | None, reason: str, now, **said) -> None:
    """A profit or time action that ran on the group, or a watch rule that fired on the
    trade (group None), as kind "action" with 0 contracts: its sig, so it never runs
    twice, any stop or target it moved, and a rule's name to close the rest by."""
    with deps.session_factory() as session:
        session.add(PaperFill(run_id=paper.id, kind=ACTION, group=position, price=Decimal(0),
                              contracts=0, reason=reason[:60], legs=[said], at=now))  # fmt: skip
        session.commit()
    paper.done.add((position, said.get("sig")))
    if said.get("blocked"):
        paper.blocked.add(position)
    if said.get("stop"):
        paper.stops[position] = said["stop"]
    if said.get("target") is not None:
        paper.targets[position] = Decimal(said["target"])
    if said.get("rule"):
        paper.rule = said["rule"]


def _watch_run(deps, paper: _Paper, now, opening: bool) -> None:
    if _watch_rules(deps, paper, now, opening):
        return
    _timed(deps, paper, now)
    _time_actions(deps, paper, now)
    for position in paper.open_groups():
        if paper.held[position] <= 0:
            continue
        if _stop(deps, paper, position, now) or _target(deps, paper, position, now):
            continue
        _profit(deps, paper, position, now, opening)


# --- watch rules ------------------------------------------------------------------------


def _watch_rules(deps, paper: _Paper, now, opening: bool) -> bool:
    """The bot's watch rules over the open groups (bot_watch's own reading of the stream):
    a rule held 2 passes closes every open group. True once it has."""
    sides = paper.open_groups()
    if paper.rule is not None:  # fired before: what's left closes as each gets a mid
        for position in sides:
            _close(deps, paper, position, paper.held[position], paper.rule, now)
        return True
    own = paper.own_watch
    rules = watch.rules_for(paper.bot, own)
    if rules is None:
        return False
    outcomes = bot_watch.evaluate_rules(deps, paper.bot, paper.legs, sides, rules, own is not None,
                                   now, paper.filled_at, paper.entry_spot)  # fmt: skip
    hit = None
    for outcome in outcomes:
        if _confirmed((paper.id, "rule", outcome.rule), outcome.fired, opening, outcome.rule,
                      now) and hit is None:  # fmt: skip
            hit = outcome
    if hit is None:
        return False
    _done((paper.id, "rule", hit.rule))
    # Remembered first (a 0-contract "action" row), so a group with no mid yet keeps
    # closing for this rule on later passes, never read against the rules again.
    _record(deps, paper, None, hit.name, now, sig=f"rule:{hit.rule}", rule=hit.name)
    for position in sides:
        _close(deps, paper, position, paper.held[position], hit.name, now)
    return True


# --- stops and targets ------------------------------------------------------------------


def _stop_level(paper: _Paper, position: int) -> tuple[Decimal, bool] | None:
    """(the stop in force, on the short?): a moved one, else the plan's on this fill
    (exit_plan.stop_for, as place_protections works it out). None: no usable stop."""
    moved = paper.stops.get(position)
    if moved:
        return Decimal(moved["trigger"]), moved["on"] == "short"
    plan = _plan(paper, position)
    if plan is None:
        return None
    own, _ = _own(paper, position)
    calc = exit_plan.stop_for(plan, own, paper.fills, paper.entry_credit, paper.bot.stop_co_mode,
                              paper.bot.stop_co_amount)  # fmt: skip
    if calc is None or calc.problem is not None or calc.trigger is None:
        return None
    return calc.trigger, plan.on_short


def _anchor(paper: _Paper, position: int, on_short: bool):
    own, _ = _own(paper, position)
    return exit_math.anchor_of(own) if on_short else None


def _stop(deps, paper: _Paper, position: int, now) -> bool:
    """The group's stop, every one watched here: on its short's mid (watched_stops.evaluate)
    or on the group's P&L (evaluate_group). True once it has closed the group."""
    level = None if position in paper.stop_off else _stop_level(paper, position)
    if level is None:
        return False
    trigger, on_short = level
    own, _ = _own(paper, position)
    quotes = _quotes(deps, [leg.symbol for leg in own])
    label = _group_name(paper, position)
    anchor = _anchor(paper, position, on_short)
    if anchor is not None:
        q = quotes[anchor.symbol]
        reading = watched_stops.evaluate(
            label, getattr(q, "bid", None), getattr(q, "ask", None), getattr(q, "at", None),
            now, paper.filled_at, trigger, quiet_ok=True,
        )  # fmt: skip
    else:
        entry = exit_math.group_entry(own, paper.fills)
        reading = watched_stops.evaluate_group(
            label, [(leg, quotes[leg.symbol]) for leg in own], entry, trigger, now,
            paper.filled_at, quiet_ok=True,
        )  # fmt: skip
    key = (paper.id, STOP, position)
    if not _confirmed(key, reading.fired):
        return False
    if not _close(deps, paper, position, paper.held[position], STOP, now):
        return False  # no mid yet: it fires on the next pass still past the stop
    _done(key)
    return True


def _target_level(paper: _Paper, position: int) -> Decimal | None:
    if position in paper.targets:
        return paper.targets[position]
    plan = _plan(paper, position)
    if plan is None:
        return None
    own, _ = _own(paper, position)
    limit, problem = exit_plan.target_for(plan, own, paper.fills)
    return None if problem is not None else limit


def _target(deps, paper: _Paper, position: int, now) -> bool:
    """The group's profit target, resting or not, armed here as the live watch pass arms
    one (bot_exits.watch_pass): the group's natural value at or past its close-at on 2
    fresh passes. It fills at the mid."""
    limit = None if position in paper.target_off else _target_level(paper, position)
    if limit is None:
        return False
    own, _ = _own(paper, position)
    quotes = _quotes(deps, [leg.symbol for leg in own])
    fresh = all(watched_stops.quote_fresh(quotes[leg.symbol], now, paper.filled_at, True)
                for leg in own)  # fmt: skip
    value = exit_math.group_value(own, quotes, natural=True) if fresh else None
    key = (paper.id, TARGET, position)
    if not _confirmed(key, value is not None and value <= limit):
        return False
    if not _close(deps, paper, position, paper.held[position], TARGET, now):
        return False
    _done(key)
    return True


# --- profit actions ---------------------------------------------------------------------


def _views(deps, paper: _Paper, position: int, now):
    _, gcd = _own(paper, position)
    return [(bot_watch.leg_view(deps, leg, now, paper.filled_at, underlying=paper.bot.underlying),
             int(leg.get("ratio", 1)) // gcd)
            for leg in paper.legs if leg["group"] == position]  # fmt: skip


def _fresh_ask(deps, paper: _Paper, symbol: str, now) -> Decimal | None:
    quote = _quotes(deps, [symbol])[symbol]
    if not watched_stops.quote_fresh(quote, now, paper.filled_at, True):
        return None
    return quote.ask if quote.ask is not None and quote.ask > 0 else None


def _profit(deps, paper: _Paper, position: int, now, opening: bool) -> None:
    """The group's next profit action and next loss action (bot_watch._profit's choice),
    on the group's P&L at the mids; one fires a pass once it has held 2 passes."""
    group = paper.groups.get(position)
    if group is None or not (group.use_profit_actions and group.profit_actions):
        return
    if position in paper.blocked:
        return
    own, _ = _own(paper, position)
    entry = exit_math.group_entry(own, paper.fills)
    if entry is None:
        return
    numbers = [
        next((n for n, a in enumerate(group.profit_actions, 1)
              if (a.at < 0) == loss and (position, a.sig) not in paper.done), None)
        for loss in (False, True)
    ]  # fmt: skip
    views = _views(deps, paper, position, now)
    for number in [n for n in numbers if n is not None]:
        action = group.profit_actions[number - 1]
        outcome = watch.profit_outcome(group, action, number, entry, views)
        key = (paper.id, "profit", position, number)
        if _confirmed(key, outcome.fired, opening):
            if _fire_profit(deps, paper, group, number, action, outcome, entry, views, now):
                _done(key)
            return  # one action a pass on a group


def _fire_profit(deps, paper, group, number, action, outcome, entry, views, now) -> bool:
    """Close part of the group and/or move its stop (scale_out.execute_action), counted in
    the trade's contracts. False (nothing written) while there's no mid to close at."""
    position, why = group.position, f"profit action {number}"
    held = paper.held[position]
    said = {"sig": action.sig, "number": number, "detail": outcome.detail}
    n = watch.contracts_for(paper.contracts * _own(paper, position)[1], action.close_pct)
    if n > 0 and held - n < 1:
        note = (f"skipped: closing {n} of the {held} open would leave nothing for the profit "
                "target, so this and any later actions on the side won't run")  # fmt: skip
        _record(deps, paper, position, why, now, **said, blocked=True, note=note)
        bot_entry.alert(
            deps, f"Paper: {paper.bot.name} {why} skipped",
            f"{_group_name(paper, position)}: {outcome.detail}. It was {note}.",
            f"/automation/{paper.bot.id}/runs", run_id=paper.id, routine=True,
        )  # fmt: skip
        return True
    if n > 0 and not _close(deps, paper, position, n, why, now):
        return False
    if action.moves_stop:
        said |= _profit_move(deps, paper, position, entry, action, why, views, now)
    _record(deps, paper, position, why, now, **said)
    if n == 0:
        bot_entry.alert(
            deps, f"Paper: {paper.bot.name} {why}",
            f"{_group_name(paper, position)}: {outcome.detail}. {said.get('note', '')}".strip(),
            f"/automation/{paper.bot.id}/runs", run_id=paper.id, routine=True,
        )  # fmt: skip
    return True


def _profit_move(deps, paper, position, entry, action, why, views, now) -> dict:
    """A profit action's new stop (stops.profit_move), applied unless
    bot_exits.level_problem refuses it (not tighter, or it would fire at once)."""
    level = _stop_level(paper, position)
    old, on_short = level if level is not None else (None, _plan_on_short(paper, position))
    anchor = _anchor(paper, position, on_short)
    if anchor is None:
        calc = stops.profit_move(entry, action.stop_pct, why, usd=action.stop_usd)
        pnl, _ = watch.group_pnl_now(entry, views)
        problem = bot_exits.level_problem(calc, old, pnl=pnl)
    else:
        fill = paper.fills.get(anchor.symbol)
        if fill is None:
            return {"note": "stop not moved: the short has no fill price"}
        calc = stops.profit_move(
            entry, action.stop_pct, why, anchor=anchor.symbol, anchor_fill=fill,
            ratio=anchor.ratio, usd=action.stop_usd,
        )  # fmt: skip
        ask = _fresh_ask(deps, paper, anchor.symbol, now)
        problem = bot_exits.level_problem(calc, old, ask=ask)
    if problem is not None:
        return {"note": f"stop not moved: {problem}"}
    on = "short" if anchor is not None else "pnl"
    return {"stop": {"trigger": f"{calc.trigger:f}", "on": on},
            "note": f"stop moved to {calc.trigger}"}  # fmt: skip


def _plan_on_short(paper: _Paper, position: int) -> bool:
    plan = _plan(paper, position)
    return bool(plan is not None and plan.on_short)


# --- timed close and time actions ---------------------------------------------------------


def _expirations(paper: _Paper, today) -> dict:
    return {p: e for p, e in bot_timed.group_expirations(paper.legs).items() if e >= today}


def _timed(deps, paper: _Paper, now) -> None:
    """The timed close (with its close DTE) as bot_timed reads it: each open group due
    now closes at the mid; while it has no mid, it waits for the next pass."""
    close_time, close_dte, _ = overrides.effective_timed(paper.bot, paper.own_timed)
    if close_time is None:
        return
    local = naive_et(now)
    for position, expiration in _expirations(paper, local.date()).items():
        if paper.held.get(position, 0) <= 0:
            continue
        if timing.timed_close_due(close_time, close_dte, expiration, local) == "due":
            _close(deps, paper, position, paper.held[position], TIMED, now)


def _time_actions(deps, paper: _Paper, now) -> None:
    """Each due time action once per group (bot_timed.actions_pass, timing.action_due):
    its stop and target moved, its close % closed at the mid. One that needs a price it
    doesn't have yet waits for the next pass."""
    if not (paper.bot.use_time_actions and paper.bot.time_actions):
        return
    local = naive_et(now)
    fill_day = paper.filled_at.astimezone(MARKET_TZ).date()
    expirations = _expirations(paper, local.date())
    for number, action in enumerate(paper.bot.time_actions, 1):
        for position, expiration in expirations.items():
            if paper.held.get(position, 0) <= 0 or (position, action.sig) in paper.done:
                continue
            due, _ = timing.action_due(action, expiration, fill_day, local)
            if due:
                _apply_time_action(deps, paper, position, action, number, now)


def _apply_time_action(deps, paper: _Paper, position: int, action, number: int, now) -> None:
    why = f"time action {number}"
    own, _ = _own(paper, position)
    entry = exit_math.group_entry(own, paper.fills)
    said: dict = {"sig": action.sig, "number": number}
    notes = []
    if action.stop_pct is not None:
        level = _stop_level(paper, position)
        on_short = level[1] if level is not None else _plan_on_short(paper, position)
        anchor = _anchor(paper, position, on_short)
        fill = paper.fills.get(anchor.symbol) if anchor is not None else None
        if entry is None or (anchor is not None and fill is None):
            notes.append("stop not moved: the group has no fill prices")
        else:
            calc = stops.action_move(
                paper.groups.get(position), action.stop_pct, entry, paper.entry_credit, why,
                anchor=anchor.symbol if anchor else None, anchor_fill=fill,
                ratio=anchor.ratio if anchor else 1,
            )  # fmt: skip
            if anchor is None:
                pnl, _ = watch.group_pnl_now(entry, _views(deps, paper, position, now))
                if pnl is None:
                    return  # waiting for a price: tried again next pass
                problem = bot_exits.level_problem(calc, None, pnl=pnl)
            else:
                ask = _fresh_ask(deps, paper, anchor.symbol, now)
                if ask is None:
                    return
                problem = bot_exits.level_problem(calc, None, ask=ask)
            if problem is not None:
                notes.append(f"stop not moved: {problem}")
            else:
                said["stop"] = {"trigger": f"{calc.trigger:f}",
                                "on": "short" if anchor is not None else "pnl"}  # fmt: skip
                notes.append(f"stop moved to {calc.trigger}")
    if action.target_pct is not None:
        if entry is None:
            notes.append("target not moved: the group has no fill prices")
        else:
            limit, problem = stops.target_limit(GroupDef(0, "", target_pct=action.target_pct),
                                                entry)  # fmt: skip
            if problem is not None:
                notes.append(f"target not moved: {problem}")
            else:
                said["target"] = f"{limit:f}"
                notes.append(f"target moved to {limit}")
    original = paper.contracts * _own(paper, position)[1]  # in units, as bot_exits
    n = watch.close_contracts(action.close_pct, original, paper.held[position])
    if n > 0 and not _close(deps, paper, position, n, why, now):
        return  # no mid yet: nothing recorded, tried again next pass
    _record(deps, paper, position, why, now, **said, notes=notes)
    if n == 0:
        bot_entry.alert(
            deps, f"Paper: {paper.bot.name} {why}",
            f"{_group_name(paper, position)}: {action.label}. {'; '.join(notes)}.",
            f"/automation/{paper.bot.id}/runs", run_id=paper.id, routine=True,
        )  # fmt: skip


# --- expiry -------------------------------------------------------------------------------


def _option(symbol: str):
    """The option a leg names, OCC or futures (as exit_math.after_expiry reads it)."""
    parsed = parse_occ(symbol)
    return parsed[1] if parsed is not None else futures.option_of(symbol)


def _intrinsic(symbol: str, spot: Decimal) -> Decimal | None:
    option = _option(symbol)
    if option is None:
        return None
    if option.put_call == "PUT":
        return max(option.strike - spot, Decimal(0))
    return max(spot - option.strike, Decimal(0))


def _settle_expired(deps, paper: _Paper, now) -> None:
    """Once the session has closed on a leg's expiration day (exit_math.after_expiry), each
    open group holding it settles: an expired leg at its intrinsic value from the last
    underlying price the stream gave that day, any later leg at its last mid. Without
    such a price it waits, said once."""
    for position in paper.open_groups():
        own, _ = _own(paper, position)
        expired = [leg for leg in own if exit_math.after_expiry(leg.symbol, now)]
        if not expired:
            continue
        options = [o for o in (_option(leg.symbol) for leg in expired) if o is not None]
        if not options:
            continue  # unreadable symbols: never settled on a guess
        day = min(o.expiration for o in options)
        # A futures option settles on its own contract's price, read where the stream
        # publishes it: "dx:" + its DXLink symbol from the nested chain (as the analyzer's
        # futures_last), in its own points
        contract = futures.contract_of(expired[0].symbol)
        if contract:
            nested = deps.chain.futures_nested(deps.clients(), chain_root(contract))
            stream = futures.contract_stream(nested, contract)
            key = DXLINK_KEY + stream if stream else contract
        else:
            key = underlyings.watch_symbol(paper.bot.underlying or "SPX")
        deps.hub.subscribe({key})
        quote = deps.hub.latest(key)
        seen = getattr(quote, "last_at", None) or getattr(quote, "at", None)
        last = getattr(quote, "last", None)
        if last is None or seen is None or seen.astimezone(MARKET_TZ).date() != day:
            if _first_time(paper.id, "no spot", position):
                bot_entry.alert(
                    deps, f"Paper: {paper.bot.name} can't settle at expiry",
                    f"{_group_name(paper, position)} expired, but the app has no "
                    f"{key} price from that day to settle it at. It stays open in the paper "
                    "journal.", f"/automation/{paper.bot.id}/runs", run_id=paper.id,
                )  # fmt: skip
            continue
        spot = last if contract else underlyings.in_points(paper.bot.underlying, last)
        values, used = {}, []
        for leg in own:
            if leg in expired:
                worth = _intrinsic(leg.symbol, spot)
            else:
                q = deps.hub.latest(leg.symbol)
                fresh = watched_stops.quote_fresh(q, now, paper.filled_at, True)
                worth = exit_math.leg_mid(q.bid, q.ask, leg.side is Side.SELL) if fresh else None
            if worth is None:
                break
            values[leg.symbol] = worth
            used.append({"symbol": leg.symbol, "side": leg.side.value, "ratio": leg.ratio,
                         "value": f"{worth:f}"})  # fmt: skip
        else:
            value = sum(
                (values[leg.symbol] * leg.ratio * (1 if leg.side is Side.SELL else -1)
                 for leg in own),
                Decimal(0),
            )  # fmt: skip
            price = -value  # per unit, as _close
            n = paper.held[position]
            with deps.session_factory() as session:
                session.add(PaperFill(run_id=paper.id, kind=EXPIRY, group=position, price=price,
                                      contracts=n, reason=EXPIRED, legs=used, at=now))  # fmt: skip
                session.commit()
            paper.held[position] = 0
            bot_entry.alert(
                deps, f"Paper: {paper.bot.name} expired at {abs(price):f}",
                f"{_group_name(paper, position)}: {n} contract(s) settled at intrinsic value, "
                f"{paper.bot.underlying or 'SPX'} at {spot:f}. Paper trades are pretend.",
                f"/automation/{paper.bot.id}/runs", run_id=paper.id, routine=True,
            )  # fmt: skip


def end_bot(deps, bot_id: int, reason: str, now) -> int:
    """Closes every open group of the bot's open paper trades: at the mid, else at the last
    price seen, else where it opened (the reason then says "last price"). Always ends the
    trades; returns how many it closed."""
    closed = 0
    for paper in _load(deps, bot_id):
        for position in paper.open_groups():
            _close(deps, paper, position, paper.held[position], reason, now, last_price=True)
        closed += not paper.open_groups()
    return closed
