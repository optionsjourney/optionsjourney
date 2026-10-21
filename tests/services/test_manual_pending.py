"""Manual bots (#158): the Trade ticket's Manage with, a pending attachment that attaches
a manual bot's rules once the ticket's order fills."""

from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select

from optionsjourney.bots.manual import ManualRules
from optionsjourney.db import make_engine, make_session_factory
from optionsjourney.db.models import BotExit, BotRun, PendingManual
from optionsjourney.services import manual_attach, manual_bots
from optionsjourney.services.sync import sync_broker
from optionsjourney.trading.orders import BrokerLeg, BrokerOrder, OrderStatus, Side, plain
from tests.services.test_manual_attach import ACCOUNT, NOW, OPENED, PUT, History, _deps, _tx

D = Decimal
PT50 = ManualRules("Half off", target_value=D("50"))


def _order(status, filled, quantity=5):
    leg = BrokerLeg(PUT, Side.SELL, True, quantity, quantity - filled)
    return BrokerOrder("500", status, (leg,))


def _row(factory):
    with factory() as session:
        return session.scalars(select(PendingManual)).one()


def _run(factory, run_id):
    with factory() as session:
        return session.get(BotRun, run_id)


def _targets(factory, run_id):
    with factory() as session:
        return list(session.scalars(select(BotExit).where(BotExit.run_id == run_id,
                                                          BotExit.kind == "target")))  # fmt: skip


def _sent(factory, rows=(), filled=0, status=OrderStatus.WORKING):
    deps = _deps(factory, list(rows))
    manual_id = manual_bots.save(factory, None, PT50, NOW)
    deps.tasty.orders["500"] = _order(status, filled)
    deps.tasty.held[plain(PUT)] = -filled
    manual_attach.remember(deps, "tastytrade", ACCOUNT, "500", manual_id)
    return deps, manual_id


def _fills(factory, deps, rows):
    sync_broker(History(rows), factory, now=deps.now)


def test_fill_attaches_rules(factory):
    deps, _ = _sent(factory)
    manual_attach.pending_pass(deps)
    assert _row(factory).status == "waiting" and _row(factory).run_id is None  # not filled yet
    _fills(factory, deps, [_tx(1, "Sell to Open", "2.00", PUT, 5)])
    deps.tasty.orders["500"], deps.tasty.held[plain(PUT)] = _order(OrderStatus.FILLED, 5), -5
    manual_attach.pending_pass(deps)
    row = _row(factory)
    assert row.status == "ended" and row.run_id is not None and row.attached_contracts == 5
    assert _run(factory, row.run_id).contracts == 5
    (target,) = _targets(factory, row.run_id)
    assert target.contracts == 5 and target.limit == D("1.00")


def _runs(factory):
    with factory() as session:
        return list(session.scalars(select(BotRun).order_by(BotRun.id)))


def test_partial_fill_then_rest_grows(factory):
    first = [_tx(1, "Sell to Open", "2.00", PUT, 2)]
    deps, _ = _sent(factory, first, filled=2, status=OrderStatus.PARTIAL)
    manual_attach.pending_pass(deps)
    row = _row(factory)
    assert row.status == "attached" and row.attached_contracts == 2
    assert _run(factory, row.run_id).contracts == 2
    first_run = row.run_id
    # 3 more fill while the order is still working: no change yet
    later = OPENED + timedelta(minutes=5)
    rest = [*first, _tx(2, "Sell to Open", "2.00", PUT, 3, at=later)]
    _fills(factory, deps, rest)
    deps.tasty.orders["500"], deps.tasty.held[plain(PUT)] = _order(OrderStatus.PARTIAL, 5), -5
    manual_attach.pending_pass(deps)
    assert _row(factory).run_id == first_run and len(_runs(factory)) == 1
    # The order is done at 5: one change to 5
    deps.tasty.orders["500"] = _order(OrderStatus.FILLED, 5)
    manual_attach.pending_pass(deps)
    manual_attach.pending_pass(deps)
    row = _row(factory)
    assert row.status == "ended" and row.attached_contracts == 5
    assert len(_runs(factory)) == 2 and _run(factory, row.run_id).contracts == 5
    assert [t.contracts for t in _targets(factory, row.run_id) if t.state != "cancelled"] == [5]
    assert _run(factory, first_run).status == manual_attach.HANDED_BACK  # its exits cancelled


def test_done_but_journal_lacks_fill_ends_after_a_day(factory):
    deps, _ = _sent(factory, filled=5, status=OrderStatus.FILLED)
    manual_attach.pending_pass(deps)
    assert _row(factory).status == "waiting" and _row(factory).done_at is not None
    deps.now = lambda: NOW + timedelta(days=1, minutes=1)
    manual_attach.pending_pass(deps)
    assert _row(factory).status == "ended" and _row(factory).run_id is None
    assert any(manual_attach.NOT_ATTACHED in str(a) for a in deps.alerts)


def test_order_unreadable_ends_after_three_passes(factory):
    deps, _ = _sent(factory)
    deps.tasty.orders.clear()  # get_order raises KeyError
    for _ in range(2):
        manual_attach.pending_pass(deps)
        assert _row(factory).status == "waiting"
    manual_attach.pending_pass(deps)
    assert _row(factory).status == "ended"
    assert any(manual_attach.NOT_ATTACHED in str(a) for a in deps.alerts)


def test_partial_close_before_rest_fills_still_ends(factory):
    first = [_tx(1, "Sell to Open", "2.00", PUT, 2)]
    deps, _ = _sent(factory, first, filled=2, status=OrderStatus.PARTIAL)
    manual_attach.pending_pass(deps)
    first_run = _row(factory).run_id
    # You close 1 of the 2; the order is then cancelled with nothing more filled
    later = OPENED + timedelta(minutes=5)
    _fills(factory, deps, [*first, _tx(2, "Buy to Close", "1.00", PUT, 1, 501, later)])
    deps.tasty.orders["500"], deps.tasty.held[plain(PUT)] = _order(OrderStatus.CANCELLED, 2), -1
    manual_attach.pending_pass(deps)
    row = _row(factory)
    assert row.status == "ended" and row.run_id == first_run  # no change for a smaller trade


def test_cancelled_order_attaches_nothing(factory):
    deps, _ = _sent(factory, status=OrderStatus.CANCELLED)
    manual_attach.pending_pass(deps)
    assert _row(factory).status == "ended" and _row(factory).run_id is None
    with factory() as session:
        assert session.scalar(select(BotRun.id)) is None
    assert not [c for c in deps.tasty.calls if c[0] == "place"]


def test_pending_survives_restart(factory):
    deps, _ = _sent(factory)
    manual_attach.pending_pass(deps)
    # A restart: a new session factory on the same database, new deps
    fresh = make_session_factory(make_engine(Path(factory.kw["bind"].url.database)))
    again = _deps(fresh, [_tx(1, "Sell to Open", "2.00", PUT, 5)])
    again.tasty.orders["500"], again.tasty.held[plain(PUT)] = _order(OrderStatus.FILLED, 5), -5
    assert _row(fresh).status == "waiting"
    manual_attach.pending_pass(again)
    assert _row(fresh).status == "ended" and _row(fresh).attached_contracts == 5


def test_template_edit_after_send_not_applied(factory):
    deps, manual_id = _sent(factory)
    manual_bots.save(factory, manual_id, ManualRules("Half off", target_value=D("80")), NOW)
    _fills(factory, deps, [_tx(1, "Sell to Open", "2.00", PUT, 5)])
    deps.tasty.orders["500"], deps.tasty.held[plain(PUT)] = _order(OrderStatus.FILLED, 5), -5
    manual_attach.pending_pass(deps)
    (target,) = _targets(factory, _row(factory).run_id)
    assert target.limit == D("1.00")  # 50% kept, the rules at Send, not the edited 80%


def test_remember_sends_nothing(factory):
    deps, _ = _sent(factory)
    assert not deps.tasty.calls
    assert _row(factory).rules["target_value"] == "50"
