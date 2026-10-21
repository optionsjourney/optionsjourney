"""The watch pass (bots slice 7; Tracker2's watch_loop.py and scale_out.py): every 2 s
in the regular session, read each open bot trade's prices fresh and act on its watch
rules and its sides' profit actions.

- **Watch rules** close the whole trade (watch_loop._fire): each open side through
  slice 6's close (bot_exits.fire_close: one claim per side, its stop and target
  pulled, chased from the mid, what's left protected again), once per trade.
- **Profit actions** (scale_out.execute_action), per side, lowest-numbered first:
  close part of the side (fire_close) and/or move its stop (slice 4's _move_stop:
  the live-ask check before any cancel, the marker and its recovery).

Fail safe: a rule or action whose prices are stale or missing never fires; a run
that stays that way for 60 s alerts once ("watch rules paused") and once when it
resumes. Nothing here touches a stop unless a rule fires. A breach must hold on 2
passes in a row, and a delta or S/L ratio breach for 30 s too (#201; in memory: a
restart only delays a fire). Nothing runs
under Freeze or outside the session, or for a preview run. Bots slice 18 (the author's
ruling, as Tracker2's watch_rules and scale_out read no stop_loss_off/profit_target_off):
a side with its stop or target switched off is watched and closed like any other, and a
profit action's stop move turns a switched-off stop back on (bot_exits.revive_stop).
Never raises."""

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from optionsjourney.bots import caps, exit_math, timing, underlyings, watch
from optionsjourney.bots.definition import MAX_DTE, BotDef, GroupDef, ProfitActionDef
from optionsjourney.core.market import MARKET_TZ
from optionsjourney.core.sessions import session_now
from optionsjourney.db.models import BotExit, BotRun
from optionsjourney.services import bot_entry, bot_exits, bot_store, bot_trade_settings

logger = logging.getLogger(__name__)
# Runs older than this can't still be open: a leg is at most MAX_DTE days out (slice 14)
LOOKBACK = timedelta(days=MAX_DTE + 10)
PROFIT = "profit:"  # the profit actions' rule names start so; the whole-trade rules' don't

_lock = threading.Lock()
# (run, rule) -> (passes in a row it held, the first of those passes' time)
_streaks: dict[tuple[int, str], tuple[int, datetime]] = {}
_stale_since: dict[int, datetime] = {}  # run -> the first pass a rule couldn't be read
_stale_alerted: dict[int, str] = {}  # run -> the rules its "paused" alert named
_once: set[tuple] = set()  # alerts said once: (run, what, ...)


def reset() -> None:
    """Forget every streak, stale clock and once-only alert (tests)."""
    with _lock:
        _streaks.clear()
        _stale_since.clear()
        _stale_alerted.clear()
        _once.clear()


def _forget(run_id: int | None = None, keep=lambda rule: False) -> None:
    """Drop streaks (of one run, or all) but those `keep` names, so a breach before a
    gap never combines with one after it (watch_loop._clear_confirm)."""
    with _lock:
        for key in list(_streaks):
            if (run_id is None or key[0] == run_id) and not keep(key[1]):
                del _streaks[key]


def _confirmed(run_id: int, outcomes, now: datetime, opening: bool = False
               ) -> watch.Outcome | None:  # fmt: skip
    """The first rule that now confirms (_confirmed, _held; watch.confirmed): held
    CONFIRM_PASSES passes running, and a delta or S/L ratio rule HOLD (30 s) since its
    first (#201). In the session's first minute (`opening`, guards.in_opening_pause)
    nothing counts as fired, so a streak starts only after it."""
    hit = None
    with _lock:
        for o in outcomes:
            key = (run_id, o.rule)
            if o.fired and not opening:
                passes, since = _streaks.get(key, (0, now))
                _streaks[key] = (passes + 1, since)
                if hit is None and watch.confirmed(o.rule, passes + 1, since, now):
                    hit = o
            else:
                _streaks.pop(key, None)
        if hit is not None:
            _streaks.pop((run_id, hit.rule), None)
    return hit


def _first_time(*key) -> bool:
    with _lock:
        if key in _once:
            return False
        _once.add(key)
        return True


@dataclass
class _Trade:
    """One open bot trade, as the pass needs it."""

    run_id: int
    bot: BotDef
    legs: list
    fills: dict
    filled_at: datetime
    entry_spot: Decimal | None
    states: dict[int, str]  # side -> "open", "busy" or "closed"
    off: set[int]
    fired: bool  # a watch rule has fired on this trade
    done: set[tuple[int, str]] = field(default_factory=set)  # (side, profit action sig)
    blocked: set[int] = field(default_factory=set)  # sides whose actions plan() stopped
    entries: dict[int, Decimal | None] = field(default_factory=dict)  # group -> entry/unit
    # Bots slice 21: each group as this trade runs it (its own profit actions laid over the
    # bot's, overrides.effective_group), and the watch rules in force (watch.rules_for:
    # the trade's own delta and ratio rules, when it has them, with the bot's underlying
    # ones); `own_rules`: a leg delta is the trade's leg by its position
    groups: dict[int, GroupDef] = field(default_factory=dict)
    rules: watch.Rules | None = None
    own_rules: bool = False

    def profit_sides(self) -> list[GroupDef]:
        return _profit_sides(self.groups.values())


def watch_pass(deps) -> None:
    """Every 2 s: each open trade's watch rules and profit actions. Never raises."""
    try:
        now = deps.now()
        if bot_exits._frozen(deps) or session_now("SPY", now.astimezone(MARKET_TZ)) != "regular":
            _forget()
            with _lock:  # _reset_run_state: a new episode starts afresh
                _stale_since.clear()
                _stale_alerted.clear()
            return
        trades = _trades(deps, now)
    except Exception:  # noqa: BLE001 -- the next pass tries again
        logger.exception("the watch pass couldn't read the open trades")
        return
    watched = set()
    for trade in trades:
        watched.add(trade.run_id)
        try:
            _watch(deps, trade, now, watch.in_opening_pause(now.astimezone(MARKET_TZ)))
        except Exception:  # noqa: BLE001 -- one trade never stops the others
            logger.exception("couldn't watch bot run %s", trade.run_id)
            _forget(trade.run_id)
    with _lock:  # a run no longer watched starts afresh if it ever comes back
        for key in [k for k in _streaks if k[0] not in watched]:
            del _streaks[key]
        for run_id in [r for r in _stale_since if r not in watched]:
            del _stale_since[run_id]
        for run_id in [r for r in _stale_alerted if r not in watched]:
            del _stale_alerted[run_id]


def _profit_sides(groups) -> list[GroupDef]:
    return [g for g in groups if g.use_profit_actions and g.profit_actions]


def trade_groups(session, run_id: int, bot: BotDef) -> dict[int, GroupDef]:
    """{position: the group as this trade runs it} (bots slice 21)."""
    return {
        g.position: bot_exits.effective_group(session, run_id, bot, g.position) for g in bot.groups
    }


def _trades(deps, now) -> list[_Trade]:
    """Live runs holding fills, not being entered, not past expiry, of a bot with a
    watch rule or profit actions on, with a side still open (watch_loop.watched_runs)."""
    since = (now.astimezone(MARKET_TZ) - LOOKBACK).date()
    with bot_entry._active_lock:
        active = set(bot_entry.ACTIVE)
    bots: dict[int, BotDef | None] = {}
    out = []
    own = bot_trade_settings.all_watch(deps.session_factory)
    with deps.session_factory() as session:
        runs = session.scalars(
            select(BotRun).where(
                BotRun.status.in_(caps.HOLDING), BotRun.live.is_(True),
                BotRun.trading_date >= since,
            )
        ).all()  # fmt: skip
        for run in runs:
            if not run.filled_legs or run.filled_at is None or run.id in active:
                continue
            if all(exit_math.after_expiry(leg.get("symbol", ""), now) for leg in run.legs):
                continue
            if run.bot_id not in bots:
                bots[run.bot_id] = bot_store.load(deps.session_factory, run.bot_id)
            bot = bots[run.bot_id]
            if bot is None:
                continue
            groups = trade_groups(session, run.id, bot)
            rules = watch.rules_for(bot, own.get(run.id))
            if rules is None and not _profit_sides(groups.values()):
                continue
            rows = list(session.scalars(select(BotExit).where(BotExit.run_id == run.id)))
            positions = sorted({leg["group"] for leg in run.legs})
            states = {
                p: timing.side_state((r.kind, r.state) for r in rows if r.group_position == p)
                for p in positions
            }
            for p in positions:
                # Slice 14, question 10: a group with an expired leg can't be priced whole;
                # what's left of it is yours (services/bot_settle.py).
                legs = [leg.get("symbol", "") for leg in run.legs if leg.get("group") == p]
                if any(exit_math.after_expiry(symbol, now) for symbol in legs):
                    states[p] = "closed"
            if all(state == "closed" for state in states.values()):
                continue
            done, blocked = profit_done(groups, rows)
            entries = {
                p: bot_exits.group_entry_of(run, exit_math.group_legs(run.legs, p)[0])
                for p in positions
            }
            out.append(_Trade(
                run.id, bot, list(run.legs), {f["symbol"]: f for f in run.filled_legs},
                run.filled_at, run.entry_spot, states,
                set(),  # bots slice 18: no side is left alone for its switches
                any(r.kind == "watch" for r in rows), done, blocked, entries, groups,
                rules, run.id in own,
            ))  # fmt: skip
    return out


def _action_sig(groups: dict[int, GroupDef], row) -> str | None:
    """The profit action a "profit" row ran, by what it says. A row written before
    actions were known by their sig is matched by its number in the group's list."""
    entry = row.log[0] if row.log else {}
    if entry.get("sig"):
        return entry["sig"]
    group = groups.get(row.group_position)
    number = entry.get("number")
    if group is None or not isinstance(number, int) or not 1 <= number <= len(group.profit_actions):
        return None
    return group.profit_actions[number - 1].sig


def profit_done(groups: dict[int, GroupDef], rows) -> tuple[set, set]:
    """((side, sig) of every profit action that ran, the sides plan() stopped)."""
    done, blocked = set(), set()
    for row in rows:
        if row.kind != "profit":
            continue
        sig = _action_sig(groups, row)
        if sig is not None:
            done.add((row.group_position, sig))
        if row.log and row.log[0].get("blocked"):
            blocked.add(row.group_position)
    return done, blocked


def _watch(deps, trade: _Trade, now, opening: bool = False) -> None:
    outcomes = []
    open_sides = [p for p, state in trade.states.items() if state != "closed"]
    rules = trade.rules
    if rules is not None and not trade.fired:
        if any(trade.states[p] == "busy" for p in open_sides):
            # A close is running (_not_active): the whole-trade rules wait and start
            # their count again after it; the profit actions keep theirs.
            _forget(trade.run_id, keep=lambda rule: rule.startswith(PROFIT))
        else:
            whole = evaluate_rules(deps, trade.bot, trade.legs, open_sides, rules, trade.own_rules,
                              now, trade.filled_at, trade.entry_spot)  # fmt: skip
            outcomes += whole
            _no_entry(deps, trade, whole)
            hit = _confirmed(trade.run_id, whole, now, opening)
            if hit is not None:
                _fire(deps, trade, hit)
    for group in trade.profit_sides():
        outcomes += _profit(deps, trade, group, now, opening)
    _track_stale(deps, trade, outcomes, now)


def evaluate_rules(deps, bot: BotDef, legs, open_sides, rules: watch.Rules, own: bool, now,
              filled_at, entry_spot) -> list[watch.Outcome]:  # fmt: skip
    """The whole-trade rules over the open groups' legs, read fresh from the stream. A
    leg delta is the bot's leg at that position (slice 19), or with the trade's own rules
    (`own`, slice 21) the trade's leg at that position (watch_loop.config_from_override:
    "Leg keys are the trade's selected_legs sort_orders")."""
    views = [
        leg_view(deps, leg, now, filled_at, _position(bot, leg, n, own), bot.underlying)
        for n, leg in enumerate(legs)
        if leg["group"] in open_sides
    ]
    spot = None
    if any(v is not None for v in (rules.up_pct, rules.down_pct, rules.put_pts, rules.call_pts)):
        spot = _spot(deps, now, filled_at, bot.underlying)
    return watch.evaluate(views, rules, spot, entry_spot)


def probe(deps, run_id: int, mine: watch.TradeWatch | None) -> tuple[list[str], list[str]]:
    """What a save of `mine` (None: the bot's rules) would arm right now (Tracker2's
    watch_overrides.probe_save / probe_revert, through the same evaluate the pass uses):
    (breach: rules already crossed on fresh data, unverified: rules the data can't check
    now). Reads the quote stream only; never sends anything."""
    now = deps.now()
    with deps.session_factory() as session:
        run = session.get(BotRun, run_id)
        if run is None or not run.filled_legs:
            return [], []
        bot = bot_store.load(deps.session_factory, run.bot_id)
        rows = list(session.scalars(select(BotExit).where(BotExit.run_id == run_id)))
        legs, filled_at, entry_spot = list(run.legs), run.filled_at, run.entry_spot
    if bot is None:
        return [], []
    rules = watch.rules_for(bot, mine)
    if rules is None:
        return [], []
    open_sides = []
    for p in sorted({leg["group"] for leg in legs}):
        state = timing.side_state((r.kind, r.state) for r in rows if r.group_position == p)
        mine_legs = [leg.get("symbol", "") for leg in legs if leg.get("group") == p]
        if state != "closed" and not any(exit_math.after_expiry(s, now) for s in mine_legs):
            open_sides.append(p)
    if not open_sides:
        return [], []
    outcomes = evaluate_rules(deps, bot, legs, open_sides, rules, mine is not None, now, filled_at,
                         entry_spot)  # fmt: skip
    breach = [o.detail for o in outcomes if o.fired]
    unverified = [o.detail for o in outcomes if not o.fired and o.skipped is not None]
    return breach, unverified


def forget_rules(run_id: int) -> None:
    """A trade's whole-trade streaks start again (watch_loop.clear_confirmation): one
    counted under the old rules never combines with the new ones."""
    _forget(run_id, keep=lambda rule: rule.startswith(PROFIT))


def _position(bot: BotDef, leg: dict, n: int, own: bool) -> int | None:
    return leg.get("position", n) if own else _bot_leg(bot, leg)


def _bot_leg(bot: BotDef, leg: dict) -> int | None:
    """The run leg's bot leg position, for a leg delta exit (bots slice 19), or None
    when the bot leg at that position isn't the same side and type (the bot was edited
    since; watch_loop.leg_rule_mismatches)."""
    position = leg.get("position")
    found = next((b for b in bot.legs if b.position == position), None)
    if found is None or str(found.side) != leg.get("side") or found.right != leg.get("right"):
        return None
    return position


def leg_view(deps, leg: dict, now, filled_at, position: int | None = None,
             underlying: str | None = None) -> watch.LegView:  # fmt: skip
    """One leg as the stream has it: its quote (fresh: newer than the fill and under 30 s
    old, or under exit_math.QUIET_MAX while the stream it came through is live, #202:
    bot_exits.stream_healthy on `underlying`, the bot's) and its delta (under 150 s and
    newer than the fill). `position`: the bot leg it is, for a leg delta exit (slice 19)."""
    symbol = leg["symbol"]
    quote = delta = None
    quiet_ok = False
    try:
        deps.hub.subscribe({symbol})
        quote = deps.hub.latest(symbol)
        reader = getattr(deps.hub, "delta", None)
        delta = reader(symbol) if reader is not None else None
        if quote is not None and underlying:
            key = underlyings.watch_symbol(underlying)
            deps.hub.subscribe({key})
            quiet_ok = bot_exits.stream_healthy(deps, quote.source, key, now)
    except Exception:  # noqa: BLE001 -- no data is the safe answer
        logger.exception("couldn't read %s for the watch rules", symbol)
    quote_fresh = quote is not None and exit_math.fresh(quote.at, now, filled_at, quiet_ok)
    delta_fresh = (
        delta is not None and now - delta[1] <= watch.GREEKS_STALE_AFTER and delta[1] > filled_at
    )
    strike = leg.get("strike")
    return watch.LegView(
        leg["side"], leg.get("right", ""), None if strike in (None, "") else Decimal(str(strike)),
        int(leg.get("ratio", 1)), quote.bid if quote else None, quote.ask if quote else None,
        delta[0] if delta else None, quote_fresh, delta_fresh, position,
    )  # fmt: skip


def _spot(deps, now, filled_at, underlying: str = "SPX") -> watch.SpotView:
    """The underlying's last trade in its own points, fresh by its own time
    (watch_loop.spot_view): XSP reads SPX ÷ 10 (underlyings.watch_symbol; slice 16)."""
    key, name = underlyings.watch_symbol(underlying), underlying.upper()
    try:
        deps.hub.subscribe({key})
        quote = deps.hub.latest(key)
    except Exception:  # noqa: BLE001 -- no price is the safe answer
        logger.exception("couldn't read %s for the watch rules", key)
        return watch.SpotView(None, False, name)
    if quote is None or quote.last is None:
        return watch.SpotView(None, False, name)
    when = getattr(quote, "last_at", None)
    fresh = when is not None and exit_math.fresh(when, now, filled_at)
    return watch.SpotView(underlyings.in_points(underlying, quote.last), fresh, name)


def _side_name(bot: BotDef, position: int) -> str:
    return next((g.name for g in bot.groups if g.position == position), f"side {position}")


def _alert(deps, bot: BotDef, title: str, detail: str, *, run_id=None, routine=False) -> None:
    bot_exits._alert(deps, f"{bot.name}: {title}", detail, bot.id, run_id=run_id,
                     routine=routine)  # fmt: skip


# --- Stale data --------------------------------------------------------------------


def _label(outcome: watch.Outcome) -> str:
    """The rule's name as its detail starts ("S/L ratio", "Put side profit action 1")."""
    return outcome.detail.split(":")[0]


def _track_stale(deps, trade: _Trade, outcomes, now) -> None:
    """One alert after 60 s of rules that can't be read, one when they're back
    (watch_loop._track_stale). Nothing is closed meanwhile; the stops stand. The alert
    names the stops still resting at the broker (#203, Tracker2 b57aab7c), and a WARNING
    logs each leg's quote and Greeks age and the streams' state (b81331a9)."""
    stale = [o for o in outcomes if o.skipped in watch.STALE_SKIPS]
    say = None
    with _lock:
        if stale:
            since = _stale_since.setdefault(trade.run_id, now)
            if now - since >= watch.STALE_ALERT_AFTER and trade.run_id not in _stale_alerted:
                names = ", ".join(dict.fromkeys(_label(o) for o in stale))
                secs = int((now - since).total_seconds())
                say = ("watch rules paused", (
                    f"No fresh prices for {secs} seconds, so these can't be checked: {names}. "
                    "Nothing is closed until the prices are back."
                ))  # fmt: skip
                if all(o.rule == "position_delta" or o.rule.startswith("leg_delta:")
                       for o in stale):  # fmt: skip
                    # Deltas come only from TastyTrade's stream: without it they never
                    # come, so this is said once a bot a New York day, not every episode.
                    day = now.astimezone(MARKET_TZ).date()
                    key = (trade.bot.id, "delta_paused", day)
                    if key in _once:
                        say = (None, say[1])  # logged, not said again
                    else:
                        _once.add(key)
                        more = (" Delta comes only from TastyTrade's quote stream: check "
                                "TastyTrade is connected. Said once a day.")  # fmt: skip
                        say = (say[0], say[1] + more)
                _stale_alerted[trade.run_id] = names if say[0] is not None else ""
        else:
            _stale_since.pop(trade.run_id, None)
            names = _stale_alerted.pop(trade.run_id, None)
            if names:
                say = ("watch rules resumed", f"Prices are back: {names} are being checked again.")
    if say is None:
        return
    if say[0] == "watch rules resumed":
        _alert(deps, trade.bot, *say, run_id=trade.run_id, routine=True)
        return
    _log_stale(deps, trade, stale, now)
    if say[0] is not None:
        _alert(deps, trade.bot, say[0], say[1] + _at_broker(deps, trade),
               run_id=trade.run_id)  # fmt: skip


def _log_stale(deps, trade: _Trade, stale, now) -> None:
    """The WARNING with a pause (watch_loop._log_stale_detail): which rules, each open
    leg's quote and Greeks age and source, and each stream's state."""
    open_sides = {p for p, state in trade.states.items() if state != "closed"}
    symbols = sorted({leg["symbol"] for leg in trade.legs if leg["group"] in open_sides})
    underlying = underlyings.watch_symbol(trade.bot.underlying)
    logger.warning("watch rules paused for run %s: %s | %s", trade.run_id,
                   "; ".join(o.detail for o in stale),
                   bot_exits.stale_detail(deps, symbols, underlying, now))  # fmt: skip


def _at_broker(deps, trade: _Trade) -> str:
    """Each stop still resting at the broker (watch_loop._broker_stops), " Your stops at
    TastyTrade still work: Call side 4.80.", or that none does. A read that breaks adds
    nothing."""
    try:
        broker = bot_exits.run_broker_name(deps, trade.run_id)
        with deps.session_factory() as session:
            rows = session.scalars(select(BotExit).where(
                BotExit.run_id == trade.run_id, BotExit.kind == "stop",
                BotExit.state == "working", BotExit.watched.is_(False),
                BotExit.trigger.is_not(None),
            ).order_by(BotExit.group_position, BotExit.id)).all()  # fmt: skip
            stops = [f"{_side_name(trade.bot, r.group_position)} {r.trigger}" for r in rows]
    except Exception:  # noqa: BLE001 -- the alert goes out without it
        logger.exception("couldn't read run %s's stops for the paused alert", trade.run_id)
        return ""
    if stops:
        return f" Your stops at {broker} still work: {', '.join(stops)}."
    return (f" Orders resting at {broker} are unaffected, but no stop rests at {broker} for "
            "this trade.")  # fmt: skip


def _no_entry(deps, trade: _Trade, outcomes) -> None:
    if any(o.skipped == "no_entry" for o in outcomes) and _first_time(trade.run_id, "no_entry"):
        _alert(deps, trade.bot, "underlying move rule off for a trade", (
            f"This trade's {trade.bot.underlying} price at entry isn't known (it was opened "
            "before the app "
            "recorded it), so the underlying move rule can't be checked for it. The other "
            "rules and the stops are unaffected."
        ), run_id=trade.run_id)  # fmt: skip


# --- Firing a watch rule -----------------------------------------------------------


def _fire(deps, trade: _Trade, hit: watch.Outcome) -> None:
    """Close every open side (watch_loop._fire, begin_close per position): each as
    slice 6's close of its whole open size, decided under the lock with the claim:
    a side with exits off is left alone, a held-back or closing one is left to that
    close. Its "watch" row is written before the claim (so the rule can never fire
    twice for it) and finished after. A side that breaks part-way doesn't stop the
    others, and the alert says to close it yourself (PARTIALLY started)."""
    started, notes, failed, busy = [], [], [], False
    for position, state in sorted(trade.states.items()):
        name = _side_name(trade.bot, position)
        if state == "closed":
            continue
        try:
            said = _fire_side(deps, trade, hit, position)
        except Exception as e:  # noqa: BLE001 -- the other sides still close
            logger.exception("watch rule close of run %s side %s broke", trade.run_id, position)
            failed.append(f"{name} ({e.__class__.__name__})")
            continue
        if said == "started":
            started.append(name)
        elif said == "off":
            notes.append(f"{name} was left alone: you turned its exits off")
        elif said == "held":
            notes.append(f"{name}: an earlier close or stop change may still be working at "
                         "your broker, so nothing new was sent")  # fmt: skip
            busy = True
        elif said == "busy":
            notes.append(f"{name}: a close is already running on it")
            busy = True
    tail = f" {'. '.join(notes)}." if notes else ""
    if failed:
        _alert(deps, trade.bot, f"{hit.name} PARTLY started", (
            f"{hit.detail}. Closing: {', '.join(started) or 'nothing'}. Couldn't start the "
            f"close of {', '.join(failed)}: close it yourself, and check your broker.{tail}"
        ), run_id=trade.run_id)  # fmt: skip
    elif started:
        _alert(deps, trade.bot, hit.name,
               f"{hit.detail}: closing the trade ({', '.join(started)}).{tail}",
               run_id=trade.run_id)  # fmt: skip
    elif busy and _first_time(trade.run_id, "not_started", hit.rule):
        _alert(deps, trade.bot, f"{hit.name} couldn't close the trade", (
            f"{hit.detail}, but no side could be closed.{tail} Close the trade yourself if you "
            "want it closed, and check your broker."
        ), run_id=trade.run_id)  # fmt: skip
    elif not busy and notes and _first_time(trade.run_id, "all_off", hit.rule):
        _alert(deps, trade.bot, f"{hit.name}: nothing closed", (
            f"{hit.detail}, but nothing was closed.{tail} Check your broker."
        ), run_id=trade.run_id)  # fmt: skip


def _fire_side(deps, trade: _Trade, hit: watch.Outcome, position: int) -> str:
    """One side of a watch close, under EXITS_LOCK with its claim (exits off read
    there, as _claim_side doesn't for a "close"): "started", "off", "held", "busy" or
    "nothing" (nothing open)."""
    with bot_exits.EXITS_LOCK:
        with deps.session_factory() as session:
            run_row = session.get(BotRun, trade.run_id)
            rows = list(session.scalars(select(BotExit).where(
                BotExit.run_id == trade.run_id, BotExit.group_position == position)))  # fmt: skip
            state = timing.side_state((r.kind, r.state) for r in rows)
            size = bot_exits.group_units(session, run_row, position)
        if state == "closed" or size <= 0:
            return "nothing"
        if bot_exits.held_back(rows):  # fire_close's claim refuses it too (slice 6)
            return "held"
        if state == "busy":
            return "busy"
        row_id = _record(deps, trade, position, "watch", "failed", {
            "rule": hit.name, "detail": hit.detail, "notes": ["starting the close"],
        })  # fmt: skip
        try:
            close_id = bot_exits.fire_close(deps, trade.run_id, position, size, size, by=hit.name)
        except Exception as e:
            # Bots slice 20: the row says so (the run page's banner turns red); the caller
            # alerts that this side wasn't closed
            _finish(deps, row_id, "failed", [f"the close broke: {e.__class__.__name__}"])
            raise
        if close_id is None:
            _finish(deps, row_id, "failed", ["the side couldn't be claimed"])
            return "busy"
        try:  # the close is running now: a bookkeeping failure must not say otherwise
            _finish(deps, row_id, "filled", [f"closing {size} (close {close_id})"])
        except Exception:  # noqa: BLE001 -- the row stays "failed": it never fires twice
            logger.exception("couldn't finish watch row %s (its close %s runs)", row_id,
                             close_id)  # fmt: skip
        return "started"


def _record(deps, trade: _Trade, position: int, kind: str, state: str, entry: dict) -> int:
    """A "watch" or "profit" row on the group: what fired and what it did, with the
    group's columns (bot_exits.group_columns) and its open units. Its id."""
    now = deps.now()
    with deps.session_factory() as session:
        run_row = session.get(BotRun, trade.run_id)
        rows = list(session.scalars(select(BotExit).where(
            BotExit.run_id == trade.run_id, BotExit.group_position == position)))  # fmt: skip
        row = BotExit(
            run_id=trade.run_id, group_position=position, kind=kind, state=state,
            **bot_exits.group_columns(session, run_row, position, rows),
            contracts=bot_exits.group_units(session, run_row, position), rearms=0,
            log=[{"at": now.isoformat(), **entry}], created_at=now, updated_at=now,
        )  # fmt: skip
        session.add(row)
        session.commit()
        return row.id


def _finish(deps, row_id: int, state: str, notes: list[str]) -> None:
    """A row written before its claim, finished with what happened."""
    with deps.session_factory() as session:
        row = session.get(BotExit, row_id)
        row.state = state
        row.log = [{**row.log[0], "notes": notes}, *row.log[1:]]
        row.updated_at = deps.now()
        session.commit()


# --- Profit actions ----------------------------------------------------------------


def _profit(deps, trade: _Trade, group: GroupDef, now, opening: bool = False
            ) -> list[watch.Outcome]:  # fmt: skip
    """The group's next profit action and, decision 0055, its next loss action (each the
    lowest-numbered of its kind not done, watch_loop.group_watches), evaluated on the
    group's P&L over all its legs (watch.group_pnl_now), each fired once it holds 2
    passes."""
    position = group.position
    prefix = f"{PROFIT}{position}:"
    if trade.states.get(position) != "open" or position in trade.off or (position in trade.blocked):
        _forget(trade.run_id, keep=lambda rule: not rule.startswith(prefix))
        return []
    entry = trade.entries.get(position)
    if entry is None:  # a leg with no fill price
        return []
    numbers = [
        next((n for n, a in enumerate(group.profit_actions, 1)
              if (a.at < 0) == loss and (position, a.sig) not in trade.done), None)
        for loss in (False, True)
    ]  # fmt: skip
    numbers = [n for n in numbers if n is not None]
    if not numbers:
        return []
    _, gcd = exit_math.group_legs(trade.legs, position)
    views = [(leg_view(deps, leg, now, trade.filled_at, underlying=trade.bot.underlying),
              int(leg.get("ratio", 1)) // gcd)
             for leg in trade.legs if leg["group"] == position]  # fmt: skip
    outcomes = []
    for number in numbers:
        action = group.profit_actions[number - 1]
        outcome = watch.profit_outcome(group, action, number, entry, views)
        outcomes.append(outcome)
        if _confirmed(trade.run_id, [outcome], now, opening) is not None:
            _fire_profit(deps, trade, group, number, action, outcome)
            break  # one action a pass on a group: the next is read again after it
    return outcomes


def _fire_profit(deps, trade: _Trade, group: GroupDef, number: int, action: ProfitActionDef,
                 outcome: watch.Outcome) -> None:  # fmt: skip
    """One profit action on one group (scale_out.begin_action, execute_action), under
    the lock, the group read again: close part of its units (fire_close, the new stop
    riding on it) or only move the stop (_move_for_profit). Counted in units: the
    original is the run's contracts x the group's gcd."""
    position = group.position
    name = _side_name(trade.bot, position)
    why = f"profit action {number}"
    with bot_exits.EXITS_LOCK:
        with deps.session_factory() as session:
            run_row = session.get(BotRun, trade.run_id)
            side = list(session.scalars(select(BotExit).where(
                BotExit.run_id == trade.run_id, BotExit.group_position == position)))  # fmt: skip
            if timing.side_state((r.kind, r.state) for r in side) != "open":
                return
            if bot_exits.held_back(side):
                # An earlier close may still be live, or a stop move was cut off (slice 6):
                # nothing new is sent or moved; tried again once settle_held lifts it.
                if _first_time(trade.run_id, "held", position, number):
                    _alert(deps, trade.bot, f"{why} waiting", (
                        f"{name}: {outcome.detail}, but an earlier close or stop change on "
                        "this side may still be working at your broker, so nothing was sent "
                        "or moved. The app tries again once that's settled."
                    ), run_id=trade.run_id)  # fmt: skip
                return
            done, blocked = profit_done(trade.groups, side)
            if (position, action.sig) in done or position in blocked:
                return
            _, gcd = exit_math.group_legs(run_row.legs, position)
            original = run_row.contracts * gcd
            held = bot_exits.group_units(session, run_row, position)
            watched = bot_exits.group_columns(session, run_row, position, side)["watched"]
            g = bot_exits._group_of(run_row, side, position, held, watched=watched)
            stop = max((r for r in side if r.kind == "stop" and r.state in bot_exits.STOP_LIVE),
                       key=lambda r: r.id, default=None)  # fmt: skip
            stop_id, old = (stop.id, stop.trigger) if stop is not None else (None, None)
        entry = {"number": number, "sig": action.sig, "detail": outcome.detail,
                 "rule": f"{why}: {action.label(group.target_unit)}"}  # fmt: skip
        n = watch.contracts_for(original, action.close_pct)
        if n > 0 and held - n < 1:
            # profit_actions.plan: this action, and every later one on the side, skipped
            note = (f"skipped: closing {n} of the {held} open would leave nothing for the "
                    "profit target, so this and any later actions on the side won't "
                    "run")  # fmt: skip
            _record(deps, trade, position, "profit", "failed",
                    entry | {"notes": [note], "blocked": True})  # fmt: skip
            said = f"{name}: {outcome.detail}. It was {note}."
            _alert(deps, trade.bot, f"{why} skipped", said, run_id=trade.run_id)
            return
        if n > 0:
            move = bot_exits.then_stop_of(action)  # slice 19: a % or a $ P&L level
            then = None if move is None else move | {"by": why}
            # Written before the claim: if anything breaks after, it never fires twice.
            row_id = _record(deps, trade, position, "profit", "failed",
                             entry | {"notes": ["starting the close"]})  # fmt: skip
            try:
                close_id = bot_exits.fire_close(deps, trade.run_id, position, n, held, by=why,
                                                then_stop=then)  # fmt: skip
            except Exception:  # noqa: BLE001 -- the row stays "failed": never run twice
                logger.exception("profit action %s on run %s side %s couldn't start", number,
                                 trade.run_id, position)  # fmt: skip
                _alert(deps, trade.bot, f"a profit action on {name} couldn't start", (
                    f"{why}: {outcome.detail}, but its close couldn't be started. It won't "
                    "be tried again. Check the trade and your broker."
                ), run_id=trade.run_id)  # fmt: skip
                return
            if close_id is None:
                _finish(deps, row_id, "failed", ["the side couldn't be claimed; not tried again"])
                return
            note = f"closing {n} of {held} (close {close_id})"
            _finish(deps, row_id, "filled", [note])
            moving = "" if then is None else " The stop moves for what's left once it fills."
            _alert(deps, trade.bot, why,
                   f"{name}: {outcome.detail}. Closing {n} of {held}.{moving}",
                   run_id=trade.run_id)  # fmt: skip
            return
        _move_for_profit(deps, trade, group, number, entry, g, stop_id, old)


def _revive_for_profit(deps, trade, group, g, why, action, done) -> None:
    """A 0% profit action on a side whose stop you switched off: it turns the stop back
    on at the action's level (Tracker2's record_profit_action_fill, "even over a removed
    one"), after the same check as a move (level_problem against the fresh price). No
    switched-off stop: "no working stop", as before. Called with EXITS_LOCK held."""
    position = group.position
    stop_id = bot_exits.revive_stop(deps, trade.run_id, position, why)
    if stop_id is None:
        return done("failed", "stop not moved: the side has no working stop")
    calc = bot_exits.profit_stop_calc(g, action.stop_pct, why, usd=action.stop_usd)
    if g.anchor is None:
        ask, pnl = None, bot_exits._live_pnl(deps, g)
    else:
        ask, pnl = bot_exits._fresh_ask(deps, g.anchor.symbol), None
    if calc is None:
        problem = "the side has no fill prices"
    elif (g.anchor is None and pnl is None) or (g.anchor is not None and ask is None):
        problem = "there's no fresh price to check it against"
    else:
        problem = bot_exits.level_problem(calc, None, ask, pnl=pnl)
    if problem is not None:
        bot_exits.unrevive_stop(deps, trade.run_id, position, stop_id, problem)
        return done("failed", f"the stop stays off: {problem}")
    entry = calc.as_log()
    entry["stop_calc"]["moved_by"] = why
    with deps.session_factory() as session:
        bot_exits._log(session.get(BotExit, stop_id), deps, event=f"moved by {why}", **entry)
        session.commit()
    placed, note = bot_exits.place_revived(deps, stop_id, calc.trigger)
    if placed:  # bots slice 21: the latest change wins over this trade's own stop style
        bot_exits.clear_stop_style(deps, trade.run_id, position)
    done("filled" if placed else "failed", f"{bot_exits.STOP_BACK_ON}: {note}")


def _move_for_profit(deps, trade, group, number, entry, g, stop_id, old) -> None:
    """A 0% profit action: the stop moved to its P&L level (bot_exits.profit_stop_calc),
    checked before any cancel (bot_exits.level_problem: never looser, and never where
    it would fire at once: at or below the anchor's fresh ask for a resting stop, at or
    above the group's live P&L for one the app watches). No fresh price: nothing
    recorded, tried again, alerted once. Called with EXITS_LOCK held."""
    position = group.position
    name = _side_name(trade.bot, position)
    why = f"profit action {number}"
    action = group.profit_actions[number - 1]

    def done(state: str, note: str) -> None:
        _record(deps, trade, position, "profit", state, entry | {"notes": [note]})
        _alert(
            deps,
            trade.bot,
            why,
            f"{name}: {entry['detail']}. {note[0].upper()}{note[1:]}.",
            run_id=trade.run_id,
            routine=state != "failed",  # the stop moved: news, nothing to do
        )

    if stop_id is None:
        return _revive_for_profit(deps, trade, group, g, why, action, done)
    calc = bot_exits.profit_stop_calc(g, action.stop_pct, why, usd=action.stop_usd)
    if calc is None:
        return done("failed", "stop not moved: the side has no fill prices")
    ask = pnl = None
    if g.anchor is None:  # a group the app watches: checked on its live P&L
        pnl = bot_exits._live_pnl(deps, g)
        missing, what = pnl is None, "the group's legs"
    else:
        ask = bot_exits._fresh_ask(deps, g.anchor.symbol)
        missing, what = ask is None, "the short"
    if missing:
        if _first_time(trade.run_id, "waiting", position, number):
            _alert(deps, trade.bot, f"{why} waiting for a price", (
                f"{name}: {entry['detail']}, but there's no fresh price for {what}, so its "
                "stop can't be checked and wasn't touched. The app tries again at each check."
            ), run_id=trade.run_id)  # fmt: skip
        return
    problem = bot_exits.level_problem(calc, old, ask, pnl=pnl)
    if problem is not None:
        return done("failed", f"stop not moved: {problem}")
    log = calc.as_log()
    log["stop_calc"]["moved_by"] = why
    # Written before the move: if anything breaks after, it never runs twice.
    row_id = _record(deps, trade, position, "profit", "failed",
                     entry | {"notes": ["moving the stop"]})  # fmt: skip
    progress: dict = {}
    try:
        run, bot = bot_exits._run_and_bot(deps, trade.run_id)
        moved, note = bot_exits._move_stop(
            deps, run, bot, group, stop_id, g, None, None, why, progress, ask,
            marker=bot_exits.MOVING_STOP_PROFIT, found=(calc, log), pnl=pnl,
        )  # fmt: skip
    except Exception as e:  # noqa: BLE001 -- recorded and alerted, never retried
        logger.exception("profit action %s on run %s side %s broke", number, trade.run_id,
                         position)  # fmt: skip
        moved, note = False, bot_exits._broke_note(deps, stop_id, "stop", e, progress)
    _finish(deps, row_id, "filled" if moved else "failed", [note])
    if moved:  # bots slice 21: the latest change wins over this trade's own stop style
        bot_exits.clear_stop_style(deps, trade.run_id, position)
    _alert(
        deps,
        trade.bot,
        why,
        f"{name}: {entry['detail']}. {note[0].upper()}{note[1:]}.",
        run_id=trade.run_id,
        routine=moved and bot_exits.NOT_AT_BROKER not in note,
    )  # the stop moved: news, nothing to do
