"""The tour's facts: worked out from what happened in the demo, not from where."""

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from tests.demo.conftest import (
    _advance,
    _arm,
    _bot_id,
    _deps,
    _enter_condor,
    _Inline,
    _parts,
    _prime_quotes,
    _spread,
)

from optionsjourney.bots.definition import BotDef, GroupDef, LegDef
from optionsjourney.db.models import Bot
from optionsjourney.demo.book import TASTY_ACCOUNT
from optionsjourney.demo.seed import MANUAL_EXITS, WATCHLIST, WATCHLIST_ID
from optionsjourney.demo.tour_facts import Visited, facts
from optionsjourney.services import bot_loops, bot_store, journal, manual_attach, manual_bots
from optionsjourney.trading.orders import Side

HEADERS = {"Origin": "http://localhost"}


def _facts(s, visited=None):
    return facts(s.session_factory, visited or Visited(), s.demo.clock)


def test_fresh_seat_has_no_facts(demo_app):
    s, _ = _parts(demo_app)
    f = _facts(s)
    assert not any(
        [
            f.opened_automation, f.condor_armed, f.condor_stopped, f.opened_condor_run,
            f.spread_managed, f.calendar_target_filled, f.watchlist_changed,
            f.own_bot_entered, f.day_over,
        ]
    )  # fmt: skip
    assert f.calendar_bot_id == _bot_id(s, "Weekly calendar")
    assert f.condor_run_id is None


def test_visited_keeps_only_tour_paths():
    visited = Visited()
    visited.record("/automation?x=1")
    visited.record("/help")
    assert visited.paths == {"/automation"}
    visited.record("/automation/runs/7?tab=exits")
    visited.record("/automation/3/runs")
    visited.record("/automation/new")
    visited.record("/trade?symbol=SPX")
    visited.record("/charts")
    assert visited.paths == {
        "/automation", "/automation/runs/7", "/automation/3/runs", "/trade", "/charts",
    }  # fmt: skip


def test_opening_a_run_page_counts(demo_app):
    s, _ = _parts(demo_app)
    visited = Visited()
    visited.record("/automation")
    assert _facts(s, visited).opened_automation
    assert not _facts(s, visited).opened_condor_run
    visited.record(f"/automation/{_bot_id(s, 'SPX 0DTE condor')}/runs")
    assert _facts(s, visited).opened_condor_run


def test_condor_armed_and_stopped(demo_app):
    s, demo = _parts(demo_app)
    deps = _deps(s)
    run = _enter_condor(s, deps)
    f = _facts(s)
    assert f.condor_armed and f.condor_run_id == run.id and not f.condor_stopped
    _advance(s, deps, 120)
    assert _facts(s).condor_stopped
    visited = Visited()
    visited.record(f"/automation/runs/{run.id}")
    assert _facts(s, visited).opened_condor_run


def test_steps_complete_from_what_happened_not_where_it_happened(demo_app):
    s, _ = _parts(demo_app)
    bot_id = _bot_id(s, "SPX 0DTE condor")
    with demo_app.test_client() as client:
        response = client.post(
            f"/automation/{bot_id}/arm",
            data={"on": "1", "confirmed": "1", "understood": "1"},
            headers=HEADERS,
        )
        assert response.status_code == 302
    assert _facts(s).condor_armed


def test_spread_managed_after_attaching_rules(demo_app):
    s, demo = _parts(demo_app)
    deps = _deps(s)
    oid = demo.tasty.place_order(TASTY_ACCOUNT, _spread())
    demo.engine.step(demo.market.now + timedelta(seconds=3))
    key = f"tastytrade:{TASTY_ACCOUNT}:{oid}"
    [(manual_id, rules)] = [
        x for x in manual_bots.listing(s.session_factory) if x[1].name == MANUAL_EXITS
    ]
    assert not _facts(s).spread_managed
    outcome = manual_attach.attach(deps, key, rules, manual_id)
    assert outcome.ok, outcome.message
    assert _facts(s).spread_managed


def test_calendar_target_filled(demo_app):
    s, demo = _parts(demo_app)
    deps = _deps(s)
    demo.director.on_tour_step(5)
    for _ in range(4):
        if _facts(s).calendar_target_filled:
            break
        _advance(s, deps, 60)
    assert _facts(s).calendar_target_filled


def test_watchlist_changed(demo_app):
    s, _ = _parts(demo_app)
    assert not _facts(s).watchlist_changed
    with demo_app.test_client() as client:
        response = client.put(
            f"/api/watchlist/groups/{WATCHLIST_ID}",
            json={"symbols": [*WATCHLIST, "SPY"]},
            headers=HEADERS,
        )
        assert response.status_code == 200
    assert _facts(s).watchlist_changed


def test_own_bot_entered(demo_app):
    s, demo = _parts(demo_app)
    deps = _deps(s)
    now = demo.clock.now()
    own = BotDef(
        None, "My spread", journal.tag_for(s.session_factory, "Credit Spreads").id,
        "tastytrade", TASTY_ACCOUNT, underlying="SPX", contracts=1, max_entries_per_day=1,
        legs=(
            LegDef(0, Side.SELL, "put", pick="spot", target=Decimal(-30)),
            LegDef(1, Side.BUY, "put", pick="offset", target=Decimal(-10), link=0),
        ),
        groups=(GroupDef(0, "Put spread", stop_pct=Decimal(100), target_pct=Decimal(50)),),
        use_floating_entry=True, floating_start="09:40", floating_end="15:00",
    )  # fmt: skip
    bot_id, problems = bot_store.save(s.session_factory, own, now)
    assert not problems, problems
    assert not _facts(s).own_bot_entered
    _arm(s, "My spread")
    _prime_quotes(s)
    bot_loops.clock_pass(deps, spawn=_Inline)
    bot_loops._monitor(deps)
    assert _facts(s).own_bot_entered
    with s.session_factory() as session:
        assert session.scalar(select(Bot.per_trade).where(Bot.id == bot_id)) is False
