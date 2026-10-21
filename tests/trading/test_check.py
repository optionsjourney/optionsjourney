from dataclasses import replace
from decimal import Decimal

import pytest

from optionsjourney.trading.check import (
    COULDNT_CHECK,
    BotFacts,
    Decision,
    Facts,
    Switches,
    check,
    check_cancel,
)
from optionsjourney.trading.orders import Leg, Order, OrderType, Side, TimeInForce, plain

D = Decimal
SHORT = "SPXW  261013P05800000"
LONG = "SPXW  261013P05790000"
BOT = BotFacts("SPX put spread", live=True, max_per_trade=2, max_per_day=3, used_today=0)
OPEN = Order((Leg(SHORT, Side.SELL), Leg(LONG, Side.BUY)), 1, True, price=D("1.50"))
CLOSE = Order((Leg(SHORT, Side.BUY), Leg(LONG, Side.SELL)), 1, False, price=D("-0.50"))
HELD = {plain(SHORT): -1, plain(LONG): 1}


def opening(**changes):
    values = {"order": OPEN, "broker": "tastytrade", "origin": "bot", "switches": Switches(),
              "price_tick": D("0.05"), "bot": BOT}  # fmt: skip
    values.update(changes)
    return Facts(**values)


def closing(**changes):
    values = {"order": CLOSE, "broker": "tastytrade", "origin": "you", "switches": Switches(),
              "price_tick": D("0.05"), "open_contracts": HELD, "working_closing": {}}  # fmt: skip
    values.update(changes)
    return Facts(**values)


def test_a_live_bot_opens():
    assert check(opening()).action == "send"


def test_preview_mode_previews():
    decision = check(opening(bot=replace(BOT, live=False)))
    assert decision.action == "preview" and "preview mode" in decision.reason


def test_freeze_beats_everything():
    decision = check(closing(switches=Switches(freeze=True)))
    assert decision.action == "refuse" and "frozen" in decision.reason
    assert check_cancel(Switches(freeze=True)).action == "refuse"
    assert check_cancel(Switches(no_new_trades=True)).action == "send"


def test_no_new_trades_refuses_opening_but_passes_closing():
    off = Switches(no_new_trades=True)
    assert check(opening(switches=off)).action == "refuse"
    assert check(closing(switches=off)).action == "send"


def test_your_opening_order_passes_within_your_limit():
    decision = check(opening(origin="you", bot=None, max_your_contracts=10))
    assert decision.action == "send"


def test_your_opening_order_is_limited_by_the_setting():
    big = Order(OPEN.legs, 11, True, price=D("1.50"))
    decision = check(opening(origin="you", bot=None, order=big, max_your_contracts=10))
    assert decision.action == "refuse"
    assert (
        decision.reason
        == "11 contracts in one leg is over your limit of 10 (Settings → Your orders)."
    )
    ratio = Order((Leg(SHORT, Side.SELL, 2), Leg(LONG, Side.BUY)), 6, True, price=D("1.50"))
    assert (
        check(opening(origin="you", bot=None, order=ratio, max_your_contracts=10)).action
        == "refuse"
    )


def test_your_opening_order_obeys_the_switches_and_no_market_orders():
    off = Switches(no_new_trades=True)
    assert (
        check(opening(origin="you", bot=None, switches=off, max_your_contracts=10)).action
        == "refuse"
    )
    market = Order(OPEN.legs, 1, True, OrderType.MARKET)
    decision = check(
        opening(origin="you", bot=None, order=market, price_tick=None, max_your_contracts=10)
    )
    assert decision.action == "refuse"


def test_your_opening_order_without_a_setting_refuses():
    assert check(opening(origin="you", bot=None)).action == "refuse"


def test_managed_trades_still_cannot_open():
    decision = check(opening(origin="managed", bot=None))
    assert decision.action == "refuse"


def test_market_orders_cannot_open():
    market = Order(OPEN.legs, 1, True, OrderType.MARKET)
    assert check(opening(order=market, price_tick=None)).action == "refuse"


@pytest.mark.parametrize(
    ("bot", "quantity", "words"),
    [
        (replace(BOT, max_per_trade=0), 1, "no size limits"),
        (BOT, 3, "per trade"),
        (replace(BOT, used_today=3), 1, "a day"),
    ],
)
def test_bot_limits(bot, quantity, words):
    decision = check(opening(bot=bot, order=replace(OPEN, quantity=quantity)))
    assert decision.action == "refuse" and words in decision.reason


def test_prices_must_be_on_tick():
    decision = check(opening(order=replace(OPEN, price=D("1.52"))))
    assert decision.action == "refuse" and "tick" in decision.reason


def test_futures_go_to_tastytrade_only_and_overnight_is_cboe_index_at_tastytrade():
    fop = Order((Leg("./ESZ6 EW2Z6 261218P7000", Side.SELL),), 1, True, price=D("5"))
    assert check(opening(order=fop, broker="schwab")).action == "refuse"
    spy = Order((Leg("SPY   261013P00580000", Side.SELL),), 1, True, price=D("1"),
                time_in_force=TimeInForce.GTC_OVERNIGHT)  # fmt: skip
    assert check(opening(order=spy, price_tick=D("0.01"))).action == "refuse"
    spx = replace(OPEN, time_in_force=TimeInForce.GTC_OVERNIGHT)
    assert check(opening(order=spx)).action == "send"
    assert check(opening(order=spx, broker="schwab")).action == "refuse"


def test_a_closing_order_must_close_something_open_on_that_side():
    decision = check(closing(open_contracts={}))
    assert decision.action == "refuse" and "would open" in decision.reason
    flipped = {plain(SHORT): 1, plain(LONG): -1}  # long the put we'd "buy to close"
    assert check(closing(open_contracts=flipped)).action == "refuse"


def test_a_closing_order_cannot_close_more_than_is_open():
    decision = check(closing(order=replace(CLOSE, quantity=2)))
    assert decision.action == "refuse" and "more than" in decision.reason


def test_working_orders_count_against_what_can_be_closed():
    covered = {plain(SHORT): 1}  # a stop already rests for the short put
    decision = check(closing(working_closing=covered))
    assert decision.action == "refuse" and "already cover" in decision.reason


def test_unknown_positions_or_working_orders_refuse_closing_orders():
    for missing in ({"open_contracts": None}, {"working_closing": None}):
        decision = check(closing(**missing))
        assert decision.action == "refuse" and decision.reason.startswith(COULDNT_CHECK)


def test_an_unclear_earlier_order_blocks_its_legs():
    decision = check(closing(unclear_symbols=frozenset({plain(SHORT)})))
    assert decision.action == "refuse" and "unclear" in decision.reason


def test_managed_trades_follow_their_own_preview_setting():
    assert check(closing(origin="managed")).action == "preview"
    assert check(closing(origin="managed", managed_live=True)).action == "send"


def test_a_bot_never_sends_a_multi_leg_market_order():
    market = Order(CLOSE.legs, 1, False, OrderType.MARKET)
    decision = check(closing(order=market, origin="bot", bot=BOT, price_tick=None))
    assert decision == Decision("refuse", "A bot never sends a multi-leg order at market.")
    # Your own orders aren't held to it, and a bot's single leg may go at market.
    assert check(closing(order=market, price_tick=None)).action == "send"
    one = Order((Leg(SHORT, Side.BUY),), 1, False, OrderType.MARKET)
    assert check(closing(order=one, origin="bot", bot=BOT, price_tick=None)).action == "send"


BODY = "SPXW  261013P05795000"
FLY = Order(
    (Leg(LONG, Side.BUY), Leg(BODY, Side.SELL, 2), Leg(SHORT, Side.BUY)),
    2, True, price=D("-0.50"),
)  # fmt: skip


def test_a_bot_order_counts_its_largest_leg():
    # 2 units of a 1x2x1 is 4 contracts in the largest leg.
    roomy = replace(BOT, max_per_trade=4, max_per_day=8)
    over = check(opening(order=FLY, bot=replace(roomy, max_per_trade=3)))
    assert over.action == "refuse"
    assert (
        over.reason
        == "4 contracts in the largest leg is over SPX put spread's limit of 3 per trade."
    )
    assert check(opening(order=FLY, bot=roomy)).action == "send"
    daily = check(opening(order=FLY, bot=replace(roomy, used_today=1, max_per_day=4)))
    assert daily.action == "refuse" and "4 more is over its limit of 4 a day" in daily.reason
    assert (
        check(opening(order=FLY, bot=replace(roomy, used_today=0, max_per_day=4))).action == "send"
    )
