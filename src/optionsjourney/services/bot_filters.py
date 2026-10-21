"""The market filters as entry guards (Tracker2's guards.vix_level_ok,
vix_overnight_ok, vix9d_ratio_ok and technical_filters_ok): read the prices
from TastyTrade's DXLink, or Schwab when TastyTrade can't give them
(services/market_feed.py), then decide with bots/market_filters.py.

`check` returns None when the entry may go ahead, or a Skip: an ordinary miss,
or a filter that couldn't be checked (`unavailable` names it), which fails
closed. Nothing here ever raises: a filter that breaks is a filter that
couldn't be checked."""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from optionsjourney.bots import market_filters as mf
from optionsjourney.bots.definition import BotDef
from optionsjourney.core.market import MARKET_TZ
from optionsjourney.services.market_feed import Got, Want

logger = logging.getLogger(__name__)

VIX, VIX9D = "VIX", "VIX9D"
MAX_AGE = timedelta(seconds=300)  # vix.RATIO_MAX_AGE_SECONDS, technicals.MAX_AGE_SECONDS
WAIT_SECONDS = 10  # an ordinary read (RATIO_WAIT_SECONDS, technicals.WAIT_SECONDS)
OPEN_WAIT_SECONDS = 60  # waiting for the 09:30 bar (DEFAULT_WAIT_SECONDS, OPEN_WAIT_SECONDS)
FIRST_BAR_WAIT_UNTIL = time(9, 31)  # technicals: the long wait only before this
DAILY_LOOKBACK = timedelta(days=300)  # covers the 200-day SMA
MINUTE_LOOKBACK = timedelta(days=5)  # reaches the previous session past a long weekend
MIN_CACHE_CLOSES = 199  # a daily history this long is cached for the day
_EPOCH = date(1970, 1, 1)

LEVEL, OVERNIGHT, RATIO = "VIX level", "VIX overnight move", "VIX9D/VIX ratio"
TECHNICALS, INTRADAY = "Technical indicators", "Intraday movement"


@dataclass(frozen=True)
class Skip:
    reason: str  # the run's reason
    unavailable: str | None = None  # the filter that couldn't be checked; None = a miss


class Unavailable(Exception):
    """A filter's prices couldn't be read: it fails closed."""


class Market:
    """The filters' prices, with Tracker2's day caches: VIX's prior close and
    open (vix.overnight_prices) and each symbol's daily closes (technicals)."""

    def __init__(self, feed):
        self.feed = feed
        self._lock = threading.Lock()
        self._overnight: dict[date, tuple[Decimal, Decimal]] = {}
        self._daily: dict[tuple[str, date], list[Decimal]] = {}

    def read(self, want: Want, wait_s: float, ready: Callable[[Got], bool]) -> Got:
        return self.feed.read(want, wait_s, ready)


def _day_ms(day: date, at: time) -> int:
    return int(datetime.combine(day, at, MARKET_TZ).timestamp() * 1000)


def _price(got: Got, symbol: str, now: datetime) -> Decimal:
    """A symbol's latest trade, refused when missing or more than 5 minutes old."""
    found = got.trades.get(symbol)
    if found is None:
        raise Unavailable(f"no {symbol} price from {got.source}")
    price, at_ms = found
    at = datetime.fromtimestamp(at_ms / 1000, MARKET_TZ)
    age = now - at
    if age > MAX_AGE:
        minutes = int(age.total_seconds() // 60)
        raise Unavailable(
            f"{symbol} last printed at {at:%H:%M:%S} New York, {minutes} min ago; too stale to use"
        )
    return price


def _fresh(got: Got, symbol: str, now: datetime) -> bool:
    found = got.trades.get(symbol)
    return found is not None and now.timestamp() - found[1] / 1000 <= MAX_AGE.total_seconds()


def _level(market: Market, bot: BotDef, now: datetime) -> tuple[bool, str]:
    # Wait for a fresh print: DXLink first replays the last one, however old.
    got = market.read(Want(trades=(VIX,)), WAIT_SECONDS, lambda g: _fresh(g, VIX, now))
    return mf.evaluate_level(_price(got, VIX, now), bot.vix_min, bot.vix_max)


def _ratio(market: Market, bot: BotDef, now: datetime) -> tuple[bool, str]:
    symbols = (VIX, VIX9D)
    got = market.read(
        Want(trades=symbols), WAIT_SECONDS, lambda g: all(_fresh(g, s, now) for s in symbols)
    )
    vix, vix9d = (_price(got, s, now) for s in symbols)
    return mf.evaluate_ratio(vix, vix9d, bot.vix9d_ratio_min, bot.vix9d_ratio_max)


def _overnight_prices(market: Market, day: date) -> tuple[Decimal, Decimal]:
    """(prior close, open), read once a day (vix.overnight_prices). The prior close
    is only taken from a Summary for today's day id; an older one holds the close
    from two sessions back. An incomplete read isn't cached."""
    with market._lock:
        if day in market._overnight:
            return market._overnight[day]
    today_id = (day - _EPOCH).days
    symbol = f"{VIX}{{=1m}}"

    def parts(got: Got):
        summary = got.summaries.get(VIX)
        prior = summary[1] if summary and summary[0] == today_id and summary[1] else None
        # DXLink sends a history newest first: until it has all arrived, the earliest
        # bar seen may not be 09:30's, so the open only counts once it's complete.
        whole = got.is_complete(symbol)
        return prior, mf.first_rth_open(got.bars(symbol), day) if whole else None

    want = Want(summaries=(VIX,), candles=((symbol, _day_ms(day, mf.RTH_OPEN)),))
    got = market.read(want, OPEN_WAIT_SECONDS, lambda g: None not in parts(g))
    prior, open_price = parts(got)
    if prior is None:
        raise Unavailable(f"no VIX prior close from {got.source}")
    if not got.is_complete(symbol):
        raise Unavailable(f"VIX 1-minute history incomplete from {got.source}")
    if open_price is None:
        raise Unavailable(_no_open(VIX, got, symbol, day, "no VIX print at or after 09:30 yet"))
    with market._lock:
        market._overnight[day] = (prior, open_price)
    return prior, open_price


def _overnight(market: Market, bot: BotDef, now: datetime) -> tuple[bool, str]:
    prior, open_price = _overnight_prices(market, now.astimezone(MARKET_TZ).date())
    return mf.evaluate_overnight(prior, open_price, bot.vix_overnight)


@dataclass(frozen=True)
class _Snapshot:
    symbol: str
    price: Decimal
    prior_closes: list[Decimal] | None
    minute_closes: list[Decimal] | None
    today_open: Decimal | None
    no_open: str = ""  # why today_open is None


NO_OPEN_YET = "no {symbol} bar at or after 09:30 yet"


def _no_open(name: str, got: Got, candle_symbol: str, day: date, yet: str) -> str:
    """Why there's no 09:30 open: `yet` (none printed so far), or a history that
    skips 09:30 (a later bar's open is never used for it)."""
    if mf.has_bar_after_open(got.bars(candle_symbol), day):
        return f"no {name} 09:30 bar from {got.source}"
    return yet


def _snapshot(market, underlying, now, need_daily, need_minute, need_open) -> _Snapshot:
    """One read for the technical indicators and the intraday move together
    (technicals.snapshot). Raises Unavailable."""
    symbol = mf.dx_symbol(underlying)
    local = now.astimezone(MARKET_TZ)
    day = local.date()
    daily_symbol, minute_symbol = f"{symbol}{{=1d}}", f"{symbol}{{=1m}}"
    cached = None
    if need_daily:
        with market._lock:
            cached = market._daily.get((symbol, day))
    candles = []
    if need_daily and cached is None:
        candles.append((daily_symbol, _day_ms(day - DAILY_LOOKBACK, time(0))))
    if need_minute or need_open:
        candles.append((minute_symbol, _day_ms(day - MINUTE_LOOKBACK, mf.RTH_OPEN)))
    wait = OPEN_WAIT_SECONDS if need_open and local.time() < FIRST_BAR_WAIT_UNTIL else WAIT_SECONDS

    def ready(got: Got) -> bool:
        have_open = not need_open or mf.first_rth_open(got.bars(minute_symbol), day) is not None
        return (
            _fresh(got, symbol, now) and all(got.is_complete(s) for s, _ in candles) and have_open
        )

    got = market.read(Want(trades=(symbol,), candles=tuple(candles)), wait, ready)
    price = _price(got, symbol, now)
    prior_closes = cached
    if need_daily and prior_closes is None:
        if not got.is_complete(daily_symbol):
            raise Unavailable(f"{symbol} daily history incomplete from {got.source}")
        latest, previous = (
            mf.latest_prior_session(got.bars(daily_symbol), day),
            mf.previous_session(day),
        )
        if latest != previous:
            ends = latest.isoformat() if latest else "nowhere"
            raise Unavailable(
                f"{symbol} daily history ends {ends}, not the previous session {previous}"
            )
        prior_closes = mf.prior_daily_closes(got.bars(daily_symbol), day)
        if len(prior_closes) >= MIN_CACHE_CLOSES:  # a short history isn't kept for the day
            with market._lock:
                market._daily[(symbol, day)] = prior_closes
    minute_closes = today_open = None
    no_open = ""
    if (need_minute or need_open) and not got.is_complete(minute_symbol):
        # Newest first: a partial history has no reliable open or EMA.
        raise Unavailable(f"{symbol} 1-minute history incomplete from {got.source}")
    if need_minute:
        minute_closes = mf.completed_rth_closes(got.bars(minute_symbol), day, now)
    if need_open:
        today_open = mf.first_rth_open(got.bars(minute_symbol), day)
        if today_open is None:
            no_open = _no_open(symbol, got, minute_symbol, day, NO_OPEN_YET.format(symbol=symbol))
    return _Snapshot(symbol, price, prior_closes, minute_closes, today_open, no_open)


def _short_history(name: str, unit: str, mode, a, b, have: int, extra: int) -> str | None:
    """Too few values for a period: the filter can't be checked (it fails closed)."""
    periods = [p for p in (a, b if mode == "compare" else None) if isinstance(p, int)]
    for n in periods:
        if have + extra < n:
            return f"not enough history for {name}({n}{unit})"
    return None


def _technicals(market, bot: BotDef, now: datetime) -> Skip | None:
    """Tracker2's technical_filters_ok: SMA / EMA, then the intraday move, from one
    read. Returns a Skip or None."""
    use_technicals = bool(bot.use_technicals and (bot.sma_mode or bot.ema_mode))
    use_move = bool(bot.use_intraday_move and bot.intraday.any_set)
    if not (use_technicals or use_move):
        return None
    need_daily, need_minute = (
        use_technicals and bool(bot.sma_mode),
        use_technicals and bool(bot.ema_mode),
    )
    try:
        snap = _snapshot(market, bot.underlying, now, need_daily, need_minute, use_move)
    except Unavailable as e:
        return _unavailable(TECHNICALS if use_technicals else INTRADAY, str(e))
    if use_technicals:
        results, short = [], None
        if bot.sma_mode:
            short = _short_history("SMA", "d", bot.sma_mode, bot.sma_days_a, bot.sma_days_b,
                                   len(snap.prior_closes), 1)  # fmt: skip
            results.append(mf.evaluate_sma(bot.sma_mode, snap.price, snap.prior_closes,
                                           bot.sma_days_a, bot.sma_days_b))  # fmt: skip
        if bot.ema_mode and short is None:
            short = _short_history("EMA", "m", bot.ema_mode, bot.ema_minutes_a, bot.ema_minutes_b,
                                   len(snap.minute_closes), 1)  # fmt: skip
            results.append(mf.evaluate_ema(bot.ema_mode, snap.price, snap.minute_closes,
                                           bot.ema_minutes_a, bot.ema_minutes_b))  # fmt: skip
        if short is not None:
            return _unavailable(TECHNICALS, f"{snap.symbol} has {short}")
        if not all(ok for ok, _ in results):
            detail = f"{snap.symbol} " + "; ".join(text for _, text in results)
            return _miss(TECHNICALS, detail)
    if use_move:
        if snap.today_open is None:
            return _unavailable(INTRADAY, snap.no_open)
        ok, detail = mf.evaluate_move(snap.symbol, snap.today_open, snap.price, bot.intraday)
        if not ok:
            return _miss(INTRADAY, detail)
    return None


def _miss(label: str, detail: str) -> Skip:
    return Skip(f"{label} filter: {detail}")


def _unavailable(label: str, why: str) -> Skip:
    return Skip(f"{label} unavailable: {why}. Not entered.", label)


def _simple(label, run, market, bot, now) -> Skip | None:
    try:
        ok, detail = run(market, bot, now)
    except Unavailable as e:
        return _unavailable(label, str(e))
    logger.info("bot %s %s %s: %s", bot.id, label, "ok" if ok else "blocked", detail)
    return None if ok else _miss(label, detail)


def check(market: Market | None, bot: BotDef, now: datetime) -> Skip | None:
    """The bot's market filters, in Tracker2's order (guards.check_all): the VIX
    level, VIX's overnight move, the VIX9D/VIX ratio, then the technical
    indicators and intraday move. The first that fails wins. A filter that's off,
    or on with nothing set, reads nothing."""
    steps = (
        (LEVEL, bot.use_vix_level and (bot.vix_min is not None or bot.vix_max is not None), _level),
        (OVERNIGHT, bot.use_vix_overnight and bot.vix_overnight.any_set, _overnight),
        (RATIO, bot.use_vix9d_ratio
         and (bot.vix9d_ratio_min is not None or bot.vix9d_ratio_max is not None), _ratio),
    )  # fmt: skip
    technicals_on = (bot.use_technicals and (bot.sma_mode or bot.ema_mode)) or (
        bot.use_intraday_move and bot.intraday.any_set
    )
    label = LEVEL
    try:
        for label, on, run in steps:
            if not on:
                continue
            if market is None:
                return _unavailable(label, "no market data source")
            found = _simple(label, run, market, bot, now)
            if found is not None:
                return found
        if technicals_on:
            label = (
                TECHNICALS if bot.use_technicals and (bot.sma_mode or bot.ema_mode) else INTRADAY
            )
            if market is None:
                return _unavailable(label, "no market data source")
            return _technicals(market, bot, now)
    except Exception as e:  # noqa: BLE001 -- a filter that breaks fails closed, never raises
        logger.exception("bot %s: the %s filter broke", bot.id, label)
        return _unavailable(label, f"couldn't read the prices ({e})")
    return None


_alerted: set[tuple[int, str, date]] = set()
_alerted_lock = threading.Lock()


def first_today(bot_id: int, label: str, day: date) -> bool:
    """True the first time a filter couldn't be checked for this bot today, so it
    alerts once a day, not at every entry time (guards._UNAVAILABLE_ALERTED)."""
    with _alerted_lock:
        if (bot_id, label, day) in _alerted:
            return False
        _alerted.add((bot_id, label, day))
        return True


def reset_alerts() -> None:
    with _alerted_lock:
        _alerted.clear()


# --- Bots slice 20: the filters as they read now -------------------------------------

PREVIEW_WAIT_SECONDS = 3  # a page's read: a fresh print if one comes, else the last one


def _last(got: Got, symbol: str, now: datetime) -> tuple[Decimal, datetime, bool]:
    """A symbol's last trade, however old: (price, when, stale)."""
    found = got.trades.get(symbol)
    if found is None:
        raise Unavailable(f"no {symbol} price from {got.source}")
    price, at_ms = found
    at = datetime.fromtimestamp(at_ms / 1000, MARKET_TZ)
    return price, at, now - at > MAX_AGE


def _shown(label: str, ok: bool, detail: str, at: datetime, stale: bool) -> dict:
    return {"label": label, "ok": ok, "detail": detail, "as_of": f"{at:%H:%M:%S}",
            "stale": stale}  # fmt: skip


def preview(market: Market | None, bot: BotDef, now: datetime) -> list[dict]:
    """The VIX level and VIX9D/VIX ratio filters as they read now (Tracker2's
    _preview_vix_level and _preview_vix9d_ratio, read with max_age_seconds=None): each
    `{label, ok, detail, as_of, stale}`, or `{label, error}` when its prices can't be read.
    After hours the prints are stale, which a live entry would refuse, so they're shown
    anyway and flagged. One read of the quotes both need; never sends anything and never
    raises."""
    steps = [
        (label, symbols)
        for label, on, symbols in (
            (LEVEL, bot.use_vix_level and (bot.vix_min is not None or bot.vix_max is not None),
             (VIX,)),
            (RATIO, bot.use_vix9d_ratio
             and (bot.vix9d_ratio_min is not None or bot.vix9d_ratio_max is not None),
             (VIX, VIX9D)),
        )
        if on
    ]  # fmt: skip
    if not steps:
        return []
    if market is None:
        return [{"label": label, "error": "no market data source"} for label, _ in steps]
    wanted = tuple(dict.fromkeys(s for _, symbols in steps for s in symbols))
    try:
        got = market.read(
            Want(trades=wanted), PREVIEW_WAIT_SECONDS,
            lambda g: all(_fresh(g, s, now) for s in wanted),
        )  # fmt: skip
    except Exception as e:  # noqa: BLE001 -- a preview that breaks says so, never raises
        logger.exception("bot %s: couldn't read the filters' prices", bot.id)
        return [{"label": label, "error": f"couldn't read the prices ({e})"} for label, _ in steps]
    out = []
    for label, symbols in steps:
        try:
            read = [_last(got, s, now) for s in symbols]
            if label == LEVEL:
                ok, detail = mf.evaluate_level(read[0][0], bot.vix_min, bot.vix_max)
            else:
                ok, detail = mf.evaluate_ratio(
                    read[0][0], read[1][0], bot.vix9d_ratio_min, bot.vix9d_ratio_max
                )
            oldest = min(r[1] for r in read)
            out.append(_shown(label, ok, detail, oldest, any(r[2] for r in read)))
        except Unavailable as e:
            out.append({"label": label, "error": str(e)})
        except Exception as e:  # noqa: BLE001
            logger.exception("bot %s: couldn't preview the %s filter", bot.id, label)
            out.append({"label": label, "error": f"couldn't read the prices ({e})"})
    return out
