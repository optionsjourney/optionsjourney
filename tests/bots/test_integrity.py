from datetime import UTC, datetime, timedelta

from optionsjourney.bots import integrity
from optionsjourney.bots.integrity import ExitFact

NOW = datetime(2026, 10, 13, 17, 0, tzinfo=UTC)  # 13:00 New York, on the expiry day
SHORT, LONG = "SPXW  261013C05910000", "SPXW  261013C05985000"
NAMES = {0: "Call side", 1: "Put side"}


def fact(kind, state, side=0, ago=600, order="111", id_=1, legs=(SHORT, LONG), anchor=SHORT):
    return ExitFact(id_, side, kind, state, order, legs, anchor, NOW - timedelta(seconds=ago))


def checks(exits, now=NOW):
    return [(f.check, f.subject) for f in integrity.findings(exits, NAMES, now)]


def test_a_filled_stop_with_no_wing_row_is_found_after_a_minute():
    stop = fact("stop", "filled", ago=61)
    assert checks([stop, fact("target", "cancelled", id_=2)]) == [("wing_missing", "side 0")]
    found = integrity.findings([stop], NAMES, NOW)[0]
    assert "Call side's stop filled" in found.summary and "check your broker" in found.summary
    assert checks([fact("stop", "filled", ago=59)]) == []  # the wing may be on its way
    for state in ("filled", "expired", "left", "chasing", "working", "failed"):
        assert checks([stop, fact("wing", state, id_=3)]) == [], state


def test_a_working_stop_with_no_order_id_is_found():
    assert checks([fact("stop", "working", order=None, id_=9)]) == [
        ("stop_without_order", "exit 9")
    ]
    assert checks([fact("stop", "working")]) == []
    assert checks([fact("stop", "pending", order=None)]) == []  # held by Freeze: no order yet


def test_an_open_side_with_no_working_stop_is_found_after_two_minutes():
    failed = fact("stop", "failed", side=1, ago=121)
    assert checks([failed, fact("target", "armed", side=1, id_=2)]) == [
        ("side_unprotected", "side 1")
    ]
    assert "Put side has no working stop" in integrity.findings([failed], NAMES, NOW)[0].summary
    assert checks([fact("stop", "failed", ago=100)]) == []  # a re-arm may be on its way
    # an older failed stop and a working one: protected
    assert checks([fact("stop", "failed", ago=900), fact("stop", "working", id_=2)]) == []
    for state in ("pending", "placing"):
        assert checks([fact("stop", "failed", ago=900), fact("stop", state, id_=2)]) == []
    assert checks([fact("target", "armed")]) == []  # a side without a stop isn't flagged


def test_closed_busy_or_expired_sides_are_not_findings():
    failed = fact("stop", "cancelled", ago=900)
    assert checks([failed, fact("target", "filled", id_=2)]) == []  # closed by its target
    assert checks([failed, fact("target", "firing", id_=2)]) == []  # a close is running
    assert checks([failed, fact("timed", "filled", id_=2)]) == []
    after_close = datetime(2026, 10, 13, 20, 30, tzinfo=UTC)  # 16:30 New York, expiry day
    assert checks([fact("stop", "failed", ago=900)], after_close) == []
    assert checks([fact("stop", "filled", ago=900)], after_close) == []


def test_a_run_past_expiry_is_not_live():
    assert integrity.is_live([SHORT, LONG], NOW)
    assert not integrity.is_live([SHORT, LONG], datetime(2026, 10, 13, 20, 30, tzinfo=UTC))
    assert not integrity.is_live([], NOW)


def test_a_side_with_exits_off_is_reported_as_off_not_unprotected():
    rows = [fact("stop", "cancelled", ago=600), fact("target", "cancelled", id_=2)]
    assert checks(rows) == [("side_unprotected", "side 0")]
    off = integrity.findings(rows, NAMES, NOW, off=frozenset({0}))
    assert [(f.check, f.subject) for f in off] == [("exits_off", "side 0")]
    assert off[0].summary.startswith("Call side: you turned its exits off")
    closed = [fact("stop", "cancelled"), fact("close", "filled", id_=2)]
    assert integrity.findings(closed, NAMES, NOW, off=frozenset({0})) == []  # it's closed
    later = NOW + timedelta(days=1)  # the options expired: nothing to protect
    assert integrity.findings(rows, NAMES, later, off=frozenset({0})) == []


# --- slice 13: any group ---------------------------------------------------------------

LOW, BODY, HIGH = "SPXW  261013C05835000", "SPXW  261013C05910000", "SPXW  261013C05985000"
FLY = (LOW, BODY, HIGH)  # a call butterfly resting its stop on the body (a credit bot)


def test_a_stop_fill_with_any_other_leg_unhandled_is_wing_missing():
    stop = fact("stop", "filled", ago=61, legs=FLY, anchor=BODY)
    one = fact("wing", "filled", id_=2, legs=(LOW,), anchor=BODY)
    assert checks([stop, one]) == [("wing_missing", "side 0")]
    (found,) = integrity.findings([stop, one], NAMES, NOW)
    assert "Call side's stop filled" in found.summary and HIGH.strip() in found.summary
    assert LOW.strip() not in found.summary
    both = fact("wing", "working", id_=3, legs=(HIGH,), anchor=BODY)
    assert checks([stop, one, both]) == []
    assert checks([fact("stop", "filled", ago=59, legs=FLY, anchor=BODY), one]) == []


def test_a_filled_close_covering_the_leg_clears_wing_missing():
    """Tracker2 e7ab1a95 (_closed_by_another_row): the wing chase died and you closed the
    long yourself. A filled (or found-nothing) close by another route counts; a stop or
    target row protects the group and says nothing about the leg."""
    stop = fact("stop", "filled", ago=61)
    for kind, state in (("close", "filled"), ("close", "expired"), ("timed", "filled"),
                        ("action", "filled")):  # fmt: skip
        assert checks([stop, fact(kind, state, id_=2)]) == [], (kind, state)
    for kind, state in (("close", "failed"), ("close", "chasing"), ("target", "filled"),
                        ("stop", "filled")):  # fmt: skip
        assert checks([stop, fact(kind, state, id_=2)]) == [("wing_missing", "side 0")], (
            kind,
            state,
        )
    # A close of only one of the fly's other legs leaves the other one flagged.
    fly = fact("stop", "filled", ago=61, legs=FLY, anchor=BODY)
    one = fact("close", "filled", id_=2, legs=(LOW,), anchor=None)
    assert checks([fly, one]) == [("wing_missing", "side 0")]
    assert checks([fly, fact("close", "filled", id_=2, legs=FLY, anchor=BODY)]) == []


def test_a_naked_short_stop_fill_needs_no_wing():
    naked = fact("stop", "filled", ago=900, legs=(SHORT,), anchor=SHORT)
    assert checks([naked, fact("target", "cancelled", id_=2, legs=(SHORT,))]) == []


def test_a_watching_watched_group_is_protected():
    names = {0: "Long call"}
    rows = [
        fact("stop", "watching", ago=900, order=None, legs=(SHORT,), anchor=None),
        fact("target", "armed", id_=2, order=None, legs=(SHORT,), anchor=None),
    ]
    assert integrity.findings(rows, names, NOW) == []


def test_a_watched_group_with_its_watch_failed_is_unprotected():
    names = {0: "Long call"}
    rows = [
        fact("stop", "failed", ago=121, order=None, legs=(SHORT,), anchor=None),
        fact("target", "armed", id_=2, order=None, legs=(SHORT,), anchor=None),
    ]
    (found,) = integrity.findings(rows, names, NOW)
    assert (found.check, found.subject) == ("side_unprotected", "side 0")
    assert found.summary.startswith("Long call has no working stop")
    later = datetime(2026, 10, 13, 20, 30, tzinfo=UTC)  # its leg expired: nothing to protect
    assert integrity.findings(rows, names, later) == []
    off = integrity.findings(rows, names, NOW, off=frozenset({0}))
    assert [f.check for f in off] == ["exits_off"]
    assert integrity.findings(rows, names, later, off=frozenset({0})) == []


# Bots slice 14: far legs held after the nearer legs expired
NEAR_LEG, FAR_LEG = "SPXW  261013P05690000", "SPXW  261020P05690000"
NEXT_MORNING = datetime(2026, 10, 14, 13, 50, tzinfo=UTC)  # Wed 09:50 New York
THAT_EVENING = datetime(2026, 10, 13, 20, 1, tzinfo=UTC)  # 16:01, a minute after expiry


def cal(kind, state, ago=600, id_=1, legs=(NEAR_LEG, FAR_LEG), anchor=None, now=NEXT_MORNING):
    return ExitFact(id_, 0, kind, state, None, legs, anchor, now - timedelta(seconds=ago))


def far_checks(exits, now=NEXT_MORNING, off=frozenset()):
    return [(f.check, f.subject) for f in integrity.findings(exits, NAMES, now, off)]


def test_far_legs_held_is_found_once_the_settle_row_is_written():
    """The far legs are left to you; this should never happen (a calendar must have a
    timed close), so it's found at once."""
    stop = cal("stop", "expired")
    settle = cal("settle", "left", id_=2, legs=(FAR_LEG,))
    assert far_checks([stop, settle]) == [("far_legs_held", "side 0")]
    found = integrity.findings([stop, settle], NAMES, NEXT_MORNING)[0]
    assert "Call side's nearer legs expired" in found.summary
    assert FAR_LEG.strip() in found.summary and "yours to sell or keep" in found.summary
    assert "Check your broker" in found.summary


def test_far_legs_sold_or_gone_are_no_finding():
    stop = cal("stop", "expired")
    settle = cal("settle", "left", id_=2, legs=(FAR_LEG,))
    for state in ("filled", "expired"):
        assert far_checks([stop, settle, cal("wing", state, id_=3, legs=(FAR_LEG,))]) == []
    # a wing that didn't sell leaves it held
    assert far_checks([stop, settle, cal("wing", "failed", id_=3, legs=(FAR_LEG,))]) == [
        ("far_legs_held", "side 0")
    ]


def test_no_settle_row_two_minutes_after_the_expiry_is_found():
    stop = cal("stop", "expired", now=THAT_EVENING)
    assert far_checks([stop], THAT_EVENING) == []  # a minute after the close: on its way
    later = THAT_EVENING + timedelta(minutes=2)
    assert far_checks([stop], later) == [("far_legs_held", "side 0")]


def test_a_split_group_is_not_also_unprotected_or_exits_off():
    """side_unprotected and exits_off leave a split group to far_legs_held."""
    stop = cal("stop", "expired")
    settle = cal("settle", "left", id_=2, legs=(FAR_LEG,))
    assert far_checks([stop, settle], off=frozenset({0})) == [("far_legs_held", "side 0")]
    failed = cal("stop", "failed", ago=900)  # a watched calendar's failed stop, near expired
    assert far_checks([failed, cal("settle", "left", id_=2, legs=(FAR_LEG,)),
                       cal("wing", "filled", id_=3, legs=(FAR_LEG,))]) == []  # fmt: skip


def test_a_stop_switched_off_alone_is_said_as_the_stop():
    """Bots slice 18: `off` as {side: whether the target is off too}."""
    rows = [fact("stop", "cancelled", ago=600), fact("target", "armed", id_=2)]
    found = integrity.findings(rows, NAMES, NOW, off={0: False})
    assert [(f.check, f.subject) for f in found] == [("exits_off", "side 0")]
    assert found[0].summary == (
        "Call side: you switched its stop off, so it has no stop. Switch it back on from the "
        "run page."
    )
    both = integrity.findings(rows, NAMES, NOW, off={0: True})
    assert both[0].summary.startswith("Call side: you turned its exits off")


def test_left_wing_is_not_flagged_as_unprotected():
    """A close that left its bid-less long to expire (Ignore bid-less longs) closed the
    side: the long still held is not "unprotected", nor a far leg."""
    rows = [fact("stop", "cancelled", ago=900), fact("target", "filled", id_=2)]
    assert checks(rows) == []
    assert checks(rows + [fact("close", "filled", id_=3)]) == []
