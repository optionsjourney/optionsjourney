"""P/L Day for the journal (#51): the snapshots it's measured from, and the
previous closes used when there's no snapshot.

- **Snapshots:** after each trading day's close (16:15 New York), every open
  trade's total P/L is saved. The checker runs in the background (from
  __main__ and the menu bar app) and catches up if the app opens later that
  evening.
- **Previous closes** (no snapshot yet, e.g. the first day): TastyTrade's
  market data, taking the close of the last trading day before today. It
  dates its "prev-close", and "close" is the session after that, so weekends
  and holidays come out right. Stocks and indexes fall back to the
  watchlist's quotes. Kept for the day."""

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from optionsjourney.core.live import multipliers_from_fills
from optionsjourney.core.market import MARKET_TZ, market_date
from optionsjourney.core.pl_day import PLDay, pl_day, schwab_close_counts, total_pl
from optionsjourney.core.positions import Status
from optionsjourney.core.quotes import is_futures_option
from optionsjourney.core.sessions import session_close
from optionsjourney.core.symbols import OCC
from optionsjourney.db.models import CloseMark, PLSnapshot
from optionsjourney.services.streaming import marks as hub_marks

logger = logging.getLogger(__name__)

SNAPSHOT_AFTER = timedelta(minutes=15)  # after the close
CHECK_EVERY = 600  # seconds


def session_day(now: datetime) -> date:
    """The day P/L Day is about: today when it's a trading day, otherwise
    the last one (at the weekend, Friday), as brokers show it."""
    today = market_date(now)
    return today if session_close(today) is not None else last_session_before(today)


def label(day: date, now: datetime | None = None) -> str:
    """ "today", or the day it's about ("Fri 10/9")."""
    if day == market_date(now or datetime.now(UTC)):
        return "today"
    return f"{day:%a} {day.month}/{day.day}"


def last_session_before(today: date) -> date:
    day = today - timedelta(days=1)
    while session_close(day) is None:
        day -= timedelta(days=1)
    return day


@dataclass
class DayPL:
    day: date  # the trading day it's about
    positions: dict[str, PLDay]
    total: Decimal | None  # of the trades that have a number
    priced: int
    approximate: bool


# --- previous closes ---------------------------------------------------------------

RETRY_AFTER = timedelta(minutes=2)  # before a close that couldn't be read is asked for again
SCHWAB_BATCH = 50

_closes_lock = threading.Lock()
_closes: dict = {"day": None, "prices": {}, "tried": set(), "retry_at": {}}


def forget_closes() -> None:
    """Empty the day's cache (tests)."""
    with _closes_lock:
        _closes.update(day=None, prices={}, tried=set(), retry_at={})


def _pick_close(item: dict, wanted: date) -> Decimal | None:
    prev, prev_day, close = item.get("prev-close"), item.get("prev-close-date"), item.get("close")
    if not prev_day:
        return None
    prev_day = date.fromisoformat(prev_day)
    if prev_day == wanted and prev:
        return Decimal(str(prev))
    if prev_day < wanted and close:
        return Decimal(str(close))  # the session after prev-close-date: the one we want
    return None


def previous_closes(
    clients: list, symbols: set[str], today: date, now: datetime
) -> dict[str, Decimal]:
    wanted = last_session_before(today)
    with _closes_lock:
        if _closes["day"] != today:
            _closes.update(day=today, prices={}, tried=set(), retry_at={})
        missing = sorted(
            s
            for s in symbols
            if s not in _closes["tried"] and _closes["retry_at"].get(s, now) <= now
        )
        # Claim them now so a second caller doesn't ask the brokers at the same time.
        _closes["retry_at"].update({s: now + RETRY_AFTER for s in missing})
    if missing:
        try:
            found, failed = _fetch_closes(clients, missing, wanted, today, now)
        except Exception:
            logger.exception("previous closes failed")
            found, failed = {}, set(missing)
        with _closes_lock:
            _closes["prices"].update(found)
            for s in missing:
                if s in failed and s not in found:
                    _closes["retry_at"][s] = now + RETRY_AFTER
                else:
                    _closes["tried"].add(s)
                    _closes["retry_at"].pop(s, None)
    with _closes_lock:
        return {s: _closes["prices"][s] for s in symbols if s in _closes["prices"]}


def _fetch_closes(
    clients: list, symbols: list[str], wanted: date, today: date, now: datetime
) -> tuple[dict[str, Decimal], set[str]]:
    """The closes found, and the symbols whose broker call failed."""
    tasty = next((c for c in clients if hasattr(c, "market_data")), None)
    schwab = next((c for c in clients if hasattr(c, "quotes")), None)
    options = [s for s in symbols if OCC.match(s)]
    stocks = [s for s in symbols if not OCC.match(s) and not is_futures_option(s)]
    found: dict[str, Decimal] = {}
    failed: set[str] = set()
    if tasty is not None:
        for start in range(0, len(options), 50):
            batch = options[start : start + 50]
            try:
                items = tasty.market_data({"equity-option": batch})
            except Exception as e:  # noqa: BLE001 -- a broker being down mustn't stop the rest
                logger.info("previous closes unavailable from TastyTrade: %s", e)
                failed.update(batch)
                continue
            for item in items:
                close = _pick_close(item, wanted)
                if close is not None:
                    found[item["symbol"]] = close
    if schwab is not None and schwab_close_counts(today, now):
        unpriced = [s for s in options if s not in found]
        for start in range(0, len(unpriced), SCHWAB_BATCH):
            batch = unpriced[start : start + SCHWAB_BATCH]
            try:
                quotes = schwab.quotes(batch)
            except Exception as e:  # noqa: BLE001
                logger.info("previous closes unavailable from Schwab: %s", e)
                failed.update(batch)
                continue
            for symbol in batch:
                close = ((quotes.get(symbol) or {}).get("quote") or {}).get("closePrice")
                if close and Decimal(str(close)) > 0:
                    found[symbol] = Decimal(str(close))
    if stocks:
        from optionsjourney.services.watchlist_quotes import snapshot_quotes  # noqa: PLC0415

        try:
            quotes = snapshot_quotes(clients, stocks)
        except Exception as e:  # noqa: BLE001
            logger.info("previous closes unavailable for stocks: %s", e)
            failed.update(stocks)
        else:
            for symbol, quote in quotes.items():
                if quote.get("prev_close") is not None:
                    found[symbol] = Decimal(str(quote["prev_close"]))
    return found, failed


# --- snapshots ---------------------------------------------------------------------


def snapshots_before(session_factory: sessionmaker, keys: set[str], today: date) -> dict:
    """Each trade's latest snapshot from before today."""
    if not keys:
        return {}
    with session_factory() as session:
        latest = (
            select(PLSnapshot.position_key, func.max(PLSnapshot.day).label("day"))
            .where(PLSnapshot.position_key.in_(keys), PLSnapshot.day < today)
            .group_by(PLSnapshot.position_key)
            .subquery()
        )
        rows = session.execute(
            select(PLSnapshot.position_key, PLSnapshot.total_pl).join(
                latest,
                (PLSnapshot.position_key == latest.c.position_key)
                & (PLSnapshot.day == latest.c.day),
            )
        )
        return {key: total for key, total in rows}


def take_snapshots(session_factory, positions, hub, now: datetime) -> int | None:
    """Save every open trade's total P/L for today, once the session has been
    closed SNAPSHOT_AFTER. None when it isn't time (or already done)."""
    today = market_date(now)
    close = session_close(today)
    if close is None:
        return None
    closed_at = datetime.combine(today, close, MARKET_TZ) + SNAPSHOT_AFTER
    if now < closed_at:
        return None
    with session_factory() as session:
        if session.scalar(select(PLSnapshot.day).where(PLSnapshot.day == today).limit(1)):
            return None
    open_positions = [p for p in positions if p.status is not Status.CLOSED]
    keys = {leg.symbol for p in open_positions for leg in p.live_legs if leg.remaining > 0}
    hub.subscribe(keys)
    # Never from a pulled market (#241): such a leg is marked at its last real mark or not at all.
    marks = hub_marks(hub, keys, now, strict=True)
    multipliers = _multipliers(open_positions)
    saved = 0
    with session_factory() as session:
        for symbol, mark in hub.good_marks(keys).items():
            session.merge(CloseMark(symbol=symbol, mark=mark, saved_at=now))
        for p in open_positions:
            total = total_pl(p, marks, multipliers)
            if total is None:
                continue
            session.merge(PLSnapshot(position_key=p.key, day=today, total_pl=total, taken_at=now))
            saved += 1
        session.commit()
    logger.info("P/L Day: saved %d trades' totals for %s", saved, today)
    return saved


def _multipliers(positions) -> dict:
    return multipliers_from_fills(
        fill for p in positions for event in p.events for order in event.orders
        for fill in order.fills
    )  # fmt: skip


def start_snapshots(session_factory, load_positions: Callable[[], list], hub) -> threading.Thread:
    """Check every 10 minutes whether today's snapshots are due."""

    def loop():
        while True:
            try:
                take_snapshots(session_factory, load_positions(), hub, datetime.now(UTC))
            except Exception:  # noqa: BLE001 -- keep checking
                logger.exception("P/L Day snapshot failed")
            time.sleep(CHECK_EVERY)

    thread = threading.Thread(target=loop, name="pl-day-snapshots", daemon=True)
    thread.start()
    return thread


# --- the journal's numbers -----------------------------------------------------------


def for_entries(session_factory, entries, hub, clients, now: datetime | None = None) -> DayPL:
    """P/L Day for the journal's trades that were open at some point today."""
    now = now or datetime.now(UTC)
    today = session_day(now)
    positions = [
        e.position
        for e in entries
        if not e.awaiting_broker
        and (
            e.position.status is not Status.CLOSED
            or (e.position.closed_at and market_date(e.position.closed_at) >= today)
        )
    ]
    keys = {leg.symbol for p in positions for leg in p.live_legs if leg.remaining > 0}
    hub.subscribe(keys)
    marks = hub_marks(hub, keys, now)
    snapshots = snapshots_before(session_factory, {p.key for p in positions}, today)
    need_closes = {
        leg.symbol
        for p in positions
        if p.key not in snapshots and market_date(p.opened_at) < today
        for leg in p.legs
    }
    closes = previous_closes(clients, need_closes, today, now) if need_closes else {}
    multipliers = _multipliers(positions)
    results = {
        p.key: pl_day(p, marks, today, snapshots.get(p.key), closes, multipliers) for p in positions
    }
    known = [r.value for r in results.values() if r.value is not None]
    return DayPL(
        day=today,
        positions=results,
        total=sum(known, Decimal("0")) if known else None,
        priced=len(known),
        approximate=any(r.approximate for r in results.values()),
    )


def load_close_marks(session_factory, hub) -> None:
    """Give the hub the marks saved at earlier closes (#241), so a restart after
    hours still prices a pulled market at its close."""
    with session_factory() as session:
        hub.seed_good_marks({row.symbol: row.mark for row in session.scalars(select(CloseMark))})


def start(services) -> threading.Thread:
    """Start the snapshot checker with the app's services."""
    from optionsjourney.services import journal  # noqa: PLC0415

    try:
        load_close_marks(services.session_factory, services.quote_hub)
    except Exception:  # noqa: BLE001 -- live marks still work without them
        logger.exception("saved close marks couldn't be loaded")

    def positions():
        view = journal.load_journal(services.session_factory, cache=services.positions_cache)
        return [e.position for e in view.entries]

    return start_snapshots(services.session_factory, positions, services.quote_hub)
