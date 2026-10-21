from datetime import date
from decimal import Decimal

from optionsjourney.core.account_values import by_month, combine, performance
from optionsjourney.core.accounts import DailyValue


def D(text):
    return Decimal(text)


def v(day, value):
    return DailyValue(date.fromisoformat(day), D(value), D("0"))


def test_one_account_is_its_own_total():
    points = combine({"A": [v("2026-09-01", "100"), v("2026-09-02", "110")]}, {})
    assert [(p.day, p.value) for p in points] == [
        (date(2026, 9, 1), D("100")),
        (date(2026, 9, 2), D("110")),
    ]


def test_accounts_add_up_and_carry_their_last_value_over_gaps():
    # B has no value on Sep 2 (a Schwab day the app was off): its Sep 1 value stands
    points = combine(
        {
            "A": [v("2026-09-01", "100"), v("2026-09-02", "110"), v("2026-09-03", "120")],
            "B": [v("2026-09-01", "50"), v("2026-09-03", "70")],
        },
        {},
    )
    assert [p.value for p in points] == [D("150"), D("160"), D("190")]


def test_an_account_joins_on_its_first_day():
    points = combine(
        {"A": [v("2026-09-01", "100"), v("2026-09-02", "100")], "B": [v("2026-09-02", "50")]}, {}
    )
    assert [p.value for p in points] == [D("100"), D("150")]
    assert points[1].joined == 1  # an account started counting that day


def test_flows_land_on_their_day():
    points = combine(
        {"A": [v("2026-09-01", "100"), v("2026-09-02", "1100")]}, {date(2026, 9, 2): D("1000")}
    )
    assert [p.flow for p in points] == [D("0"), D("1000")]


def test_performance_takes_deposits_out_of_the_gain():
    points = combine(
        {"A": [v("2026-09-01", "1000"), v("2026-09-02", "2050"), v("2026-09-03", "2000")]},
        {date(2026, 9, 2): D("1000"), date(2026, 9, 3): D("-100")},
    )
    perf = performance(points)
    assert (perf.start, perf.end) == (D("1000"), D("2000"))
    assert perf.net_flows == D("900")
    assert perf.gain == D("100")  # 2000 - 1000 - 900


def test_a_deposit_on_the_first_day_is_already_in_the_start_value():
    points = combine(
        {"A": [v("2026-09-01", "1000"), v("2026-09-02", "1010")]}, {date(2026, 9, 1): D("1000")}
    )
    assert performance(points).net_flows == D("0")
    assert performance(points).gain == D("10")


def test_an_account_joining_is_not_a_gain():
    points = combine(
        {"A": [v("2026-09-01", "100"), v("2026-09-02", "100")], "B": [v("2026-09-02", "50")]}, {}
    )
    assert performance(points).gain == D("0")


def test_by_month():
    points = combine(
        {"A": [v("2026-08-31", "1000"), v("2026-09-15", "1500"), v("2026-09-30", "1600")]},
        {date(2026, 9, 15): D("400")},
    )
    rows = by_month(points)
    assert [(r.month, r.end, r.flows, r.change) for r in rows] == [
        (date(2026, 8, 1), D("1000"), D("0"), D("0")),
        (date(2026, 9, 1), D("1600"), D("400"), D("200")),  # 600 more, 400 of it deposited
    ]
