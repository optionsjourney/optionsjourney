"""The 09:00 check that today's entries can be placed (#327, decision 0130; Tracker2's
market_data_check).

On 2026-11-09 TastyTrade changed its SPX chain in a way Tracker2 couldn't read, and the
first anyone heard of it was two entries failing at 09:47, though the same error had
repeated since 08:16. So, before the first entry, for each underlying an armed bot
(Live or Paper) trades today, this asks what an entry reads:

- each broker's expiration list (TastyTrade's, and Schwab's: the backup);
- for SPX and XSP, whose options quote overnight, one chain (the nearest expiration),
  with deltas when a bot on it picks by delta. Other underlyings have no option quotes
  before 09:30, so their chain is read only when the check runs after the open.

It alerts only on a problem (bots/market_check.py decides): "will NOT be placed", or
"degraded" when a backup covers it. A quiet morning means every check passed.

Like the 09:20 held-trades check (bot_overnight.preopen_pass): once a trading day, from
09:00 New York until the close, remembered in the `_market_data_checked` setting, so the
app started later still checks the entries to come. Not in the demo, whose pretend
brokers can't fail. It reads only: nothing is sent, cancelled or changed."""

import logging
import threading
from datetime import date, time

from optionsjourney.bots import market_check, schedule, strikes
from optionsjourney.core import black_scholes
from optionsjourney.core.market import MARKET_TZ
from optionsjourney.core.sessions import session_close
from optionsjourney.db.models import Setting
from optionsjourney.services import bot_entry, bot_store, economic_events

logger = logging.getLogger(__name__)
CHECK_FROM = time(9, 0)  # Tracker2's 09:00 ET job, before the earliest entries
CHECKED_KEY = "_market_data_checked"  # the New York date the check last ran


def check_pass(deps) -> None:
    """From 09:00 New York on a trading day until its close, once a day: the check.
    Never raises."""
    try:
        if getattr(deps, "demo", False):
            return
        now = deps.now()
        local = now.astimezone(MARKET_TZ)
        close = session_close(local.date())
        if close is None or not CHECK_FROM <= local.time() < close:
            return
        today = local.date().isoformat()
        with deps.session_factory() as session:
            row = session.get(Setting, CHECKED_KEY)
            if row is not None and row.value == today:
                return
            row = row or Setting(key=CHECKED_KEY)
            row.value, row.updated_at = today, now
            session.add(row)
            session.commit()
        start(_safe_check, deps)
    except Exception:  # noqa: BLE001 -- the monitor's other steps go on
        logger.exception("the market data check failed")


def start(fn, deps) -> None:
    """Off the monitor's thread: a broker timing out (10 s a call, two brokers, a chain
    and TastyTrade's 4 s quote wait) mustn't hold up the exits for long. Tests run it
    inline."""
    threading.Thread(target=fn, args=(deps,), name="bot-market-check", daemon=True).start()


def _safe_check(deps) -> None:
    try:
        check(deps)
    except Exception:  # noqa: BLE001 -- logged, never alerted
        logger.exception("the market data check failed")


def check(deps) -> list[market_check.Result]:
    """Check every underlying due now and alert on any problem. Returns each underlying's
    answer. A check that goes wrong is logged, never alerted: it says nothing about the
    entries."""
    local = deps.now().astimezone(MARKET_TZ)
    hhmm = local.strftime("%H:%M")
    bots = bot_store.armed(deps.session_factory)
    due = market_check.due(bots, local.date(), hhmm, _lists(deps, bots, local.date()))
    if not due:
        return []
    clients = deps.clients()
    results = [
        _check_one(deps, clients, symbol, deltas, local.date(), hhmm)
        for symbol, deltas in sorted(due.items())
    ]
    found = market_check.alert(results)
    if found is None:
        logger.info("market data check ok: %s", ", ".join(sorted(due)))
        return results
    title, detail = found
    logger.warning("market data check: %s", detail)
    bot_entry.alert(deps, title, detail, "/automation")
    return results


def _lists(deps, bots, day: date) -> dict:
    """Today's answer for the date lists the bots rely on, without the clock's alerts."""
    names = sorted({name for bot in bots for name in schedule.lists_in_use(bot)})
    if not names:
        return {}
    try:
        return economic_events.lists_for(deps.session_factory, names, day, deps.now())
    except Exception:  # noqa: BLE001 -- a list that can't tell adds no day
        logger.exception("market data check: couldn't read the date lists")
        return {}


def _check_one(deps, clients, symbol: str, deltas: bool, today: date, hhmm: str):
    listed = deps.chain.broker_expirations(clients, symbol)
    result, days = market_check.expirations(symbol, listed["tastytrade"], listed["schwab"])
    if result.blocked or not market_check.chain_due(symbol, hhmm):
        return result
    expiration = market_check.nearest(days, today)
    if expiration is None:
        market_check.chain(result, None, found=False, backup=False, deltas=None)
        return result
    try:
        rows, source = deps.chain.option_rows_from(
            clients, deps.hub, symbol, expiration.isoformat()
        )
    except Exception:  # noqa: BLE001 -- no chain is what the entry would see too
        logger.exception("market data check: the %s chain", symbol)
        rows, source = None, None
    has = _has_deltas(deps, clients, symbol, rows, expiration) if rows and deltas else None
    market_check.chain(
        result, expiration, found=bool(rows),
        backup=source == "tastytrade" and listed["schwab"].connected, deltas=has,
    )  # fmt: skip
    return result


def _has_deltas(deps, clients, symbol: str, rows: dict, expiration: date) -> bool | None:
    """Whether the chain's deltas would let a delta leg pick (strikes.chain_has_deltas),
    built as an entry builds it (bot_entry._pick). Before the open the underlying's
    price isn't moving, so streamed rows count while TastyTrade's stream is up. None
    when there's no price to work them out from: the entry reads a fresh one."""
    spot = bot_entry.underlying_price(deps, clients, symbol)
    if not spot:
        return None
    status = deps.hub.status() if hasattr(deps.hub, "status") else {}
    built = strikes.chain_from_rows(
        rows, spot, deltas=True, now=deps.now(),
        expires_at=black_scholes.expiry_close_utc(expiration),
        stream_ok=status.get("tastytrade") == "streaming",
    )  # fmt: skip
    return strikes.chain_has_deltas(built)
