from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from optionsjourney.core.accounts import BrokerAccount
from optionsjourney.core.transactions import RawTransaction
from optionsjourney.db.models import Bot, BotExit, BotRun, OrderLog
from optionsjourney.services import bot_adopt, bot_entry, bot_store, journal, orders
from optionsjourney.services.sync import sync_broker
from optionsjourney.trading.orders import (
    BrokerLeg,
    BrokerOrder,
    Leg,
    Order,
    OrderStatus,
    Side,
    plain,
)
from tests.services.bot_fakes import (
    FAR,
    NEAR,
    FakeChain,
    FakeHub,
    FakeSchwab,
    FakeTasty,
    make,
    make_calendar,
    make_schwab,
    make_shape,
    sym,
)

D = Decimal
OPENED = datetime(2026, 10, 13, 13, 40, tzinfo=UTC)  # 09:40 New York
NOW = datetime(2026, 10, 13, 14, 30, tzinfo=UTC)  # 10:30 New York
ACCOUNT = "5WX01234"
CALL, CALL_WING = sym(5900, "call"), sym(5975, "call")
PUT, PUT_WING = sym(5700, "put"), sym(5625, "put")
KEY = f"tastytrade:{ACCOUNT}:500"
LATER = OPENED + timedelta(minutes=5)


def _tx(tid, action, price, symbol, qty=1, order_id=500, at=OPENED):
    return {
        "id": tid,
        "symbol": symbol,
        "instrument-type": "Equity Option",
        "underlying-symbol": "SPXW",
        "transaction-type": "Trade",
        "action": action,
        "quantity": str(qty),
        "price": price,
        "value": str(float(price) * 100 * qty),
        "value-effect": "Credit" if action.startswith("Sell") else "Debit",
        "executed-at": at.isoformat(),
        "order-id": order_id,
    }


def condor(qty=1, order_id=500, at=OPENED, first=1):
    return [
        _tx(first, "Sell to Open", "2.50", CALL, qty, order_id, at),
        _tx(first + 1, "Buy to Open", "0.40", CALL_WING, qty, order_id, at),
        _tx(first + 2, "Sell to Open", "2.00", PUT, qty, order_id, at),
        _tx(first + 3, "Buy to Open", "0.30", PUT_WING, qty, order_id, at),
    ]


class History:
    name, broker = "TastyTrade", "tastytrade"

    def __init__(self, rows):
        self.rows = rows

    def accounts(self):
        return [BrokerAccount("tastytrade", ACCOUNT, "Alpha")]

    def transactions(self, number, start, end):
        return [
            RawTransaction(str(r["id"]), datetime.fromisoformat(r["executed-at"]), "Trade", r)
            for r in self.rows
        ]


def synced(factory, rows):
    sync_broker(History(rows), factory, now=lambda: NOW)


@pytest.fixture
def deps(factory):
    tasty = FakeTasty()
    d = bot_entry.BotDeps(
        factory, lambda: [tasty], FakeHub(), FakeChain(), notify=lambda e: None,
        sleep=lambda s: None, now=lambda: NOW, background=lambda fn, *args: fn(*args),
    )  # fmt: skip
    d.tasty = tasty
    d.alerts = []
    d.notify = d.alerts.append
    synced(factory, condor())
    hold(d)
    quote(d)
    return d


def hold(deps, qty=1, client=None):
    client = client or deps.tasty
    for symbol, sign in ((CALL, -1), (CALL_WING, 1), (PUT, -1), (PUT_WING, 1)):
        client.held[plain(symbol)] = sign * qty


def quote(deps, call_ask="2.10", put_ask="1.60", at=NOW):
    deps.hub.set(CALL, D(call_ask) - D("0.10"), D(call_ask), at)
    deps.hub.set(PUT, D(put_ask) - D("0.10"), D(put_ask), at)
    deps.hub.set(CALL_WING, D("0.25"), D("0.35"), at)
    deps.hub.set(PUT_WING, D("0.20"), D("0.30"), at)


def runs(factory):
    with factory() as session:
        return list(session.scalars(select(BotRun).order_by(BotRun.id)))


def exits(factory, kind=None):
    with factory() as session:
        query = select(BotExit).order_by(BotExit.id)
        if kind:
            query = query.where(BotExit.kind == kind)
        return list(session.scalars(query))


def sends(deps, client=None):
    client = client or deps.tasty
    return [c for c in client.calls if c[0] in ("place", "replace", "cancel")]


def checked(deps, bot_id, key=KEY):
    return bot_adopt.check(deps, key, bot_id)


def handed(deps, bot_id, key=KEY):
    found = checked(deps, bot_id, key)
    assert found.ok, found.message
    return bot_adopt.adopt(deps, key, bot_id, found.fingerprint)


# --- The check --------------------------------------------------------------------------


def test_the_check_records_and_sends_nothing(deps, factory):
    bot_id = make(factory, live=True)
    found = checked(deps, bot_id)
    assert found.ok, found.message
    assert [s.stop.trigger for s in found.sides] == [D("4.80"), D("3.90")]
    assert found.asks == {0: D("2.10"), 1: D("1.60")}  # by group: the short's ask
    assert found.matched.contracts == 1 and found.matched.credit == D("3.80")
    assert runs(factory) == [] and exits(factory) == []
    assert sends(deps) == []


def test_working_orders_on_the_trade_refuse_and_are_named(deps, factory):
    bot_id = make(factory, live=True)
    deps.tasty.orders["900"] = BrokerOrder(
        "900", OrderStatus.WORKING, (BrokerLeg(CALL, Side.BUY, False, 1, 1),)
    )
    found = checked(deps, bot_id)
    assert not found.ok and "900" in found.message and "Cancel them" in found.message
    assert bot_adopt.adopt(deps, KEY, bot_id, "anything").ok is False
    assert runs(factory) == [] and sends(deps) == []


def test_a_working_order_on_other_options_doesnt_matter(deps, factory):
    bot_id = make(factory, live=True)
    other = sym(6100, "call")
    deps.tasty.orders["901"] = BrokerOrder(
        "901", OrderStatus.WORKING, (BrokerLeg(other, Side.BUY, False, 1, 1),)
    )
    assert checked(deps, bot_id).ok


def test_a_bots_own_trade_is_refused(deps, factory):
    bot_id = make(factory, live=True)
    other = make(factory, live=True)
    with factory() as session:
        now = NOW
        session.add(
            BotRun(bot_id=other, key="x", trading_date=now.date(), entry_time="09:40",
                   status="filled", live=True, account_number=ACCOUNT, legs=[], filled_legs=[],
                   broker_order_id="500", created_at=now, updated_at=now)
        )  # fmt: skip
        session.commit()
    found = checked(deps, bot_id)
    assert not found.ok and "already" in found.message
    assert len(runs(factory)) == 1


@pytest.mark.parametrize(
    "rows, words",
    [
        (condor()[:3], "doesn't match a 4-leg bot"),
        (condor(at=OPENED - timedelta(days=1)), "opened today"),
        ([*condor(), _tx(9, "Buy to Close", "1.00", CALL, order_id=501, at=LATER),
          _tx(10, "Sell to Close", "0.10", CALL_WING, order_id=501, at=LATER)], "isn't open"),
    ],
)  # fmt: skip
def test_trades_no_bot_can_take(factory, rows, words):
    deps = bot_entry.BotDeps(factory, lambda: [FakeTasty()], FakeHub(), FakeChain(),
                             now=lambda: NOW)  # fmt: skip
    synced(factory, rows)
    bot_id = make(factory, live=True)
    found = checked(deps, bot_id)
    assert not found.ok and words in found.message, found.message


def test_a_preview_mode_bot_is_refused(deps, factory):
    found = checked(deps, make(factory, live=False))
    assert not found.ok and "Paper bot" in found.message


def test_a_bot_on_another_account_is_refused(deps, factory):
    bot_id = make(factory, live=True)
    with factory() as session:
        session.get(Bot, bot_id).account_number = "5WX09999"
        session.commit()
    found = checked(deps, bot_id)
    assert not found.ok and "another account" in found.message


def test_a_trade_bigger_than_the_bots_limit_is_refused(factory):
    deps = bot_entry.BotDeps(factory, lambda: [FakeTasty()], FakeHub(), FakeChain(),
                             now=lambda: NOW)  # fmt: skip
    synced(factory, condor(qty=2))
    found = checked(deps, make(factory, live=True))
    assert not found.ok and "per trade" in found.message


def test_blank_limits_take_over_a_bigger_trade(factory):
    """Decision 0058: a trade you hand a bot with blank limits isn't held to its size."""
    deps = bot_entry.BotDeps(factory, lambda: [FakeTasty()], FakeHub(), FakeChain(),
                             now=lambda: NOW)  # fmt: skip
    synced(factory, condor(qty=2))
    found = checked(deps, make(factory, live=True, max_per_trade=None, max_per_day=None))
    assert "per trade" not in found.message


@pytest.mark.parametrize(
    "switch, words", [("freeze", "frozen"), ("no_new_trades", "No new trades")]
)
def test_either_switch_refuses(deps, factory, switch, words):
    bot_id = make(factory, live=True)
    orders.set_switch(factory, switch, True, NOW)
    found = checked(deps, bot_id)
    assert not found.ok and words in found.message


def test_outside_the_session_is_refused(deps, factory):
    bot_id = make(factory, live=True)
    deps.now = lambda: datetime(2026, 10, 13, 21, 0, tzinfo=UTC)  # 17:00 New York
    found = checked(deps, bot_id)
    assert not found.ok and "market is open" in found.message


def test_unreadable_positions_refuse(deps, factory):
    bot_id = make(factory, live=True)
    deps.tasty.errors["positions"] = RuntimeError("down")
    found = checked(deps, bot_id)
    assert not found.ok and "positions" in found.message


def test_unreadable_working_orders_refuse(deps, factory):
    bot_id = make(factory, live=True)

    def broken(account):
        raise RuntimeError("down")

    deps.tasty.working_orders = broken
    found = checked(deps, bot_id)
    assert not found.ok and "working orders" in found.message


def test_legs_the_broker_doesnt_show_refuse(deps, factory):
    bot_id = make(factory, live=True)
    deps.tasty.held[plain(PUT)] = 0
    found = checked(deps, bot_id)
    assert not found.ok and "doesn't show" in found.message


def test_an_unclear_order_on_a_leg_refuses(deps, factory):
    bot_id = make(factory, live=True)
    order = Order((Leg(CALL, Side.BUY),), 1, False, price=D("-1.00"))
    with factory() as session:
        session.add(
            OrderLog(created_at=NOW, broker="tastytrade", account_number=ACCOUNT, origin="you",
                     action="send", opening=False, quantity=1, price=D("1.00"),
                     order_type="limit", order={"order": order.to_dict()},
                     result=f"{orders.UNCLEAR} timed out", ok=False)
        )  # fmt: skip
        session.commit()
    found = checked(deps, bot_id)
    assert not found.ok and "unclear" in found.message


def test_an_entry_being_settled_refuses(deps, factory):
    bot_id = make(factory, live=True)
    with factory() as session:
        session.add(
            BotRun(bot_id=bot_id, key="k", trading_date=NOW.date(), entry_time="10:20",
                   status="running", live=True, account_number=ACCOUNT, legs=[],
                   filled_legs=[], created_at=NOW, updated_at=NOW)
        )  # fmt: skip
        session.commit()
    found = checked(deps, bot_id)
    assert not found.ok and "being settled" in found.message


def test_candidates_offer_the_matching_bots_and_say_why_not(deps, factory):
    good = make(factory, live=True)
    preview = make(factory, live=False)
    trade = bot_adopt.find(deps, KEY)
    offered = {bot.id: problem for bot, problem in bot_adopt.candidates(deps, trade)}
    assert offered[good] is None and "Paper bot" in offered[preview]


def test_a_split_half_hands_over_the_whole_condor(deps, factory):
    journal.set_split(factory, KEY, True)
    trade = bot_adopt.find(deps, f"{KEY}#put")
    assert trade.key == KEY and len(trade.legs) == 4 and trade.problem is None


# --- The live-ask check and warnings ----------------------------------------------------


def test_a_stop_at_or_below_the_ask_refuses_and_records_nothing(deps, factory):
    bot_id = make(factory, live=True)
    quote(deps, call_ask="4.80")  # the call stop is 4.80
    found = checked(deps, bot_id)
    assert not found.ok and "at or below the short's ask 4.80" in found.message
    assert bot_adopt.adopt(deps, KEY, bot_id, "x").ok is False
    assert runs(factory) == [] and sends(deps) == []


def test_no_fresh_ask_refuses(deps, factory):
    bot_id = make(factory, live=True)
    quote(deps, at=NOW - timedelta(minutes=5))
    found = checked(deps, bot_id)
    assert not found.ok and "no fresh quote" in found.message
    assert runs(factory) == [] and sends(deps) == []


def test_a_stop_the_style_cant_use_refuses(deps, factory):
    bot_id = make(factory, live=True)
    bot = bot_store.load(factory, bot_id)
    groups = tuple(replace(g, stop_basis="entry_premium", stop_pct=D("40")) for g in bot.groups)
    bot_store.save(factory, replace(bot, groups=groups), NOW)  # 40% of 3.80 is below the fills
    found = checked(deps, bot_id)
    assert not found.ok and "can't protect" in found.message


def test_a_reached_target_and_a_past_timed_close_warn(deps, factory):
    bot_id = make(factory, live=True)
    bot = bot_store.load(factory, bot_id)
    groups = (replace(bot.groups[0], target_pct=D("50")), bot.groups[1])
    bot_store.save(factory, replace(bot, groups=groups, close_time="10:00"), NOW)
    quote(deps)  # the call side costs 2.05 - 0.30 = 1.75 to close: not reached
    deps.hub.set(CALL, D("0.90"), D("1.00"), NOW)  # 0.95 - 0.30 = 0.65 <= 1.05: reached
    found = checked(deps, bot_id)
    assert found.ok, found.message
    text = " ".join(found.warnings)
    assert "already reached" in text and "timed close (10:00) has passed" in text


# --- Confirm ----------------------------------------------------------------------------


def test_a_condor_is_handed_over_with_the_stops_a_bot_fill_would_get(deps, factory):
    bot_id = make(factory, live=True)
    bot = bot_store.load(factory, bot_id)
    bot_store.save(factory, replace(bot, groups=tuple(
        replace(g, target_pct=D("50")) for g in bot.groups)), NOW)  # fmt: skip
    outcome = handed(deps, bot_id)
    assert outcome.ok, outcome.message
    (run,) = runs(factory)
    assert run.status == "filled" and run.live and run.key == f"adopt-{bot_id}-500"
    assert run.broker_order_id == "500" and run.contracts == 1 and run.fill_credit == D("3.80")
    assert run.entry_time == "09:40" and run.entry_spot is None
    stops = exits(factory, "stop")
    assert [(s.state, s.trigger) for s in stops] == [("working", D("4.80")), ("working", D("3.90"))]
    assert [t.state for t in exits(factory, "target")] == ["armed", "armed"]
    placed = [c for c in sends(deps)]
    assert len(placed) == 2 and all(c[2]["order-type"] == "Stop" for c in placed)
    assert "stop at 4.80" in outcome.message
    view = journal.load_journal(factory)
    linked = [e for e in view.entries if e.bot is not None]
    assert linked and all(e.bot.run_id == run.id for e in linked)
    assert bot_adopt.find(deps, KEY).problem.startswith("It's already")
    assert run.id not in bot_entry.ACTIVE


def test_a_second_confirm_records_nothing_more(deps, factory):
    bot_id = make(factory, live=True)
    found = checked(deps, bot_id)
    assert bot_adopt.adopt(deps, KEY, bot_id, found.fingerprint).ok
    again = bot_adopt.adopt(deps, KEY, bot_id, found.fingerprint)
    assert not again.ok and "already" in again.message
    assert len(runs(factory)) == 1 and len(sends(deps)) == 2


def test_a_record_race_records_one_run(deps, factory):
    bot_id = make(factory, live=True)
    found = checked(deps, bot_id)
    assert bot_adopt._record(deps, found) is not None
    assert bot_adopt._record(deps, found) is None
    bot_entry.ACTIVE.clear()
    assert len(runs(factory)) == 1


def test_a_changed_check_refuses(deps, factory):
    bot_id = make(factory, live=True)
    found = checked(deps, bot_id)
    bot = bot_store.load(factory, bot_id)
    bot_store.save(factory, replace(bot, groups=tuple(
        replace(g, stop_pct=D("90")) for g in bot.groups)), NOW)  # fmt: skip
    outcome = bot_adopt.adopt(deps, KEY, bot_id, found.fingerprint)
    assert not outcome.ok and outcome.message == bot_adopt.CHANGED
    assert runs(factory) == [] and sends(deps) == []


def test_a_schwab_bots_stops_rest_at_schwab(factory, monkeypatch):
    """Decision 0060: a trade handed to a Schwab bot gets TastyTrade's exits: a single-leg
    stop on each short resting at Schwab."""
    schwab = FakeSchwab()
    deps = bot_entry.BotDeps(
        factory, lambda: [schwab], FakeHub(), FakeChain(), notify=lambda e: None,
        sleep=lambda s: None, now=lambda: NOW,
    )  # fmt: skip
    synced(factory, condor())
    hold(deps, client=schwab)
    quote(deps)
    real = bot_adopt.find
    monkeypatch.setattr(bot_adopt, "find", lambda d, key: replace(real(d, key), broker="schwab"))
    bot_id = make_schwab(factory, live=True)
    outcome = handed(deps, bot_id)
    assert outcome.ok, outcome.message
    assert {(s.state, s.app_held) for s in exits(factory, "stop")} == {("working", False)}
    stops = [c[-1] for c in sends(deps, schwab) if c[-1]["orderType"] == "STOP"]
    assert len(stops) == 2 and all(len(b["orderLegCollection"]) == 1 for b in stops)


def test_exits_that_break_leave_the_run_and_alert(deps, factory, monkeypatch):
    bot_id = make(factory, live=True)

    def broken(d, run_id, **kw):
        raise RuntimeError("boom")

    monkeypatch.setattr(bot_entry, "place_protections", broken)
    outcome = handed(deps, bot_id)
    (run,) = runs(factory)
    assert run.reason.startswith(bot_entry.NEEDS_YOU)
    assert any("exits not placed" in a.title for a in deps.alerts)
    assert outcome.ok and outcome.warning
    assert "NO EXITS PLACED" in outcome.message and "none" not in outcome.message
    assert run.id not in bot_entry.ACTIVE


def test_a_clean_hand_over_is_no_warning(deps, factory):
    outcome = handed(deps, make(factory, live=True))
    assert outcome.ok and not outcome.warning


def test_a_bot_at_its_max_open_trades_is_refused(deps, factory):
    bot_id = make(factory, live=True)
    with factory() as session:
        session.get(Bot, bot_id).max_open_trades = 1
        session.add(
            BotRun(bot_id=bot_id, key="held", trading_date=NOW.date(), entry_time="09:31",
                   status="filled", live=True, account_number=ACCOUNT,
                   legs=[{"symbol": sym(6000, "call"), "side": "sell", "group": 0, "ratio": 1}],
                   filled_legs=[{"symbol": sym(6000, "call"), "side": "sell",
                                 "fill_price": "1", "contracts": 1}],
                   broker_order_id="77", contracts=1, created_at=NOW, updated_at=NOW)
        )  # fmt: skip
        session.commit()
    found = checked(deps, bot_id)
    assert not found.ok and "Max open trades" in found.message, found.message
    assert len(runs(factory)) == 1 and sends(deps) == []


def test_a_trade_through_its_stop_by_the_send_sends_nothing_and_needs_you(deps, factory):
    bot_id = make(factory, live=True)
    found = checked(deps, bot_id)
    assert found.ok
    quote(deps, call_ask="4.90")  # moved through the 4.80 call stop after the check
    real = bot_adopt.check
    bot_adopt.check = lambda d, k, b: found  # the check saw the old ask
    try:
        outcome = bot_adopt.adopt(deps, KEY, bot_id, found.fingerprint)
    finally:
        bot_adopt.check = real
    (run,) = runs(factory)
    stops = {s.group_position: s for s in exits(factory, "stop")}
    assert stops[0].state == "failed" and stops[1].state == "working"
    placed = [c for c in sends(deps) if c[0] == "place"]
    assert len(placed) == 1  # only the put stop went out
    assert run.reason.startswith(bot_entry.NEEDS_YOU)
    assert any("through its stop" in a.detail for a in deps.alerts)
    assert outcome.warning and "NO STOP" in outcome.message


def test_an_adopted_run_is_an_open_trade_but_not_an_entry_today(deps, factory):
    bot_id = make(factory, live=True)
    assert handed(deps, bot_id).ok
    bot = bot_store.load(factory, bot_id)
    assert bot_entry._entries_today(deps, bot_id, NOW.date(), run_id=-1) == 0
    assert bot_entry._open_trades(deps, bot) >= 1


def test_a_side_with_no_stop_and_the_spx_move_rule_warn(deps, factory):
    bot_id = make(factory, live=True)
    bot = bot_store.load(factory, bot_id)
    groups = (replace(bot.groups[0], stop_pct=None), bot.groups[1])
    changed = replace(bot, groups=groups, use_underlying_exit=True, underlying_up_pct=D("1"))
    assert bot_store.save(factory, changed, NOW)[1] == {}
    found = checked(deps, bot_id)
    assert found.ok, found.message
    text = " ".join(found.warnings)
    assert "gets no stop" in text and "SPX move watch rule can't run" in text
    assert found.sides[0].stop is None and CALL not in found.asks


# --- Slice 15: any shape a bot can hold ---------------------------------------------------

SPREAD, SPREAD_WING = sym(5650, "put"), sym(5600, "put")
SPREAD_KEY = f"tastytrade:{ACCOUNT}:600"
NEAR_PUT, FAR_PUT = sym(5700, "put", NEAR), sym(5700, "put", FAR)
CALENDAR_KEY = f"tastytrade:{ACCOUNT}:700"


def spread(deps, factory):
    synced(factory, [*condor(), _tx(11, "Sell to Open", "2.00", SPREAD, order_id=600),
                     _tx(12, "Buy to Open", "0.80", SPREAD_WING, order_id=600)])  # fmt: skip
    deps.tasty.held |= {plain(SPREAD): -1, plain(SPREAD_WING): 1}
    deps.hub.set(SPREAD, D("1.90"), D("2.00"), NOW)
    deps.hub.set(SPREAD_WING, D("0.70"), D("0.80"), NOW)


def calendar(deps, factory, near_mid="4.00", far_mid="6.00", near_fill="4.00", far_fill="6.00"):
    synced(factory, [*condor(), _tx(13, "Sell to Open", near_fill, NEAR_PUT, order_id=700),
                     _tx(14, "Buy to Open", far_fill, FAR_PUT, order_id=700)])  # fmt: skip
    deps.tasty.held |= {plain(NEAR_PUT): -1, plain(FAR_PUT): 1}
    deps.hub.set(NEAR_PUT, D(near_mid) - D("0.05"), D(near_mid) + D("0.05"), NOW)
    deps.hub.set(FAR_PUT, D(far_mid) - D("0.05"), D(far_mid) + D("0.05"), NOW)


def test_a_put_credit_spread_is_handed_to_a_spread_bot(deps, factory):
    spread(deps, factory)
    bot_id = make_shape(factory, "put_credit_spread", live=True, stop=D("95"), target=D("50"))
    outcome = handed(deps, bot_id, SPREAD_KEY)
    assert outcome.ok, outcome.message
    (run,) = runs(factory)
    assert run.fill_credit == D("1.20") and run.contracts == 1
    (stop,) = exits(factory, "stop")
    assert (stop.state, stop.trigger, stop.anchor_symbol) == ("working", D("3.90"), SPREAD)
    assert [t.state for t in exits(factory, "target")] == ["armed"]
    assert [c[2]["order-type"] for c in sends(deps)] == ["Stop"]


def test_a_calendar_is_handed_over_and_watched(deps, factory):
    calendar(deps, factory)
    bot_id = make_calendar(factory, live=True, target="25")
    found = checked(deps, bot_id, CALENDAR_KEY)
    assert found.ok, found.message
    (side,) = found.sides
    assert side.watched and side.stop.trigger == D("-1.00") and found.asks[0] == D("0.00")
    outcome = bot_adopt.adopt(deps, CALENDAR_KEY, bot_id, found.fingerprint)
    assert outcome.ok, outcome.message
    (run,) = runs(factory)
    assert run.fill_credit == D("-2.00")
    assert [(leg["expiration"], leg["dte"]) for leg in run.legs] == [(NEAR, 7), (FAR, 14)]
    (stop,) = exits(factory, "stop")
    assert (stop.state, stop.watched, stop.trigger) == ("watching", True, D("-1.00"))
    # decision 0048: a debit trade in a group that chose Stop rests its target at TastyTrade
    # and the app watches its stop on the group's P&L (the fall-back)
    assert [t.state for t in exits(factory, "target")] == ["working"]
    (sent,) = sends(deps)  # the resting target, nothing else
    assert (sent[2]["time-in-force"], sent[2]["order-type"]) == ("GTC", "Limit")


def test_a_watched_group_through_its_level_is_refused(deps, factory):
    # P&L = -2.00 - (4.00 - 4.50) = -1.50, through the stop at -1.00
    calendar(deps, factory, far_mid="4.50")
    bot_id = make_calendar(factory, live=True)
    found = checked(deps, bot_id, CALENDAR_KEY)
    assert not found.ok and "fire at once" in found.message, found.message
    assert runs(factory) == [] and sends(deps) == []


def test_a_calendar_needs_a_bot_that_holds_overnight(deps, factory):
    calendar(deps, factory)
    bot_id = make_shape(factory, "put_credit_spread", live=True, stop=D("95"))
    found = checked(deps, bot_id, CALENDAR_KEY)
    assert not found.ok and "same-day bot" in found.message


def test_adopting_a_debit_trade_into_a_bot_that_used_to_be_credit_only(deps, factory):
    """Decision 0048: a credit diagonal's bot takes a calendar bought for a debit; its stop
    on the short's fill can't be read on a debit, so it has none, and the check says so."""
    calendar(deps, factory)  # a debit of 2.00
    bot_id = make_calendar(factory, live=True, credit=True, target="25")
    found = checked(deps, bot_id, CALENDAR_KEY)
    assert found.ok, found.message
    (side,) = found.sides
    assert side.stop is None and side.rests == "target" and side.target == D("-2.50")
    assert any("No stop set" in note for note in side.notes)
    # final review I2: it's this fill that drops the stop, never "the bot sets none"
    [said] = [w for w in found.warnings if "gets no stop" in w]
    assert "the bot sets none" not in said and "No stop set" in said


def test_adopting_a_credit_trade_into_a_bot_that_used_to_be_debit_only(deps, factory):
    # Filled and quoted as a credit of 2.00 (sold the near at 6.00, bought the far at 4.00)
    calendar(deps, factory, near_mid="6.00", far_mid="4.00", near_fill="6.00", far_fill="4.00")
    bot_id = make_calendar(factory, live=True, stop=None, target="25")
    found = checked(deps, bot_id, CALENDAR_KEY)
    assert found.ok, found.message
    (side,) = found.sides
    assert side.rests == "target" and side.target == D("1.50")  # 25% of the credit kept
    trade = bot_adopt.find(deps, CALENDAR_KEY)
    assert dict((b.id, why) for b, why in bot_adopt.candidates(deps, trade))[bot_id] is None


def test_a_watched_group_through_its_level_by_the_write_gets_no_stop(deps, factory, monkeypatch):
    # The P&L crosses the level after the check: the stop row is written failed, nothing
    # is watched, the run needs you and it's alerted (slice 12's resting-stop re-check).
    calendar(deps, factory)
    bot_id = make_calendar(factory, live=True)
    found = checked(deps, bot_id, CALENDAR_KEY)
    assert found.ok, found.message
    deps.hub.set(FAR_PUT, D("4.45"), D("4.55"), NOW)  # P&L -1.50, through -1.00
    monkeypatch.setattr(bot_adopt, "_live_check", lambda d, sides: ({}, None))
    outcome = bot_adopt.adopt(deps, CALENDAR_KEY, bot_id, found.fingerprint)
    assert outcome.ok and outcome.warning
    (stop,) = exits(factory, "stop")
    assert stop.state == "failed"
    (run,) = runs(factory)
    assert run.reason.startswith(bot_entry.NEEDS_YOU)
    assert any("through its stop" in a.detail for a in deps.alerts)


def test_an_spy_trade_isnt_taken_when_its_increments_cant_be_loaded(deps, factory):
    """Bots slice 16 (Tracker2's adopt: "Couldn't load TastyTrade's option increments")."""
    from optionsjourney.brokers.http import BrokerError

    spy = [
        dict(row, symbol=row["symbol"].replace("SPXW  ", "SPY   "), **{"underlying-symbol": "SPY"})
        for row in condor(order_id=501, first=11)
    ]
    synced(factory, condor() + spy)
    for symbol, sign in ((CALL, -1), (CALL_WING, 1), (PUT, -1), (PUT_WING, 1)):
        deps.tasty.held[plain(symbol.replace("SPXW  ", "SPY   "))] = sign
    bot_id = make(factory, live=True, underlying="SPY")

    def broken(symbol):
        raise BrokerError("TastyTrade is down")

    deps.tasty.equity_tick_sizes = broken
    found = checked(deps, bot_id, key=f"tastytrade:{ACCOUNT}:501")
    assert not found.ok
    assert found.message == "Couldn't load TastyTrade's option increments for SPY. Try again."
    assert runs(factory) == [] and sends(deps) == []


def test_an_spx_hand_over_reads_no_increments(deps, factory):
    asked = []
    deps.tasty.equity_tick_sizes = lambda symbol: asked.append(symbol) or []
    assert checked(deps, make(factory, live=True)).ok
    assert asked == []


# --- Bots slice 17: a futures trade handed over ------------------------------------------

from tests.services.bot_fakes import (  # noqa: E402
    es_nested,
    fut_sym,
    make_futures,
    on_futures,
)

ES_PUT = fut_sym(7000, "put")
EVENING_OPEN = datetime(2026, 10, 13, 22, 30, tzinfo=UTC)  # 18:30 New York: CME open
EVENING = datetime(2026, 10, 13, 23, 0, tzinfo=UTC)
ES_KEY = f"tastytrade:{ACCOUNT}:600"


def es_trade(contract="./ESZ6"):
    return [{
        "id": 61, "symbol": ES_PUT, "instrument-type": "Future Option",
        "underlying-symbol": contract, "transaction-type": "Trade", "action": "Sell to Open",
        "quantity": "1", "price": "12.50", "value": "625.0", "value-effect": "Credit",
        "description": "Sold 1 /ESZ6 EW3X6 11/20/26 Put 7000.00 @ 12.50",
        "executed-at": EVENING_OPEN.isoformat(), "order-id": 600,
    }]  # fmt: skip


@pytest.fixture
def fdeps(factory):
    tasty = FakeTasty()
    d = bot_entry.BotDeps(
        factory, lambda: [tasty], FakeHub(), FakeChain(), notify=lambda e: None,
        sleep=lambda s: None, now=lambda: EVENING, background=lambda fn, *args: fn(*args),
    )  # fmt: skip
    d.tasty = tasty
    d.alerts = []
    d.notify = d.alerts.append
    on_futures(d)
    sync_broker(History(es_trade()), factory, now=lambda: EVENING)
    tasty.held[plain(ES_PUT)] = -1
    tasty.close = "work"
    d.hub.set(ES_PUT, D("12.00"), D("12.50"), EVENING)
    return d


def test_a_futures_trade_is_handed_over_after_hours_with_its_target_only(fdeps, factory):
    """adopt.preflight's futures branch: the legs looked up in TastyTrade's series by
    their own symbols (resolve_held), no session check, only the profit target placed."""
    bot_id = make_futures(factory)
    found = bot_adopt.check(fdeps, ES_KEY, bot_id)
    assert found.ok, found.message
    assert found.trade.underlying == "/ES"
    (side,) = found.sides
    assert side.stop is None and side.target == D("6.25")
    assert any("Futures: profit target only, no stop" in w for w in found.warnings)
    done = bot_adopt.adopt(fdeps, ES_KEY, bot_id, found.fingerprint)
    assert done.ok, done.message
    assert "target rests at 6.25" in done.message
    with factory() as session:
        run = session.get(BotRun, done.run_id)
        assert (run.pt_only, run.multiplier, run.contract) == (True, D(50), "/ESZ6")
    assert [(e.kind, e.state) for e in exits(factory)] == [("target", "working")]
    (place,) = sends(fdeps)
    assert place[2]["order-type"] == "Limit" and place[2]["time-in-force"] == "GTC"


def test_a_futures_trade_on_another_contract_than_its_series_is_refused(factory):
    tasty = FakeTasty()
    d = bot_entry.BotDeps(
        factory, lambda: [tasty], FakeHub(), FakeChain(), notify=lambda e: None,
        sleep=lambda s: None, now=lambda: EVENING,
    )  # fmt: skip
    d.tasty = tasty
    on_futures(d)
    sync_broker(History(es_trade(contract="./ESH7")), factory, now=lambda: EVENING)
    found = bot_adopt.check(d, ES_KEY, make_futures(factory))
    assert not found.ok
    assert found.message == "TastyTrade lists these options on /ESZ6, not the journal's /ESH7."
    assert sends(d) == [] and runs(factory) == []


def test_an_spx_bot_cant_take_a_futures_trade(fdeps, factory):
    found = bot_adopt.check(fdeps, ES_KEY, make(factory, live=True))
    assert not found.ok and "It trades SPX, not /ES." in found.message
    assert es_nested()  # the chain was there: the bot is what refused


def test_a_fall_back_hand_over_through_its_level_never_says_the_stop_is_watched(
    deps, factory, monkeypatch
):
    """Final review C2b: a debit trade in a group that chose Stop (the fall-back), already
    through its P&L level by the write, gets no stop: the fall-back alert says so."""
    calendar(deps, factory)
    bot_id = make_calendar(factory, live=True, target="25")
    bot = bot_store.load(factory, bot_id)
    chose_stop = replace(bot, groups=(replace(bot.groups[0], broker_exit="stop"),))
    assert bot_store.save(factory, chose_stop, NOW)[1] == {}
    found = checked(deps, bot_id, CALENDAR_KEY)
    assert found.ok, found.message
    deps.hub.set(FAR_PUT, D("4.45"), D("4.55"), NOW)  # P&L -1.50, through -1.00
    monkeypatch.setattr(bot_adopt, "_live_check", lambda d, sides: ({}, None))
    assert bot_adopt.adopt(deps, CALENDAR_KEY, bot_id, found.fingerprint).ok
    (stop,) = exits(factory, "stop")
    assert stop.state == "failed"
    [fell] = [a for a in deps.alerts if "opened for a debit" in a.title]
    assert "watches the stop" not in fell.detail and "stop is watched" not in fell.title
    assert "It has no stop." in fell.detail
