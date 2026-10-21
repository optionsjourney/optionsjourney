from decimal import Decimal
from functools import partial

from optionsjourney.bots import sizing, strikes
from optionsjourney.bots.definition import ChaseDef

D = Decimal
PLAN = ChaseDef(D("0"), D("0.05"), 10, 6)  # 6 tries: 5 steps after the first price


def nickel(price):
    return D("0.05")


def test_the_worst_price_is_the_last_tries_price():
    # -1.20, then five steps of 0.05: the chase's last replace is at -1.45
    assert sizing.worst_price(D("-1.20"), PLAN, strikes.next_price, nickel) == D("-1.45")


def test_the_worst_price_steps_by_the_tick_when_it_is_bigger():
    dime = lambda price: D("0.10")  # noqa: E731 -- a single SPX leg at 3.00 or more
    assert sizing.worst_price(D("-3.20"), PLAN, strikes.next_price, dime) == D("-3.70")


def test_the_worst_price_stops_at_the_premium_maximum():
    capped = partial(strikes.next_price, debit_max=D("1.30"))
    assert sizing.worst_price(D("-1.20"), PLAN, capped, nickel) == D("-1.30")


def test_the_worst_price_is_rounded_onto_the_tick_paying_more():
    one_try = ChaseDef(D("0"), D("0.05"), 10, 1)
    assert sizing.worst_price(D("-1.23"), one_try, strikes.next_price, nickel) == D("-1.25")


def _tasty(isolated, effect="Debit", fees="2.3"):
    """A TastyTrade dry run's reply, trimmed to what sizing reads (shapes as seen live)."""
    return {
        "buying-power-effect": {
            "change-in-buying-power": "224.96", "change-in-buying-power-effect": "Credit",
            "isolated-order-margin-requirement": isolated,
            "isolated-order-margin-requirement-effect": effect,
        },
        "fee-calculation": {"total-fees": fees, "total-fees-effect": "Debit"},
    }  # fmt: skip


def test_tastytrade_cost_is_the_trade_on_its_own_not_net_of_holdings():
    # 5-wide credit spread at 0.05: 500 - 5 + 2.30; the netted change (it frees 224.96) is ignored
    assert sizing.tastytrade_unit_cost(_tasty("500.0"), D("0.05")) == D("497.30")
    # the debit spread: no margin, the 0.05 paid plus fees
    assert sizing.tastytrade_unit_cost(_tasty("0.0", "None"), D("-0.05")) == D("7.30")
    assert sizing.tastytrade_unit_cost({}, D("-0.05")) is None


def _schwab(fund):
    return {"orderStrategy": {"orderBalance": {"projectedBuyingPower": 1.0,
                                               "projectedAvailableFund": fund}}}  # fmt: skip


def test_schwab_cost_is_what_one_more_unit_takes():
    assert sizing.schwab_unit_cost(_schwab(12000.00), _schwab(9502.86)) == D("2497.14")
    assert sizing.schwab_unit_cost(_schwab(12000.00), {}) is None


def test_buying_power_floors_the_budget_over_one_units_cost():
    sized = sizing.buying_power_size(D("2000"), D("497.30"), D("0.05"), max_per_trade=10,
                                     ratio=1, broker="TastyTrade")  # fmt: skip
    assert (sized.contracts, sized.asked, sized.problem) == (4, 4, None)
    assert sized.record == {
        "sizing": "buying_power", "broker": "TastyTrade", "budget": "2000.00", "worst": "0.05",
        "unit_cost": "497.30", "asked": 4, "contracts": 4,
    }  # fmt: skip


def test_buying_power_is_capped_at_max_per_trade():
    # 1000 / 145 = 6, but Max per trade 4 in a leg of ratio 2 allows 2 units
    sized = sizing.buying_power_size(D("1000"), D("145"), D("-1.45"), max_per_trade=4,
                                     ratio=2, broker="Schwab")  # fmt: skip
    assert (sized.contracts, sized.asked) == (2, 6)


def test_buying_power_refuses_too_small_a_budget_and_an_unknown_cost():
    sized = sizing.buying_power_size(D("100"), D("145"), D("-1.45"), max_per_trade=10,
                                     ratio=1, broker="Schwab")  # fmt: skip
    assert sized.contracts == 0 and sized.too_small
    assert sized.problem == (
        "One contract needs $145.00 of buying power at the worst price -1.45, more than the "
        "$100.00 budget."
    )
    for cost in (None, D("0"), D("-225")):
        sized = sizing.buying_power_size(D("300"), cost, D("1.20"), max_per_trade=10, ratio=1,
                                         broker="TastyTrade")  # fmt: skip
        assert sized.contracts == 0 and "didn't say" in sized.problem
