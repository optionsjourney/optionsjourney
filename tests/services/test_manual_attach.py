"""Manual bots (#158): attaching a manual bot's rules to a trade as a hidden per-trade bot."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from optionsjourney.bots.manual import ManualRules
from optionsjourney.core.accounts import BrokerAccount
from optionsjourney.core.transactions import RawTransaction
from optionsjourney.db.models import Bot, BotExit, BotRun, OrderLog
from optionsjourney.services import (
    bot_adopt,
    bot_store,
    bot_ticket,
    manual_attach,
    manual_bots,
    orders,
)
from optionsjourney.services.bot_entry import BotDeps
from optionsjourney.services.sync import sync_broker
from optionsjourney.trading.orders import plain
from tests.services.bot_fakes import FakeChain, FakeHub, FakeTasty, make, on_futures, sym

D = Decimal
OPENED = datetime(2026, 10, 9, 13, 40, tzinfo=UTC)  # days before: any open trade, not today's
NOW = datetime(2026, 10, 13, 14, 30, tzinfo=UTC)  # 10:30 New York
ACCOUNT = "5WX01234"
EXP = "2026-10-30"
PUT, PUT_WING = sym(5700, "put", EXP), sym(5625, "put", EXP)
CALL, CALL_WING = sym(5900, "call", EXP), sym(5975, "call", EXP)
KEY = f"tastytrade:{ACCOUNT}:500"


def _tx(tid, action, price, symbol, qty=1, order_id=500, at=OPENED, root="SPXW"):
    return {
        "id": tid, "symbol": symbol, "instrument-type": "Equity Option",
        "underlying-symbol": root, "transaction-type": "Trade", "action": action,
        "quantity": str(qty), "price": price, "value": str(float(price) * 100 * qty),
        "value-effect": "Credit" if action.startswith("Sell") else "Debit",
        "executed-at": at.isoformat(), "order-id": order_id,
    }  # fmt: skip


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


def _deps(factory, rows, now=NOW):
    tasty = FakeTasty()
    d = BotDeps(
        factory, lambda: [tasty], FakeHub(), FakeChain(), notify=lambda e: None,
        sleep=lambda s: None, now=lambda: now, background=lambda fn, *args: fn(*args),
    )  # fmt: skip
    d.tasty, d.alerts = tasty, []
    d.notify = d.alerts.append
    sync_broker(History(rows), factory, now=lambda: now)
    return d


def short_put(qty=1):
    return [_tx(1, "Sell to Open", "2.00", PUT, qty)]


def condor(qty=4):
    return [
        _tx(1, "Sell to Open", "2.50", CALL, qty), _tx(2, "Buy to Open", "0.40", CALL_WING, qty),
        _tx(3, "Sell to Open", "2.00", PUT, qty), _tx(4, "Buy to Open", "0.30", PUT_WING, qty),
    ]  # fmt: skip


PT50 = ManualRules("Half off", target_value=D("50"))


def sends(deps):
    return [c for c in deps.tasty.calls if c[0] in ("place", "replace", "cancel")]


def the_run(factory, run_id):
    with factory() as session:
        return session.get(BotRun, run_id)


def test_attach_places_exits_through_submit(factory):
    deps = _deps(factory, short_put())
    deps.tasty.held[plain(PUT)] = -1
    done = manual_attach.attach(deps, KEY, PT50, None)
    assert done.ok, done.message
    with factory() as session:
        rows = list(session.scalars(select(BotExit).where(BotExit.run_id == done.run_id)))
        logged = list(session.scalars(select(OrderLog)))
    (target,) = rows
    assert target.kind == "target" and target.limit == D("1.00")  # 50% of the 2.00 credit
    placed = [c for c in sends(deps) if c[0] == "place"]
    assert len(placed) == 1
    # orders.submit logs every order it lets through: nothing reached the broker another way
    assert len(logged) >= len(placed) and all(r.bot_run_id == done.run_id for r in logged)


def test_basis_after_partial_close(factory):
    later = OPENED + timedelta(days=1)
    rows = condor(4) + [
        _tx(11, "Buy to Close", "1.00", CALL, 1, 501, later),
        _tx(12, "Sell to Close", "0.10", CALL_WING, 1, 501, later),
        _tx(13, "Buy to Close", "1.00", PUT, 1, 501, later),
        _tx(14, "Sell to Close", "0.10", PUT_WING, 1, 501, later),
    ]
    deps = _deps(factory, rows)
    done = manual_attach.attach(deps, KEY, PT50, None)
    assert done.ok, done.message
    run = the_run(factory, done.run_id)
    assert run.contracts == 3
    assert run.fill_credit == D("2.50") - D("0.40") + D("2.00") - D("0.30")


def test_per_trade_bots_not_listed(factory):
    deps = _deps(factory, short_put())
    make(factory, live=True)
    before = (
        [b.id for b in bot_store.listing(factory)], [b.id for b in bot_store.armed(factory)],
        bot_store.archived_count(factory),
    )  # fmt: skip
    from tests.services.test_bot_ticket import ticket

    offered_before = bot_ticket.choices(deps, "tastytrade", ACCOUNT, ticket())
    manual_id = manual_bots.save(factory, None, PT50, NOW)
    done = manual_attach.attach(deps, KEY, PT50, manual_id)
    assert done.ok, done.message
    after = (
        [b.id for b in bot_store.listing(factory)], [b.id for b in bot_store.armed(factory)],
        bot_store.archived_count(factory),
    )  # fmt: skip
    assert after == before
    offered_after = bot_ticket.choices(deps, "tastytrade", ACCOUNT, ticket())
    assert [b.id for b, *_ in offered_after[1]] == [b.id for b, *_ in offered_before[1]]
    with factory() as session:
        row = session.get(Bot, the_run(factory, done.run_id).bot_id)
        assert (row.hidden, row.per_trade, row.manual_bot_id, row.enabled) == (
            True, True, manual_id, False,
        )  # fmt: skip


def test_stop_all_bots_blocks_exit(factory):
    """Stop all bots is Freeze (migration 0014): the central check refuses the exits."""
    deps = _deps(factory, short_put())
    orders.set_switch(factory, "freeze", True, NOW)
    done = manual_attach.attach(deps, KEY, PT50, None)
    assert done.run_id is not None
    assert sends(deps) == []
    assert done.ok and done.warning and "NO PROFIT TARGET" in done.message


def test_already_managed_refused(factory):
    deps = _deps(factory, short_put())
    assert manual_attach.attach(deps, KEY, PT50, None).ok
    again = manual_attach.attach(deps, KEY, PT50, None)
    assert not again.ok and again.message == "This trade is already managed by a bot."
    with factory() as session:
        assert len(list(session.scalars(select(BotRun)))) == 1


def test_no_such_trade_refused(factory):
    deps = _deps(factory, short_put())
    done = manual_attach.attach(deps, "tastytrade:x:1", PT50, None)
    assert not done.ok and done.run_id is None


def test_multi_underlying_refused(factory):
    rows = short_put() + [_tx(2, "Sell to Open", "1.00", "SPY   261030P00570000", 1,
                              root="SPY")]  # fmt: skip
    deps = _deps(factory, rows)
    done = manual_attach.attach(deps, KEY, PT50, None)
    assert not done.ok and done.message == "Manage this trade works on one underlying."


ES_PUT = "./ESZ6 EW3X6 261120P7000"
EVENING = datetime(2026, 10, 13, 23, 0, tzinfo=UTC)


def test_futures_trade_pt_only(factory):
    row = {
        "id": 1, "symbol": ES_PUT, "instrument-type": "Future Option",
        "underlying-symbol": "./ESZ6", "transaction-type": "Trade", "action": "Sell to Open",
        "quantity": "1", "price": "12.50", "value": "625.0", "value-effect": "Credit",
        "description": "Sold 1 /ESZ6 EW3X6 11/20/26 Put 7000.00 @ 12.50",
        "executed-at": (EVENING - timedelta(days=2)).isoformat(), "order-id": 600,
    }  # fmt: skip
    deps = _deps(factory, [row], now=EVENING)
    on_futures(deps)
    deps.tasty.held[plain(ES_PUT)] = -1
    rules = ManualRules("ES half", target_value=D("50"), stop_value=D("100"), close_time="15:45")
    done = manual_attach.attach(deps, f"tastytrade:{ACCOUNT}:600", rules, None)
    assert done.ok, done.message
    run = the_run(factory, done.run_id)
    assert (run.pt_only, run.multiplier, run.contract) == (True, D(50), "/ESZ6")
    with factory() as session:
        kinds = [(e.kind, e.state) for e in session.scalars(select(BotExit))]
    assert kinds == [("target", "working")]
    assert "the stop loss and the exit time not placed" in done.message


def test_multi_expiry_uses_nearest(factory):
    far = sym(5600, "put", "2026-11-20")
    rows = [_tx(1, "Sell to Open", "2.00", PUT), _tx(2, "Buy to Open", "3.00", far)]
    deps = _deps(factory, rows)
    trade = manual_attach.read_trade(deps, KEY)[0]
    assert manual_attach.nearest_expiry(trade) == date(2026, 10, 30)
    rules = ManualRules("Timed", close_time="15:45", close_dte=1)
    bot = manual_attach.bot_for(rules, trade, None)
    assert (bot.close_time, bot.close_dte) == ("15:45", 1)
    from optionsjourney.services.bot_timed import group_expirations

    legs = manual_attach.matched(trade).legs
    assert group_expirations(legs) == {0: date(2026, 10, 30)}  # the timed close's clock


def test_assignable_spy_not_spx(factory):
    spy = "SPY   261030P00570000"
    deps = _deps(factory, [_tx(1, "Sell to Open", "2.00", spy, root="SPY")])
    assert manual_attach.assignable(manual_attach.read_trade(deps, KEY)[0]) == "SPY"


def test_assignable_none_for_cash_settled(factory):
    xsp = "XSP   261030P00570000"
    rows = [_tx(1, "Sell to Open", "2.00", PUT, order_id=700),
            _tx(2, "Sell to Open", "0.50", xsp, order_id=701, root="XSP")]  # fmt: skip
    deps = _deps(factory, rows)
    spx = manual_attach.read_trade(deps, f"tastytrade:{ACCOUNT}:700")[0]
    xsp_trade = manual_attach.read_trade(deps, f"tastytrade:{ACCOUNT}:701")[0]
    assert manual_attach.assignable(spx) is None and manual_attach.assignable(xsp_trade) is None


def test_run_history_shows_manual_name(factory):
    deps = _deps(factory, short_put())
    manual_id = manual_bots.save(factory, None, PT50, NOW)
    done = manual_attach.attach(deps, KEY, PT50, manual_id)
    run = the_run(factory, done.run_id)
    assert bot_store.label(factory, run.bot_id) == "Half off"
    manual_bots.save(factory, manual_id, ManualRules("Renamed", target_value=D("50")), NOW)
    assert bot_store.label(factory, run.bot_id) == "Renamed"
    assert [r.id for r in bot_store.runs(factory, run.bot_id)] == [done.run_id]


@pytest.mark.parametrize("unit", ["pct", "usd"])
def test_stop_units_carry_over(factory, unit):
    deps = _deps(factory, short_put())
    rules = ManualRules("Stop", stop_value=D("100") if unit == "pct" else D("1.5"),
                        stop_unit=unit)  # fmt: skip
    trade = manual_attach.read_trade(deps, KEY)[0]
    (group,) = manual_attach.bot_for(rules, trade, None).groups
    assert group.stop_unit == unit and group.stop_value == rules.stop_value
    assert group.name == "Whole trade" and group.position == 0


def test_a_stop_rests_at_the_loss_of_the_credit(factory):
    """SL 100%: a loss of the whole 2.00 credit, so the stop rests on the short at 4.00."""
    deps = _deps(factory, short_put())
    deps.tasty.held[plain(PUT)] = -1
    deps.hub.set(PUT, D("1.50"), D("1.60"), NOW)
    done = manual_attach.attach(deps, KEY, ManualRules("SL", stop_value=D("100")), None)
    assert done.ok and not done.warning, done.message
    with factory() as session:
        (stop,) = session.scalars(select(BotExit).where(BotExit.kind == "stop"))
    assert stop.trigger == D("4.00") and stop.state == "working"


def _hidden_bots(factory):
    with factory() as session:
        return [(b.hidden, b.per_trade, b.enabled, b.archived_at is not None)
                for b in session.scalars(select(Bot))]  # fmt: skip


def test_the_bot_is_hidden_in_the_save_that_creates_it(factory, monkeypatch):
    """No visible moment: a failure right after the save leaves nothing listed."""
    deps = _deps(factory, short_put())

    def boom(*a, **k):
        assert bot_store.listing(factory) == [] and bot_store.armed(factory) == []
        raise RuntimeError("load failed")

    monkeypatch.setattr(bot_store, "load", boom)
    done = manual_attach.attach(deps, KEY, PT50, None)
    assert not done.ok
    assert _hidden_bots(factory) == [(True, True, False, True)]  # hidden, archived
    with factory() as session:
        assert list(session.scalars(select(BotRun))) == []


def test_a_failed_record_archives_the_bot(factory, monkeypatch):
    deps = _deps(factory, short_put())
    monkeypatch.setattr(bot_adopt, "record_run", lambda *a: None)
    done = manual_attach.attach(deps, KEY, PT50, None)
    assert not done.ok and done.message == manual_attach.MANAGED
    assert _hidden_bots(factory) == [(True, True, False, True)]
    assert sends(deps) == []


def _debit_run(factory, rows, quotes, rules):
    deps = _deps(factory, rows)
    for symbol, (bid, ask) in quotes.items():
        deps.tasty.held[plain(symbol)] = 1
        deps.hub.set(symbol, D(bid), D(ask), NOW)
    done = manual_attach.attach(deps, KEY, rules, None)
    assert done.ok, done.message
    with factory() as session:
        return deps, done, list(session.scalars(select(BotExit).where(BotExit.kind == "stop")))


def test_a_debit_spreads_stop_is_the_debit_lost(factory):
    """SL 50% on a 2.00 debit: watched at a P&L of -1.00 (closes at 1.00 of value)."""
    rows = [_tx(1, "Buy to Open", "3.00", PUT), _tx(2, "Sell to Open", "1.00", PUT_WING)]
    deps, done, stops = _debit_run(
        factory, rows, {PUT: ("2.90", "3.10"), PUT_WING: ("0.90", "1.10")},
        ManualRules("SL", stop_value=D("50")),
    )  # fmt: skip
    (stop,) = stops
    assert (stop.state, stop.trigger) == ("watching", D("-1.00"))
    assert sends(deps) == []  # nothing rests: the app watches it


def test_a_long_puts_stop_is_the_debit_lost(factory):
    rows = [_tx(1, "Buy to Open", "2.00", PUT)]
    deps, done, stops = _debit_run(
        factory, rows, {PUT: ("1.90", "2.10")}, ManualRules("SL", stop_value=D("50")),
    )  # fmt: skip
    (stop,) = stops
    assert (stop.state, stop.trigger) == ("watching", D("-1.00"))
    assert not done.warning and sends(deps) == []


def test_a_trade_over_the_limits_is_refused_plainly(factory):
    deps = _deps(factory, short_put(qty=101))
    done = manual_attach.attach(deps, KEY, PT50, None)
    assert not done.ok and done.message == (
        "A manual bot can't manage this trade: it's more than 100 contracts."
    )
    assert _hidden_bots(factory) == []


# --- Task 4: change, remove, closing ---------------------------------------------------

WAIT = "A close is working on this trade. You can change its rules once it finishes."
SL100 = ManualRules("SL", stop_value=D("100"))


def _stopped(factory):
    deps = _deps(factory, short_put())
    deps.tasty.held[plain(PUT)] = -1
    deps.hub.set(PUT, D("1.50"), D("1.60"), NOW)
    done = manual_attach.attach(deps, KEY, SL100, None)
    assert done.ok, done.message
    return deps, done


def test_remove_cancels_exits_and_hands_back(factory):
    deps, done = _stopped(factory)
    before = len(sends(deps))
    out = manual_attach.remove(deps, done.run_id)
    assert out.ok, out.message
    assert [c[0] for c in sends(deps)[before:]] == ["cancel"]
    assert the_run(factory, done.run_id).status == manual_attach.HANDED_BACK
    with factory() as session:
        (stop,) = session.scalars(select(BotExit).where(BotExit.kind == "stop"))
    assert stop.state == "cancelled"
    assert manual_attach.read_trade(deps, KEY)[1] is None  # Manage this trade again


def test_remove_then_reattach(factory):
    deps, done = _stopped(factory)
    assert manual_attach.remove(deps, done.run_id).ok
    again = manual_attach.attach(deps, KEY, PT50, None)
    assert again.ok, again.message
    with factory() as session:
        statuses = sorted(r.status for r in session.scalars(select(BotRun)))
    assert statuses == ["filled", manual_attach.HANDED_BACK]


def test_two_attaches_at_once_make_one_run(factory):
    import threading

    deps = _deps(factory, short_put())
    results, start = [], threading.Barrier(2)

    def go():
        start.wait()
        results.append(manual_attach.attach(deps, KEY, PT50, None))

    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(r.ok for r in results) == [False, True]
    with factory() as session:
        assert len(list(session.scalars(select(BotRun)))) == 1


def test_owner_still_blocks_a_scheduled_bots_trade(factory):
    deps, done = _stopped(factory)
    order_id = the_run(factory, done.run_id).broker_order_id
    assert bot_adopt._owner(factory, order_id) == "SL"
    manual_attach.remove(deps, done.run_id)
    assert bot_adopt._owner(factory, order_id) is None


def _firing_close(factory, run_id):
    with factory() as session:
        session.add(BotExit(run_id=run_id, group_position=0, kind="close", state="firing",
                            short_symbol=PUT, long_symbol="", contracts=1,
                            log=[], created_at=NOW, updated_at=NOW))  # fmt: skip
        session.commit()


def test_change_waits_while_close_works(factory):
    deps, done = _stopped(factory)
    _firing_close(factory, done.run_id)
    before = list(deps.tasty.calls)
    assert manual_attach.closing(deps, done.run_id)
    for out in (manual_attach.change(deps, done.run_id, PT50),
                manual_attach.remove(deps, done.run_id)):  # fmt: skip
        assert not out.ok and out.message == WAIT
    assert deps.tasty.calls == before
    assert the_run(factory, done.run_id).status == "filled"


def test_change_replaces_the_rules(factory):
    deps, done = _stopped(factory)
    assert not manual_attach.closing(deps, done.run_id)
    out = manual_attach.change(deps, done.run_id, PT50)
    assert out.ok, out.message
    assert out.run_id != done.run_id
    assert the_run(factory, done.run_id).status == manual_attach.HANDED_BACK
    with factory() as session:
        target = session.scalar(select(BotExit).where(BotExit.run_id == out.run_id))
    assert target.kind == "target" and target.limit == D("1.00")


def _futures_run(factory):
    row = {
        "id": 1, "symbol": ES_PUT, "instrument-type": "Future Option",
        "underlying-symbol": "./ESZ6", "transaction-type": "Trade", "action": "Sell to Open",
        "quantity": "1", "price": "12.50", "value": "625.0", "value-effect": "Credit",
        "description": "Sold 1 /ESZ6 EW3X6 11/20/26 Put 7000.00 @ 12.50",
        "executed-at": (EVENING - timedelta(days=2)).isoformat(), "order-id": 600,
    }  # fmt: skip
    deps = _deps(factory, [row], now=EVENING)
    on_futures(deps)
    deps.tasty.held[plain(ES_PUT)] = -1
    done = manual_attach.attach(deps, f"tastytrade:{ACCOUNT}:600", PT50, None)
    assert done.ok, done.message
    return deps, done


def test_change_futures_without_target_leaves_rules(factory):
    deps, done = _futures_run(factory)
    before = list(deps.tasty.calls)
    out = manual_attach.change(deps, done.run_id, SL100)
    assert not out.ok and out.message.startswith(manual_attach.FUTURES_TARGET)
    assert deps.tasty.calls == before  # no cancel sent
    assert the_run(factory, done.run_id).status == "filled"
    with factory() as session:
        (target,) = session.scalars(select(BotExit))
    assert (target.kind, target.state) == ("target", "working")


def test_change_failing_after_remove_says_nothing_manages(factory, monkeypatch):
    deps, done = _stopped(factory)
    monkeypatch.setattr(bot_adopt, "record_run", lambda *a: None)
    out = manual_attach.change(deps, done.run_id, PT50)
    assert not out.ok and out.warning and "Nothing manages this trade now" in out.message
    assert the_run(factory, done.run_id).status == manual_attach.HANDED_BACK


def test_remove_with_unconfirmed_cancel_keeps_rules(factory, monkeypatch):
    from optionsjourney.services import bot_manage

    deps, done = _stopped(factory)
    unconfirmed = "The stop's cancel wasn't confirmed, so exits are still on."
    monkeypatch.setattr(bot_manage, "exits_off",
                        lambda *a: bot_manage.Outcome(False, unconfirmed))  # fmt: skip
    out = manual_attach.remove(deps, done.run_id)
    assert not out.ok and out.message == f"{unconfirmed} The rules stay on."
    assert the_run(factory, done.run_id).status == "filled"
