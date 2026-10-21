"""Option Omega's market entry filters, as Tracker2 runs them (its vix.py and
technicals.py): the VIX level, VIX's overnight move, the VIX9D / VIX ratio,
SMA and EMA on the underlying, and the underlying's intraday move. Pure: the
prices come in from services/bot_filters.py, which reads them from DXLink.

Each evaluate_* returns (ok, detail). A filter that can't be decided (an
unknown mode, a period not set, too little history) returns not ok: it fails
closed, never open."""

from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from optionsjourney.bots import underlyings
from optionsjourney.bots.definition import MoveLimits
from optionsjourney.core import sessions
from optionsjourney.core.candles import Candle, ema
from optionsjourney.core.market import MARKET_TZ

MODES = ("below", "above", "compare")  # technicals.MODES; None/blank = off
RTH_OPEN, RTH_CLOSE = time(9, 30), time(16, 0)


def dx_symbol(underlying: str) -> str:
    """technicals.dx_symbol: XSP is a tenth of SPX, so every % and ratio is the same, and
    SPX prints reliably on DXLink (bots/underlyings.py)."""
    return underlyings.watch_symbol(underlying)


def pct(value: Decimal) -> str:
    return f"{value:+.2f}%".replace("+0.00%", "0.00%")


def move_failures(move: Decimal, limits: MoveLimits) -> list[str]:
    """The limits a signed % move falls outside of; empty = pass (vix.move_failures).
    Each max caps only its own direction; each min demands a move that way; with
    both mins set, either one is enough."""
    up, down = max(move, Decimal(0)), max(-move, Decimal(0))
    failed = []
    if limits.max_up is not None and up > limits.max_up:
        failed.append(f"max up {limits.max_up:.2f}%")
    if limits.max_down is not None and down > limits.max_down:
        failed.append(f"max down {limits.max_down:.2f}%")
    ups = limits.min_up is not None and up >= limits.min_up
    downs = limits.min_down is not None and down >= limits.min_down
    if limits.min_up is not None and limits.min_down is not None:
        if not (ups or downs):
            failed.append(f"min up {limits.min_up:.2f}% or min down {limits.min_down:.2f}%")
    elif limits.min_up is not None and not ups:
        failed.append(f"min up {limits.min_up:.2f}%")
    elif limits.min_down is not None and not downs:
        failed.append(f"min down {limits.min_down:.2f}%")
    return failed


def _move(label: str, start: Decimal, end: Decimal, limits: MoveLimits) -> tuple[bool, str]:
    move = (end - start) / start * 100
    failed = move_failures(move, limits)
    detail = f"{label} {start} -> {end} ({pct(move)})"
    if failed:
        detail += "; outside " + ", ".join(failed)
    return not failed, detail


def evaluate_overnight(
    prior_close: Decimal, open_price: Decimal, limits: MoveLimits
) -> tuple[bool, str]:
    """VIX's move from the prior close to today's first print (vix.evaluate)."""
    return _move("VIX", prior_close, open_price, limits)


def evaluate_move(
    symbol: str, open_price: Decimal, price: Decimal, limits: MoveLimits
) -> tuple[bool, str]:
    """The underlying's move from today's open (technicals.evaluate_move)."""
    return _move(symbol, open_price, price, limits)


def _bounds(value: Decimal, low: Decimal | None, high: Decimal | None) -> list[str]:
    failed = []
    if low is not None and value < low:
        failed.append(f"below min {low:g}")
    if high is not None and value > high:
        failed.append(f"above max {high:g}")
    return failed


def evaluate_level(vix: Decimal, low: Decimal | None, high: Decimal | None) -> tuple[bool, str]:
    """VIX inside [low, high], blank = no limit (vix.evaluate_level)."""
    failed = _bounds(vix, low, high)
    detail = f"VIX {vix}" + ("; " + ", ".join(failed) if failed else "")
    return not failed, detail


def evaluate_ratio(
    vix: Decimal, vix9d: Decimal, low: Decimal | None, high: Decimal | None
) -> tuple[bool, str]:
    """VIX9D / VIX inside [low, high] (vix.evaluate_ratio)."""
    ratio = vix9d / vix
    failed = _bounds(ratio, low, high)
    detail = f"VIX9D/VIX {vix9d} / {vix} = {ratio:.3f}"
    if failed:
        detail += "; " + ", ".join(failed)
    return not failed, detail


def sma(price: Decimal, prior_closes: list[Decimal], n: int) -> Decimal | None:
    """Option Omega's SMA(n): the current price plus the n-1 latest prior daily
    closes (newest first), over n. None with too few closes (technicals.sma)."""
    if len(prior_closes) < n - 1:
        return None
    return (price + sum(prior_closes[: n - 1], Decimal(0))) / n


def _final_ema(values: list[Decimal], n: int) -> Decimal | None:
    """The last EMA(n) of values, oldest first; the chart's formula (core.candles)."""
    if len(values) < n:
        return None
    return ema(values, n)[-1]


def _period(n) -> bool:
    return isinstance(n, int) and not isinstance(n, bool) and n > 0


def _decide(kind, name, mode, price, average_of, a, b) -> tuple[bool, str]:
    """Above / Below / Compare (technicals._decide). Fails closed on an unknown
    mode, a period not set, or too little history."""
    if mode not in MODES:
        return False, f"{kind} mode {mode!r} not recognised"
    if not _period(a) or (mode == "compare" and not _period(b)):
        return False, f"{kind} period not set"
    if mode == "compare":
        first, second = average_of(a), average_of(b)
        if first is None or second is None:
            return False, f"not enough history for {name(a)} / {name(b)}"
        ok = first > second
        return ok, f"{name(a)} {first:.2f} {'>' if ok else '<='} {name(b)} {second:.2f}"
    average = average_of(a)
    if average is None:
        return False, f"not enough history for {name(a)}"
    ok = price > average if mode == "above" else price < average
    relation = ">" if price > average else "<" if price < average else "="
    return ok, f"price {price:.2f} {relation} {name(a)} {average:.2f}"


def evaluate_sma(mode, price: Decimal, prior_closes: list[Decimal], a, b) -> tuple[bool, str]:
    """SMA Entry, daily (technicals.evaluate_sma)."""
    return _decide(
        "SMA", lambda n: f"SMA({n}d)", mode, price, lambda n: sma(price, prior_closes, n), a, b
    )


def evaluate_ema(mode, price: Decimal, bar_closes: list[Decimal], a, b) -> tuple[bool, str]:
    """EMA Entry on 1-minute closes, the current price as the last value
    (technicals.evaluate_ema)."""
    values = [*bar_closes, price]
    return _decide(
        "EMA", lambda n: f"EMA({n}m)", mode, price, lambda n: _final_ema(values, n), a, b
    )


def first_rth_open(bars: Iterable[Candle], day: date) -> Decimal | None:
    """The open of the 1-minute bar that starts at 09:30 New York on `day`; None if
    it isn't there (vix.first_rth_open). Not the day's open field: VIX trades from
    03:00. Unlike Tracker2, a later bar's open never stands in for a missing 09:30
    bar: a history that starts late (a Schwab start time taken as exclusive, a lost
    first bar) fails the filter closed rather than pass it on the wrong open."""
    found = [
        b.open
        for b in bars
        if b.open > 0
        and b.time.astimezone(MARKET_TZ).date() == day
        and b.time.astimezone(MARKET_TZ).time() == RTH_OPEN
    ]
    return found[0] if found else None


def has_bar_after_open(bars: Iterable[Candle], day: date) -> bool:
    """Whether `day` has any bar after 09:30 New York: then a missing 09:30 bar is
    a gap in the history, not one that hasn't printed yet."""
    return any(
        b.time.astimezone(MARKET_TZ).date() == day
        and b.time.astimezone(MARKET_TZ).time() > RTH_OPEN
        for b in bars
    )


def prior_daily_closes(bars: Iterable[Candle], day: date) -> list[Decimal]:
    """Closes of daily bars for sessions before `day`, newest first. Today's
    forming bar is left out by its date, never by position; a daily bar's time is
    midnight UTC of its session (technicals.prior_daily_closes)."""
    by_day: dict[date, Decimal] = {}
    for b in sorted(bars, key=lambda b: b.time):
        session = b.time.astimezone(UTC).date()
        if b.close > 0 and session < day:
            by_day[session] = b.close
    return [by_day[d] for d in sorted(by_day, reverse=True)]


def latest_prior_session(bars: Iterable[Candle], day: date) -> date | None:
    """The newest session before `day` that has a daily close."""
    found = [b.time.astimezone(UTC).date() for b in bars if b.close > 0]
    found = [d for d in found if d < day]
    return max(found) if found else None


def previous_session(day: date) -> date:
    """The last day before `day` the market was open (core.sessions' calendar)."""
    before = day - timedelta(days=1)
    while sessions.session_close(before) is None:
        before -= timedelta(days=1)
    return before


def completed_rth_closes(bars: Iterable[Candle], day: date, now: datetime) -> list[Decimal]:
    """Closes of finished 1-minute regular-session bars from the latest earlier
    session and `day`, oldest first. The bar still forming at `now` is dropped
    (technicals.completed_rth_closes)."""
    by_time: dict[datetime, tuple[date, Decimal]] = {}
    for b in bars:
        start = b.time.astimezone(MARKET_TZ)
        if b.close <= 0 or not RTH_OPEN <= start.time() < RTH_CLOSE:
            continue
        if b.time + timedelta(minutes=1) > now:
            continue
        by_time[b.time] = (start.date(), b.close)
    earlier = [d for d, _ in by_time.values() if d < day]
    keep = {day} | ({max(earlier)} if earlier else set())
    return [close for _, (d, close) in sorted(by_time.items()) if d in keep]
