"""A Paper bot's scheduled entry: the order check as a preview, then a pretend fill at
the mid (services/bot_paper.py, decision 0061)."""

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from optionsjourney.bots.definition import EntryTimeDef
from optionsjourney.db.models import BotRun, PaperFill
from optionsjourney.services import bot_entry, bot_paper, bot_store, orders
from tests.services.bot_fakes import (
    OPEN_TIME,
    FakeChain,
    FakeHub,
    FakeTasty,
    make,
    stream_chain,
)

D = Decimal


@pytest.fixture
def deps(factory, monkeypatch):
    tasty = FakeTasty()
    placed = []
    monkeypatch.setattr(bot_entry, "place_protections", lambda deps, run_id: placed.append(run_id))
    alerts = []
    d = bot_entry.BotDeps(
        factory,
        lambda: [tasty],
        FakeHub(),
        FakeChain(),
        notify=alerts.append,
        sleep=lambda s: None,
        now=lambda: OPEN_TIME,
        background=lambda fn, *args: fn(*args),
    )
    stream_chain(d.hub)
    d.tasty, d.placed, d.alerts = tasty, placed, alerts
    return d


def run_of(factory, run_id):
    with factory() as session:
        return session.get(BotRun, run_id)


def paper_fills(factory):
    with factory() as session:
        return list(session.scalars(select(PaperFill)))


def test_paper_entry_fills_at_mid(deps, factory):
    run_id = bot_entry.fire(deps, make(factory), "09:47")
    run = run_of(factory, run_id)
    mid = sum(D(leg["mid"]) * (1 if leg["side"] == "sell" else -1) for leg in run.legs)
    assert run.paper is True and run.live is False and run.status == "filled"
    assert run.fill_credit == mid and run.filled_at is not None and run.contracts == 1
    fills = paper_fills(factory)
    assert [(f.kind, f.reason, f.run_id, f.price, f.contracts) for f in fills] == [
        ("entry", "entry", run_id, mid, 1)
    ]
    assert [c[0] for c in deps.tasty.calls] == ["preview"]
    assert deps.placed == []  # its exits are the paper watcher's
    assert any(a.title == f"Paper: IC entered at {mid:f} (estimated at mid)" for a in deps.alerts)


def test_paper_fill_is_the_combo_mid(deps, factory):
    bot_id = make(factory)
    run_id = bot_entry._claim(deps, bot_store.load(factory, bot_id), "k", "09:47", False)
    symbols = ["SPXW  261013C05910000", "SPXW  261013C05985000", "SPXW  261013P05690000",
               "SPXW  261013P05615000"]  # fmt: skip
    legs = [
        {"symbol": s, "side": side, "ratio": 1, "mid": mid}
        for s, side, mid in zip(
            symbols,
            ("sell", "buy", "sell", "buy"),
            ("3.05", "0.275", "2.375", "0.125"),
            strict=True,
        )
    ]
    assert bot_paper.enter(deps, run_id, legs, 1, OPEN_TIME) is None
    run = run_of(factory, run_id)
    assert run.status == "filled" and run.paper and run.fill_credit == D("5.025")
    assert [f.price for f in paper_fills(factory)] == [D("5.025")]
    assert any(a.title == "Paper: IC entered at 5.025 (estimated at mid)" for a in deps.alerts)


def test_paper_entry_never_sends(deps, factory):
    bot_entry.fire(deps, make(factory), "09:47")
    assert [c for c in deps.tasty.calls if c[0] in ("place", "replace")] == []
    with factory() as session:
        from optionsjourney.db.models import OrderLog

        assert session.scalars(select(OrderLog).where(OrderLog.action == "send")).all() == []


def test_paper_entry_respects_stop_all_bots(deps, factory):
    orders.set_switch(factory, "no_new_trades", True, OPEN_TIME)
    run = run_of(factory, bot_entry.fire(deps, make(factory), "09:47"))
    assert run.status == "skipped"
    assert paper_fills(factory) == []


def test_paper_entry_fills_on_new_strikes_the_stream_hasnt_sent_yet(deps, factory):
    """14:23 on 2026-10-16: freshly picked strikes have no stream quote yet. The entry's
    own chain mids are the fill, so it mustn't wait on the stream."""
    deps.hub.quotes.clear()
    run = run_of(factory, bot_entry.fire(deps, make(factory), "09:47"))
    assert run.status == "filled" and run.paper
    assert [f.kind for f in paper_fills(factory)] == ["entry"]


def _legs_without_mids():
    symbols = ["SPXW  261013C05910000", "SPXW  261013C05985000", "SPXW  261013P05690000",
               "SPXW  261013P05615000"]  # fmt: skip
    return [{"symbol": s, "side": side, "ratio": 1}
            for s, side in zip(symbols, ("sell", "buy", "sell", "buy"), strict=True)]  # fmt: skip


def _claimed(deps, factory):
    return bot_entry._claim(deps, bot_store.load(factory, make(factory)), "k", "09:47", False)


def test_paper_entry_waits_for_a_legs_first_stream_quote(deps, factory):
    run_id, legs = _claimed(deps, factory), _legs_without_mids()
    quotes = dict(deps.hub.quotes)
    deps.hub.quotes.clear()
    waits = []

    def sleep(seconds):  # the first quotes arrive after the first wait
        waits.append(seconds)
        deps.hub.quotes.update(quotes)

    deps.sleep = sleep
    assert bot_paper.enter(deps, run_id, legs, 1, OPEN_TIME) is None
    assert waits == [bot_paper.STREAM_POLL]
    assert run_of(factory, run_id).status == "filled"


def test_paper_entry_with_no_price_at_all_skips_after_waiting(deps, factory):
    run_id, legs = _claimed(deps, factory), _legs_without_mids()
    first = legs[0]["symbol"]
    deps.hub.quotes[first] = replace(deps.hub.quotes[first], bid=None)
    waits = []
    deps.sleep = waits.append
    assert bot_paper.enter(deps, run_id, legs, 1, OPEN_TIME) == bot_paper.NO_PRICE
    assert sum(waits) == bot_paper.STREAM_WAIT
    assert paper_fills(factory) == []


def test_paper_entry_never_uses_an_old_stream_quote(deps, factory):
    run_id, legs = _claimed(deps, factory), _legs_without_mids()
    stream_chain(deps.hub, OPEN_TIME - timedelta(minutes=10))
    assert bot_paper.enter(deps, run_id, legs, 1, OPEN_TIME) == bot_paper.NO_PRICE
    assert paper_fills(factory) == []


def test_paper_caps_count_paper_entries(deps, factory):
    bot_id = make(factory)
    bot = bot_store.load(factory, bot_id)
    times = (EntryTimeDef("09:47"), EntryTimeDef("10:00"))
    _, problems = bot_store.save(factory, replace(bot, max_per_day=1, entry_times=times), OPEN_TIME)
    assert problems == {}
    assert run_of(factory, bot_entry.fire(deps, bot_id, "09:47")).status == "filled"
    assert bot_paper.used_today(factory, bot_id, OPEN_TIME) == 1
    later = OPEN_TIME + timedelta(minutes=13)
    deps.now = lambda: later
    stream_chain(deps.hub, later)
    run = run_of(factory, bot_entry.fire(deps, bot_id, "10:00"))
    assert run.status == "skipped" and "limit of 1 a day" in run.reason
    assert len(paper_fills(factory)) == 1


def test_preview_now_stays_a_check_only_preview(deps, factory):
    run = run_of(factory, bot_entry.fire(deps, make(factory), None, manual=True))
    assert run.status == "preview" and not run.paper
    assert paper_fills(factory) == []


def _held_by(name, deps, factory):
    """The run ids a loop's selection of held runs picks up."""
    from optionsjourney.services import (
        bot_integrity,
        bot_overnight,
        bot_rearm,
        bot_settle,
        bot_timed,
    )

    today = OPEN_TIME.date()
    return {
        "timed": lambda: [r[0] for r in bot_timed._open_runs(deps, lambda b, r: True, today)],
        "overnight": lambda: bot_overnight.held_runs(deps, OPEN_TIME),
        "settle": lambda: [r[0] for r in bot_settle._split_runs(deps, OPEN_TIME)],
        "rearm": lambda: bot_rearm.open_runs(factory, OPEN_TIME),
        "integrity": lambda: [r.id for r in bot_integrity._live_runs(deps, OPEN_TIME)],
    }[name]()


@pytest.mark.parametrize("name", ["timed", "overnight", "settle", "rearm", "integrity"])
def test_the_live_loops_leave_a_paper_run_alone(deps, factory, name):
    run_id = bot_entry.fire(deps, make(factory), "09:47")
    assert run_of(factory, run_id).paper
    calls = len(deps.tasty.calls)
    assert run_id not in _held_by(name, deps, factory)
    assert len(deps.tasty.calls) == calls  # no broker reads


def test_the_exits_check_leaves_a_paper_run_alone(deps, factory):
    from optionsjourney.services import bot_loops

    run_id = bot_entry.fire(deps, make(factory), "09:47")
    seen = []
    deps.notify = seen.append
    calls = len(deps.tasty.calls)
    bot_loops.check_exits(deps)
    assert seen == [] and len(deps.tasty.calls) == calls
    assert run_of(factory, run_id).status == "filled"
