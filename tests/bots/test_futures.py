"""Bots slice 17: a futures option's series (Tracker2's services/bots/futures_legs.py).
Made-up chains in TastyTrade's nested shape."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from optionsjourney.bots import futures

D = Decimal
NOW = datetime(2026, 10, 13, 14, 0, tzinfo=UTC)


def series(day, code, contract="/ESZ6", nv="0.5", strikes=(7000, 7050), expires="21:00"):
    yymmdd = day.replace("-", "")[2:]
    return {
        "expiration-date": day, "option-root-symbol": code[:-2], "option-contract-symbol": code,
        "underlying-symbol": contract, "settlement-type": "PM", "notional-value": nv,
        "expires-at": f"{day}T{expires}:00.000+00:00",
        "tick-sizes": [{"threshold": "5.0", "value": "0.05"}, {"value": "0.25"}],
        "strikes": [
            {"strike-price": f"{k}.0",
             "call": f".{contract} {code} {yymmdd}C{k}",
             "put": f".{contract} {code} {yymmdd}P{k}",
             "call-streamer-symbol": f"./{code[:-2]}{code[-2]}2{code[-1]}C{k}:XCME",
             "put-streamer-symbol": f"./{code[:-2]}{code[-2]}2{code[-1]}P{k}:XCME"}
            for k in strikes
        ],
    }  # fmt: skip


NESTED = {
    "futures": [{"symbol": "/ESZ6", "streamer-symbol": "/ESZ26:XCME"},
                {"symbol": "/ESH7", "streamer-symbol": "/ESH27:XCME"}],
    "option-chains": [{"expirations": [
        series("2026-10-16", "EW3V6"),
        series("2026-12-18", "EW3Z6"),
        series("2027-03-19", "EW3H7", contract="/ESH7"),
        series("2026-10-09", "EW2V6"),  # already expired
    ]}],
}  # fmt: skip


def test_listed_reads_every_option_by_both_names():
    by_stream, by_symbol = futures.listed(NESTED)
    found = by_stream["./EW3V26P7000:XCME"]
    assert found.symbol == "./ESZ6 EW3V6 261016P7000"
    assert (found.right, found.strike, found.series, found.contract) == (
        "put", D("7000"), "EW3V6", "/ESZ6")  # fmt: skip
    assert found.expiration.isoformat() == "2026-10-16"
    assert found.expires_at == datetime(2026, 10, 16, 21, 0, tzinfo=UTC)
    assert found.multiplier == D("50") and str(found.multiplier) == "50"
    assert futures.find(by_symbol, found.symbol) == found


def test_resolve_a_tickets_streamer_symbols():
    resolved = futures.resolve(
        "/ES", ["./EW3Z26P7000:XCME", "./EW3Z26C7050:XCME"], futures.listed(NESTED)[0], NOW
    )
    assert [leg.symbol for leg in resolved.legs] == [
        "./ESZ6 EW3Z6 261218P7000", "./ESZ6 EW3Z6 261218C7050"]  # fmt: skip
    assert (resolved.contract, resolved.multiplier) == ("/ESZ6", D("50"))


def test_resolve_a_journal_trades_own_symbols():
    resolved = futures.resolve("/ES", ["./ESZ6 EW3V6 261016P7000"], futures.listed(NESTED)[1], NOW)
    assert resolved.legs[0].stream_symbol == "./EW3V26P7000:XCME"


@pytest.mark.parametrize(
    ("wanted", "words"),
    [
        (["./EW3Z26P6000:XCME"], "./EW3Z26P6000:XCME isn't listed in TastyTrade's /ES options."),
        (["./EW2V26P7000:XCME"], "/ES 2026-10-09 has already expired."),
        (
            ["./EW3Z26P7000:XCME", "./EW3H27P7000:XCME"],
            "A bot's futures trade must be on one futures contract.",
        ),
        ([], "There are no legs to trade."),
    ],
)
def test_what_cant_be_resolved(wanted, words):
    with pytest.raises(futures.Unresolvable, match=words.replace(".", r"\.").replace("$", r"\$")):
        futures.resolve("/ES", wanted, futures.listed(NESTED)[0], NOW)


def test_an_expiry_is_by_the_series_own_time():
    """/GC expires at 13:30 New York (futures_legs._expired, expires_at)."""
    gold = {"option-chains": [{"expirations": [
        series("2026-10-13", "OGX6", contract="/GCZ6", nv="1", expires="17:30")]}]}  # fmt: skip
    by_stream = futures.listed(gold)[0]
    wanted = ["./OGX26P7000:XCME"]
    assert futures.resolve("/GC", wanted, by_stream, NOW).multiplier == D("100")
    late = datetime(2026, 10, 13, 17, 30, tzinfo=UTC)
    with pytest.raises(futures.Unresolvable, match="already expired"):
        futures.resolve("/GC", wanted, by_stream, late)


def test_a_contract_must_be_a_month_of_the_bots_root():
    assert futures.contract_problem("/ES", "/ESZ6") is None
    assert futures.contract_problem("/ES", "./ESZ6") is None
    assert futures.contract_problem("/ES", "/MESZ6") == "/MESZ6 isn't a /ES contract."
    assert futures.contract_problem("/MES", "/ESZ6") == "/ESZ6 isn't a /MES contract."


def test_a_futures_options_parts_from_its_symbol():
    assert futures.is_futures_symbol("./ESZ6 EW3Z6 261218P7000")
    assert not futures.is_futures_symbol("SPXW  261013P05800000")
    option = futures.option_of("./ESZ6 EW3Z6 261218P7000")
    assert (option.put_call, option.strike, option.expiration.isoformat()) == (
        "PUT", D("7000"), "2026-12-18")  # fmt: skip
    assert futures.contract_of("./ESZ6 EW3Z6 261218P7000") == "/ESZ6"
    assert futures.series_of("./ESZ6 EW3Z6 261218P7000") == "EW3Z6"
    assert futures.option_of("SPXW  261013P05800000") is None
