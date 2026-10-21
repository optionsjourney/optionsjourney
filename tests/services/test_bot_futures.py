"""Bots slice 17: futures-options bots (decision 0043; Tracker2's analyzer_entry futures
path, futures_legs, adopt's futures branch). Every broker is a fake; nothing live is sent."""

from decimal import Decimal

import pytest
from sqlalchemy import select

from optionsjourney.db.models import Bot, BotRun
from optionsjourney.services import bot_entry, bot_loops, bot_store
from tests.services.bot_fakes import (
    OPEN_TIME,
    FakeChain,
    FakeHub,
    FakeTasty,
    make_futures,
    on_futures,
)

D = Decimal
ACCOUNT = "5WX01234"


@pytest.fixture
def deps(factory):
    tasty = FakeTasty()
    d = bot_entry.BotDeps(
        factory, lambda: [tasty], FakeHub(), FakeChain(), notify=lambda e: None,
        sleep=lambda s: None, now=lambda: OPEN_TIME, background=lambda fn, *args: fn(*args),
    )  # fmt: skip
    d.tasty = tasty
    d.alerts = []
    d.notify = d.alerts.append
    bot_entry.ACTIVE.clear()
    from optionsjourney.services import bot_limit as limits

    limits._unreadable.clear()
    on_futures(d)
    return d


def runs(factory):
    with factory() as session:
        return list(session.scalars(select(BotRun).order_by(BotRun.id)))


# --- A futures bot (Task 2) --------------------------------------------------------------


def test_a_futures_bot_cant_be_armed_or_previewed(deps, factory):
    """The author's ruling: a futures bot enters only from the Trade page's Manage with a
    bot. It's never armed, so the clock never fires it, and Preview now refuses."""
    bot_id = make_futures(factory)
    assert bot_store.set_armed(factory, bot_id, True, OPEN_TIME) is False
    with factory() as session:
        assert session.get(Bot, bot_id).enabled is False
    with factory() as session:  # even armed straight in the table, it never fires
        session.get(Bot, bot_id).enabled = True
        session.commit()
    started = []
    bot_loops.clock_pass(deps, spawn=lambda **kw: started.append(kw) or _Thread())
    for call in started:
        call["target"](*call["args"])
    run_id = bot_entry.fire(deps, bot_id, None, manual=True)
    (run,) = [r for r in runs(factory) if r.id == run_id]
    assert run.status == "skipped" and run.reason == bot_entry.FUTURES_ONLY_FROM_TICKET
    assert all(r.status == "skipped" for r in runs(factory))
    assert [c for c in deps.tasty.calls if c[0] in ("preview", "place")] == []


class _Thread:
    def start(self):
        return None


def test_a_futures_root_must_have_listed_options_when_saved(deps, factory):
    from dataclasses import replace

    bot_id = make_futures(factory)
    bot = replace(bot_store.load(factory, bot_id), id=None, name="GC", underlying="/GC")
    _, problems = bot_store.save(factory, bot, OPEN_TIME, listed=lambda ticker: False)
    assert problems["underlying"] == "TastyTrade lists no futures options on /GC. Check the root."
    _, problems = bot_store.save(factory, bot, OPEN_TIME, listed=lambda ticker: True)
    assert problems == {}


# --- Profit target only (Task 5) -----------------------------------------------------------

from optionsjourney.db.models import BotExit  # noqa: E402
from optionsjourney.services import bot_exits, bot_manage, orders  # noqa: E402
from optionsjourney.trading.orders import OrderStatus, plain  # noqa: E402
from tests.services.bot_fakes import fut_sym  # noqa: E402

PUT7000, CALL7200 = fut_sym(7000, "put"), fut_sym(7200, "call")


def filled_run(factory, deps, bot_id, legs=((PUT7000, "sell", "12.50", 0),), contracts=1):
    """A filled futures run as the resting entry records it (Task 6 sends it for real):
    its legs, fills, contract and ×50, profit target only. The broker holds the legs."""
    from optionsjourney.core.market import MARKET_TZ

    now = deps.now()
    with factory() as session:
        run = BotRun(
            bot_id=bot_id, key=f"ticket-{bot_id}-{len(runs(factory)):012x}",
            trading_date=now.astimezone(MARKET_TZ).date(), entry_time="09:47", status="filled",
            live=True, account_number=ACCOUNT, broker="tastytrade",
            legs=[{"position": n, "symbol": s, "strike": s.split()[-1][7:], "side": side,
                   "right": "put" if "P" in s.split()[-1] else "call", "ratio": 1,
                   "group": group, "mid": price, "expiration": "2026-11-20", "dte": 38}
                  for n, (s, side, price, group) in enumerate(legs)],
            filled_legs=[{"symbol": s, "side": side, "fill_price": price,
                          "contracts": contracts} for s, side, price, _ in legs],
            fill_credit=sum(D(p) * (1 if side == "sell" else -1) for _, side, p, _ in legs),
            target_credit=D("12.50"), contracts=contracts, chase_log=[], filled_at=now,
            multiplier=D(50), contract="/ESZ6", pt_only=True, entry_mode="limit",
            broker_order_id="900", created_at=now, updated_at=now,
        )  # fmt: skip
        session.add(run)
        session.commit()
        run_id = run.id
    for s, side, _, _ in legs:
        deps.tasty.held[plain(s)] = -contracts if side == "sell" else contracts
    return run_id


def exits_of(factory, run_id):
    with factory() as session:
        rows = session.scalars(select(BotExit).where(BotExit.run_id == run_id).order_by(BotExit.id))
        return [(e.kind, e.state, e.limit, e.group_position, e.broker_order_id) for e in rows]


def placed(tasty):
    return [c for c in tasty.calls if c[0] == "place"]


def test_a_futures_fill_rests_one_gtc_target_and_no_stop(deps, factory):
    """Tracker2's _place_close_limit: a 50% target on a 12.50 short put rests a GTC buy
    at 6.25, on /ES's 0.25 tick above 5, at TastyTrade. No stop is ever placed."""
    run_id = filled_run(factory, deps, make_futures(factory))
    bot_exits.place_protections(deps, run_id)
    assert exits_of(factory, run_id) == [("target", "working", D("6.25"), 0, "101")]
    ((_, _, body),) = placed(deps.tasty)
    assert body["time-in-force"] == "GTC" and body["order-type"] == "Limit"
    assert body["price"] == "6.25" and body["price-effect"] == "Debit"
    (leg,) = body["legs"]
    assert (leg["symbol"], leg["action"], leg["quantity"]) == (PUT7000, "Buy to Close", 1)
    assert leg["instrument-type"] == "Future Option"
    assert not [c for c in placed(deps.tasty) if c[2]["order-type"] in ("Stop", "Stop Limit")]


def test_a_strangle_gets_a_target_per_group_rounded_toward_a_fill(deps, factory):
    bot_id = make_futures(factory, legs=(("sell", "put"), ("sell", "call")), target="40")
    run_id = filled_run(
        factory, deps, bot_id, legs=((PUT7000, "sell", "12.30", 0), (CALL7200, "sell", "3.10", 1))
    )
    bot_exits.place_protections(deps, run_id)
    # 12.30 x 60% = 7.38 → 7.50 (pays more: toward a fill, as Tracker2's round_for_order);
    # 3.10 x 60% = 1.86 → 1.90 on the 0.05 tick under 5
    assert [e[:4] for e in exits_of(factory, run_id)] == [
        ("target", "working", D("7.50"), 0),
        ("target", "working", D("1.90"), 1),
    ]
    assert [c[2]["price"] for c in placed(deps.tasty)] == ["7.50", "1.90"]


def test_a_target_held_back_by_freeze_is_placed_when_its_off(deps, factory):
    run_id = filled_run(factory, deps, make_futures(factory))
    orders.set_switch(factory, "freeze", True)
    bot_exits.place_protections(deps, run_id)
    assert [e[:2] for e in exits_of(factory, run_id)] == [("target", "pending")]
    assert any("waiting for Freeze" in a.title for a in deps.alerts)
    assert placed(deps.tasty) == []
    orders.set_switch(factory, "freeze", False)
    bot_exits.monitor_pass(deps)
    assert [e[:2] for e in exits_of(factory, run_id)] == [("target", "working")]
    assert len(placed(deps.tasty)) == 1


def test_an_unclear_futures_target_send_holds_the_group(deps, factory):
    """Final review C1: a target whose send got no clear answer may rest at TastyTrade:
    held (an "unclear" event, Needs you), never just "failed"."""
    from tests.services.test_bot_exits import target_sends_unclear

    run_id = filled_run(factory, deps, make_futures(factory))
    target_sends_unclear(deps)
    bot_exits.place_protections(deps, run_id)
    with factory() as session:
        (row,) = session.scalars(select(BotExit).where(BotExit.run_id == run_id))
        assert row.state == "failed" and row.log[-1]["event"] == bot_exits.TARGET_UNCLEAR
        assert bot_exits.target_may_rest([row])
        assert session.get(BotRun, run_id).reason.startswith(bot_entry.NEEDS_YOU)
    assert [a for a in deps.alerts if a.title.endswith("profit target unclear")]
    assert not [a for a in deps.alerts if "has no exit at all" in a.detail]


def test_a_stale_placing_futures_target_after_a_restart_is_held(deps, factory):
    from datetime import timedelta

    run_id = filled_run(factory, deps, make_futures(factory))
    bot_exits.place_protections(deps, run_id)
    with factory() as session:
        (row,) = session.scalars(select(BotExit).where(BotExit.run_id == run_id))
        row.state, row.broker_order_id = "placing", None
        session.commit()
    deps.now = lambda: OPEN_TIME + timedelta(minutes=3)
    bot_loops.check_exits(deps)
    with factory() as session:
        (row,) = session.scalars(select(BotExit).where(BotExit.run_id == run_id))
        assert row.state == "failed" and row.log[-1]["event"] == bot_exits.TARGET_UNCLEAR


def test_a_target_ended_at_the_broker_alerts_and_needs_you(deps, factory):
    """You cancelled (or changed) the target at TastyTrade: the app says so at once,
    urgently, marks the run Needs you, and never places it again."""
    from dataclasses import replace

    run_id = filled_run(factory, deps, make_futures(factory))
    bot_exits.place_protections(deps, run_id)
    order_id = exits_of(factory, run_id)[0][4]
    deps.tasty.orders[order_id] = replace(deps.tasty.orders[order_id],
                                          status=OrderStatus.CANCELLED)  # fmt: skip
    deps.alerts.clear()
    bot_exits.monitor_pass(deps)
    bot_exits.monitor_pass(deps)
    assert [e[:2] for e in exits_of(factory, run_id)] == [("target", "failed")]
    (alert,) = deps.alerts
    assert "profit target" in alert.title and alert.routine is False
    assert "This futures trade has no exits now" in alert.detail
    with factory() as session:
        assert session.get(BotRun, run_id).reason.startswith(bot_entry.NEEDS_YOU)
    assert len(placed(deps.tasty)) == 1


def test_a_target_the_broker_expires_after_expiry_ends_quietly(deps, factory):
    from dataclasses import replace
    from datetime import UTC, datetime

    run_id = filled_run(factory, deps, make_futures(factory))
    bot_exits.place_protections(deps, run_id)
    order_id = exits_of(factory, run_id)[0][4]
    deps.tasty.orders[order_id] = replace(deps.tasty.orders[order_id], status=OrderStatus.EXPIRED)
    deps.now = lambda: datetime(2026, 11, 20, 21, 30, tzinfo=UTC)
    deps.alerts.clear()
    bot_exits.monitor_pass(deps)
    assert [e[:2] for e in exits_of(factory, run_id)] == [("target", "expired")]
    assert deps.alerts == []


def test_a_target_that_fills_closes_the_group(deps, factory):
    bot_id = make_futures(factory)
    run_id = filled_run(factory, deps, bot_id)
    bot_exits.place_protections(deps, run_id)
    deps.tasty.fill(exits_of(factory, run_id)[0][4], D("6.25"))
    bot_exits.monitor_pass(deps)
    assert [e[:2] for e in exits_of(factory, run_id)] == [("target", "filled")]
    assert any("profit target filled" in a.title for a in deps.alerts)
    assert bot_entry._open_trades(deps, bot_store.load(factory, bot_id)) == 0


def test_a_futures_trade_isnt_managed_from_the_run_page_yet(deps, factory):
    run_id = filled_run(factory, deps, make_futures(factory))
    bot_exits.place_protections(deps, run_id)
    for outcome in (
        bot_manage.change_stop(deps, run_id, 0, "200"),
        bot_manage.exits_off(deps, run_id, 0),
        bot_manage.exits_on(deps, run_id, 0),
        bot_manage.close_part(deps, run_id, 0, "1"),
    ):
        assert not outcome.ok and outcome.message == bot_manage.FUTURES_NOT_YET
    assert len(placed(deps.tasty)) == 1
    assert [e[:2] for e in exits_of(factory, run_id)] == [("target", "working")]


def test_a_futures_trade_says_target_only_on_the_run_page_and_in_the_journal(deps, factory):
    from types import SimpleNamespace

    bot_id = make_futures(factory)
    run_id = filled_run(factory, deps, bot_id)
    bot_exits.place_protections(deps, run_id)
    (side,) = bot_manage.sides(factory, run_id, bot_store.load(factory, bot_id))
    assert side.futures and side.target == D("6.25") and side.target_order == "101"
    assert side.stop is None
    assert [c.label for c in side.chips] == ["Futures: profit target only, no stop"]
    entry = SimpleNamespace(
        bot=SimpleNamespace(run_id=run_id),
        position=SimpleNamespace(key="k1", legs=[SimpleNamespace(symbol=PUT7000)]),
    )
    chips = bot_manage.journal_chips(factory, [entry])
    assert [c.label for c in chips["k1"]] == ["Futures: profit target only, no stop"]
    # Bots slice 20: the journal's live refresh reads one row's chips the same way
    assert bot_manage.chips_for(factory, run_id, [PUT7000]) == chips["k1"]
    assert bot_manage.chips_for(factory, run_id, ["SPXW  261014C09999000"]) == ()
    assert bot_manage.chips_for(factory, 999, [PUT7000]) == ()


# --- The resting entry from the Trade page (Task 6) ---------------------------------------

from datetime import UTC, datetime, timedelta  # noqa: E402

from optionsjourney.services import bot_limit, bot_ticket, trade_ticket  # noqa: E402
from tests.services.bot_fakes import fut_stream, make  # noqa: E402

TOKEN = "0123456789ab"
EVENING = datetime(2026, 10, 13, 23, 0, tzinfo=UTC)  # 19:00 New York: CME open, SPX closed


def es_ticket(legs=((7000, "put", "sell"),), quantity=1, token=TOKEN, bot_id=None, **extra):
    """A futures ticket as the page posts it: the page's ticker, the legs' DXLink names."""
    rows = [{"stream_symbol": fut_stream(k, right), "side": side, "ratio": 1}
            for k, right, side in legs]  # fmt: skip
    return {"ticker": "/ES", "legs": rows, "quantity": quantity, "token": token,
            "bot_id": bot_id, **extra}  # fmt: skip


def kept(deps, body, price="12.50", tif="gtc"):
    """A passed review of the ticket at `price`, kept as Review keeps it (_keep)."""
    from optionsjourney.trading.orders import Leg, Order

    by_stream = {fut_stream(k, r): fut_sym(k, r) for k in range(6800, 7205, 25)
                 for r in ("call", "put")}  # fmt: skip
    legs = tuple(Leg(by_stream[leg["stream_symbol"]], leg["side"], leg["ratio"])
                 for leg in body["legs"] if leg["stream_symbol"] in by_stream)  # fmt: skip
    if len(legs) != len(body["legs"]):  # an unlisted leg: no review could have passed
        legs = (Leg(fut_sym(7000, "put"), "sell"),)
    order = Order(legs, body["quantity"], True, price=D(price),
                  time_in_force=trade_ticket.TIFS[tif])  # fmt: skip
    return trade_ticket._keep("tastytrade", ACCOUNT, order, deps.now())


def send(deps, body, **review):
    if "code" not in body:
        body = {**body, "code": kept(deps, body, **review)}
    return bot_ticket.enter(deps, "tastytrade", ACCOUNT, body)


def openings(tasty):
    return [c for c in tasty.calls if c[0] == "place"
            and c[2]["legs"][0]["action"].endswith("Open")]  # fmt: skip


def closings(tasty):
    return [c for c in tasty.calls if c[0] == "place"
            and c[2]["legs"][0]["action"].endswith("Close")]  # fmt: skip


def run_of(factory, run_id):
    with factory() as session:
        return session.get(BotRun, run_id)


def test_the_picker_offers_a_futures_bot_and_says_target_only(deps, factory):
    es = make_futures(factory)
    spx = make(factory, live=True)
    problem, offered = bot_ticket.choices(deps, "tastytrade", ACCOUNT, es_ticket())
    assert problem is None
    why = {bot.id: (reason, lines) for bot, reason, lines in offered}
    assert why[es] == (None, ["Put: sell 7000P Nov 20 · target 50% · no stop (futures)"])
    assert why[spx][0] == "It trades SPX, not /ES."
    assert deps.tasty.calls == [] or all(c[0] in ("fop_ticks", "future_ticks")
                                         for c in deps.tasty.calls)  # fmt: skip
    assert runs(factory) == []


def test_an_es_short_put_rests_fills_and_gets_only_its_target(deps, factory):
    """Tracker2's futures Limit: the reviewed limit rests GTC at TastyTrade; when it fills
    the bot rests one GTC profit target and never a stop. The run keeps x50 and /ESZ6."""
    deps.tasty.entry = "work"
    bot_id = make_futures(factory)
    sent = send(deps, es_ticket(bot_id=bot_id))
    assert sent.ok, sent.message
    assert "rests at TastyTrade as its entry" in sent.message
    run = run_of(factory, sent.run_id)
    assert run.status == "working" and run.key == f"ticket-{bot_id}-{TOKEN}"
    assert (run.multiplier, run.contract, run.pt_only, run.entry_mode) == (
        D(50), "/ESZ6", True, "limit")  # fmt: skip
    assert run.target_credit == D("12.50") and run.contracts == 1
    (leg,) = run.legs
    assert leg["symbol"] == fut_sym(7000, "put") and leg["expiration"] == "2026-11-20"
    assert leg["expires_at"] == "2026-11-20T21:00:00+00:00"
    assert leg["stream_symbol"] == fut_stream(7000, "put")
    assert run.chase_log[0]["event"] == bot_ticket.FROM_TICKET
    ((_, order_id, body),) = openings(deps.tasty)
    assert (body["time-in-force"], body["price"], body["price-effect"]) == ("GTC", "12.50",
                                                                            "Credit")  # fmt: skip
    assert body["legs"][0]["instrument-type"] == "Future Option"
    bot_limit.check_pass(deps)  # still resting
    assert run_of(factory, sent.run_id).status == "working" and closings(deps.tasty) == []
    deps.tasty.fill(order_id, D("12.50"))
    bot_limit.check_pass(deps)
    run = run_of(factory, sent.run_id)
    assert run.status == "filled" and run.fill_credit == D("12.50")
    assert exits_of(factory, sent.run_id) == [("target", "working", D("6.25"), 0, "102")]
    ((_, _, close),) = closings(deps.tasty)
    assert close["order-type"] == "Limit" and close["time-in-force"] == "GTC"
    assert not [c for c in deps.tasty.calls if c[0] == "place" and c[2]["order-type"] == "Stop"]
    filled = [a for a in deps.alerts if "filled" in a.title]
    assert filled and "$625.00 a contract" in filled[0].detail


def test_a_strangle_from_the_ticket_gets_a_target_per_group(deps, factory):
    bot_id = make_futures(factory, legs=(("sell", "put"), ("sell", "call")))
    body = es_ticket(legs=((7000, "put", "sell"), (7200, "call", "sell")), bot_id=bot_id)
    sent = send(deps, body, price="15.60")
    assert sent.ok, sent.message
    deps.tasty.prices.update({plain(PUT7000): D("12.30"), plain(CALL7200): D("3.30")})
    bot_limit.check_pass(deps)  # the fake filled it at once: the monitor records it
    assert run_of(factory, sent.run_id).status == "filled"
    assert [e[:2] + (e[3],) for e in exits_of(factory, sent.run_id)] == [
        ("target", "working", 0), ("target", "working", 1)]  # fmt: skip


@pytest.mark.parametrize(
    ("setup", "body", "review", "words"),
    [
        ("freeze", {}, {}, "Orders are frozen."),
        ("no_new_trades", {}, {}, "No new trades is on."),
        ("preview", {}, {}, "It's a Paper bot, so it would place nothing."),
        (None, {}, {"price": "0.03"}, "The credit is below $0.05"),
        (
            None,
            {"legs": ((6000, "put", "sell"),)},
            {},
            "./EW3X26P6000:XCME isn't listed in TastyTrade's /ES options.",
        ),
        ("no_ticks", {}, {}, "Couldn't load TastyTrade's option increments for EW3X6."),
    ],
)
def test_a_futures_ticket_that_cant_go_is_refused_before_the_claim(
    deps, factory, setup, body, review, words
):
    """Nothing is recorded or sent (run_analyzer_entry: "The symbols resolve BEFORE the
    run is claimed so a refusal leaves no BotRun")."""
    bot_id = make_futures(factory, live=setup != "preview")
    if setup in ("freeze", "no_new_trades"):
        orders.set_switch(factory, setup, True)
    if setup == "no_ticks":
        deps.tasty.fop_ticks = {}
    legs = body.pop("legs", ((7000, "put", "sell"),))
    sent = send(deps, es_ticket(legs=legs, bot_id=bot_id, **body), **review)
    assert not sent.ok and words in sent.message, sent.message
    assert runs(factory) == [] and openings(deps.tasty) == []


def test_a_partial_fill_protects_the_filled_and_places_the_rest_again(deps, factory):
    """Tracker2's _handle with _hold_partial: in the session the rest gets 60 seconds;
    then it's cancelled, the filled unit gets its target, and the rest rests again as
    its own run at the same price (_replace_remainder)."""
    deps.tasty.entry = "partial"
    bot_id = make_futures(factory)
    sent = send(deps, es_ticket(quantity=2, bot_id=bot_id))
    assert sent.ok, sent.message
    bot_limit.check_pass(deps)
    assert run_of(factory, sent.run_id).status == "working"
    assert not [c for c in deps.tasty.calls if c[0] == "cancel"]
    later = OPEN_TIME + timedelta(seconds=61)
    deps.now = lambda: later
    bot_limit.check_pass(deps)
    root = run_of(factory, sent.run_id)
    assert root.status == "filled" and root.contracts == 1
    assert [e[:3] for e in exits_of(factory, root.id)] == [("target", "working", D("6.25"))]
    (child,) = [r for r in runs(factory) if r.parent_run_id == root.id]
    assert child.status == "working" and child.contracts == 1 and child.key == f"{root.key}#t2"
    assert (child.pt_only, child.multiplier, child.contract) == (True, D(50), "/ESZ6")
    first, second = openings(deps.tasty)
    assert (first[2]["legs"][0]["quantity"], second[2]["legs"][0]["quantity"]) == (2, 1)
    assert second[2]["price"] == "12.50"


def test_outside_the_session_a_partial_is_settled_at_once(deps, factory):
    deps.tasty.entry = "partial"
    deps.now = lambda: EVENING
    bot_id = make_futures(factory)
    sent = send(deps, es_ticket(quantity=2, bot_id=bot_id))
    assert sent.ok, sent.message
    bot_limit.check_pass(deps)
    assert run_of(factory, sent.run_id).status == "filled"
    assert len(openings(deps.tasty)) == 2


def test_an_entry_cancelled_or_expired_at_the_broker_ends_and_alerts(deps, factory):
    from dataclasses import replace

    deps.tasty.entry = "work"
    bot_id = make_futures(factory)
    first = send(deps, es_ticket(bot_id=bot_id))
    second = send(deps, es_ticket(bot_id=bot_id, token="0123456789ac"))
    one, two = (c[1] for c in openings(deps.tasty))
    deps.tasty.orders[one] = replace(deps.tasty.orders[one], status=OrderStatus.CANCELLED)
    deps.tasty.orders[two] = replace(deps.tasty.orders[two], status=OrderStatus.EXPIRED)
    deps.alerts.clear()
    bot_limit.check_pass(deps)
    assert run_of(factory, first.run_id).status == "cancelled"
    assert run_of(factory, second.run_id).status == "missed"
    words = " ".join(a.detail for a in deps.alerts)
    assert "doesn't follow a new one" in words and "expired" in words
    assert closings(deps.tasty) == []


def test_cancel_entry_cancels_a_resting_entry_and_keeps_any_fill(deps, factory):
    """analyzer_entry.cancel_entry: an app-asked cancel keeps the fill and places no
    remainder."""
    deps.tasty.entry = "work"
    bot_id = make_futures(factory)
    sent = send(deps, es_ticket(bot_id=bot_id))
    outcome = bot_limit.cancel_entry(deps, sent.run_id)
    assert outcome.ok and "cancelled" in outcome.message
    assert run_of(factory, sent.run_id).status == "cancelled"
    deps.tasty.entry = "partial"
    partly = send(deps, es_ticket(quantity=2, bot_id=bot_id, token="0123456789ad"))
    outcome = bot_limit.cancel_entry(deps, partly.run_id)
    assert outcome.ok, outcome.message
    assert run_of(factory, partly.run_id).status == "filled"
    assert run_of(factory, partly.run_id).contracts == 1
    assert [r for r in runs(factory) if r.parent_run_id == partly.run_id] == []
    assert len(closings(deps.tasty)) == 1  # its target
    assert not bot_limit.cancel_entry(deps, partly.run_id).ok  # nothing resting now


def test_the_reconciler_leaves_a_resting_entry_alone(deps, factory):
    """A resting entry is "working", never "running": recovery never cancels it as an
    orphan (check_working_entries' runs are their own)."""
    deps.tasty.entry = "work"
    sent = send(deps, es_ticket(bot_id=make_futures(factory)))
    bot_loops.reconcile(deps)
    bot_loops.recover(deps)
    assert not [c for c in deps.tasty.calls if c[0] == "cancel"]
    assert run_of(factory, sent.run_id).status == "working"


def test_a_resting_entry_counts_toward_max_open_trades(deps, factory):
    deps.tasty.entry = "work"
    bot_id = make_futures(factory, max_open_trades=1)
    assert send(deps, es_ticket(bot_id=bot_id)).ok
    deps.now = lambda: OPEN_TIME + timedelta(days=1)  # a working entry counts on any day
    refused = send(deps, es_ticket(bot_id=bot_id, token="0123456789ae"))
    assert not refused.ok and "Max open trades" in refused.message
    assert len(openings(deps.tasty)) == 1


def test_the_monitor_leaves_a_held_futures_trade_alone(deps, factory):
    """Every monitor step reads a futures trade held overnight without a word: no stop
    is expected of it, its resting target is working, its legs aren't past expiry."""
    deps.tasty.close = "work"  # the target rests
    bot_id = make_futures(factory)
    sent = send(deps, es_ticket(bot_id=bot_id))
    bot_limit.check_pass(deps)
    assert run_of(factory, sent.run_id).status == "filled"
    deps.alerts.clear()
    calls = len(deps.tasty.calls)
    for when in (
        OPEN_TIME + timedelta(minutes=5),
        datetime(2026, 10, 14, 13, 25, tzinfo=UTC),
        datetime(2026, 10, 14, 19, 55, tzinfo=UTC),
        EVENING + timedelta(days=1),
    ):
        deps.now = lambda when=when: when
        bot_loops._monitor(deps)
        bot_loops._watch(deps)
    (said,) = deps.alerts  # 09:20's routine check, a futures trade's way
    assert said.title == "Held trades checked" and said.routine
    assert "each futures trade's profit target resting at TastyTrade" in said.detail
    assert not [c for c in deps.tasty.calls[calls:] if c[0] in ("place", "replace", "cancel")]
    assert [e[:2] for e in exits_of(factory, sent.run_id)] == [("target", "working")]


def test_the_09_20_check_says_a_futures_target_has_gone(deps, factory):
    from dataclasses import replace

    from optionsjourney.services import bot_overnight

    deps.tasty.close = "work"
    sent = send(deps, es_ticket(bot_id=make_futures(factory)))
    bot_limit.check_pass(deps)
    order_id = exits_of(factory, sent.run_id)[0][4]
    deps.tasty.orders[order_id] = replace(deps.tasty.orders[order_id],
                                          status=OrderStatus.CANCELLED)  # fmt: skip
    deps.now = lambda: datetime(2026, 10, 14, 13, 21, tzinfo=UTC)
    deps.alerts.clear()
    problems = bot_overnight.check(deps)
    assert problems == ["ES Put: its profit target shows cancelled at your broker"]


def test_a_futures_ticket_not_in_lowest_terms_is_refused(deps, factory):
    bot_id = make_futures(factory)
    body = es_ticket(bot_id=bot_id)
    body["legs"] = [dict(leg, ratio=2) for leg in body["legs"]]
    sent = send(deps, body)
    assert not sent.ok and sent.message == bot_ticket.NOT_AS_REVIEWED
    assert runs(factory) == [] and openings(deps.tasty) == []


def test_an_unclear_futures_send_is_found_and_left_resting(deps, factory):
    """Final review: an unclear send of a resting entry is never cancelled as an orphan.
    Once the order log finds it at the broker, the run is "working" again and
    check_pass owns it (the crash window between the submit and the status, too)."""
    from optionsjourney.brokers.http import RequestUnclear

    deps.tasty.entry = "work"
    real = deps.tasty.place_order

    def lost(account, body):
        real(account, body)
        raise RequestUnclear("Lost the connection")

    deps.tasty.place_order = lost
    sent = send(deps, es_ticket(bot_id=make_futures(factory)))
    assert sent.ok, sent.message
    run = run_of(factory, sent.run_id)
    assert run.status == "running"
    (said,) = [a for a in deps.alerts if "entry unclear" in a.title]
    assert "becomes the bot's resting entry" in said.detail and "cancel" not in said.detail
    deps.tasty.place_order = real
    bot_loops.reconcile(deps)
    run = run_of(factory, sent.run_id)
    assert (run.status, run.broker_order_id) == ("working", "101")
    assert not [c for c in deps.tasty.calls if c[0] == "cancel"]
    deps.tasty.fill("101", D("12.50"))
    bot_limit.check_pass(deps)
    assert run_of(factory, sent.run_id).status == "filled"
    assert [e[:2] for e in exits_of(factory, sent.run_id)] == [("target", "working")]


def test_a_crash_between_the_send_and_its_status_leaves_the_entry_resting(deps, factory):
    deps.tasty.entry = "work"
    sent = send(deps, es_ticket(bot_id=make_futures(factory)))
    bot_entry.update_run(deps, sent.run_id, status="running", broker_order_id=None)  # the crash
    bot_loops.recover(deps)
    run = run_of(factory, sent.run_id)
    assert (run.status, run.broker_order_id) == ("working", "101")
    assert not [c for c in deps.tasty.calls if c[0] == "cancel"]


def test_a_target_ended_after_its_series_own_expiry_is_quiet(deps, factory):
    """Final review: a series expiring before the equity close (/GC at 13:30) ends its
    target "expired" by the leg's own expires_at, with no false urgent alert."""
    from dataclasses import replace

    run_id = filled_run(factory, deps, make_futures(factory))
    with factory() as session:
        run = session.get(BotRun, run_id)
        run.legs = [dict(leg, expires_at="2026-11-20T18:30:00+00:00") for leg in run.legs]
        session.commit()
    bot_exits.place_protections(deps, run_id)
    order_id = exits_of(factory, run_id)[0][4]
    deps.tasty.orders[order_id] = replace(deps.tasty.orders[order_id], status=OrderStatus.EXPIRED)
    deps.now = lambda: datetime(2026, 11, 20, 19, 0, tzinfo=UTC)  # 14:00 New York
    deps.alerts.clear()
    bot_exits.monitor_pass(deps)
    assert [e[:2] for e in exits_of(factory, run_id)] == [("target", "expired")]
    assert deps.alerts == []


# --- Final review: the resting entry's crash windows and ends ------------------------------


def _partly(deps, factory, when=None):
    deps.tasty.entry = "partial"
    sent = send(deps, es_ticket(quantity=2, bot_id=make_futures(factory)))
    assert sent.ok, sent.message
    if when is not None:
        deps.now = lambda: when
    return sent.run_id


def test_a_crash_between_the_cancel_and_the_fill_still_places_the_rest(deps, factory, monkeypatch):
    run_id = _partly(deps, factory, EVENING)
    deps.now = lambda: EVENING
    real = bot_limit._partial
    monkeypatch.setattr(bot_limit, "_partial", lambda *a: (_ for _ in ()).throw(RuntimeError()))
    bot_limit.check_pass(deps)  # cancelled and confirmed, then the "crash"
    assert run_of(factory, run_id).status == "working"
    monkeypatch.setattr(bot_limit, "_partial", real)
    bot_limit.check_pass(deps)
    assert run_of(factory, run_id).status == "filled"
    (child,) = [r for r in runs(factory) if r.parent_run_id == run_id]
    assert child.status == "working" and child.contracts == 1


def test_a_part_filled_entry_cancelled_at_the_broker_says_the_rest_was_dropped(deps, factory):
    from dataclasses import replace

    run_id = _partly(deps, factory)
    (order_id,) = [c[1] for c in openings(deps.tasty)]
    deps.tasty.orders[order_id] = replace(deps.tasty.orders[order_id],
                                          status=OrderStatus.CANCELLED)  # fmt: skip
    deps.alerts.clear()
    bot_limit.check_pass(deps)
    assert run_of(factory, run_id).status == "filled" and run_of(factory, run_id).contracts == 1
    assert [r for r in runs(factory) if r.parent_run_id == run_id] == []
    assert any("1 contract(s) of the entry weren't placed again" in a.detail for a in deps.alerts)


def test_an_unconfirmed_partial_cancel_is_tried_twice_then_needs_you(deps, factory):
    run_id = _partly(deps, factory, EVENING)
    (order_id,) = [c[1] for c in openings(deps.tasty)]
    deps.tasty.stuck.add(order_id)
    deps.alerts.clear()
    for _ in range(4):
        bot_limit.check_pass(deps)
    assert len([c for c in deps.tasty.calls if c[0] == "cancel"]) == 2
    run = run_of(factory, run_id)
    assert run.status == "working" and run.reason.startswith(bot_entry.NEEDS_YOU)
    assert len([a for a in deps.alerts if "needs you" in a.title]) == 1


def test_a_resting_entry_that_stays_unreadable_alerts_once(deps, factory):
    from optionsjourney.brokers.http import BrokerError

    deps.tasty.entry = "work"
    sent = send(deps, es_ticket(bot_id=make_futures(factory)))
    deps.tasty.errors["get_order"] = BrokerError("down")
    deps.alerts.clear()
    for _ in range(bot_limit.UNREADABLE_PASSES + 3):
        bot_limit.check_pass(deps)
    (said,) = [a for a in deps.alerts if "can't be read" in a.title]
    assert run_of(factory, sent.run_id).status == "working"
    del deps.tasty.errors["get_order"]
    bot_limit.check_pass(deps)  # readable again: the count starts over
    assert bot_limit._unreadable.get(sent.run_id) is None


def test_a_remainder_keeps_the_roots_time_in_force(deps, factory):
    deps.tasty.entry = "partial"
    deps.now = lambda: EVENING
    sent = send(deps, es_ticket(quantity=2, bot_id=make_futures(factory)), tif="day")
    bot_limit.check_pass(deps)
    first, second = openings(deps.tasty)
    assert first[2]["time-in-force"] == second[2]["time-in-force"] == "Day"
    assert run_of(factory, sent.run_id).status == "filled"


def test_cancel_entry_waits_for_the_monitors_lock(deps, factory, monkeypatch):
    deps.tasty.entry = "work"
    bot_id = make_futures(factory)
    sent = send(deps, es_ticket(bot_id=bot_id))
    monkeypatch.setattr(bot_limit, "LOCK_WAIT", 0.01)
    lock = bot_limit._lock(bot_id)
    lock.acquire()
    try:
        busy = bot_limit.cancel_entry(deps, sent.run_id)
    finally:
        lock.release()
    assert not busy.ok and "busy" in busy.message
    assert not [c for c in deps.tasty.calls if c[0] == "cancel"]
    assert bot_limit.cancel_entry(deps, sent.run_id).ok


def test_a_target_that_partly_fills_then_is_cancelled_alerts(deps, factory):
    from dataclasses import replace

    run_id = filled_run(factory, deps, make_futures(factory), contracts=2)
    deps.tasty.close = "partial"
    bot_exits.place_protections(deps, run_id)
    order_id = exits_of(factory, run_id)[0][4]
    bot_exits.monitor_pass(deps)  # partly filled, still working: left alone
    assert [e[:2] for e in exits_of(factory, run_id)] == [("target", "working")]
    deps.tasty.orders[order_id] = replace(deps.tasty.orders[order_id],
                                          status=OrderStatus.CANCELLED)  # fmt: skip
    deps.alerts.clear()
    bot_exits.monitor_pass(deps)
    assert [e[:2] for e in exits_of(factory, run_id)] == [("target", "failed")]
    assert any("profit target gone" in a.title for a in deps.alerts)


def test_check_exits_covers_a_resting_entry_filled_on_a_later_day(deps, factory):
    """Final review: a GTC entry claimed yesterday and filled today whose target row is
    stuck "placing" (a crash) is flagged, as any of today's fills is."""
    run_id = filled_run(factory, deps, make_futures(factory))
    now = deps.now()
    with factory() as session:
        run = session.get(BotRun, run_id)
        run.trading_date = run.trading_date - timedelta(days=1)
        session.add(BotExit(run_id=run_id, group_position=0, kind="target", state="placing",
                            short_symbol=PUT7000, long_symbol="", contracts=1, rearms=0,
                            log=[], created_at=now, updated_at=now))  # fmt: skip
        session.commit()
    deps.now = lambda: now + timedelta(minutes=3)
    bot_loops.check_exits(deps)
    assert [e[:2] for e in exits_of(factory, run_id)] == [("target", "failed")]
    assert run_of(factory, run_id).reason.startswith(bot_entry.NEEDS_YOU)


def test_legs_in_two_series_with_different_price_steps_are_refused(deps, factory):
    """Final review: one order is priced on one tick, so legs in two series whose
    increments differ are refused before the claim (the safer of the two choices)."""
    from tests.services.bot_fakes import ES_TICKS, es_nested, fut_series

    other = [{"threshold": "5.0", "value": "0.05"}, {"value": "0.10"}]
    deps.chain.nested = es_nested(fut_series(day="2026-11-27", code="EW4X6"))
    deps.tasty.fop_ticks = {"ES": {"EW3X6": ES_TICKS, "EW4X6": other}}
    bot_id = make_futures(factory, legs=(("sell", "put"), ("sell", "call")))
    body = es_ticket(legs=((7000, "put", "sell"),), bot_id=bot_id)
    body["legs"].append({"stream_symbol": fut_stream(7200, "call", "EW4X6"), "side": "sell",
                         "ratio": 1})  # fmt: skip
    from optionsjourney.trading.orders import Leg, Order

    order = Order((Leg(fut_sym(7000, "put"), "sell"),
                   Leg(fut_sym(7200, "call", "EW4X6", day="2026-11-27"), "sell")), 1, True,
                  price=D("15.50"), time_in_force=trade_ticket.TIFS["gtc"])  # fmt: skip
    body["code"] = trade_ticket._keep("tastytrade", ACCOUNT, order, deps.now())
    sent = bot_ticket.enter(deps, "tastytrade", ACCOUNT, body)
    assert not sent.ok and "different price steps" in sent.message, sent.message
    assert runs(factory) == [] and openings(deps.tasty) == []
    from optionsjourney.db.models import TickSize

    with factory() as session:  # the same steps (the stored table): it goes
        session.get(TickSize, "FOP:ES:EW4X6").sizes = ES_TICKS
        session.commit()
    body = {
        **body,
        "token": "0123456789af",
        "code": trade_ticket._keep("tastytrade", ACCOUNT, order, deps.now()),
    }
    assert bot_ticket.enter(deps, "tastytrade", ACCOUNT, body).ok


def test_a_refused_partial_cancel_never_places_the_rest_later(deps, factory):
    """Re-review: a cancel the broker refused leaves no "asked" mark behind, so if you then
    cancel at TastyTrade yourself the rest isn't placed again as an order you didn't want."""
    from dataclasses import replace

    from optionsjourney.brokers.http import BrokerError

    run_id = _partly(deps, factory, EVENING)
    (order_id,) = [c[1] for c in openings(deps.tasty)]

    def refuse(account, oid):
        raise BrokerError("Order can't be cancelled now")

    real = deps.tasty.cancel_order
    deps.tasty.cancel_order = refuse
    bot_limit.check_pass(deps)
    deps.tasty.cancel_order = real
    assert run_of(factory, run_id).status == "working"
    deps.tasty.orders[order_id] = replace(
        deps.tasty.orders[order_id], status=OrderStatus.CANCELLED
    )  # you, at the broker
    bot_limit.check_pass(deps)
    assert run_of(factory, run_id).status == "filled"
    assert [r for r in runs(factory) if r.parent_run_id == run_id] == []
    assert len(openings(deps.tasty)) == 1


def test_the_unreadable_mark_clears_once_the_entry_is_read(deps, factory):
    from optionsjourney.brokers.http import BrokerError

    deps.tasty.entry = "work"
    sent = send(deps, es_ticket(bot_id=make_futures(factory)))
    deps.tasty.errors["get_order"] = BrokerError("down")
    for _ in range(bot_limit.UNREADABLE_PASSES):
        bot_limit.check_pass(deps)
    assert run_of(factory, sent.run_id).reason.startswith(bot_entry.NEEDS_YOU)
    del deps.tasty.errors["get_order"]
    bot_limit.check_pass(deps)
    assert not (run_of(factory, sent.run_id).reason or "").startswith(bot_entry.NEEDS_YOU)


def test_restore_places_a_dead_futures_target_again_at_tastytrade(deps, factory):
    """Bots slice 18 (rearm.py's incident: a target that died at the broker): the dry run
    lists it, Restore rests it again at its last limit, and never a stop."""
    from dataclasses import replace

    from optionsjourney.services import bot_rearm

    run_id = filled_run(factory, deps, make_futures(factory))
    bot_exits.place_protections(deps, run_id)
    order_id = exits_of(factory, run_id)[0][4]
    deps.tasty.orders[order_id] = replace(deps.tasty.orders[order_id],
                                          status=OrderStatus.CANCELLED)  # fmt: skip
    bot_exits.monitor_pass(deps)
    assert [e[:2] for e in exits_of(factory, run_id)] == [("target", "failed")]
    [item] = bot_rearm.rearm_trade(deps, run_id, dry_run=True)
    assert (item.kind, item.status, item.price) == ("target", "missing", D("6.25"))
    assert len(placed(deps.tasty)) == 1
    deps.tasty.held[plain(PUT7000)] = -1  # the fake filled it on placing; it didn't
    [item] = bot_rearm.rearm_trade(deps, run_id, dry_run=False)
    assert item.status == "placed" and "rests at TastyTrade" in item.detail, item
    [(kind, state, limit, _, new_id)] = exits_of(factory, run_id)
    assert (kind, state, limit) == ("target", "working", D("6.25")) and new_id != order_id
    sent = placed(deps.tasty)[-1][2]
    assert sent["time-in-force"] == "GTC" and sent["order-type"] == "Limit"
    assert bot_rearm.rearm_trade(deps, run_id, dry_run=True) == []


def test_a_futures_run_is_warned_once_in_its_series_expiry_window(deps, factory):
    """Bots slice 18 (from slice 17's review): from 10 minutes before the series' own
    expires-at to 5 after, one urgent alert per run per day; not before, not after, not
    for a closed group."""
    from datetime import UTC, datetime

    from optionsjourney.services import bot_expiry

    run_id = filled_run(factory, deps, make_futures(factory))
    with factory() as session:
        run = session.get(BotRun, run_id)
        run.legs = [{**leg, "expires_at": "2026-11-20T21:00:00+00:00"} for leg in run.legs]
        session.commit()
    deps.alerts.clear()
    for when in (datetime(2026, 11, 20, 20, 49, tzinfo=UTC), datetime(2026, 11, 20, 21, 5,
                                                                       tzinfo=UTC)):  # fmt: skip
        deps.now = lambda when=when: when
        bot_expiry.warn_pass(deps)
    assert deps.alerts == []
    deps.now = lambda: datetime(2026, 11, 20, 20, 52, tzinfo=UTC)
    bot_expiry.warn_pass(deps)
    bot_expiry.warn_pass(deps)
    (alert,) = deps.alerts
    assert alert.title == "ES: URGENT: futures options held into expiry"
    assert "expire at 16:00 New York: sold /ES 7000 put" in alert.detail
    assert "becomes a futures position" in alert.detail and alert.routine is False


def test_no_futures_warning_for_a_closed_group(deps, factory):
    from datetime import UTC, datetime

    from optionsjourney.services import bot_expiry

    run_id = filled_run(factory, deps, make_futures(factory))
    bot_exits.place_protections(deps, run_id)
    with factory() as session:
        run = session.get(BotRun, run_id)
        run.legs = [{**leg, "expires_at": "2026-11-20T21:00:00+00:00"} for leg in run.legs]
        for row in session.scalars(select(BotExit).where(BotExit.run_id == run_id)):
            row.state = "filled"
        session.commit()
    deps.alerts.clear()
    deps.now = lambda: datetime(2026, 11, 20, 20, 52, tzinfo=UTC)
    bot_expiry.warn_pass(deps)
    assert deps.alerts == []


def test_the_timeline_reads_a_resting_entrys_log(deps, factory):
    from optionsjourney.bots import timeline

    deps.tasty.entry = "partial"
    bot_id = make_futures(factory)
    sent = send(deps, es_ticket(quantity=2, bot_id=bot_id))
    bot_limit.check_pass(deps)
    later = OPEN_TIME + timedelta(seconds=61)
    deps.now = lambda: later
    bot_limit.check_pass(deps)
    root = run_of(factory, sent.run_id)
    (child,) = [r for r in runs(factory) if r.parent_run_id == root.id]
    assert [s.event for s in timeline.order_timeline(root, [])] == [
        "From the Trade page", "Placed (resting)", "Partly filled (seen)", "Cancel asked", "Filled",
    ]  # fmt: skip
    assert [s.event for s in timeline.order_timeline(child, [])] == [
        "Rest placed (tranche)", "Placed (resting)",
    ]  # fmt: skip


# --- Bots slice 21: a futures trade's target from the run page ----------------------------


def resting(deps, factory, **bot):
    deps.tasty.close = "work"  # the target rests
    run_id = filled_run(factory, deps, make_futures(factory, **bot))
    bot_exits.place_protections(deps, run_id)
    return run_id


def test_a_futures_target_off_confirms_its_cancel_and_on_places_it_again(deps, factory):
    run_id = resting(deps, factory)
    [(_, _, _, _, old_id)] = exits_of(factory, run_id)
    off = bot_manage.target_off(deps, run_id, 0)
    assert off.ok and "no exit at all" in off.message
    assert [e[:2] for e in exits_of(factory, run_id)] == [("target", "cancelled")]
    assert ("cancel", old_id) in [c[:2] for c in deps.tasty.calls]
    bot_exits.monitor_pass(deps)  # a target you switched off isn't "ended at the broker"
    assert not any("profit target" in a.title and "not" in a.title for a in deps.alerts)
    on = bot_manage.target_on(deps, run_id, 0)
    assert on.ok, on.message
    rows = exits_of(factory, run_id)
    assert rows[-1][:3] == ("target", "working", D("6.25")) and len(placed(deps.tasty)) == 2


def test_a_futures_target_off_whose_cancel_isnt_confirmed_changes_nothing(deps, factory):
    run_id = resting(deps, factory)
    [(_, _, _, _, old_id)] = exits_of(factory, run_id)
    deps.tasty.stuck.add(old_id)
    off = bot_manage.target_off(deps, run_id, 0)
    assert not off.ok and "still rests" in off.message
    assert exits_of(factory, run_id)[0][:2] == ("target", "working")
    with factory() as session:
        assert not bot_exits.target_off(session, run_id, 0)


# --- Bots slice 21: Modify a resting entry; follow one replaced at the broker ---------------


def resting_entry(deps, factory):
    deps.tasty.entry = "work"
    sent = send(deps, es_ticket(bot_id=make_futures(factory)))
    assert sent.ok, sent.message
    return sent.run_id, run_of(factory, sent.run_id).broker_order_id


def edited_at_the_broker(deps, old_id, price="13.00", quantity=1):
    """You changed the entry in the TastyTrade app: a new order replaces it, and the old one
    reads Cancelled naming it (replacing-order-id)."""
    from dataclasses import replace

    body = {
        "order-type": "Limit",
        "price": price,
        "legs": [{"symbol": PUT7000, "action": "Sell to Open", "quantity": quantity}],
    }
    new_id = deps.tasty.replace_order(ACCOUNT, old_id, body)
    deps.tasty.orders[old_id] = replace(deps.tasty.orders[old_id], replaced_by=new_id)
    return new_id


def test_an_entry_replaced_at_the_broker_is_followed(deps, factory):
    """analyzer_entry._follow_successor: TastyTrade's replacing order id is the run's order
    from then on; when it fills, its target goes on."""
    from optionsjourney.services import bot_limit

    run_id, old_id = resting_entry(deps, factory)
    new_id = edited_at_the_broker(deps, old_id)
    bot_limit.check_pass(deps)
    run = run_of(factory, run_id)
    assert (run.status, run.broker_order_id) == ("working", new_id)
    assert any(e.get("event") == "followed" and e.get("replaced_order_id") == old_id
               for e in run.chase_log)  # fmt: skip
    deps.tasty.close = "work"
    deps.tasty.fill(new_id, D("13.00"))
    bot_limit.check_pass(deps)
    assert run_of(factory, run_id).status == "filled"
    assert [e[:2] for e in exits_of(factory, run_id)] == [("target", "working")]


def test_a_replacement_with_other_legs_is_left_to_you(deps, factory):
    from optionsjourney.services import bot_limit

    run_id, old_id = resting_entry(deps, factory)
    edited_at_the_broker(deps, old_id, quantity=2)  # not this run's size
    bot_limit.check_pass(deps)
    run = run_of(factory, run_id)
    assert run.status == "error" and run.reason.startswith(bot_entry.NEEDS_YOU)
    assert any("replaced at your broker" in a.title for a in deps.alerts)


def test_modify_replaces_a_working_entry_in_place(deps, factory):
    """analyzer_entry.modify_entry: an unfilled resting entry is replaced at the new price
    (orders.replace, the opening check), and the new id is tracked."""
    from optionsjourney.services import bot_limit

    run_id, old_id = resting_entry(deps, factory)
    out = bot_limit.modify_entry(deps, run_id, "13.00")
    assert out.ok, out.message
    [replaced] = [c for c in deps.tasty.calls if c[0] == "replace"]
    assert replaced[1] == old_id and replaced[3]["price"] == "13.00"
    run = run_of(factory, run_id)
    assert (run.broker_order_id, run.target_credit, run.status) == (replaced[2], D("13.00"),
                                                                    "working")  # fmt: skip
    bot_limit.check_pass(deps)  # the old id's cancel isn't read as "cancelled at the broker"
    assert run_of(factory, run_id).status == "working"
    assert not bot_limit.modify_entry(deps, run_id, "0").ok
    assert not bot_limit.modify_entry(deps, run_id, "x").ok


def test_modify_records_a_fill_found_first_and_modifies_nothing(deps, factory):
    from optionsjourney.services import bot_limit

    run_id, old_id = resting_entry(deps, factory)
    deps.tasty.close = "work"
    deps.tasty.fill(old_id, D("12.50"))
    out = bot_limit.modify_entry(deps, run_id, "13.00")
    assert not out.ok and "filled" in out.message
    assert [c for c in deps.tasty.calls if c[0] == "replace"] == []
    assert run_of(factory, run_id).status == "filled"


def test_an_entry_that_filled_before_the_modify_landed_needs_you(deps, factory):
    from dataclasses import replace

    from optionsjourney.services import bot_limit

    run_id, old_id = resting_entry(deps, factory)
    real = deps.tasty.replace_order

    def filled_meanwhile(account, order_id, body):
        new_id = real(account, order_id, body)
        old = deps.tasty.orders[order_id]
        deps.tasty.orders[order_id] = replace(
            old, legs=tuple(replace(leg, remaining=0, fill_price=D("12.50")) for leg in old.legs)
        )
        return new_id

    deps.tasty.replace_order = filled_meanwhile
    out = bot_limit.modify_entry(deps, run_id, "13.00")
    assert not out.ok and "before the modify landed" in out.message
    run = run_of(factory, run_id)
    assert run.status == "error" and run.reason.startswith(bot_entry.NEEDS_YOU)


def test_an_unclear_modify_is_never_ended_as_a_plain_cancel(deps, factory):
    from dataclasses import replace

    from optionsjourney.brokers.http import RequestUnclear
    from optionsjourney.services import bot_limit

    run_id, old_id = resting_entry(deps, factory)

    def unclear(account, order_id, body):
        deps.tasty.orders[order_id] = replace(deps.tasty.orders[order_id],
                                              status=OrderStatus.CANCELLED)  # fmt: skip
        raise RequestUnclear("timed out")

    deps.tasty.replace_order = unclear
    out = bot_limit.modify_entry(deps, run_id, "13.00")
    assert not out.ok and "check your broker" in out.message.lower()
    bot_limit.check_pass(deps)
    run = run_of(factory, run_id)
    assert run.status == "error" and run.reason.startswith(bot_entry.NEEDS_YOU)


def test_a_modify_cut_off_after_its_replace_is_never_a_plain_cancel(deps, factory):
    from dataclasses import replace

    from optionsjourney.services import bot_limit

    run_id, old_id = resting_entry(deps, factory)
    bot_limit._log(deps, run_id, event=bot_limit.MODIFYING, price="13.00", order_id=old_id)
    deps.tasty.orders[old_id] = replace(deps.tasty.orders[old_id], status=OrderStatus.CANCELLED)
    bot_limit.check_pass(deps)
    run = run_of(factory, run_id)
    assert run.status == "error" and run.reason.startswith(bot_entry.NEEDS_YOU)


def test_changing_a_futures_target_cancels_then_places_it(deps, factory):
    """Question 10 (Tracker2's apply_override, cancel before place): the order read first,
    cancelled and confirmed, then the new target placed, sized to what's held. Never a
    replace in place."""
    run_id = resting(deps, factory)
    [(_, _, _, _, old_id)] = exits_of(factory, run_id)
    out = bot_manage.change_target(deps, run_id, 0, "30")
    assert out.ok, out.message
    rows = exits_of(factory, run_id)
    assert rows[0][:2] == ("target", "cancelled")
    assert rows[1][:3] == ("target", "working", D("8.75"))  # 12.50 x 70%
    assert [c for c in deps.tasty.calls if c[0] == "replace"] == []
    calls = [c[:2] for c in deps.tasty.calls if c[0] in ("cancel", "place")]
    assert calls[-2] == ("cancel", old_id) and calls[-1][0] == "place"
    bot_exits.monitor_pass(deps)
    assert exits_of(factory, run_id)[1][:2] == ("target", "working")


def test_an_unclear_futures_target_cancel_holds_the_group(deps, factory):
    from optionsjourney.services import bot_rearm

    run_id = resting(deps, factory)
    [(_, _, _, _, old_id)] = exits_of(factory, run_id)
    deps.tasty.stuck.add(old_id)  # the broker never confirms the cancel
    out = bot_manage.change_target(deps, run_id, 0, "30")
    assert not out.ok and "check your broker" in out.message.lower()
    assert [e[1] for e in exits_of(factory, run_id)] == ["failed"]
    assert len(placed(deps.tasty)) == 1  # no second target
    with factory() as session:
        rows = list(session.scalars(select(BotExit).where(BotExit.run_id == run_id)))
        assert bot_exits.held_back(rows) == "close"
        assert session.get(BotRun, run_id).reason.startswith(bot_entry.NEEDS_YOU)
    assert any("profit target change unclear" in a.title for a in deps.alerts)
    items = bot_rearm.rearm_trade(deps, run_id, dry_run=True)
    assert all(i.status != "missing" for i in items)
    assert not bot_manage.change_target(deps, run_id, 0, "40").ok


def test_a_futures_target_change_not_sent_or_part_filled_changes_nothing(deps, factory):
    from dataclasses import replace

    run_id = resting(deps, factory)
    [(_, _, _, _, old_id)] = exits_of(factory, run_id)
    old = deps.tasty.orders[old_id]
    deps.tasty.orders[old_id] = replace(old, status=OrderStatus.PARTIAL, legs=tuple(
        replace(leg, quantity=2, remaining=1) for leg in old.legs))  # fmt: skip
    out = bot_manage.change_target(deps, run_id, 0, "30")
    assert not out.ok and "part of its target has filled" in out.message.lower()
    deps.tasty.orders[old_id] = old
    deps.tasty.cancel_order = lambda account, order_id: (_ for _ in ()).throw(
        __import__("optionsjourney.brokers.http", fromlist=["BrokerError"]).BrokerError("no")
    )
    out = bot_manage.change_target(deps, run_id, 0, "30")
    assert not out.ok
    assert exits_of(factory, run_id) == [("target", "working", D("6.25"), 0, old_id)]
    assert [c for c in deps.tasty.calls if c[0] == "replace"] == []


def test_a_new_target_not_placed_after_the_cancel_is_alerted(deps, factory):
    run_id = resting(deps, factory)

    def refuse(account, body):
        from optionsjourney.brokers.http import BrokerError

        raise BrokerError("not now")

    deps.tasty.place_order = refuse
    out = bot_manage.change_target(deps, run_id, 0, "30")
    assert not out.ok and "no exit now" in out.message
    assert any("profit target not placed" in a.title for a in deps.alerts)
    with factory() as session:
        assert session.get(BotRun, run_id).reason.startswith(bot_entry.NEEDS_YOU)


def test_a_target_change_cut_off_after_its_cancel_is_held(deps, factory):
    """Recovery, in the real order of events: the change is marked, the cancel goes out,
    then the app dies. The monitor finds the old order gone: held, never a second target.
    Marked but the cancel never sent: read still working, the marker is cleared."""
    from optionsjourney.services import bot_rearm

    run_id = resting(deps, factory)
    [(_, _, _, _, old_id)] = exits_of(factory, run_id)
    with factory() as session:
        row = session.scalar(select(BotExit).where(BotExit.run_id == run_id))
        bot_exits._log(row, deps, event=bot_exits.CHANGING_TARGET, to="8.75")
        session.commit()
    bot_exits.monitor_pass(deps)  # the cancel hadn't gone out: still working
    with factory() as session:
        row = session.scalar(select(BotExit).where(BotExit.run_id == run_id))
        assert row.state == "working" and row.log[-1]["event"].startswith("not changed")
        bot_exits._log(row, deps, event=bot_exits.CHANGING_TARGET, to="8.75")
        session.commit()
    deps.tasty.cancel_order(ACCOUNT, old_id)  # then the cancel went out, and the app died
    bot_exits.monitor_pass(deps)
    assert [e[1] for e in exits_of(factory, run_id)] == ["failed"]
    items = bot_rearm.rearm_trade(deps, run_id, dry_run=True)
    assert all(i.status != "missing" for i in items)
    assert len(placed(deps.tasty)) == 1


def test_a_part_filled_entry_replaced_at_the_broker_is_left_to_you(deps, factory):
    """Review I2 (_follow_successor: "the replaced order has partial fills"): Needs you,
    and nothing is placed again for the rest."""
    from dataclasses import replace

    from optionsjourney.services import bot_limit

    deps.tasty.entry = "partial"
    sent = send(deps, es_ticket(quantity=2, bot_id=make_futures(factory)))
    run_id = sent.run_id
    old_id = run_of(factory, run_id).broker_order_id
    old = deps.tasty.orders[old_id]
    deps.tasty.orders[old_id] = replace(old, status=OrderStatus.CANCELLED, replaced_by="777")
    before = len(openings(deps.tasty))
    bot_limit.check_pass(deps)
    run = run_of(factory, run_id)
    assert run.status == "error" and run.reason.startswith(bot_entry.NEEDS_YOU)
    assert len(openings(deps.tasty)) == before  # no second order for the rest
    assert [r for r in runs(factory) if r.parent_run_id == run_id] == []


def test_a_modify_whose_old_order_cant_be_read_back_needs_you(deps, factory):
    """Review I3: never assume nothing filled before the modify landed."""
    from optionsjourney.services import bot_limit

    run_id, old_id = resting_entry(deps, factory)
    real_replace, real_get = deps.tasty.replace_order, deps.tasty.get_order
    state = {"replaced": False}

    def replace_order(account, order_id, body):
        state["replaced"] = True
        return real_replace(account, order_id, body)

    def get_order(account, order_id):
        if state["replaced"] and order_id == old_id:
            raise RuntimeError("timed out")
        return real_get(account, order_id)

    deps.tasty.replace_order, deps.tasty.get_order = replace_order, get_order
    out = bot_limit.modify_entry(deps, run_id, "13.00")
    assert not out.ok and "check your broker" in out.message.lower()
    assert run_of(factory, run_id).reason.startswith(bot_entry.NEEDS_YOU)
    assert any("entry modified: check it" in a.title for a in deps.alerts)


def _crash_in_send(deps, monkeypatch, after: bool):
    """The app dies in the change's send: before the order goes out, or after the broker
    took it but before the app recorded it."""
    real = bot_exits._send_target

    def dies(*args, **kwargs):
        if after:
            real(*args, **kwargs)
        raise SystemExit("the app died")

    monkeypatch.setattr(bot_exits, "_send_target", dies)


@pytest.mark.parametrize("after", [False, True])
def test_a_target_change_cut_off_in_its_send_is_held_and_never_placed_again(
    deps, factory, monkeypatch, after
):
    from optionsjourney.services import bot_rearm

    run_id = resting(deps, factory)
    _crash_in_send(deps, monkeypatch, after)
    with pytest.raises(SystemExit):
        bot_manage.change_target(deps, run_id, 0, "30")
    monkeypatch.undo()
    places = len(placed(deps.tasty))
    deps.alerts.clear()
    bot_exits.recover_targets(deps)  # the restart: held, Needs you, nothing placed
    states = [e[1] for e in exits_of(factory, run_id)]
    assert states == ["cancelled", "failed"] and len(placed(deps.tasty)) == places
    with factory() as session:
        rows = list(session.scalars(select(BotExit).where(BotExit.run_id == run_id)))
        assert bot_exits.held_back(rows) == "close"
        assert session.get(BotRun, run_id).reason.startswith(bot_entry.NEEDS_YOU)
    assert any("profit target change unclear" in a.title for a in deps.alerts)
    bot_exits.monitor_pass(deps)  # settle_held: released only when nothing works on its legs
    bot_rearm.rearm_trade(deps, run_id, dry_run=False)
    working = [o for o in deps.tasty.working_orders(ACCOUNT)
               if o.legs[0].symbol == PUT7000 and not o.legs[0].opening]  # fmt: skip
    assert len(working) == 1  # never two targets: the unknown one, or one Restore placed


def test_restore_never_places_a_futures_target_over_a_working_order(deps, factory):
    from dataclasses import replace

    from optionsjourney.services import bot_rearm

    run_id = resting(deps, factory)
    [(_, _, _, _, old_id)] = exits_of(factory, run_id)
    with factory() as session:  # the target row says it's dead, but an order still works
        row = session.scalar(select(BotExit).where(BotExit.run_id == run_id))
        row.state = "failed"
        session.commit()
    deps.tasty.orders[old_id] = replace(deps.tasty.orders[old_id], status=OrderStatus.WORKING)
    items = bot_rearm.rearm_trade(deps, run_id, dry_run=False)
    assert any("working at your broker" in i.detail for i in items)
    assert len(placed(deps.tasty)) == 1


def test_a_target_changed_under_freeze_says_the_trade_has_no_exit_until_it_lifts(deps, factory):
    orders.set_switch(factory, "freeze", True)
    run_id = filled_run(factory, deps, make_futures(factory))
    bot_exits.place_protections(deps, run_id)
    assert [e[1] for e in exits_of(factory, run_id)] == ["pending"]
    deps.alerts.clear()
    out = bot_manage.change_target(deps, run_id, 0, "30")
    assert out.ok, out.message
    assert any("waits for Freeze" in a.title and "no exit" in a.detail for a in deps.alerts)
