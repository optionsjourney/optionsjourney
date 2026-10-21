"""The option chain's pure helpers (#90), as Tracker2's analyzer server."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from optionsjourney.core.chain import (
    clean_ticker,
    filter_expirations,
    held_legs,
    holds_ticker,
    occ_from_dxlink,
)
from optionsjourney.core.positions import Position, PositionLeg
from optionsjourney.core.transactions import InstrumentType, OptionDetails

OCT17 = date(2026, 10, 17)


def test_tickers():
    assert clean_ticker(" spx ") == "SPX" and clean_ticker("/ES") == "/ES"
    assert clean_ticker("/ESZ6") is None  # a contract month, not a root
    assert clean_ticker("SPY 1") is None


def test_expirations_are_today_through_five_years_out():
    today = date(2026, 10, 10)
    days = [date(2026, 10, 9), today, date(2031, 12, 31), date(2032, 1, 1), today]
    assert filter_expirations(days, today) == ["2026-10-10", "2031-12-31"]


def test_dxlink_option_symbols_become_occ_keys():
    assert occ_from_dxlink(".SPXW261017C5800") == "SPXW  261017C05800000"
    assert occ_from_dxlink(".HOOD261023P42.5") == "HOOD  261023P00042500"
    assert occ_from_dxlink("SPX") is None


def series(day, root, strikes=1, settlement="PM", nv="0.5"):
    return {
        "expiration-date": day, "option-root-symbol": root, "underlying-symbol": "/ESZ6",
        "settlement-type": settlement, "notional-value": nv,
        "expires-at": f"{day}T21:00:00.000+00:00",
        "strikes": [
            {"strike-price": str(7500 + 5 * i),
             "call-streamer-symbol": f"./{root}X26C{7500 + 5 * i}:XCME",
             "put-streamer-symbol": f"./{root}X26P{7500 + 5 * i}:XCME"}
            for i in range(strikes)
        ],
    }  # fmt: skip


def test_futures_roots_and_series():
    from optionsjourney.core.chain import chain_root, pick_series, series_info, symbol_format

    assert chain_root("./ESZ6") == "/ES" and chain_root("SPX") == "SPX"
    picked = pick_series(
        [
            series("2026-11-06", "EW1", 3),
            series("2026-11-06", "E1A", 5),
            series("2026-12-18", "ES", 9, settlement="AM"),
            series("2026-10-01", "EW3"),
        ],
        date(2026, 10, 10),
    )
    assert list(picked) == ["2026-11-06"] and picked["2026-11-06"]["option-root-symbol"] == "E1A"
    info = series_info(picked["2026-11-06"])
    assert info["multiplier"] == 50 and info["underlying"] == "/ESZ6"
    assert symbol_format(picked["2026-11-06"]) == ("./E1AX26", ":XCME")


# --- held legs: the Trade page chain's Pos column (#210) ---


def held_leg(strike, put_call="PUT", sign=-1, opened=1, closed=0, underlying="SPX", rolled=False,
             option=True):  # fmt: skip
    return PositionLeg(
        symbol=f"{underlying}-{put_call}{strike}",
        underlying=underlying,
        instrument_type=InstrumentType.OPTION if option else InstrumentType.STOCK,
        option=OptionDetails(put_call, Decimal(str(strike)), date(2026, 11, 6)) if option else None,
        sign=sign,
        opened=Decimal(opened),
        closed=Decimal(closed),
        rolled=rolled,
    )


def held_position(account, *legs, broker="tastytrade"):
    return Position(
        key=f"{broker}:{account}:1",
        broker=broker,
        account_number=account,
        opened_at=datetime(2026, 10, 1, tzinfo=UTC),
        legs=list(legs),
        units=Decimal(1),
        entry_price=Decimal(0),
    )


ACCOUNT_IDS = {("tastytrade", "A1"): 1, ("tastytrade", "A2"): 2, ("schwab", "S3"): 3}


@pytest.mark.parametrize(
    "underlying,ticker,ok",
    [
        ("SPX", "SPX", True),
        ("SPXW", "SPX", True),  # a weekly root is its index
        ("SPY", "SPX", False),
        ("/GCZ6", "/GC", True),
        ("./ESZ6", "/ES", True),
        ("/ESZ6", "/MES", False),
        ("/MESZ6", "/ES", False),
        ("GC", "/GC", False),
    ],
)
def test_held_leg_matches_the_chain_ticker(underlying, ticker, ok):
    assert holds_ticker(underlying, ticker) is ok


def test_held_legs_net_per_account_strike_and_type():
    positions = [
        held_position("A1", held_leg(75, opened=2), held_leg(75, sign=1)),  # -2 + 1
        held_position("A2", held_leg(75, sign=1, opened=3, closed=1)),  # +2, one closed
        held_position("A1", held_leg(90, put_call="CALL")),
        # nets to zero in one account: dropped
        held_position("S3", held_leg(75), held_leg(75, sign=1), broker="schwab"),
    ]
    assert held_legs(positions, ACCOUNT_IDS, "SPX") == [
        {"account_id": 1, "expiration": "2026-11-06", "strike": 75.0, "type": "put", "qty": -1},
        {"account_id": 1, "expiration": "2026-11-06", "strike": 90.0, "type": "call", "qty": -1},
        {"account_id": 2, "expiration": "2026-11-06", "strike": 75.0, "type": "put", "qty": 2},
    ]


def test_held_legs_skip_closed_rolled_shares_other_tickers_and_unknown_accounts():
    positions = [
        held_position("A1", held_leg(75, closed=1)),  # fully closed
        held_position("A1", held_leg(80, rolled=True)),  # rolled away
        held_position("A1", held_leg(0, option=False)),  # shares
        held_position("A1", held_leg(500, underlying="SPY")),  # another ticker
        held_position("ZZ", held_leg(75)),  # an account the app doesn't list
    ]
    assert held_legs(positions, ACCOUNT_IDS, "SPX") == []


def test_held_legs_futures_root_and_fractional_strikes():
    positions = [held_position("A1", held_leg(3842.5, underlying="/GCZ6"))]
    assert held_legs(positions, ACCOUNT_IDS, "/GC") == [
        {"account_id": 1, "expiration": "2026-11-06", "strike": 3842.5, "type": "put", "qty": -1}
    ]
