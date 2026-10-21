from datetime import UTC, datetime

from optionsjourney.bots.caps import ExitRow, RunRow, open_trades
from optionsjourney.bots.exit_math import after_expiry

MORNING = datetime(2026, 10, 13, 13, 47, tzinfo=UTC)  # Tue 09:47 New York
EVENING = datetime(2026, 10, 13, 20, 30, tzinfo=UTC)  # Tue 16:30 New York, after the close
NEXT_DAY = datetime(2026, 10, 14, 13, 47, tzinfo=UTC)


def legs(expiry="261013"):
    """An iron condor's legs: side 0 the calls, side 1 the puts."""
    return (
        {"symbol": f"SPXW  {expiry}C05910000", "group": 0},
        {"symbol": f"SPXW  {expiry}C05985000", "group": 0},
        {"symbol": f"SPXW  {expiry}P05690000", "group": 1},
        {"symbol": f"SPXW  {expiry}P05615000", "group": 1},
    )


def run(run_id, root=None, status="filled", exits=(), filled=True, expiry="261013"):
    return RunRow(root or run_id, run_id, status, legs(expiry), tuple(exits), filled)


def test_after_expiry():
    symbol = "SPXW  261013C05910000"
    assert not after_expiry(symbol, MORNING)
    assert after_expiry(symbol, EVENING) and after_expiry(symbol, NEXT_DAY)
    assert not after_expiry("not an option", NEXT_DAY)


def test_a_filled_run_with_no_exits_closed_is_open():
    assert open_trades([run(1)], split=True, now=MORNING) == 2  # each side its own trade
    assert open_trades([run(1)], split=False, now=MORNING) == 1


def test_two_runs_count_twice():
    assert open_trades([run(1), run(2)], split=False, now=MORNING) == 2


def test_only_filled_or_partial_runs_with_fills_count():
    rows = [run(1, status=s) for s in ("pending", "running", "preview", "missed", "skipped")]
    rows.append(run(2, status="filled", filled=False))
    assert open_trades(rows, split=False, now=MORNING) == 0
    assert open_trades([run(3, status="partial")], split=False, now=MORNING) == 1


def test_a_run_whose_sides_are_all_closed_is_not_open():
    exits = (
        ExitRow(0, "target", "filled"),  # the call side took its profit
        ExitRow(1, "stop", "filled"),  # the put side stopped out, and its wing was sold
        ExitRow(1, "wing", "filled"),
    )
    assert open_trades([run(1, exits=exits)], split=True, now=MORNING) == 0
    assert open_trades([run(1, exits=exits)], split=False, now=MORNING) == 0


def test_a_stopped_side_with_its_wing_sold_leaves_the_other_side_open():
    exits = (ExitRow(1, "stop", "filled"), ExitRow(1, "wing", "filled"))
    assert open_trades([run(1, exits=exits)], split=True, now=MORNING) == 1
    assert open_trades([run(1, exits=exits)], split=False, now=MORNING) == 1


def test_a_stopped_side_counts_as_closed_once_its_wing_is_sold_or_gone():
    for wing in ("filled", "expired"):
        exits = (ExitRow(0, "stop", "filled"), ExitRow(0, "wing", wing))
        assert open_trades([run(1, exits=exits)], split=True, now=MORNING) == 1
    for wing in ("working", "failed"):  # the wing is still held: that side is open
        exits = (ExitRow(0, "stop", "filled"), ExitRow(0, "wing", wing))
        assert open_trades([run(1, exits=exits)], split=True, now=MORNING) == 2
    stop_only = (ExitRow(0, "stop", "filled"),)  # the wing isn't sold yet
    assert open_trades([run(1, exits=stop_only)], split=True, now=MORNING) == 2


def test_a_stop_expired_because_the_position_is_gone_closes_the_side():
    exits = (ExitRow(0, "stop", "expired"), ExitRow(1, "stop", "expired"))
    assert open_trades([run(1, exits=exits)], split=True, now=MORNING) == 0


def test_a_partly_closed_run_is_open():
    exits = (ExitRow(0, "target", "filled"), ExitRow(1, "stop", "working"))
    assert open_trades([run(1, exits=exits)], split=False, now=MORNING) == 1


def test_unknown_or_unsettled_exit_states_leave_the_side_open():
    exits = (ExitRow(0, "target", "firing"), ExitRow(1, "stop", "something new"))
    assert open_trades([run(1, exits=exits)], split=True, now=MORNING) == 2


def test_an_expired_run_is_not_open():
    yesterday = run(1, expiry="261012")
    assert open_trades([yesterday], split=True, now=MORNING) == 0
    assert open_trades([run(2)], split=True, now=EVENING) == 0  # today's, after the close


def test_a_run_whose_legs_cant_be_read_for_expiry_stays_open():
    row = RunRow(1, 1, "filled", ({"symbol": "junk", "group": 0},), (), True)
    assert open_trades([row], split=True, now=NEXT_DAY) == 1


def test_a_filled_run_without_legs_counts_as_held():
    assert open_trades([RunRow(1, 1, "filled", (), (), True)], split=True, now=MORNING) == 1


def test_tranches_of_one_entry_count_once_by_the_largest_run():
    root, child = run(1), run(2, root=1)
    assert open_trades([root, child], split=True, now=MORNING) == 2  # not 4
    assert open_trades([root, child, run(3)], split=True, now=MORNING) == 4
    closed = (ExitRow(0, "target", "filled"),)
    rows = [run(1, exits=closed), run(2, root=1)]  # the child still holds both sides
    assert open_trades(rows, split=True, now=MORNING) == 2


def test_a_filled_timed_close_closes_the_side():
    closed = [ExitRow(0, "timed", "filled"), ExitRow(1, "timed", "expired")]
    assert open_trades([run(1, exits=closed)], True, MORNING) == 0
    failed = [ExitRow(0, "timed", "failed"), ExitRow(1, "timed", "missed")]
    assert open_trades([run(1, exits=failed)], True, MORNING) == 2


def test_open_runs_names_each_run_holding_an_open_side():
    from optionsjourney.bots.caps import open_runs

    closed = (ExitRow(0, "target", "filled"), ExitRow(1, "target", "filled"))
    runs = [run(1), run(2, exits=closed), run(3, root=1), run(4, status="missed")]
    assert open_runs(runs, MORNING) == [1, 3]
    assert open_runs(runs, EVENING) == []


def test_caps_count_a_closed_side():
    closed = [ExitRow(0, "close", "filled"), ExitRow(1, "close", "reduced")]
    assert open_trades([run(1, exits=closed)], split=True, now=MORNING) == 1  # the put side
    gone = [ExitRow(0, "close", "expired"), ExitRow(1, "close", "filled")]
    assert open_trades([run(1, exits=gone)], split=False, now=MORNING) == 0


# Slice 13: any group. A stop row names its group's legs and the leg it rests on; a
# wing row names the one leg it sold.

PUT = "SPXW  261013P05690000"
LOW, BODY, HIGH = ("SPXW  261013C05900000", "SPXW  261013C05950000", "SPXW  261013C06010000")


def one_group(*symbols):
    return tuple({"symbol": s, "group": 0} for s in symbols)


def test_a_naked_short_is_closed_when_its_stop_fills():
    held = RunRow(1, 1, "filled", one_group(PUT), (), True)
    assert open_trades([held], split=True, now=MORNING) == 1
    stopped = (ExitRow(0, "stop", "filled", legs=(PUT,), anchor=PUT),)  # no wing to sell
    row = RunRow(1, 1, "filled", one_group(PUT), stopped, True)
    assert open_trades([row], split=True, now=MORNING) == 0
    working = (ExitRow(0, "stop", "working", legs=(PUT,), anchor=PUT),)
    assert open_trades([RunRow(1, 1, "filled", one_group(PUT), working, True)], True, MORNING) == 1


def test_a_fly_is_open_until_both_wings_are_handled():
    # A credit broken-wing call fly: buy 1 low, sell 2 at the body, buy 1 high; the stop
    # rests on the body, and each wing is sold on its own row after it fills.
    legs = one_group(LOW, BODY, HIGH)
    stop = ExitRow(0, "stop", "filled", legs=(LOW, BODY, HIGH), anchor=BODY)

    def count(*wings):
        return open_trades([RunRow(1, 1, "filled", legs, (stop, *wings), True)], True, MORNING)

    assert count() == 1
    assert count(ExitRow(0, "wing", "filled", legs=(LOW,))) == 1  # the high wing is held
    left = ExitRow(0, "wing", "left", legs=(HIGH,))  # left open, as the group is set to
    assert count(ExitRow(0, "wing", "filled", legs=(LOW,)), left) == 1
    failed = ExitRow(0, "wing", "failed", legs=(HIGH,))
    assert count(ExitRow(0, "wing", "filled", legs=(LOW,)), failed) == 1
    both = (ExitRow(0, "wing", "filled", legs=(LOW,)), ExitRow(0, "wing", "expired", legs=(HIGH,)))
    assert count(*both) == 0


def test_a_watched_groups_stop_close_closes_it():
    # A long call fly the app watches: its stop starts a close of the whole group, and
    # the stop row is cancelled for it. The close filling closes the group.
    legs = one_group(LOW, BODY, HIGH)
    stop = ExitRow(0, "stop", "cancelled", legs=(LOW, BODY, HIGH))

    def count(close_state):
        close = ExitRow(0, "close", close_state, legs=(LOW, BODY, HIGH))
        return open_trades([RunRow(1, 1, "filled", legs, (stop, close), True)], True, MORNING)

    assert count("filled") == 0
    assert count("chasing") == 1
    assert count("reduced") == 1  # part of it closed
    assert count("failed") == 1  # left to you part-way through its leg-out: legs still held


def test_a_group_left_to_you_part_way_through_a_leg_out_is_still_open():
    # A timed close that bought back the body and then couldn't go on: its row ends
    # failed, no stop is put back, and the wings are still held.
    legs = one_group(LOW, BODY, HIGH)
    exits = (
        ExitRow(0, "stop", "cancelled", legs=(LOW, BODY, HIGH), anchor=BODY),
        ExitRow(0, "timed", "failed", legs=(LOW, BODY, HIGH), anchor=BODY),
    )
    assert open_trades([RunRow(1, 1, "filled", legs, exits, True)], True, MORNING) == 1


class _Row:
    """A BotExit's columns, as ExitRow.of reads them."""

    def __init__(self, kind, state, legs=None, anchor=None, short="", long="", watched=False):
        self.group_position, self.kind, self.state = 0, kind, state
        self.legs = legs or []
        self.anchor_symbol, self.short_symbol, self.long_symbol = anchor, short, long
        self.watched = watched


def test_an_exit_row_reads_its_legs_and_anchor():
    from optionsjourney.bots.caps import ExitRow

    def leg(symbol, side="buy"):
        return {"symbol": symbol, "side": side, "ratio": 1}

    stop = _Row("stop", "filled", [leg(LOW), leg(BODY, "sell"), leg(HIGH)], BODY, BODY, LOW)
    assert ExitRow.of(stop) == ExitRow(0, "stop", "filled", (LOW, BODY, HIGH), BODY)
    watched = _Row("stop", "watching", [leg(LOW), leg(BODY, "sell")], watched=True)
    assert ExitRow.of(watched) == ExitRow(0, "stop", "watching", (LOW, BODY), None)
    old = _Row("wing", "filled", short=PUT, long="SPXW  261013P05615000")  # before slice 13
    assert ExitRow.of(old).legs == (PUT, "SPXW  261013P05615000")
    assert ExitRow.of(old).anchor == PUT


# Bots slice 14: a calendar whose near leg expired is still open until its far leg is sold.
NEAR_LEG, FAR_LEG = "SPXW  261013P05690000", "SPXW  261020P05690000"


def calendar_run(*exits):
    legs = ({"symbol": NEAR_LEG, "group": 0}, {"symbol": FAR_LEG, "group": 0})
    return RunRow(1, 1, "filled", legs, tuple(exits), True)


def test_a_split_group_stays_open_until_its_far_legs_are_sold():
    stop = ExitRow(0, "stop", "expired", (NEAR_LEG, FAR_LEG), NEAR_LEG)
    assert open_trades([calendar_run(stop)], split=True, now=NEXT_DAY) == 1
    settle = ExitRow(0, "settle", "left", (FAR_LEG,))
    assert open_trades([calendar_run(stop, settle)], split=True, now=NEXT_DAY) == 1
    working = ExitRow(0, "wing", "working", (FAR_LEG,))
    assert open_trades([calendar_run(stop, settle, working)], split=True, now=NEXT_DAY) == 1
    sold = ExitRow(0, "wing", "filled", (FAR_LEG,))
    assert open_trades([calendar_run(stop, settle, sold)], split=True, now=NEXT_DAY) == 0
    gone = ExitRow(0, "wing", "expired", (FAR_LEG,))
    assert open_trades([calendar_run(stop, gone)], split=True, now=NEXT_DAY) == 0


def test_a_watched_split_group_left_to_you_stays_open_until_its_far_leg_expires():
    """The far legs are yours (the bot never sells them): the trade counts until they
    expire or a wing row shows them gone."""
    stop = ExitRow(0, "stop", "expired", (NEAR_LEG, FAR_LEG), None)
    settle = ExitRow(0, "settle", "left", (FAR_LEG,))
    assert open_trades([calendar_run(stop, settle)], split=True, now=NEXT_DAY) == 1
    after_far = datetime(2026, 10, 20, 20, 30, tzinfo=UTC)
    assert open_trades([calendar_run(stop, settle)], split=True, now=after_far) == 0


def test_an_expired_stop_in_one_expiration_still_closes_its_group():
    """Same-day groups as before: the stop expired with the position gone."""
    stop = ExitRow(0, "stop", "expired", (FAR_LEG,), FAR_LEG)
    legs = ({"symbol": FAR_LEG, "group": 0},)
    gone = RunRow(1, 1, "filled", legs, (stop,), True)
    assert open_trades([gone], split=True, now=MORNING) == 0


def per_leg_run(exits=()):
    """#165: a MEIC-style run, each leg its own group (0, 1 the calls; 2, 3 the puts)."""
    rows = tuple(dict(leg, group=i) for i, leg in enumerate(legs()))
    return RunRow(1, 1, "filled", rows, tuple(exits), True)


def test_a_split_condor_with_a_group_per_leg_counts_each_side_once():
    assert open_trades([per_leg_run()], split=True, now=MORNING) == 2
    assert open_trades([per_leg_run()], split=False, now=MORNING) == 1
    calls_closed = [ExitRow(0, "target", "filled"), ExitRow(1, "close", "filled")]
    assert open_trades([per_leg_run(calls_closed)], split=True, now=MORNING) == 1
    one_call_closed = [ExitRow(0, "target", "filled")]
    assert open_trades([per_leg_run(one_call_closed)], split=True, now=MORNING) == 2
