"""Preview order: a bot's order picked and checked with the broker, never sent, no run
saved (services/bot_entry.preview_order)."""

from decimal import Decimal

import pytest
from sqlalchemy import func, select

from optionsjourney.db.models import BotRun
from optionsjourney.services import bot_entry, orders
from tests.services.bot_fakes import OPEN_TIME, FakeChain, FakeHub, FakeTasty, make, stream_chain


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


def runs(factory) -> int:
    with factory() as session:
        return session.scalar(select(func.count()).select_from(BotRun))


@pytest.mark.parametrize("live", [True, False])
def test_preview_order_saves_no_run(deps, factory, live):
    bot_id = make(factory, live=live)
    before = runs(factory)
    result = bot_entry.preview_order(deps, bot_id)
    assert runs(factory) == before
    assert result.ok and len(result.legs) == 4 and result.contracts == 1
    assert result.price is not None and result.graph is not None
    assert len(result.graph["legs"]) == 4 and result.graph["symbol"] == "SPX"
    assert all(leg["live_price"] is not None for leg in result.graph["legs"])
    assert [c[0] for c in deps.tasty.calls] == ["preview"]  # checked, never placed


def test_preview_order_graph_is_entered_at_the_mids(deps, factory):
    result = bot_entry.preview_order(deps, make(factory))
    by_strike = {float(leg["strike"]): leg for leg in result.legs}
    for leg in result.graph["legs"]:
        picked = by_strike[leg["strike"]]
        sign = 1 if picked["side"] == "sell" else -1  # + received, - paid
        assert Decimal(str(leg["entry_price"])) == sign * Decimal(picked["mid"])
        assert leg["side"] == ("SHORT" if picked["side"] == "sell" else "LONG")
        assert leg["quantity"] == 1 and leg["multiplier"] == 100


def test_preview_order_says_each_groups_exits(deps, factory):
    result = bot_entry.preview_order(deps, make(factory))  # a 95% stop on each side
    assert len(result.exits) == 2, result.exits
    assert all("stop" in line and "no stop" not in line for line in result.exits)


def test_preview_order_works_when_the_market_is_closed(deps, factory):
    """It sends nothing, so the market's hours don't apply (Tracker2's require_open=False)."""
    from datetime import timedelta

    bot_id = make(factory)
    deps.now = lambda: OPEN_TIME + timedelta(hours=8)
    result = bot_entry.preview_order(deps, bot_id)
    assert result.ok, result.reason
    assert result.legs and result.graph is not None
    assert runs(factory) == 0


def test_preview_order_frozen(deps, factory):
    bot_id = make(factory)
    orders.set_switch(factory, "freeze", True, OPEN_TIME)
    result = bot_entry.preview_order(deps, bot_id)
    assert not result.ok and result.reason == "Orders are frozen." and result.graph is None


def test_preview_order_unknown_bot(deps):
    assert not bot_entry.preview_order(deps, 999).ok


def test_preview_order_survives_a_broker_failure(deps, factory):
    from optionsjourney.brokers.http import BrokerError

    def boom(*args, **kwargs):
        raise BrokerError("connection reset")

    bot_id = make(factory)
    before = runs(factory)
    deps.chain.expirations = boom
    result = bot_entry.preview_order(deps, bot_id)
    assert not result.ok and "Couldn't reach the broker: connection reset" in result.reason
    assert result.graph is None and runs(factory) == before


def test_preview_order_survives_an_unexpected_error(deps, factory):
    def boom(*args, **kwargs):
        raise ValueError("odd")

    bot_id = make(factory)
    deps.chain.option_rows = boom
    result = bot_entry.preview_order(deps, bot_id)
    assert not result.ok and "Something went wrong (ValueError)" in result.reason


def test_preview_order_shows_what_the_fallback_try_would_pick(deps, factory):
    from dataclasses import replace

    from optionsjourney.services import bot_store

    bot_id = make(factory)
    bot = bot_store.load(factory, bot_id)
    legs = tuple(replace(leg, fallback_target=Decimal("1.00")) if leg.position in (0, 2) else leg
                 for leg in bot.legs)  # fmt: skip
    assert bot_store.save(factory, replace(bot, legs=legs, min_short_width=Decimal("5")),
                          OPEN_TIME)[1] == {}  # fmt: skip
    result = bot_entry.preview_order(deps, bot_id)
    assert len(result.fallback_legs) == 4 and result.fallback_problem is None
    assert result.fallback_price < result.price  # further out, a smaller credit
    assert [c[0] for c in deps.tasty.calls] == ["preview"]  # the fallback isn't sent


def test_preview_order_has_no_fallback_without_one(deps, factory):
    result = bot_entry.preview_order(deps, make(factory))
    assert result.fallback_legs == () and result.fallback_problem is None
