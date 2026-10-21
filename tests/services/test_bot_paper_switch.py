"""Going Live ends a bot's paper trades: they close at the mid and are watched no more
(services/bot_paper.end_paper, decision 0061)."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from optionsjourney.bots.definition import EntryTimeDef
from optionsjourney.db.models import Bot, BotRun, PaperFill
from optionsjourney.services import bot_entry, bot_paper, bot_paper_exits, bot_store, orders
from tests.services.bot_fakes import (
    OPEN_TIME,
    FakeChain,
    FakeHub,
    FakeTasty,
    make,
    stream_chain,
)

D = Decimal


@pytest.fixture(autouse=True)
def fresh_memory():
    bot_paper_exits.reset()
    yield
    bot_paper_exits.reset()


@pytest.fixture
def deps(factory, monkeypatch):
    tasty = FakeTasty()
    monkeypatch.setattr(bot_entry, "place_protections", lambda deps, run_id: None)
    d = bot_entry.BotDeps(
        factory,
        lambda: [tasty],
        FakeHub(),
        FakeChain(),
        notify=lambda alert: None,
        sleep=lambda s: None,
        now=lambda: OPEN_TIME,
        background=lambda fn, *args: fn(*args),
    )
    stream_chain(d.hub)
    d.tasty = tasty
    return d


def exits(factory):
    with factory() as session:
        return list(session.scalars(select(PaperFill).where(PaperFill.kind == "exit")))


def live_of(factory, bot_id):
    with factory() as session:
        return session.get(Bot, bot_id).live


def test_switch_to_live_closes_paper(deps, factory):
    bot_id = make(factory)
    assert bot_entry.fire(deps, bot_id, "09:47") is not None
    [run] = bot_paper_exits.open_runs(factory, bot_id)
    later = OPEN_TIME + timedelta(seconds=30)
    for leg in run.legs:
        deps.hub.set(leg["symbol"], D("1.00"), D("1.10"), later)
    first = run.legs[0]  # worth 1.00 more than the rest: a sold leg costs that to buy back
    deps.hub.set(first["symbol"], D("2.00"), D("2.10"), later)
    bot_store.set_mode(factory, bot_id, "live", later, deps)
    fills = exits(factory)
    assert {f.reason for f in fills} == {"switched to Live"} and fills
    by_group = {f.group: f.price for f in fills}
    assert by_group[first["group"]] == (D("-1.00") if first["side"] == "sell" else D("1.00"))
    assert len(fills) == len({leg["group"] for leg in run.legs})
    assert bot_paper_exits.open_runs(factory, bot_id) == []
    assert live_of(factory, bot_id) is True
    assert [c for c in deps.tasty.calls if c[0] == "place"] == []


def test_switch_to_live_without_a_fresh_price_closes_at_the_last_one(deps, factory):
    bot_id = make(factory)
    bot_entry.fire(deps, bot_id, "09:47")
    later = OPEN_TIME + timedelta(minutes=10)  # every quote is now older than QUIET_MAX
    assert bot_paper.end_paper(deps, bot_id, later) == 1
    fills = exits(factory)
    assert fills and {f.reason for f in fills} == {"switched to Live (last price)"}
    assert bot_paper_exits.open_runs(factory, bot_id) == []


def test_switch_to_live_needs_deps_for_open_paper(deps, factory):
    bot_id = make(factory)
    bot_entry.fire(deps, bot_id, "09:47")
    with pytest.raises(ValueError):
        bot_store.set_mode(factory, bot_id, "live", OPEN_TIME)
    assert live_of(factory, bot_id) is False


def test_live_entry_after_switch_counts_only_real(deps, factory):
    bot_id = make(factory)
    bot = bot_store.load(factory, bot_id)
    times = (EntryTimeDef("09:47"), EntryTimeDef("10:00"))
    bot_store.save(factory, replace(bot, entry_times=times), OPEN_TIME)
    bot_entry.fire(deps, bot_id, "09:47")
    assert orders.used_today(factory, bot_id, OPEN_TIME) == 1  # the pretend entry
    bot_store.set_mode(factory, bot_id, "live", OPEN_TIME, deps)
    assert orders.used_today(factory, bot_id, OPEN_TIME) == 0
    assert not bot_store.load(factory, bot_id).enabled  # going Live disarms (final review I1)
    bot_store.set_armed(factory, bot_id, True, OPEN_TIME)
    later = OPEN_TIME + timedelta(minutes=13)
    deps.now = lambda: later
    stream_chain(deps.hub, later)
    bot_entry.fire(deps, bot_id, "10:00")
    assert [c for c in deps.tasty.calls if c[0] == "place"]


def _regroup(factory, run_id, drop_fill_of=None, groups=None):
    with factory() as session:
        run = session.get(BotRun, run_id)
        if groups:
            run.legs = [{**leg, "group": g} for leg, g in zip(run.legs, groups, strict=True)]
        if drop_fill_of is not None:
            run.filled_legs = [
                f for f in run.filled_legs if f["symbol"] != run.legs[drop_fill_of]["symbol"]
            ]
        session.commit()


def test_no_price_ever_closes_at_where_it_opened(deps, factory):
    bot_id = make(factory)
    run_id = bot_entry.fire(deps, bot_id, "09:47")
    deps.hub.quotes.clear()
    assert bot_paper.end_paper(deps, bot_id, OPEN_TIME + timedelta(minutes=1)) == 1
    fills = exits(factory)
    assert {f.reason for f in fills} == {"switched to Live (last price)"}
    with factory() as session:
        assert sum(f.price for f in fills) == -session.get(BotRun, run_id).fill_credit
    assert bot_paper_exits.open_runs(factory, bot_id) == []


def test_a_leg_with_no_fill_record_still_closes(deps, factory):
    bot_id = make(factory)
    run_id = bot_entry.fire(deps, bot_id, "09:47")
    _regroup(factory, run_id, drop_fill_of=0)
    deps.hub.quotes.clear()
    assert bot_paper.end_paper(deps, bot_id, OPEN_TIME + timedelta(minutes=1)) == 1
    fills = exits(factory)
    assert {f.reason for f in fills} == {"switched to Live (last price)"}
    assert fills[0].price == D(0)  # the group with the unrecorded fill; the other has its own
    assert bot_paper_exits.open_runs(factory, bot_id) == []


def test_two_groups_one_without_a_price_both_close(deps, factory):
    bot_id = make(factory)
    run_id = bot_entry.fire(deps, bot_id, "09:47")
    _regroup(factory, run_id, groups=[0, 0, 1, 1], drop_fill_of=3)
    [run] = bot_paper_exits.open_runs(factory, bot_id)
    for leg in run.legs[:2]:
        deps.hub.set(leg["symbol"], D("1.00"), D("1.10"), OPEN_TIME + timedelta(seconds=30))
    for leg in run.legs[2:]:
        deps.hub.quotes.pop(leg["symbol"], None)
    bot_store.set_mode(factory, bot_id, "live", OPEN_TIME + timedelta(seconds=40), deps)
    assert sorted(f.group for f in exits(factory)) == [0, 1]
    assert bot_paper_exits.open_runs(factory, bot_id) == []
    assert live_of(factory, bot_id) is True


def test_the_watcher_ignores_a_live_bots_leftover_paper_run(deps, factory):
    bot_id = make(factory)
    bot_entry.fire(deps, bot_id, "09:47")
    bot_store.set_live(factory, bot_id, True, OPEN_TIME)  # as if left behind
    assert bot_paper_exits._load(deps) == []
    bot_paper_exits.watch_pass(deps, OPEN_TIME + timedelta(minutes=1))
    assert exits(factory) == []


def test_a_close_rechecks_what_is_held(deps, factory):
    bot_id = make(factory)
    bot_entry.fire(deps, bot_id, "09:47")
    [paper] = bot_paper_exits._load(deps, bot_id)
    later = OPEN_TIME + timedelta(seconds=30)
    for leg in paper.legs:
        deps.hub.set(leg["symbol"], D("1.00"), D("1.10"), later)
    assert bot_paper_exits._close(deps, paper, 0, 1, "stop", later) is True
    paper.held[0] = 1  # this pass's copy is out of date
    assert bot_paper_exits._close(deps, paper, 0, 1, "stop", later) is False
    assert len(exits(factory)) == 1
