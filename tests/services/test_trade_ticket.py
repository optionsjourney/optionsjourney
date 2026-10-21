from datetime import UTC, datetime, timedelta

import pytest

from optionsjourney.brokers.http import BrokerError
from optionsjourney.db import make_engine, make_session_factory, migrate
from optionsjourney.services import orders, trade_ticket
from optionsjourney.trading.orders import plain
from tests.services.test_orders import FakeBroker

NOW = datetime(2026, 10, 13, 14, 0, tzinfo=UTC)
PUT, WING = "SPXW  261013P05800000", "SPXW  261013P05790000"
BODY = {
    "legs": [
        {"stream_symbol": ".SPXW261013P5800", "side": "sell", "ratio": 1},
        {"stream_symbol": ".SPXW261013P5790", "side": "buy", "ratio": 1},
    ],
    "quantity": 1, "price": 1.5, "tif": "day",
}  # fmt: skip


@pytest.fixture(autouse=True)
def _no_leftover_codes():
    trade_ticket._PENDING.clear()
    yield
    trade_ticket._PENDING.clear()


@pytest.fixture
def factory(tmp_path):
    engine = make_engine(tmp_path / "t.db")
    migrate(engine)
    return make_session_factory(engine)


def test_an_opening_trade_is_previewed_never_sent(factory):
    tasty = FakeBroker(held={})
    result = trade_ticket.review(factory, [tasty], "tastytrade", "5WX01234", BODY, now=NOW).result
    assert result.action == "preview" and result.ok
    assert "place" not in [c[0] for c in tasty.calls]


def test_closing_goes_through_the_closing_checks(factory):
    held = {plain(PUT): -1, plain(WING): 1}
    body = {**BODY, "price": -0.5, "legs": [
        {"stream_symbol": ".SPXW261013P5800", "side": "buy", "ratio": 1},
        {"stream_symbol": ".SPXW261013P5790", "side": "sell", "ratio": 1},
    ]}  # fmt: skip
    result = trade_ticket.review(
        factory, [FakeBroker(held=held)], "tastytrade", "5WX01234", body, now=NOW
    ).result
    assert result.action == "preview" and result.ok
    too_many = {**body, "quantity": 3}
    refused = trade_ticket.review(
        factory, [FakeBroker(held=held)], "tastytrade", "5WX01234", too_many, now=NOW
    ).result
    assert refused.action == "refuse" and "left to close" in refused.message


def test_a_roll_is_refused(factory):
    held = {plain(PUT): -1}
    body = {**BODY, "legs": [
        {"stream_symbol": ".SPXW261013P5800", "side": "buy", "ratio": 1},
        {"stream_symbol": ".SPXW261013P5790", "side": "sell", "ratio": 1},
    ]}  # fmt: skip
    tasty = FakeBroker(held=held)
    result = trade_ticket.review(factory, [tasty], "tastytrade", "5WX01234", body, now=NOW).result
    assert result.action == "refuse"
    assert result.message == "Rolls aren't supported yet: close and open separately."
    assert not tasty.calls


def test_positions_unreadable_refuses(factory):
    tasty = FakeBroker(errors={"positions": BrokerError("down")})
    result = trade_ticket.review(factory, [tasty], "tastytrade", "5WX01234", BODY, now=NOW).result
    assert result.action == "refuse" and "couldn't read your positions" in result.message


def test_futures_come_later(factory):
    body = {**BODY, "legs": [{"stream_symbol": "./E2AZ26P7000:XCME", "side": "sell", "ratio": 1}]}
    result = trade_ticket.review(
        factory, [FakeBroker(held={})], "tastytrade", "5WX01234", body, now=NOW
    ).result
    assert result.message == "Futures orders from the ticket come later."


@pytest.mark.parametrize("change", [
    {"legs": []}, {"quantity": 0}, {"price": 0}, {"tif": "forever"},
    {"legs": [{"stream_symbol": "nope", "side": "sell", "ratio": 1}]},
    {"legs": [{"stream_symbol": ".SPXW261013P5800", "side": "hold", "ratio": 1}]},
])  # fmt: skip
def test_bad_tickets_are_refused_before_the_broker(factory, change):
    tasty = FakeBroker(held={})
    result = trade_ticket.review(
        factory, [tasty], "tastytrade", "5WX01234", {**BODY, **change}, now=NOW
    ).result
    assert result.action == "refuse" and not result.ok and not tasty.calls and not tasty.reads


@pytest.mark.parametrize("change", [
    {"quantity": 1.9}, {"quantity": "2"}, {"quantity": True},
    {"legs": [{"stream_symbol": ".SPXW261013P5800", "side": "sell", "ratio": 2.5}]},
    {"legs": ["x"]}, {"legs": "abc"},
])  # fmt: skip
def test_tickets_need_whole_numbers_and_proper_legs(factory, change):
    tasty = FakeBroker(held={})
    result = trade_ticket.review(
        factory, [tasty], "tastytrade", "5WX01234", {**BODY, **change}, now=NOW
    ).result
    assert result.action == "refuse" and not tasty.calls and not tasty.reads


def test_a_bad_ticket_never_reads_the_broker(factory):
    tasty = FakeBroker(held={})
    result = trade_ticket.review(
        factory, [tasty], "tastytrade", "5WX01234", {**BODY, "tif": "x"}, now=NOW
    ).result
    assert result.action == "refuse" and not tasty.reads


def test_all_sells_cannot_be_a_debit_and_all_buys_cannot_be_a_credit(factory):
    one = {"stream_symbol": ".SPXW261013P5800", "ratio": 1}
    sells = {**BODY, "price": -1.0, "legs": [{**one, "side": "sell"}]}
    buys = {**BODY, "price": 1.0, "legs": [{**one, "side": "buy"}]}
    for body, words in (
        (sells, "Selling only takes in a credit"),
        (buys, "Buying only pays a debit"),
    ):
        result = trade_ticket.review(
            factory, [FakeBroker(held={})], "tastytrade", "5WX01234", body, now=NOW
        ).result
        assert result.action == "refuse" and words in result.message


def test_your_preview_reads_plainly(factory):
    result = trade_ticket.review(
        factory, [FakeBroker(held={})], "tastytrade", "5WX01234", BODY, now=NOW
    ).result
    assert result.message.startswith("Checked by your broker, not sent.")


def _reviewed(factory, tasty, body=BODY, now=NOW):
    return trade_ticket.review(factory, [tasty], "tastytrade", "5WX01234", body, now=now)


def test_review_gives_a_code_and_plain_words(factory):
    got = _reviewed(factory, FakeBroker(held={}))
    assert got.result.action == "preview" and got.code
    assert got.summary == "Sell 1 SPXW 13 Oct 5800P / Buy 1 SPXW 13 Oct 5790P · $1.50 credit · Day"


def test_a_bigger_debit_reads_plainly(factory):
    body = {**BODY, "quantity": 2, "price": -0.5, "tif": "gtc", "legs": [
        {"stream_symbol": ".SPXW261013P5800", "side": "buy", "ratio": 1},
        {"stream_symbol": ".SPXW261013P5790", "side": "sell", "ratio": 1},
    ]}  # fmt: skip
    got = _reviewed(factory, FakeBroker(held={plain(PUT): -2, plain(WING): 2}), body)
    assert got.summary == (
        "2 × (Buy 1 SPXW 13 Oct 5800P / Sell 1 SPXW 13 Oct 5790P) · $0.50 debit · GTC"
    )


def test_a_refused_review_gives_no_code(factory):
    got = _reviewed(factory, FakeBroker(held={}), {**BODY, "price": 0})
    assert got.code is None


def test_confirm_sends_exactly_the_reviewed_order(factory):
    tasty = FakeBroker(held={})
    code = _reviewed(factory, tasty).code
    result = trade_ticket.confirm(factory, [tasty], "tastytrade", "5WX01234", code, now=NOW)
    assert result.action == "send" and result.ok and result.broker_order_id == "777"
    placed = [c for c in tasty.calls if c[0] == "place"]
    assert len(placed) == 1 and placed[0][2]["price"] == "1.50"


def test_a_code_sends_once(factory):
    tasty = FakeBroker(held={})
    code = _reviewed(factory, tasty).code
    trade_ticket.confirm(factory, [tasty], "tastytrade", "5WX01234", code, now=NOW)
    again = trade_ticket.confirm(factory, [tasty], "tastytrade", "5WX01234", code, now=NOW)
    assert again.action == "refuse" and "already been used or has expired" in again.message
    assert len([c for c in tasty.calls if c[0] == "place"]) == 1


def test_a_code_expires(factory):
    tasty = FakeBroker(held={})
    code = _reviewed(factory, tasty).code
    late = trade_ticket.confirm(factory, [tasty], "tastytrade", "5WX01234", code,
                                now=NOW + timedelta(minutes=5, seconds=1))  # fmt: skip
    assert late.action == "refuse" and not [c for c in tasty.calls if c[0] == "place"]


def test_a_code_belongs_to_its_account(factory):
    tasty = FakeBroker(held={})
    code = _reviewed(factory, tasty).code
    other = trade_ticket.confirm(factory, [tasty], "tastytrade", "5WY99999", code, now=NOW)
    assert other.action == "refuse" and not [c for c in tasty.calls if c[0] == "place"]


@pytest.mark.parametrize("now_held", [
    {plain(PUT): 1},  # selling PUT would now close the long: a roll
    {plain(PUT): 1, plain(WING): -1},  # every leg now closes: opening became closing
])  # fmt: skip
def test_positions_changed_since_review_refuses(factory, now_held):
    tasty = FakeBroker(held={})
    code = _reviewed(factory, tasty).code
    tasty.held = now_held
    result = trade_ticket.confirm(factory, [tasty], "tastytrade", "5WX01234", code, now=NOW)
    assert result.message == "Your positions changed since the review. Review again."
    assert not [c for c in tasty.calls if c[0] == "place"]


def test_the_summary_shows_the_price_that_is_sent(factory):
    tasty = FakeBroker(held={})
    body = {**BODY, "price": -0.53, "legs": [
        {"stream_symbol": ".SPXW261013P5800", "side": "buy", "ratio": 1},
        {"stream_symbol": ".SPXW261013P5790", "side": "buy", "ratio": 1},
    ]}  # fmt: skip
    got = _reviewed(factory, tasty, body)
    trade_ticket.confirm(factory, [tasty], "tastytrade", "5WX01234", got.code, now=NOW)
    sent = [c for c in tasty.calls if c[0] == "place"][0][2]["price"]
    assert f"${sent} debit" in got.summary
    assert sent != "0.53"


def test_confirm_obeys_the_switches(factory):
    tasty = FakeBroker(held={})
    code = _reviewed(factory, tasty).code
    orders.set_switch(factory, "no_new_trades", True, NOW)
    result = trade_ticket.confirm(factory, [tasty], "tastytrade", "5WX01234", code, now=NOW)
    assert result.action == "refuse" and not [c for c in tasty.calls if c[0] == "place"]


def test_unreadable_positions_at_confirm_refuse_and_use_up_the_code(factory):
    tasty = FakeBroker(held={})
    code = _reviewed(factory, tasty).code
    tasty.errors = {"positions": BrokerError("down")}
    result = trade_ticket.confirm(factory, [tasty], "tastytrade", "5WX01234", code, now=NOW)
    assert result.action == "refuse" and "couldn't read your positions" in result.message
    assert not [c for c in tasty.calls if c[0] == "place"]


# --- Bots slice 17: a futures ticket reviews, so it can go to a bot ----------------------


def _futures(tasty_ticks=True):
    from tests.services.bot_fakes import FakeChain, FakeTasty, es_nested

    tasty, analyzer = FakeTasty(), FakeChain()
    analyzer.nested = es_nested()
    if tasty_ticks:
        tasty.fop_ticks = {"ES": {"EW3X6": [{"threshold": "5.0", "value": "0.05"},
                                            {"value": "0.25"}]}}  # fmt: skip
    return tasty, analyzer


def _es_body(**changes):
    from tests.services.bot_fakes import fut_stream

    legs = [{"stream_symbol": fut_stream(7000, "put"), "side": "sell", "ratio": 1}]
    return {"ticker": "/ES", "legs": legs, "quantity": 1, "price": 12.6, "tif": "gtc", **changes}


def _review(factory, tasty, analyzer, body):
    from optionsjourney.services import bot_futures

    resolver = bot_futures.ticket_resolver(analyzer, [tasty], NOW)
    return trade_ticket.review(
        factory, [tasty], "tastytrade", "5WX01234", body, now=NOW, futures=resolver
    )


def test_a_futures_ticket_reviews_on_tastytrades_symbols_and_ticks(factory):
    """The page's DXLink names resolved in TastyTrade's series (futures_legs.resolve);
    priced on the series' tick (12.60 rounds to 12.50 on /ES's 0.25 above 5)."""
    tasty, analyzer = _futures()
    reviewed = _review(factory, tasty, analyzer, _es_body())
    assert reviewed.result.action == "preview" and reviewed.result.ok, reviewed.result
    assert reviewed.summary == "Sell 1 /ESZ6 20 Nov 7000P (×50) · $12.50 credit · GTC"
    assert reviewed.futures is True
    (preview,) = [c for c in tasty.calls if c[0] == "preview"]
    (leg,) = preview[1]["legs"]
    assert (leg["symbol"], leg["instrument-type"]) == ("./ESZ6 EW3X6 261120P7000", "Future Option")
    assert preview[1]["price"] == "12.50"


def test_a_futures_review_can_only_go_to_a_bot(factory):
    """Plain Send stays off for futures (#93): Confirm refuses, nothing is placed."""
    tasty, analyzer = _futures()
    reviewed = _review(factory, tasty, analyzer, _es_body())
    result = trade_ticket.confirm(factory, [tasty], "tastytrade", "5WX01234", reviewed.code, NOW)
    assert result.action == "refuse"
    assert result.message == trade_ticket.FUTURES_TO_A_BOT
    assert [c for c in tasty.calls if c[0] == "place"] == []


@pytest.mark.parametrize(
    ("changes", "ticks", "words"),
    [
        ({}, False, "Couldn't load TastyTrade's option increments for EW3X6. Try again."),
        ({"ticker": None}, True, "The ticket didn't say which futures it's on. Reload the page."),
        (
            {"legs": [{"stream_symbol": "./EW3X26P6000:XCME", "side": "sell", "ratio": 1}]},
            True,
            "./EW3X26P6000:XCME isn't listed in TastyTrade's /ES options.",
        ),
        (
            {
                "legs": [
                    {"stream_symbol": "./EW3X26P7000:XCME", "side": "sell", "ratio": 1},
                    {"stream_symbol": ".SPXW261013P5800", "side": "buy", "ratio": 1},
                ]
            },
            True,
            "A ticket can't mix futures and stock options.",
        ),
    ],
)
def test_a_futures_ticket_that_cant_be_resolved_is_refused(factory, changes, ticks, words):
    tasty, analyzer = _futures(ticks)
    reviewed = _review(factory, tasty, analyzer, _es_body(**changes))
    assert reviewed.result.action == "refuse" and reviewed.result.message == words
    assert reviewed.code is None
    assert [c for c in tasty.calls if c[0] == "preview"] == []
