"""Settling expiring trades at the close (#147, decision 0062).

After each trading day's close (16:15 New York, like Tracker2's auto-expire),
when the user has turned it on, the option legs of open trades that expire
that day are settled at their underlying's close (core/expiry.py has the
math and the skips). Settlements are stored, and folded into the fills when
positions are built (services/journal.py), so the trades close the same
day. A broker's own expiration, assignment or exercise record replaces a
settlement as soon as it's synced.

Like Tracker2, nothing is settled until a trade sync has finished after the
close, so closing orders filled up to the bell are counted first. The job
syncs first itself, so it doesn't depend on the timed sync. If the sync
fails or a close isn't out yet, it tries again on the next check that
evening, until GIVE_UP_AT."""

import hashlib
import json
import logging
import threading
import time
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from datetime import time as clock
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from optionsjourney.core.expiry import BROKER_RECORDS, Settlement, is_settlement, settle_expiring
from optionsjourney.core.expiry_prices import PriceRead, pl_changed
from optionsjourney.core.market import MARKET_TZ, market_date
from optionsjourney.core.sessions import next_session_after, session_close
from optionsjourney.core.transactions import OptionDetails
from optionsjourney.db.models import ExpirySettlement, Setting, SyncState
from optionsjourney.services import app_alerts
from optionsjourney.services.transactions import load_fills

logger = logging.getLogger(__name__)

SWITCH_KEY = "expiry_settle_on"
LAST_RUN_KEY = "expiry_settle_last_run"
SETTLE_AFTER = timedelta(minutes=15)  # after the close
GIVE_UP_AT = clock(20, 0)  # New York: stop retrying for the day
CHECK_EVERY = 300  # seconds
NO_CLOSE = "no close for"  # a skip worth retrying
UNSYNCED = "its account hasn't synced since the close; tried again until 8 PM"


# --- the switch and the last run -----------------------------------------------------


def is_on(session_factory: sessionmaker) -> bool:
    with session_factory() as session:
        row = session.get(Setting, SWITCH_KEY)
    return row is not None and row.value == "1"


def set_on(session_factory: sessionmaker, on: bool) -> None:
    _save(session_factory, SWITCH_KEY, "1" if on else "0")


def last_run(session_factory: sessionmaker) -> dict | None:
    """{"day", "at", "done", "settled", "skipped": [{"name", "reason"}], "note"}."""
    with session_factory() as session:
        row = session.get(Setting, LAST_RUN_KEY)
    return json.loads(row.value) if row is not None else None


def _save(session_factory, key: str, value: str) -> None:
    with session_factory() as session:
        row = session.get(Setting, key) or Setting(key=key)
        row.value, row.updated_at = value, datetime.now(UTC)
        session.add(row)
        session.commit()


# --- stored settlements ----------------------------------------------------------------


def load_settlements(session_factory: sessionmaker) -> list[Settlement]:
    with session_factory() as session:
        return [_settlement_of(r) for r in session.scalars(select(ExpirySettlement))]


def _settlement_of(r: ExpirySettlement) -> Settlement:
    return Settlement(
        broker=r.broker,
        account_number=r.account_number,
        symbol=r.symbol,
        underlying=r.underlying,
        option=OptionDetails(r.put_call, r.strike, r.expiration),
        sign=r.sign,
        quantity=r.quantity,
        close=r.close_price,
        fee=r.fee,
        status=r.status,
        source=r.price_source,
    )


def version(session_factory: sessionmaker) -> tuple:
    """Changes whenever a settlement is added or corrected (part of the positions cache's
    key)."""
    with session_factory() as session:
        return tuple(
            session.execute(
                select(
                    func.count(),
                    func.max(ExpirySettlement.created_at),
                    func.max(ExpirySettlement.finalized_at),
                    # status only goes prelim -> final, so this rises with each correction
                    # whatever the clock says
                    func.count().filter(ExpirySettlement.status == "final"),
                )
            ).one()
        )


def _store(session_factory, rows: list[Settlement], now: datetime) -> list[Settlement]:
    """Add the settlements not stored yet; returns the ones added."""
    added = []
    with session_factory() as session:
        for s in rows:
            exists = session.scalar(
                select(ExpirySettlement.id).where(
                    ExpirySettlement.broker == s.broker,
                    ExpirySettlement.account_number == s.account_number,
                    ExpirySettlement.symbol == s.symbol,
                    ExpirySettlement.sign == s.sign,
                )
            )
            if exists:
                continue
            added.append(s)
            session.add(
                ExpirySettlement(
                    broker=s.broker,
                    account_number=s.account_number,
                    symbol=s.symbol,
                    underlying=s.underlying,
                    put_call=s.option.put_call,
                    strike=s.option.strike,
                    expiration=s.option.expiration,
                    sign=s.sign,
                    quantity=s.quantity,
                    close_price=s.close,
                    fee=s.fee,
                    created_at=now,
                    status=s.status,
                    price_source=s.source,
                    prelim_close=s.close if s.status == "prelim" else None,
                )
            )
        session.commit()
    return added


# --- the daily run -----------------------------------------------------------------------


def synced_since(session_factory: sessionmaker, cutoff: datetime, accounts=None) -> bool:
    """Every synced account (or just `accounts`, (broker, number) pairs) has finished a
    sync after `cutoff`."""
    with session_factory() as session:
        states = list(session.scalars(select(SyncState).where(SyncState.broker != "manual")))
    if accounts is not None:
        states = [s for s in states if (s.broker, s.account_number) in accounts]
    return bool(states) and all(
        s.synced_through is not None and s.synced_through >= cutoff and not s.last_error
        for s in states
    )


def run_if_due(
    session_factory: sessionmaker,
    *,
    build_book: Callable[[], object],
    prices: Callable[[set], dict[tuple[str, date], PriceRead]],
    sync: Callable[[], object],
    now: datetime,
) -> dict | None:
    """Settle today's expirations if it's time and they're switched on.
    Returns the run's record, or None when nothing was due."""
    if not is_on(session_factory):
        return None
    today = market_date(now)
    close = session_close(today)
    if close is None:
        return None
    closed_at = datetime.combine(today, close, MARKET_TZ)
    if now < closed_at + SETTLE_AFTER:
        return None
    last = last_run(session_factory)
    if last and last["day"] == today.isoformat() and last["done"]:
        return None
    giving_up = now >= datetime.combine(today, GIVE_UP_AT, MARKET_TZ)

    record = {"day": today.isoformat(), "at": now.isoformat(), "settled": 0, "skipped": []}
    # Only the accounts holding something that expires today must have synced since the
    # close: a broker that can't sync (Schwab logged out) doesn't hold up the others.
    expiring = _expiring_accounts(build_book(), today)
    if not expiring:
        record.update(done=True, note="Nothing open expires today.")
        _save(session_factory, LAST_RUN_KEY, json.dumps(record))
        return record
    if not synced_since(session_factory, closed_at, expiring):
        try:
            sync()
        except Exception:  # accounts that synced since the close still settle
            logger.exception("The trade sync before settling failed")
    # An account that still hasn't synced waits on its own (#185): the others settle now.
    waiting = {a for a in expiring if not synced_since(session_factory, closed_at, {a})}
    book = build_book()
    held_up = [
        {"name": p.name, "reason": UNSYNCED}
        for p in book.positions
        if (p.broker, p.account_number) in waiting and _expires(p, today)
    ]
    if waiting == expiring:
        record.update(done=giving_up, skipped=held_up,
                      note="No trade sync since the close; nothing settled.")  # fmt: skip
        _save(session_factory, LAST_RUN_KEY, json.dumps(record))
        return record

    ready = [p for p in book.positions if (p.broker, p.account_number) not in waiting]
    unmatched = {
        (order.broker, order.account_number, fill.symbol)
        for order in book.unmatched
        for fill in order.fills
    }
    wanted = {
        (leg.underlying, today)
        for p in ready
        for leg in p.live_legs
        if leg.remaining > 0 and leg.option is not None and leg.option.expiration == today
    }
    reads: dict = prices(wanted) if wanted else {}
    rows, skipped = settle_expiring(ready, today, reads, unmatched)
    stored = _store(session_factory, rows, now)
    skipped_keys = {s.position_key for s in skipped}
    settled_keys = {p.key for p in ready if _expires(p, today) and p.key not in skipped_keys}
    if stored:
        try:
            _say_the_pl(session_factory, build_book(), today, settled_keys, stored, now)
        except Exception:  # noqa: BLE001 -- the settlements are stored; the run goes on
            logger.exception("Saying the P/L of the settled trades failed")
    retry = waiting or any(s.reason.startswith(NO_CLOSE) for s in skipped)
    record.update(
        settled=len(rows),
        skipped=[{"name": s.name, "reason": s.reason} for s in skipped] + held_up,
        done=giving_up or not retry,
        note=None,
    )
    _save(session_factory, LAST_RUN_KEY, json.dumps(record, default=_text))
    logger.info("Settled %d contracts at the close; skipped %d trades", len(rows), len(skipped))
    return record


def _say_the_pl(session_factory, book, day: date, keys: set[str], stored, now) -> None:
    """One unread alert with the P/L of the trades this run settled (#186)."""
    settled = [p for p in book.positions if p.key in keys]
    if not settled:
        return
    pl = sum((p.realized_pl for p in settled), Decimal(0))
    prelim = any(s.status == "prelim" for s in stored)
    count = len(settled)
    digest = hashlib.sha1("|".join(sorted(keys)).encode()).hexdigest()[:16]  # noqa: S324
    detail = f"{count} trade{'s' if count != 1 else ''} settled at the close."
    if prelim:
        detail += (
            " The prices are preliminary and are corrected to the official close when it's out."
        )
    app_alerts.add(
        session_factory,
        dedupe_key=f"settled:{day.isoformat()}:{digest}",
        kind="settled",
        title=f"Today's expirations settled: P/L {_money(pl)}" + (" (prelim)" if prelim else ""),
        detail=detail,
        link="/journal",
        now=now,
    )


# --- the correction pass ---------------------------------------------------------------


def _recorded(session_factory) -> set[tuple[str, str, str]]:
    """(broker, account, symbol) of each contract the broker's own expiration, assignment
    or exercise record has closed: its settlement no longer counts (with_settlements)."""
    return {
        (f.broker, f.account_number, f.symbol)
        for f in load_fills(session_factory).fills
        if f.kind in BROKER_RECORDS and not is_settlement(f)
    }


def _correctable(session_factory, now: datetime) -> list[ExpirySettlement]:
    """The prelim rows still inside their correction window (until 8 PM New York on the
    next trading day) that no broker record has replaced yet."""
    with session_factory() as session:
        prelims = list(
            session.scalars(select(ExpirySettlement).where(ExpirySettlement.status == "prelim"))
        )
    due = [
        r
        for r in prelims
        if now < datetime.combine(next_session_after(r.expiration), GIVE_UP_AT, MARKET_TZ)
    ]
    if not due:
        return []
    recorded = _recorded(session_factory)
    return [r for r in due if (r.broker, r.account_number, r.symbol) not in recorded]


def correct_prelims(
    session_factory: sessionmaker,
    *,
    official: Callable[[set], dict[tuple[str, date], PriceRead]],
    shown: Callable[[], dict[str, tuple[str, Decimal, tuple[str, str]]]],
    now: datetime,
) -> int:
    """Replace prelim settlements with the official close once it's out (#186), until
    8 PM New York on the next trading day, whether or not the switch is on. A prelim the
    broker's record has replaced is left alone: alert_broker_records compares that
    record with the P/L the user saw. Each trade whose shown P/L moved by a dollar or
    more gets one unread alert. `shown` gives {position key: (name, realized P/L,
    (broker, account))}. Returns the number of rows finalized."""
    due = _correctable(session_factory, now)
    if not due:
        return 0
    due_ids = {r.id for r in due}
    reads = official({(r.underlying, r.expiration) for r in due})
    pairs = sorted(
        {(r.underlying, r.expiration) for r in due if (r.underlying, r.expiration) in reads}
    )
    if not pairs:
        return 0

    # One (underlying, expiration) at a time, so a P/L that moves is pinned to the close
    # that moved it. version() counts the final rows, so the positions cache rebuilds
    # between them.
    corrected = 0
    shown_now = shown()
    for underlying, expiration in pairs:
        read = reads[(underlying, expiration)]
        with session_factory() as session:
            rows = [
                r
                for r in session.scalars(
                    select(ExpirySettlement).where(
                        ExpirySettlement.status == "prelim",
                        ExpirySettlement.underlying == underlying,
                        ExpirySettlement.expiration == expiration,
                    )
                )
                if r.id in due_ids
            ]
            sources = {r.id: r.price_source for r in rows}  # to undo the correction
            # Accounts can settle in separate runs, each at its own prelim
            prelims: dict[tuple[str, str], set[Decimal]] = {}
            for row in rows:
                prelims.setdefault((row.broker, row.account_number), set()).add(row.prelim_close)
                row.close_price, row.status = read.price, "final"
                row.price_source = read.source
                row.finalized_at = now
            session.commit()
        if not rows:
            continue
        before = shown_now
        try:
            shown_now = shown()
            for key, (name, old, account) in before.items():
                if key not in shown_now or not pl_changed(old, shown_now[key][1]):
                    continue
                prelim = prelims.get(account) or set().union(*prelims.values())
                app_alerts.add(
                    session_factory,
                    dedupe_key=f"correction:{key}:{expiration.isoformat()}",
                    kind="correction",
                    title=f"{name}: P/L corrected",
                    detail=(
                        f"{underlying} closed at {read.price}, "
                        f"not {' or '.join(str(p) for p in sorted(prelim))}. "
                        f"P/L {_money(old)} → {_money(shown_now[key][1])}."
                    ),
                    link="/journal",
                    now=now,
                )
        except Exception:  # noqa: BLE001 -- undone, so the next check tries again
            logger.exception("Alerting the correction of %s %s failed", underlying, expiration)
            _undo_correction(session_factory, sources)
            shown_now = before
            continue
        corrected += len(rows)
    logger.info("Corrected %d settlements to the official close", corrected)
    return corrected


def _undo_correction(session_factory, sources: dict[int, str]) -> None:
    """Put corrected rows back to prelim, as they were before the correction."""
    with session_factory() as session:
        for row_id, source in sources.items():
            row = session.get(ExpirySettlement, row_id)
            row.close_price, row.status = row.prelim_close, "prelim"
            row.price_source, row.finalized_at = source, None
        session.commit()


# --- the broker's record ---------------------------------------------------------------


_broker_check = threading.Lock()


def alert_broker_records(session_factory: sessionmaker, now: datetime) -> int:
    """Once a broker's own expiration, assignment or exercise of a settled contract is
    synced, compare its cash with the app's settlement, fees left out on both sides
    (#186). A difference of a dollar or more gets one unread alert. Each settlement is
    checked once. Returns the number of alerts raised.

    One check at a time (another in progress: returns 0), and the checked rows are
    marked in one commit: each settlement commit wakes the journal's watcher, which
    runs this check again."""
    if not _broker_check.acquire(blocking=False):
        return 0
    try:
        return _alert_broker_records(session_factory, now)
    finally:
        _broker_check.release()


def _alert_broker_records(session_factory: sessionmaker, now: datetime) -> int:
    from optionsjourney.services import journal  # noqa: PLC0415 -- journal imports this

    with session_factory() as session:
        pending = list(
            session.scalars(
                select(ExpirySettlement).where(ExpirySettlement.broker_alerted_at.is_(None))
            )
        )
    if not pending:
        return 0
    records: dict[tuple, list] = {}
    for f in load_fills(session_factory).fills:
        if f.kind in BROKER_RECORDS and not is_settlement(f):
            records.setdefault((f.broker, f.account_number, f.symbol), []).append(f)
    keys: dict[tuple, list[ExpirySettlement]] = {}
    for row in pending:
        key = (row.broker, row.account_number, row.symbol)
        if key in records:
            keys.setdefault(key, []).append(row)
    if not keys:
        return 0

    book = journal.build_unsplit(session_factory)[1]  # only for the trade names
    by_fill = {
        (fill.broker, fill.account_number, fill.transaction_id): position.name
        for position in book.positions
        for event in position.events
        for order in event.orders
        for fill in order.fills
    }
    raised = 0
    for key, rows in keys.items():
        app = sum((_settlement_of(r).value for r in rows), Decimal(0))
        broker = sum((f.value for f in records[key]), Decimal(0))
        # Schwab's in-the-money cash settlement isn't parsed yet (#187): its removal row
        # carries $0, so only out-of-the-money Schwab settlements are compared
        schwab_itm = key[0] == "schwab" and app != 0
        if not schwab_itm and pl_changed(app, broker):
            name = next(
                (
                    by_fill[(f.broker, f.account_number, f.transaction_id)]
                    for f in records[key]
                    if (f.broker, f.account_number, f.transaction_id) in by_fill
                ),
                key[2],
            )
            raised += app_alerts.add(
                session_factory,
                dedupe_key=f"broker:{min(r.id for r in rows)}",
                kind="broker_record",
                title=f"{name}: your broker's record differs",
                detail=f"Settled by the app: {_money(app)}. Your broker: {_money(broker)}.",
                link="/journal",
                now=now,
            )
    with session_factory() as session:
        for rows in keys.values():
            for r in rows:
                session.get(ExpirySettlement, r.id).broker_alerted_at = now
        session.commit()
    return raised


def _money(value: Decimal) -> str:
    return f"{'-' if value < 0 else ''}${abs(value):,.2f}"


def _expiring_accounts(book, today) -> set[tuple[str, str]]:
    """(broker, account) of each open trade with an option leg expiring `today`."""
    return {(p.broker, p.account_number) for p in book.positions if _expires(p, today)}


def _expires(position, today) -> bool:
    return any(
        leg.remaining > 0 and leg.option is not None and leg.option.expiration == today
        for leg in position.live_legs
    )


def _text(value):
    return str(value) if isinstance(value, Decimal) else value


def _shown(services, journal) -> dict[str, tuple[str, Decimal, tuple[str, str]]]:
    """{position key: (name, realized P/L, (broker, account))} of the journal's trades
    closed by a settlement."""
    view = journal.load_journal(services.session_factory, cache=services.positions_cache)
    return {
        e.position.key: (
            e.position.name,
            e.position.realized_pl,
            (e.position.broker, e.position.account_number),
        )
        for e in view.entries
        if e.settled_by_app
    }


def start(services) -> threading.Thread:
    """Check every few minutes whether today's settlements are due, whether any
    prelim settlement can be corrected to the official close, and whether a synced
    broker record changes a settled P/L."""
    from optionsjourney.services import expiry_prices, journal  # noqa: PLC0415
    from optionsjourney.services.sync import sync_all  # noqa: PLC0415

    def loop():
        while True:
            try:
                run_if_due(
                    services.session_factory,
                    build_book=lambda: journal.build_unsplit(services.session_factory)[1],
                    prices=lambda wanted: expiry_prices.prelim_prices(
                        services.broker_clients(), services.quote_hub, wanted
                    ),
                    sync=lambda: sync_all(services.broker_clients(), services.session_factory),
                    now=datetime.now(UTC),
                )
            except Exception:  # noqa: BLE001 -- keep checking
                logger.exception("Settling expirations failed")
            try:
                correct_prelims(
                    services.session_factory,
                    official=lambda wanted: expiry_prices.official_closes(
                        services.broker_clients(), wanted
                    ),
                    shown=lambda: _shown(services, journal),
                    now=datetime.now(UTC),
                )
            except Exception:  # noqa: BLE001 -- keep checking
                logger.exception("Correcting prelim settlements failed")
            try:
                alert_broker_records(services.session_factory, datetime.now(UTC))
            except Exception:  # noqa: BLE001 -- keep checking
                logger.exception("Comparing the brokers' records with the settlements failed")
            time.sleep(CHECK_EVERY)

    thread = threading.Thread(target=loop, name="expiry-settle", daemon=True)
    thread.start()
    return thread
