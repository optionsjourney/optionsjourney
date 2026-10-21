"""American-style options held into their expiration (spec: bots slice 16, section 6;
decision 0042).

Stock and ETF options (everything but SPX and XSP) settle in shares: in the money at the
close, a sold option is assigned and a bought one exercised. Tracker2 leaves that to you
(its expiry_service.auto_expire skips an "ITM physically-settled" leg until the broker's
record arrives, and its bots have no assignment code); so does this app, but it says so.
From 10 minutes before the close (15:50, 12:50 on a half day) to 5 minutes after it, one
safety alert per run per day says which legs are still held and where each stands. That
it was said is kept in the run's log,
so a restart never says it again.

Alert only: nothing here sends, cancels or changes an order."""

import logging
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select

from optionsjourney.bots import timing, underlyings
from optionsjourney.core.market import MARKET_TZ
from optionsjourney.core.sessions import session_close
from optionsjourney.core.symbols import parse_occ
from optionsjourney.db.models import Bot, BotExit, BotRun
from optionsjourney.services import bot_entry, bot_exits, bot_trade_settings
from optionsjourney.services.bot_integrity import LOOKBACK
from optionsjourney.trading.orders import Side

logger = logging.getLogger(__name__)
HOLDING = ("filled", "partial")
WARN_BEFORE = timedelta(minutes=10)  # before the session's close
WARN_AFTER = timedelta(minutes=5)  # and until a few minutes after it
WARNED = "expiry_warned"  # the run's log entry: said for that day (survives a restart)


def reset() -> None:
    """Nothing is kept in memory any more (the run's log says it was said); for tests."""


def warn_pass(deps) -> None:
    """Each monitor pass, from 10 minutes before the close to 5 minutes after it: warn
    once per run about American-style legs it still holds that expire today. Bots slice
    18: and about futures options in their own series' window (_futures_pass). Never
    raises."""
    _futures_pass(deps)
    try:
        now = deps.now()
        local = now.astimezone(MARKET_TZ)
        today = local.date()
        close = session_close(today)
        if close is None:
            return
        closing = datetime.combine(today, close, tzinfo=MARKET_TZ)
        if not closing - WARN_BEFORE <= local < closing + WARN_AFTER:
            return
        for run_id in _runs(deps, today):
            try:
                _warn(deps, run_id, today)
            except Exception:  # noqa: BLE001 -- one run never stops the others
                logger.exception("couldn't check run %s's legs held into expiration", run_id)
    except Exception:  # noqa: BLE001 -- the monitor's other steps go on
        logger.exception("the expiration warning pass failed")


def _expires_today(symbol: str, today: date) -> bool:
    parsed = parse_occ(symbol)
    return parsed is not None and parsed[1].expiration == today


def _runs(deps, today: date) -> list[int]:
    """Live runs holding fills on an American-style underlying, not being entered, with
    a leg that expires today."""
    with bot_entry._active_lock:
        active = set(bot_entry.ACTIVE)
    with deps.session_factory() as session:
        found = session.execute(
            select(BotRun.id, BotRun.legs, BotRun.filled_legs, BotRun.chase_log)
            .join(Bot, Bot.id == BotRun.bot_id)
            .where(
                BotRun.status.in_(HOLDING),
                BotRun.live.is_(True),
                BotRun.trading_date >= today - LOOKBACK,
                Bot.underlying.notin_(underlyings.CASH),
            )
            .order_by(BotRun.id)
        ).all()
    return [
        run_id
        for run_id, legs, filled, log in found
        if filled and legs and run_id not in active
        and any(_expires_today(leg["symbol"], today) for leg in legs)
        and not _said(log, today)
    ]  # fmt: skip


def _said(log, today: date) -> bool:
    day = today.isoformat()
    return any(
        isinstance(e, dict) and e.get("event") == WARNED and e.get("day") == day for e in log or ()
    )


def _warn(deps, run_id: int, today: date) -> None:
    """Say it for this run, if anything of it is still held, and note in the run's log
    that it was looked at today (said, or nothing held), so it's never said twice."""
    with deps.session_factory() as session:
        run = session.get(BotRun, run_id)
        bot = session.get(Bot, run.bot_id)
        rows = list(session.scalars(select(BotExit).where(BotExit.run_id == run_id)))
        legs, name, underlying = list(run.legs), bot.name, bot.underlying
        bot_id = bot.id
        # bots slice 21: this trade's own timed close, when it has one
        mine = bot_trade_settings.trade_timed(session, run_id)
        close_time = (mine.close_time or None) if mine is not None else bot.close_time
    held, busy = [], False
    for position in sorted({leg["group"] for leg in legs}):
        state = timing.side_state((r.kind, r.state) for r in rows if r.group_position == position)
        if state == "closed":
            continue
        expiring = [leg for leg in legs if leg["group"] == position
                    and _expires_today(leg["symbol"], today)]  # fmt: skip
        if expiring:
            held += expiring
            busy = busy or state == "busy"
    _note(deps, run_id, today, said=bool(held))
    if not held:
        return
    spot = _spot(deps, underlying)
    words = ", ".join(_leg_words(leg, underlying, spot) for leg in held)
    detail = f"Run {run_id} still holds options that expire at today's close: {words}."
    if busy:
        detail += " A close is working on it."
    elif close_time:
        detail += f" Its timed close is at {close_time}."
    detail += (
        f" {underlying} options are American-style: in the money at the close, a sold option "
        "is assigned (you'll hold shares) and a bought one is exercised. Close them, or check "
        "your broker."
    )
    bot_exits._alert(deps, f"{name}: URGENT: {underlying} options held into expiration",
                     detail, bot_id, run_id=run_id)  # fmt: skip


def _note(deps, run_id: int, today: date, said: bool) -> None:
    """Written before the alert goes out: a crash after it can only miss one, never
    repeat it on every pass."""
    with deps.session_factory() as session:
        run = session.get(BotRun, run_id)
        run.chase_log = [*(run.chase_log or []), {
            "at": deps.now().isoformat(), "event": WARNED, "day": today.isoformat(),
            "said": said,
        }]  # fmt: skip
        session.commit()


def _spot(deps, underlying: str) -> Decimal | None:
    try:
        deps.hub.subscribe({underlying})
        quote = deps.hub.latest(underlying)
    except Exception:  # noqa: BLE001 -- said as "price unknown"
        return None
    if quote is None:
        return None
    return quote.last if quote.last is not None else quote.mark


def _leg_words(leg: dict, underlying: str, spot: Decimal | None) -> str:
    """'sold SPY 580 put (in the money by 1.20)'."""
    side = "sold" if Side(leg["side"]) is Side.SELL else "bought"
    _, option = parse_occ(leg["symbol"])
    strike, right = option.strike, option.put_call.lower()
    text = f"{side} {underlying} {strike.normalize():f} {right}"
    if spot is None:
        return f"{text} (price unknown)"
    inside = spot - strike if right == "call" else strike - spot
    cents = abs(inside).quantize(Decimal("0.01"))
    where = "in the money" if inside > 0 else "out of the money"
    return f"{text} ({where} by {cents})" if inside != 0 else f"{text} (at the money)"


# --- Futures options (bots slice 18) ------------------------------------------------------
# A futures option in the money at its expiry becomes a futures position, and a futures
# bot has no stop (decision 0043): nothing would close it. Tracker2 has no warning; this
# says so once per run per day, in each series' own window around its expires-at (an /ES
# weekly at 16:00 New York, /GC at 13:30), as the stock options' 15:50 warning does.
FUTURES_WARNED = "futures_expiry_warned"


def _when(text) -> datetime | None:
    if not text:
        return None
    try:
        found = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return found if found.tzinfo else found.replace(tzinfo=MARKET_TZ)


def _futures_pass(deps) -> None:
    """Each live futures run holding a leg whose series expires within the window: one
    urgent alert, noted in the run's log first. Never raises."""
    try:
        now = deps.now()
        with bot_entry._active_lock:
            active = set(bot_entry.ACTIVE)
        with deps.session_factory() as session:
            found = session.execute(
                select(BotRun.id, BotRun.legs, BotRun.filled_legs, BotRun.chase_log).where(
                    BotRun.status.in_(HOLDING), BotRun.live.is_(True), BotRun.pt_only.is_(True)
                )
            ).all()
        for run_id, legs, filled, log in found:
            if not filled or not legs or run_id in active:
                continue
            due = [leg for leg in legs if _in_window(_when(leg.get("expires_at")), now)]
            if not due:
                continue
            day = _when(due[0]["expires_at"]).astimezone(MARKET_TZ).date()
            if _said_futures(log, day):
                continue
            try:
                _warn_futures(deps, run_id, day, now)
            except Exception:  # noqa: BLE001 -- one run never stops the others
                logger.exception("couldn't check run %s's futures legs held into expiry", run_id)
    except Exception:  # noqa: BLE001 -- the monitor's other steps go on
        logger.exception("the futures expiry warning pass failed")


def _in_window(expires_at: datetime | None, now: datetime) -> bool:
    return expires_at is not None and expires_at - WARN_BEFORE <= now < expires_at + WARN_AFTER


def _said_futures(log, day: date) -> bool:
    return any(
        isinstance(e, dict) and e.get("event") == FUTURES_WARNED and e.get("day") == day.isoformat()
        for e in log or ()
    )


def _warn_futures(deps, run_id: int, day: date, now: datetime) -> None:
    from optionsjourney.bots import futures

    with deps.session_factory() as session:
        run = session.get(BotRun, run_id)
        bot = session.get(Bot, run.bot_id)
        rows = list(session.scalars(select(BotExit).where(BotExit.run_id == run_id)))
        legs, name, root, bot_id = list(run.legs), bot.name, bot.underlying, bot.id
    held = []
    for position in sorted({leg["group"] for leg in legs}):
        state = timing.side_state((r.kind, r.state) for r in rows if r.group_position == position)
        if state == "closed":
            continue
        held += [leg for leg in legs if leg["group"] == position
                 and _in_window(_when(leg.get("expires_at")), now)]  # fmt: skip
    with deps.session_factory() as session:  # noted first: a crash can only miss one
        run = session.get(BotRun, run_id)
        run.chase_log = [*(run.chase_log or []), {
            "at": now.isoformat(), "event": FUTURES_WARNED, "day": day.isoformat(),
            "said": bool(held),
        }]  # fmt: skip
        session.commit()
    if not held:
        return
    at = _when(held[0]["expires_at"]).astimezone(MARKET_TZ).strftime("%H:%M")
    words = []
    for leg in held:
        option = futures.option_of(leg["symbol"])
        side = "sold" if Side(leg["side"]) is Side.SELL else "bought"
        if option is None:
            words.append(f"{side} {leg['symbol'].strip()}")
        else:
            right = option.put_call.lower()
            words.append(f"{side} {root} {option.strike.normalize():f} {right}")
    detail = (
        f"Run {run_id} still holds {root} options that expire at {at} New York: "
        f"{', '.join(words)}. An option in the money at expiry becomes a futures position, and "
        "nothing closes it (a futures bot has no stop). Close it, or check your broker."
    )
    bot_exits._alert(deps, f"{name}: URGENT: futures options held into expiry", detail, bot_id,
                     run_id=run_id)  # fmt: skip
