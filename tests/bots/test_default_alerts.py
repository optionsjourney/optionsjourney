from decimal import Decimal

from optionsjourney.bots.default_alerts import specs
from optionsjourney.bots.definition import DefaultAlertDef

D = Decimal


def leg(strike, right, side):
    symbol = f"SPXW  261013{right[0].upper()}0{strike}000"
    return {"symbol": symbol, "strike": strike, "right": right, "side": side, "ratio": 1}


LEGS = [leg("5700", "put", "sell"), leg("5675", "put", "buy"), leg("5800", "call", "sell"),
        leg("5825", "call", "buy")]  # fmt: skip


def pnl(direction, value):
    return DefaultAlertDef("pnl_pct", direction=direction, value=D(value))


def near(points):
    return DefaultAlertDef("strike_offset", points=D(points))


def test_pnl_above_is_the_close_cost_falling_below_its_share_of_the_credit():
    [out], skipped = specs("Iron fly", 42, [pnl("above", "15")], D("2.00"), 1, LEGS, None)
    assert skipped == []
    assert (out.kind, out.direction, out.level) == ("bot_pl", "below", D("1.70"))
    assert out.key == "pnl_pct:above:15"
    assert out.name == "Iron fly · run 42 · P&L ≥ +15%"
    assert out.legs == [
        {"key": "SPXW  261013P05700000", "qty": -1}, {"key": "SPXW  261013P05675000", "qty": 1},
        {"key": "SPXW  261013C05800000", "qty": -1}, {"key": "SPXW  261013C05825000", "qty": 1},
    ]  # fmt: skip


def test_pnl_below_a_loss_is_the_close_cost_rising_above():
    [out], _ = specs("IF", 7, [pnl("below", "-50")], D("2.00"), 1, LEGS, None)
    assert (out.direction, out.level, out.key) == ("above", D("3.00"), "pnl_pct:below:-50")
    assert out.name == "IF · run 7 · P&L ≤ −50%"


def test_a_level_that_cant_be_reached_is_skipped():
    out, skipped = specs("IF", 7, [pnl("above", "100")], D("2.00"), 1, LEGS, None)
    assert out == [] and "can't be reached" in skipped[0]


def test_an_entry_under_50_dollars_gets_no_pnl_alert():
    out, skipped = specs("IF", 7, [pnl("above", "50")], D("0.45"), 1, LEGS, None)
    assert out == [] and "$50" in skipped[0]
    out, _ = specs("IF", 7, [pnl("above", "50")], D("0.45"), 2, LEGS, None)
    assert len(out) == 1  # 0.45 x 100 x 2 = $90


def test_near_short_strike_makes_one_per_sold_strike_puts_first():
    out, _ = specs("IF", 7, [near("10")], D("2.00"), 1, list(reversed(LEGS)), D("5750"))
    assert [(o.kind, o.target, o.direction, o.level, o.key) for o in out] == [
        ("price", "SPX", "below", D("5710"), "strike:P:5700:10"),
        ("price", "SPX", "above", D("5790"), "strike:C:5800:10"),
    ]
    assert [o.name for o in out] == [
        "IF · run 7 · near put 5700 (10 pts)", "IF · run 7 · near call 5800 (10 pts)",
    ]  # fmt: skip


def test_zero_points_is_at_the_strike():
    out, _ = specs("IF", 7, [near("0")], D("2.00"), 1, LEGS, None)
    assert out[0].name == "IF · run 7 · at put 5700" and out[0].level == D("5700")


def test_a_strike_alert_already_true_at_the_fill_is_skipped():
    out, skipped = specs("IF", 7, [near("60")], D("2.00"), 1, LEGS, D("5750"))
    # put: below 5760 is already true at 5750; call: above 5740 is too
    assert out == [] and len(skipped) == 2


def test_with_no_spot_the_strike_alerts_are_made():
    out, _ = specs("IF", 7, [near("60")], D("2.00"), 1, LEGS, None)
    assert len(out) == 2


def test_a_long_bot_name_is_shortened():
    [out], _ = specs("x" * 100, 7, [pnl("above", "10")], D("2.00"), 1, LEGS, None)
    assert out.name.startswith("x" * 59 + "…")
