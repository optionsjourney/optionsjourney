from datetime import timedelta

import pytest
from sqlalchemy import select

from optionsjourney.bots import integrity
from optionsjourney.db.models import BotExit, BotIntegrityAlert, BotRun
from optionsjourney.services import bot_entry, bot_integrity
from tests.services.bot_fakes import (
    OPEN_TIME,
    SHAPE_MATRIX,
    FakeChain,
    FakeHub,
    FakeTasty,
    make,
    make_shape,
)

SHORT, LONG = "SPXW  261013C05910000", "SPXW  261013C05985000"
LATER = OPEN_TIME + timedelta(minutes=10)


def deps_at(factory, when, sent):
    return bot_entry.BotDeps(
        factory, lambda: [FakeTasty()], FakeHub(), FakeChain(), notify=sent.append,
        sleep=lambda s: None, now=lambda: when,
    )  # fmt: skip


def held(factory, bot_id, exits, key="r1", status="filled"):
    """A filled run on side 0 (the calls) with (kind, state, order id) exit rows."""
    with factory() as session:
        run = BotRun(
            bot_id=bot_id, key=key, trading_date=OPEN_TIME.date(), entry_time="09:47",
            status=status, live=True, chase_log=[], contracts=1,
            legs=[{"symbol": SHORT, "side": "sell", "ratio": 1, "group": 0},
                  {"symbol": LONG, "side": "buy", "ratio": 1, "group": 0}],
            filled_legs=[{"symbol": SHORT}], created_at=OPEN_TIME, updated_at=OPEN_TIME,
        )  # fmt: skip
        session.add(run)
        session.flush()
        for kind, state, order in exits:
            session.add(BotExit(
                run_id=run.id, group_position=0, kind=kind, state=state, broker_order_id=order,
                short_symbol=SHORT, long_symbol=LONG, contracts=1, rearms=0, log=[],
                created_at=OPEN_TIME, updated_at=OPEN_TIME,
            ))  # fmt: skip
        session.commit()
        return run.id


def set_state(factory, run_id, kind, state):
    with factory() as session:
        row = session.scalar(select(BotExit).where(BotExit.run_id == run_id, BotExit.kind == kind))
        row.state, row.broker_order_id = state, "s1"
        session.commit()


def test_a_finding_alerts_once_clears_and_alerts_again(factory):
    bot_id = make(factory)
    run_id = held(factory, bot_id, [("stop", "filled", "s1")])
    sent = []
    deps = deps_at(factory, LATER, sent)
    found = bot_integrity.check(deps)
    assert [(f.run_id, f.check, f.subject) for f in found] == [(run_id, "wing_missing", "side 0")]
    assert len(sent) == 1 and "Bot integrity: IC" in sent[0].title
    assert "Call side's stop filled" in sent[0].detail
    assert sent[0].link == f"/automation/runs/{run_id}"
    bot_integrity.check(deps)
    assert len(sent) == 1  # still there: not alerted again
    assert [f["summary"][:20] for f in bot_integrity.open_findings(factory)] == [
        "Call side's stop fil"
    ]
    with factory() as session:  # the wing gets handled
        session.add(BotExit(
            run_id=run_id, group_position=0, kind="wing", state="filled", short_symbol=SHORT,
            long_symbol=LONG, contracts=1, rearms=0, log=[], created_at=LATER, updated_at=LATER,
        ))  # fmt: skip
        session.commit()
    assert bot_integrity.check(deps) == [] and bot_integrity.open_findings(factory) == []
    with factory() as session:
        session.execute(BotExit.__table__.delete().where(BotExit.kind == "wing"))
        session.commit()
    bot_integrity.check(deps)
    assert len(sent) == 2  # it came back: a new occurrence
    with factory() as session:
        assert session.scalar(select(BotIntegrityAlert)).cleared_at is None


def test_a_check_that_raises_does_not_stop_the_others(factory, monkeypatch, caplog):
    bot_id = make(factory)
    held(factory, bot_id, [("stop", "working", None)])

    def broken(exits, names, now):
        raise RuntimeError("boom")

    monkeypatch.setattr(integrity, "CHECKS", (broken, integrity.stop_without_order))
    sent = []
    found = bot_integrity.check(deps_at(factory, LATER, sent))
    assert [f.check for f in found] == ["stop_without_order"] and len(sent) == 1
    assert "integrity check" in caplog.text


def test_a_failure_reading_the_runs_never_raises(factory, monkeypatch):
    def broken(*args):
        raise RuntimeError("db down")

    monkeypatch.setattr(bot_integrity, "_live_runs", broken)
    assert bot_integrity.check(deps_at(factory, LATER, [])) == []


def test_finished_runs_are_not_checked(factory):
    bot_id = make(factory)
    held(factory, bot_id, [("stop", "working", None)], key="missed", status="missed")
    sent = []
    after_expiry = OPEN_TIME + timedelta(days=1)
    held(factory, bot_id, [("stop", "working", None)], key="old")
    assert bot_integrity.check(deps_at(factory, after_expiry, sent)) == []
    assert sent == []


def test_open_findings_lists_what_is_open(factory):
    bot_id = make(factory)
    run_id = held(factory, bot_id, [("stop", "failed", None)])
    bot_integrity.check(deps_at(factory, LATER, []))
    [finding] = bot_integrity.open_findings(factory)
    assert (finding["run_id"], finding["bot"], finding["check"]) == (
        run_id,
        "IC",
        "side_unprotected",
    )
    set_state(factory, run_id, "stop", "working")
    bot_integrity.check(deps_at(factory, LATER, []))
    assert bot_integrity.open_findings(factory) == []


def test_a_finding_is_not_cleared_by_a_check_that_raised(factory, monkeypatch):
    bot_id = make(factory)
    held(factory, bot_id, [("stop", "working", None)])
    deps = deps_at(factory, LATER, [])
    bot_integrity.check(deps)
    assert len(bot_integrity.open_findings(factory)) == 1

    def broken(exits, names, now):
        raise RuntimeError("boom")

    broken.__name__ = "stop_without_order"
    monkeypatch.setattr(integrity, "CHECKS", (broken,))
    bot_integrity.check(deps)
    assert len(bot_integrity.open_findings(factory)) == 1  # unknown, not gone


# --- slice 13: any group ---------------------------------------------------------------

LOW, BODY, HIGH = "SPXW  261013C05835000", "SPXW  261013C05910000", "SPXW  261013C05985000"


def _row(run_id, kind, state, legs, anchor=None, watched=False, at=OPEN_TIME, log=()):
    """An exit row as slice 13 writes it: its legs, its anchor (None when watched)."""
    return BotExit(
        run_id=run_id, group_position=0, kind=kind, state=state, short_symbol=anchor or "",
        long_symbol=next((s for s, _ in legs if s != anchor), ""), anchor_symbol=anchor,
        watched=watched, legs=[{"symbol": s, "side": side, "ratio": 1} for s, side in legs],
        contracts=1, rearms=0, log=list(log), created_at=at, updated_at=at,
    )  # fmt: skip


def test_a_fly_whose_stop_filled_reads_each_wing_rows_own_leg(factory):
    bot_id = make(factory)
    run_id = held(factory, bot_id, [])
    fly = [(LOW, "buy"), (BODY, "sell"), (HIGH, "buy")]
    with factory() as session:
        session.add(_row(run_id, "stop", "filled", fly, anchor=BODY))
        session.add(_row(run_id, "wing", "filled", [(LOW, "buy")], anchor=BODY))
        session.commit()
    sent = []
    found = bot_integrity.check(deps_at(factory, LATER, sent))
    assert [(f.check, f.subject) for f in found] == [("wing_missing", "side 0")]
    assert HIGH.strip() in sent[0].detail
    with factory() as session:
        session.add(_row(run_id, "wing", "working", [(HIGH, "buy")], anchor=BODY))
        session.commit()
    assert bot_integrity.check(deps_at(factory, LATER, sent)) == []


def test_a_group_left_to_you_by_a_leg_out_is_flagged_once(factory):
    # A watched long call whose close stopped part-way through its leg-out: the close
    # ended failed ("left to you"), its stop cancelled for it and never put back.
    bot_id = make(factory)
    run_id = held(factory, bot_id, [])
    call = [(SHORT, "buy")]
    with factory() as session:
        session.add(_row(run_id, "stop", "cancelled", call, watched=True))
        session.add(_row(run_id, "target", "cancelled", call, watched=True))
        session.add(_row(run_id, "close", "failed", call, watched=True,
                         log=[{"event": "left to you: the session ended"}]))  # fmt: skip
        session.commit()
    sent = []
    for minutes in (10, 11, 12):
        found = bot_integrity.check(deps_at(factory, OPEN_TIME + timedelta(minutes=minutes), sent))
        assert [(f.check, f.subject) for f in found] == [("side_unprotected", "side 0")]
    assert len(sent) == 1 and "Call side has no working stop" in sent[0].detail


# --- slice 13, Task 16: every shape ------------------------------------------------------


def shape_deps(factory, sent):
    tasty, now = FakeTasty(), [OPEN_TIME]
    deps = bot_entry.BotDeps(
        factory, lambda: [tasty], FakeHub(), FakeChain(), notify=sent.append,
        sleep=lambda s: None, now=lambda: now[0], background=lambda fn, *args: fn(*args),
    )  # fmt: skip
    deps.later = lambda minutes: now.__setitem__(0, OPEN_TIME + timedelta(minutes=minutes))
    return deps


def protected_run(factory, key, deps):
    """A live run of `key` with a 50% stop and target on every group, its entry filled."""
    bot_id = make_shape(factory, key, live=True, stop=50, target=50)
    return bot_entry.fire(deps, bot_id, "09:47")


@pytest.mark.parametrize("key", SHAPE_MATRIX)
def test_every_matrix_shape_is_quiet_in_integrity_while_protected(factory, key):
    sent = []
    deps = shape_deps(factory, sent)
    run_id = protected_run(factory, key, deps)
    stops = [r for r in _exit_rows(factory, run_id) if r.kind == "stop"]
    assert stops and {s.state for s in stops} <= {"working", "watching"}
    sent.clear()
    for minutes in (10, 11):
        deps.later(minutes)
        assert bot_integrity.check(deps) == []
    assert sent == [] and bot_integrity.open_findings(factory) == []


@pytest.mark.parametrize("key", SHAPE_MATRIX)
def test_every_matrix_shape_is_flagged_once_its_stop_is_gone(factory, key):
    sent = []
    deps = shape_deps(factory, sent)
    run_id = protected_run(factory, key, deps)
    with factory() as session:  # the first group's stop is gone (cancelled, never put back)
        stop = session.scalar(
            select(BotExit).where(
                BotExit.run_id == run_id, BotExit.kind == "stop", BotExit.group_position == 0
            )
        )
        stop.state, stop.updated_at = "cancelled", OPEN_TIME
        session.commit()
    sent.clear()
    for minutes in (10, 11, 12):
        deps.later(minutes)
        found = bot_integrity.check(deps)
        assert [(f.check, f.subject) for f in found] == [("side_unprotected", "side 0")]
    assert len(sent) == 1 and f"/automation/runs/{run_id}" == sent[0].link
    assert [f["check"] for f in bot_integrity.open_findings(factory)] == ["side_unprotected"]


def _exit_rows(factory, run_id):
    with factory() as session:
        return list(session.scalars(select(BotExit).where(BotExit.run_id == run_id)))
