from dataclasses import replace
from decimal import Decimal

import pytest

from optionsjourney.bots.definition import iron_condor_skeleton
from optionsjourney.bots.filters import premium_problem

D = Decimal
BOT = replace(
    iron_condor_skeleton("IC"),
    use_premium_bounds=True,
    premium_min=D("1.00"),
    premium_max=D("3.00"),
)


def test_bounds_off_means_no_problem():
    assert premium_problem(replace(BOT, use_premium_bounds=False), D("-9")) is None


@pytest.mark.parametrize("net", ["1.00", "2.50", "3.00", "3.004", "0.996"])
def test_inside_the_bounds_inclusive(net):
    assert premium_problem(BOT, D(net)) is None


def test_below_the_minimum():
    assert premium_problem(BOT, D("0.99")) == "net credit 0.99 < min 1.00"


def test_above_the_maximum():
    assert premium_problem(BOT, D("3.01")) == "net credit 3.01 > max 3.00"


def test_bounds_compare_the_size_of_the_price_on_either_side():
    assert premium_problem(BOT, D("1.50")) is None
    assert premium_problem(BOT, D("-2.20")) is None
    assert premium_problem(BOT, D("-0.99")) == "net debit 0.99 < min 1.00"
    assert premium_problem(BOT, D("-3.01")) == "net debit 3.01 > max 3.00"


def test_the_fallback_try_has_no_minimum_but_keeps_the_maximum():
    assert premium_problem(BOT, D("0.50"), floor=False) is None
    assert premium_problem(BOT, D("3.50"), floor=False) == "net credit 3.50 > max 3.00"
