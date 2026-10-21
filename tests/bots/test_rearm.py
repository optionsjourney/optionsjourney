"""Bots slice 18: what a group should have resting, and what's missing (rearm.py)."""

from decimal import Decimal

from optionsjourney.bots import rearm
from optionsjourney.bots.rearm import Row, Want

D = Decimal
BOTH = Want(stop=True, target=True)


def kinds(found):
    return [(m.kind, m.status, m.row_id, m.price) for m in found]


def test_nothing_is_missing_when_both_are_in_force():
    for stop in ("working", "pending", "watching"):
        for target in ("armed", "firing", "working", "pending"):
            rows = [Row(1, "stop", stop, trigger=D("4.80")), Row(2, "target", target)]
            assert rearm.missing(rows, BOTH) == [], (stop, target)


def test_a_failed_stop_and_a_cancelled_target_are_missing_at_their_last_levels():
    rows = [
        Row(1, "stop", "cancelled", trigger=D("4.50")),
        Row(3, "stop", "failed", trigger=D("4.80")),
        Row(2, "target", "cancelled", limit=D("0.50")),
    ]
    assert kinds(rearm.missing(rows, BOTH)) == [
        ("stop", "missing", 3, D("4.80")),
        ("target", "missing", 2, D("0.50")),
    ]


def test_a_switched_off_or_unset_exit_is_never_missing():
    rows = [Row(1, "stop", "cancelled", trigger=D("4.80")), Row(2, "target", "cancelled")]
    assert rearm.missing(rows, Want(True, True, stop_off=True, target_off=True)) == []
    assert rearm.missing(rows, Want(stop=False, target=False)) == []
    assert kinds(rearm.missing(rows, Want(True, True, target_off=True))) == [
        ("stop", "missing", 1, D("4.80"))
    ]


def test_a_closed_busy_or_held_back_group_is_left_alone():
    closed = [Row(1, "stop", "filled", trigger=D("4.80")), Row(2, "target", "cancelled")]
    assert rearm.missing(closed, BOTH) == []
    busy = [Row(1, "stop", "cancelled", trigger=D("4.80")), Row(2, "target", "firing")]
    assert kinds(rearm.missing(busy, BOTH)) == [("group", "busy", None, None)]
    held = [Row(1, "stop", "cancelled", trigger=D("4.80")), Row(2, "target", "armed")]
    assert kinds(rearm.missing(held, Want(True, True, held_back=True))) == [
        ("group", "held back", None, None)
    ]


def test_an_expired_exit_or_one_with_no_level_isnt_placed():
    expired = [Row(1, "target", "expired"), Row(2, "stop", "working", trigger=D("1"))]
    assert rearm.missing(expired, BOTH) == []
    unpriced = [Row(1, "stop", "failed"), Row(2, "target", "failed")]
    assert kinds(rearm.missing(unpriced, BOTH)) == [
        ("stop", "set it by hand", 1, None),
        ("target", "set it by hand", 2, None),
    ]
    priced = rearm.missing(unpriced, Want(True, True, target_price=D("0.60")))
    assert kinds(priced)[1] == ("target", "missing", 2, D("0.60"))
    assert kinds(rearm.missing([], Want(True, True, target_price=D("0.60")))) == [
        ("stop", "set it by hand", None, None),
        ("target", "missing", None, D("0.60")),
    ]


def test_a_futures_group_only_ever_wants_its_target():
    rows = [Row(1, "target", "failed", limit=D("6.25"))]
    assert kinds(rearm.missing(rows, Want(stop=False, target=True))) == [
        ("target", "missing", 1, D("6.25"))
    ]
