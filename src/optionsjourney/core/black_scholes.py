"""Black-Scholes without dividends (rate 4.5%), as Tracker2's analyzer math:
an option's price, the volatility that gives a price, and greeks per share
(vega per 1.00 of IV, theta per year). Analyze's page does its own modelling in
the browser; this fills in deltas the brokers don't send (#26).

Floats, not Decimals: this is modelling, not money."""

import math
from datetime import date, datetime, time

from optionsjourney.core.market import MARKET_TZ

RATE = 0.045
YEAR = 365 * 24 * 3600


def _n(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _pdf(x: float) -> float:
    return math.exp(-x * x / 2) / math.sqrt(2 * math.pi)


def _intrinsic(spot: float, strike: float, put_call: str) -> float:
    return max(spot - strike, 0.0) if put_call == "CALL" else max(strike - spot, 0.0)


def bs_price(
    spot: float, strike: float, years: float, rate: float, vol: float, put_call: str
) -> float:
    if years <= 0 or vol <= 0 or spot <= 0:
        return _intrinsic(spot, strike, put_call)
    root = vol * math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + vol * vol / 2) * years) / root
    d2 = d1 - root
    discount = strike * math.exp(-rate * years)
    if put_call == "CALL":
        return spot * _n(d1) - discount * _n(d2)
    return discount * _n(-d2) - spot * _n(-d1)


def implied_vol(
    price: float, spot: float, strike: float, years: float, rate: float, put_call: str
) -> float | None:
    """The volatility that prices the option at `price` (bisection between
    0.5% and 500%), or None when no volatility does."""
    if price <= 0 or years <= 0:
        return None
    low, high = 0.005, 5.0
    if (
        not bs_price(spot, strike, years, rate, low, put_call)
        <= price
        <= bs_price(spot, strike, years, rate, high, put_call)
    ):
        return None
    for _ in range(100):
        mid = (low + high) / 2
        if bs_price(spot, strike, years, rate, mid, put_call) < price:
            low = mid
        else:
            high = mid
    return (low + high) / 2


def greeks(
    spot: float, strike: float, years: float, rate: float, vol: float, put_call: str
) -> dict:
    """Per share: delta, gamma, vega (per 1.00 of IV) and theta (per year)."""
    if years <= 0 or vol <= 0:
        itm = _intrinsic(spot, strike, put_call) > 0
        delta = (1.0 if put_call == "CALL" else -1.0) if itm else 0.0
        return {"delta": delta, "gamma": 0.0, "vega": 0.0, "theta": 0.0}
    root = vol * math.sqrt(years)
    d1 = (math.log(spot / strike) + (rate + vol * vol / 2) * years) / root
    d2 = d1 - root
    common = -spot * _pdf(d1) * vol / (2 * math.sqrt(years))
    carry = rate * strike * math.exp(-rate * years)
    if put_call == "CALL":
        return {
            "delta": _n(d1),
            "gamma": _pdf(d1) / (spot * root),
            "vega": spot * _pdf(d1) * math.sqrt(years),
            "theta": common - carry * _n(d2),
        }
    return {
        "delta": _n(d1) - 1,
        "gamma": _pdf(d1) / (spot * root),
        "vega": spot * _pdf(d1) * math.sqrt(years),
        "theta": common + carry * _n(-d2),
    }


def expiry_close_utc(day: date) -> datetime:
    from datetime import UTC  # noqa: PLC0415

    return datetime.combine(day, time(16, 0), tzinfo=MARKET_TZ).astimezone(UTC)
