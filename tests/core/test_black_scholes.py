"""Black-Scholes basics (#26)."""

from datetime import UTC, date, datetime

import pytest

from optionsjourney.core.black_scholes import bs_price, expiry_close_utc, greeks, implied_vol

NOW = datetime(2026, 10, 9, 14, 0, tzinfo=UTC)  # Fri 10:00 New York
EXP = date(2026, 10, 16)


def test_black_scholes_textbook_values():
    assert bs_price(100, 100, 1, 0.05, 0.2, "CALL") == pytest.approx(10.4506, abs=1e-4)
    assert bs_price(100, 100, 1, 0.05, 0.2, "PUT") == pytest.approx(5.5735, abs=1e-4)
    assert bs_price(100, 90, 0, 0.05, 0.2, "CALL") == 10  # at expiration: intrinsic


def test_implied_vol_recovers_the_volatility():
    price = bs_price(5800, 5750, 30 / 365, 0.045, 0.18, "PUT")
    assert implied_vol(price, 5800, 5750, 30 / 365, 0.045, "PUT") == pytest.approx(0.18, abs=1e-4)
    assert implied_vol(0.0, 5800, 5000, 30 / 365, 0.045, "PUT") is None  # no price, no IV


def test_greeks_signs_and_textbook_delta():
    g = greeks(100, 100, 1, 0.05, 0.2, "CALL")
    assert g["delta"] == pytest.approx(0.6368, abs=1e-4)
    assert g["gamma"] > 0 and g["vega"] > 0 and g["theta"] < 0
    assert greeks(100, 100, 1, 0.05, 0.2, "PUT")["delta"] == pytest.approx(0.6368 - 1, abs=1e-4)


def test_expiry_close_is_4pm_new_york():
    assert expiry_close_utc(EXP) == datetime(2026, 10, 16, 20, 0, tzinfo=UTC)
