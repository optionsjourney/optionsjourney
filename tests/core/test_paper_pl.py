from decimal import Decimal

from optionsjourney.core import paper_pl

D = Decimal


def test_paper_trade_pl():
    # 5.025 credit, closed whole at a 4.90 debit (stored as -4.90): 12.50
    assert paper_pl.trade_pl(D("5.025"), 1, D(100), [(D("-4.90"), 1)]) == D("12.50")


def test_group_exits_add_up_and_contracts_weigh():
    # 2 contracts in at 5.00; call side closed for 1.00 x2, put side for 2.50 on 1 then 1
    exits = [(D("-1.00"), 2), (D("-2.50"), 1), (D("-2.00"), 1)]
    assert paper_pl.trade_pl(D("5.00"), 2, D(100), exits) == D("350")
    assert paper_pl.exit_price(2, exits) == D("-3.25")


def test_expiry_worth_nothing_keeps_the_credit_and_a_loss_is_negative():
    assert paper_pl.trade_pl(D("2.00"), 3, D(100), [(D(0), 3)]) == D("600")
    assert paper_pl.trade_pl(D("2.00"), 1, D(100), [(D("-5.00"), 1)]) == D("-300")


def test_totals_count_closed_trades_only():
    t = paper_pl.totals([D("100"), D("-40"), None, D("0")])
    assert (t.pl, t.wins, t.losses) == (D("60"), 1, 1)
    assert t.win_rate == D(100) / 3
    assert paper_pl.totals([None]).win_rate is None


def test_closed_by_prefers_the_exits_over_the_expiry():
    assert paper_pl.closed_by([("exit", "stop"), ("expiry", "expired")]) == "stop"
    assert paper_pl.closed_by([("exit", "stop"), ("exit", "target"), ("exit", "stop")]) == (
        "stop, target"
    )
    assert paper_pl.closed_by([("expiry", "expired")]) == "expired"
    assert paper_pl.closed_by([]) is None


def test_condor_sides():
    legs = [
        {"symbol": "P1", "right": "put", "side": "sell", "group": 0},
        {"symbol": "P2", "right": "put", "side": "buy", "group": 0},
        {"symbol": "C1", "right": "call", "side": "sell", "group": 1},
        {"symbol": "C2", "right": "call", "side": "buy", "group": 1},
    ]
    sides = paper_pl.condor_sides(legs)
    assert [leg["symbol"] for leg in sides["put"]] == ["P1", "P2"]
    assert paper_pl.legs_price(sides["put"], {"P1": D("2.5"), "P2": D("0.1")}) == D("2.4")
    one_group = [{**leg, "group": 0} for leg in legs]
    assert paper_pl.condor_sides(one_group) is None  # a group holding both sides
    assert paper_pl.condor_sides(legs[:2]) is None  # a put spread
    assert paper_pl.condor_sides([{"symbol": "A", "side": "sell", "group": 0}] * 4) is None


def test_marked_pl_values_the_legs_still_held_at_their_marks():
    # 5.225 credit, the call short bought back for 5.15; the put spread still held:
    # the short put marks 3.00 (to buy back) and its wing 0.10 (to sell)
    held = [("sell", 1, D("3.00")), ("buy", 1, D("0.10"))]
    assert paper_pl.marked_pl(D("5.225"), 1, D(100), [(D("-5.15"), 1)], held) == D("-282.5")
    assert paper_pl.marked_pl(D("5.225"), 1, D(100), [], [("sell", 1, None)]) is None
