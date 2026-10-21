"""One trade's own timed close and watch rules (bots slice 21; Tracker2's
timed_close_overrides.py and watch_overrides.py): read, validate, save and revert, and
the checks that ask you to confirm a save that would act at once.

A row replaces the bot's setting for that run wholesale (a blank time switches the
timed close off for that trade only); no row is the bot's. The passes read them:
bot_timed.timed_pass (overrides.effective_timed) and bot_watch (watch.rules_for).
Nothing here sends, cancels or replaces an order."""

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import select

from optionsjourney.bots import overrides, timing, validation, watch
from optionsjourney.core.market import MARKET_TZ
from optionsjourney.core.sessions import naive_et
from optionsjourney.db.models import BotExit, BotRun, BotRunTimedClose, BotRunWatchRules

logger = logging.getLogger(__name__)
HOLDING = ("filled", "partial")


@dataclass(frozen=True)
class Saved:
    """What a save did. `confirm`: nothing was saved; this would act at once, said in
    words, and the page asks before saving it (`confirmed=True`)."""

    ok: bool
    message: str
    confirm: str | None = None


def _refused(message: str) -> Saved:
    return Saved(False, message)


# --- The timed close -------------------------------------------------------------------


def timed_row(session, run_id: int) -> BotRunTimedClose | None:
    return session.scalar(select(BotRunTimedClose).where(BotRunTimedClose.run_id == run_id))


def trade_timed(session, run_id: int) -> overrides.TradeTimed | None:
    """The trade's own timed close as overrides.effective_timed reads it, or None."""
    row = timed_row(session, run_id)
    return None if row is None else overrides.TradeTimed(row.close_time, row.close_dte)


def all_timed(session_factory) -> dict[int, overrides.TradeTimed]:
    """{run id: its own timed close} for every run that has one (the pass reads them once)."""
    with session_factory() as session:
        return {
            r.run_id: overrides.TradeTimed(r.close_time, r.close_dte)
            for r in session.scalars(select(BotRunTimedClose))
        }


def _not_editable(run: BotRun | None) -> str | None:
    """timed_close_overrides.not_editable_reason, and a futures run has no timed close."""
    if run is None or run.status not in HOLDING or not run.filled_legs:
        return "This trade isn't open."
    if run.pt_only:
        return "A futures trade has no timed close (decision 0043)."
    return None


def _multi_expiry(legs) -> bool:
    """A group with legs in more than one expiration (a calendar or diagonal)."""
    by_group: dict[int, set] = {}
    for leg in legs or ():
        by_group.setdefault(leg.get("group", 0), set()).add(
            timing.nearest_expiration([leg.get("symbol", "")])
        )
    return any(len(days) > 1 for days in by_group.values())


def _read_timed(time_text: str, dte_text: str):
    """((time or None, dte or None), problem)."""
    close_time = (time_text or "").strip() or None
    raw = (dte_text or "").strip()
    dte = None
    if raw:
        try:
            dte = int(raw)
        except ValueError:
            return None, "Days to expiration is a whole number, 0 or more."
    return (close_time, dte), None


def _fired_today(rows, position: int, today: date) -> bool:
    return any(
        r.kind == "timed" and r.group_position == position
        and r.created_at.astimezone(MARKET_TZ).date() == today
        for r in rows
    )  # fmt: skip


def _next_closes(session, run: BotRun, close_time, close_dte, now) -> list[timing.NextClose]:
    """Each still-open group's next timed close at these settings (timed_close.next_close
    per group: each group closes by its own nearest expiration, bots slice 14)."""
    from optionsjourney.services import bot_timed

    local = naive_et(now)
    rows = list(session.scalars(select(BotExit).where(BotExit.run_id == run.id)))
    out = []
    for position, nearest in sorted(bot_timed.group_expirations(run.legs).items()):
        state = timing.side_state((r.kind, r.state) for r in rows if r.group_position == position)
        if state == "closed":
            continue
        found = timing.next_close(close_time, close_dte, nearest, local,
                                  _fired_today(rows, position, local.date()))  # fmt: skip
        if found is not None:
            out.append(found)
    return out


def _when(found: timing.NextClose | None, today: date) -> str:
    if found is None:
        return "none before it expires"
    if found.day == today:
        return f"today at {found.at}"
    return f"{found.day:%a %d %b} at {found.at}"


def _due_words(closes, close_time) -> str | None:
    if any(c.due_now for c in closes):
        return (
            f"The {close_time} timed close has already passed today: saving it starts the "
            "close at the next check (within 30 seconds)."
        )
    return None


def save_timed(deps, run_id: int, time_text: str, dte_text: str,
               confirmed: bool = False) -> Saved:  # fmt: skip
    """This trade's own timed close (timed_close_overrides.validate, due_now, save): a time
    from 09:30 to 15:59 and a DTE, or a blank time to switch it off for this trade (never
    for a calendar or diagonal). A time already passed today asks first."""
    typed, problem = _read_timed(time_text, dte_text)
    if problem:
        return _refused(problem)
    close_time, dte = typed
    now = deps.now()
    with deps.session_factory() as session:
        run = session.get(BotRun, run_id)
        why = _not_editable(run)
        if why:
            return _refused(why)
        problem = validation.trade_timed_problem(close_time, dte, _multi_expiry(run.legs))
        if problem:
            return _refused(problem)
        closes = _next_closes(session, run, close_time, dte or 0, now)
        ask = _due_words(closes, close_time)
        if ask and not confirmed:
            return Saved(False, "Not saved yet.", ask)
        row = timed_row(session, run_id) or BotRunTimedClose(run_id=run_id)
        row.close_time, row.close_dte, row.changed_at = close_time, dte, now
        session.add(row)
        session.commit()
    if close_time is None:
        return Saved(True, "This trade has no timed close now; the bot keeps its own.")
    today = naive_et(now).date()
    first = min(closes, key=lambda c: (c.day, c.at), default=None)
    return Saved(True, f"This trade closes at {close_time}, {dte or 0} DTE or fewer: next "
                       f"{_when(first, today)}.")  # fmt: skip


def revert_timed(deps, run_id: int, confirmed: bool = False) -> Saved:
    """Back to the bot's timed close (timed_close_overrides.revert), after the same check:
    the bot's, already passed today, asks first."""
    from optionsjourney.services import bot_store

    now = deps.now()
    with deps.session_factory() as session:
        run = session.get(BotRun, run_id)
        why = _not_editable(run)
        if why:
            return _refused(why)
        row = timed_row(session, run_id)
        if row is None:
            return _refused("This trade already uses the bot's timed close.")
        bot = bot_store.load(deps.session_factory, run.bot_id)
        close_time, dte, _ = overrides.effective_timed(bot, None)
        closes = _next_closes(session, run, close_time, dte, now)
        ask = _due_words(closes, close_time)
        if ask and not confirmed:
            return Saved(False, "Not changed yet.", ask)
        session.delete(row)
        session.commit()
    if close_time is None:
        return Saved(True, "This trade uses the bot's timed close again: none.")
    return Saved(True, f"This trade uses the bot's timed close again: {close_time}, {dte} DTE "
                       "or fewer.")  # fmt: skip


@dataclass(frozen=True)
class TimedView:
    """The run page's timed close: what's in force, the bot's, when it next runs."""

    time: str | None
    dte: int
    source: str  # "bot" or "trade"
    bot_time: str | None
    bot_dte: int
    next_close: str | None
    editable: bool
    reason: str | None


def timed_view(session_factory, run_id: int, bot, now) -> TimedView | None:
    """None for a run the card doesn't apply to (a futures trade, a run with no bot)."""
    with session_factory() as session:
        run = session.get(BotRun, run_id)
        if run is None or run.pt_only or bot is None:
            return None
        mine = trade_timed(session, run_id)
        close_time, dte, source = overrides.effective_timed(bot, mine)
        reason = _not_editable(run)
        closes = _next_closes(session, run, close_time, dte, now) if reason is None else []
    today = naive_et(now).date()
    first = min(closes, key=lambda c: (c.day, c.at), default=None)
    nxt = None if close_time is None or reason else _when(first, today)
    return TimedView(close_time, dte, source, bot.close_time, bot.close_dte or 0, nxt,
                     reason is None, reason)  # fmt: skip


# --- The watch rules -------------------------------------------------------------------


def watch_row(session, run_id: int) -> BotRunWatchRules | None:
    return session.scalar(select(BotRunWatchRules).where(BotRunWatchRules.run_id == run_id))


def _dec(text) -> Decimal | None:
    return None if text in (None, "") else Decimal(str(text))


def trade_watch_of(row: BotRunWatchRules | None) -> watch.TradeWatch | None:
    """A stored row as watch.rules_for reads it (config_from_override), or None."""
    if row is None:
        return None
    legs = []
    for key, pair in (row.leg_deltas or {}).items():
        try:
            position = int(key)
            below, above = (_dec(v) for v in pair)
        except TypeError, ValueError, InvalidOperation:
            continue  # an unreadable key is ignored, never re-targeted
        legs.append((position, below, above))
    return watch.TradeWatch(
        bool(row.use_delta_exit), (row.position_delta_below, row.position_delta_above),
        tuple(sorted(legs)), bool(row.use_sl_ratio_exit), (row.sl_ratio_below, row.sl_ratio_above),
    )  # fmt: skip


def all_watch(session_factory) -> dict[int, watch.TradeWatch]:
    """{run id: its own watch rules} for every run that has them (the pass reads them once)."""
    with session_factory() as session:
        return {r.run_id: trade_watch_of(r) for r in session.scalars(select(BotRunWatchRules))}


def _watch_not_editable(session, run: BotRun | None) -> str | None:
    """watch_overrides.not_editable_reason: the trade isn't open, a watch rule already fired
    on it, or a close is running."""
    if run is None or run.status not in HOLDING or not run.filled_legs:
        return "This trade isn't open."
    if run.pt_only:
        return "A futures trade has no watch rules (decision 0043)."
    rows = list(session.scalars(select(BotExit).where(BotExit.run_id == run.id)))
    if any(r.kind == "watch" for r in rows):
        return "A watch rule already fired on this trade."
    for position in {leg["group"] for leg in run.legs}:
        own = [(r.kind, r.state) for r in rows if r.group_position == position]
        if timing.side_state(own) == "busy":
            return "A close is running on this trade. Try again when it's done."
    return None


_NUMBERS = (
    ("position_delta_below", "delta below"), ("position_delta_above", "delta above"),
    ("sl_ratio_below", "S/L ratio below"), ("sl_ratio_above", "S/L ratio above"),
)  # fmt: skip


def _number(form, field: str, words: str):
    raw = str(form.get(field) or "").strip()
    if not raw:
        return None, None
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return None, f"The {words} {raw!r} isn't a number."
    if not value.is_finite():
        return None, f"The {words} {raw!r} isn't a number."
    return value, None


def read_watch(form, positions) -> tuple[watch.TradeWatch | None, str | None]:
    """The run page's watch rule boxes (named as the bot form's): Use delta, the position's
    below/above, each trade leg's leg-N-delta-below/-above, Use S/L ratio and its pair."""
    values = {}
    for field, words in _NUMBERS:
        values[field], problem = _number(form, field, words)
        if problem:
            return None, problem
    legs = []
    for position in positions:
        pair = []
        for end in ("below", "above"):
            value, problem = _number(form, f"leg-{position}-delta-{end}",
                                     f"leg {position + 1} delta {end}")  # fmt: skip
            if problem:
                return None, problem
            pair.append(value)
        if any(v is not None for v in pair):
            legs.append((position, *pair))
    return watch.TradeWatch(
        form.get("use_delta_exit") == "on",
        (values["position_delta_below"], values["position_delta_above"]), tuple(legs),
        form.get("use_sl_ratio_exit") == "on",
        (values["sl_ratio_below"], values["sl_ratio_above"]),
    ), None  # fmt: skip


def _probe_words(breach: list[str], unverified: list[str]) -> str | None:
    """watch_overrides' confirm (C1a): a rule already crossed fires within seconds, and
    one that can't be checked now may already be crossed."""
    parts = []
    if breach:
        parts.append(f"Already crossed on fresh prices, so it closes the trade within seconds: "
                     f"{'; '.join(breach)}.")  # fmt: skip
    if unverified:
        parts.append(f"Can't be checked right now, so it may already be crossed: "
                     f"{'; '.join(unverified)}.")  # fmt: skip
    return " ".join(parts) or None


def save_watch(deps, run_id: int, form, confirmed: bool = False) -> Saved:
    """This trade's own watch rules (watch_overrides.validate, probe_save, save): they
    replace the bot's delta and S/L ratio rules for this trade (its underlying rules
    stay). A rule that would fire at once, or that can't be checked now, asks first.
    Saved: the trade's streaks start again."""
    from optionsjourney.services import bot_watch

    with deps.session_factory() as session:
        run = session.get(BotRun, run_id)
        why = _watch_not_editable(session, run)
        if why:
            return _refused(why)
        positions = [leg.get("position", n) for n, leg in enumerate(run.legs)]
    mine, problem = read_watch(form, positions)
    if problem:
        return _refused(problem)
    problem = validation.trade_watch_problem(mine, positions)
    if problem:
        return _refused(problem)
    ask = _probe_words(*bot_watch.probe(deps, run_id, mine))
    if ask and not confirmed:
        return Saved(False, "Not saved yet.", ask)
    now = deps.now()
    with deps.session_factory() as session:
        row = watch_row(session, run_id) or BotRunWatchRules(run_id=run_id)
        row.use_delta_exit, row.use_sl_ratio_exit = mine.use_delta, mine.use_sl_ratio
        row.position_delta_below, row.position_delta_above = mine.position_delta
        row.sl_ratio_below, row.sl_ratio_above = mine.sl_ratio
        row.leg_deltas = {
            str(position): [None if v is None else f"{v:f}" for v in (below, above)]
            for position, below, above in mine.leg_deltas
        }
        row.changed_at = now
        session.add(row)
        session.commit()
    bot_watch.forget_rules(run_id)
    return Saved(True, "This trade's own watch rules are saved; the bot keeps its own.")


def revert_watch(deps, run_id: int, confirmed: bool = False) -> Saved:
    """Back to the bot's watch rules (watch_overrides.revert, after probe_revert)."""
    from optionsjourney.services import bot_watch

    with deps.session_factory() as session:
        run = session.get(BotRun, run_id)
        why = _watch_not_editable(session, run)
        if why:
            return _refused(why)
        if watch_row(session, run_id) is None:
            return _refused("This trade already uses the bot's watch rules.")
    ask = _probe_words(*bot_watch.probe(deps, run_id, None))
    if ask and not confirmed:
        return Saved(False, "Not changed yet.", ask)
    with deps.session_factory() as session:
        row = watch_row(session, run_id)
        if row is not None:
            session.delete(row)
            session.commit()
    bot_watch.forget_rules(run_id)
    return Saved(True, "This trade uses the bot's watch rules again.")


@dataclass(frozen=True)
class WatchLeg:
    position: int
    label: str  # "sold 5910 call"
    below: Decimal | None
    above: Decimal | None


@dataclass(frozen=True)
class WatchView:
    """The run page's watch rules: what's in force (the trade's own or the bot's)."""

    source: str  # "bot" or "trade"
    rules: watch.TradeWatch
    legs: tuple[WatchLeg, ...]
    editable: bool
    reason: str | None


def watch_view(session_factory, run_id: int, bot) -> WatchView | None:
    """None for a run the card doesn't apply to (a futures trade, no bot)."""
    with session_factory() as session:
        run = session.get(BotRun, run_id)
        if run is None or run.pt_only or bot is None:
            return None
        mine = trade_watch_of(watch_row(session, run_id))
        reason = _watch_not_editable(session, run)
        legs = list(run.legs)
    if mine is None:  # the bot's, by the trade's leg positions
        own = {leg.position: leg for leg in bot.legs}
        rules = watch.TradeWatch(
            bot.use_delta_exit, (bot.position_delta_below, bot.position_delta_above),
            tuple((p, b.exit_delta_below, b.exit_delta_above) for p, b in own.items()
                  if b.exit_delta_below is not None or b.exit_delta_above is not None),
            bot.use_sl_ratio_exit, (bot.sl_ratio_below, bot.sl_ratio_above),
        )  # fmt: skip
    else:
        rules = mine
    thresholds = {p: (b, a) for p, b, a in rules.leg_deltas}
    words = {"sell": "sold", "buy": "bought"}
    view_legs = tuple(
        WatchLeg(
            leg.get("position", n),
            f"{words.get(leg.get('side'), leg.get('side', ''))} {leg.get('strike', '')} "
            f"{leg.get('right', '')}".strip(),
            *thresholds.get(leg.get("position", n), (None, None)),
        )
        for n, leg in enumerate(legs)
    )  # fmt: skip
    return WatchView("bot" if mine is None else "trade", rules, view_legs, reason is None, reason)
