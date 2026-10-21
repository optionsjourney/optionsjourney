"""Bots slice 18: restore a trade's missing exits, with a dry run (rearm.rearm_trade)."""

from decimal import Decimal

from optionsjourney.bots import rearm
from optionsjourney.db.models import BotExit
from optionsjourney.services import bot_entry, bot_exits, bot_manage, bot_rearm
from tests.services import test_bot_manage as manage_tests
from tests.services.test_bot_manage import at, placed, quotes, rows, titles, trade

deps = manage_tests.deps  # the same fixture: a live iron condor's trade on the fake broker

D = Decimal


def fail_stop(factory, deps, side=0, cancel=True):
    """The side's stop gone: cancelled at the broker and written failed (its re-arms
    used up, say)."""
    stop = rows(factory, "stop", side)[0]
    if cancel:
        deps.tasty.cancel_order("5WX01234", stop.broker_order_id)
    with factory() as session:
        row = session.get(BotExit, stop.id)
        row.state = "failed"
        row.log = [*row.log, {"event": "re-arm limit reached"}]
        session.commit()
    return stop


def stops_sent(deps):
    return [c for c in placed(deps) if c[2]["order-type"] == "Stop"]


def test_the_dry_run_lists_a_failed_stop_and_sends_nothing(deps, factory):
    run_id = trade(deps, factory)
    stop = fail_stop(factory, deps)
    calls = len(deps.tasty.calls)
    items = bot_rearm.rearm_trade(deps, run_id, dry_run=True)
    assert [(i.group, i.kind, i.status, i.price, i.contracts) for i in items] == [
        ("Call side", "stop", "missing", stop.trigger, 1)
    ]
    assert len(deps.tasty.calls) == calls
    assert rows(factory, "stop", 0)[0].state == "failed"  # nothing written either


def test_restore_places_a_failed_stop_after_the_live_check_sized_to_held(deps, factory):
    run_id = trade(deps, factory, contracts=2)
    stop = fail_stop(factory, deps)
    at(deps, "11:00")
    quotes(deps, factory, "11:00")
    sent = len(stops_sent(deps))
    items = bot_rearm.rearm_trade(deps, run_id, dry_run=False)
    assert [(i.kind, i.status) for i in items] == [("stop", "placed")], items
    row = rows(factory, "stop", 0)[0]
    assert row.state == "working" and row.trigger == stop.trigger and row.rearms == stop.rearms
    new = stops_sent(deps)[sent:]
    assert len(new) == 1 and new[0][2]["legs"][0]["quantity"] == 2
    assert "IC: missing exits not restored" not in titles(deps)


def test_restore_refuses_a_stop_that_would_fire_at_once_and_alerts(deps, factory):
    run_id = trade(deps, factory)
    fail_stop(factory, deps)
    at(deps, "11:00")
    quotes(deps, factory, "11:00", short_mid="9.00")
    sent = len(stops_sent(deps))
    [item] = bot_rearm.rearm_trade(deps, run_id, dry_run=False)
    assert item.status == "refused" and "would fire at once" in item.detail
    assert len(stops_sent(deps)) == sent
    assert rows(factory, "stop", 0)[0].state == "failed"
    assert "IC: missing exits not restored" in titles(deps)


def test_restore_arms_a_target_a_close_couldnt_put_back(deps, factory):
    run_id = trade(deps, factory)
    target = rows(factory, "target", 1)[0]
    with factory() as session:
        row = session.get(BotExit, target.id)
        row.state = "cancelled"
        row.log = [*row.log, {"event": bot_exits.CANCELLED_FOR_TIMED}]
        session.commit()
    calls = len(deps.tasty.calls)
    [item] = bot_rearm.rearm_trade(deps, run_id, dry_run=True)
    assert (item.group, item.kind, item.price) == ("Put side", "target", target.limit)
    [item] = bot_rearm.rearm_trade(deps, run_id, dry_run=False)
    assert item.status == "placed"
    assert rows(factory, "target", 1)[0].state == "armed"
    assert [c for c in deps.tasty.calls[calls:] if c[0] in ("place", "cancel")] == []


def test_restore_leaves_a_switched_off_or_held_back_group_alone(deps, factory):
    run_id = trade(deps, factory)
    assert bot_manage.stop_off(deps, run_id, 0).ok
    assert bot_rearm.rearm_trade(deps, run_id, dry_run=True) == []
    stop = rows(factory, "stop", 1)[0]
    with factory() as session:  # a stop move cut off part-way: its own recovery's
        row = session.get(BotExit, stop.id)
        row.state = "cancelled"
        row.log = [*row.log, {"event": bot_exits.MOVING_STOP}]
        session.commit()
    [item] = bot_rearm.rearm_trade(deps, run_id, dry_run=True)
    assert (item.group, item.status) == ("Put side", "held back")
    sent = len(placed(deps))
    [item] = bot_rearm.rearm_trade(deps, run_id, dry_run=False)
    assert item.status == "refused" and len(placed(deps)) == sent


def test_restore_is_refused_under_freeze_and_for_a_preview_run(deps, factory, monkeypatch):
    from optionsjourney.db.models import BotRun

    run_id = trade(deps, factory)
    fail_stop(factory, deps)
    with monkeypatch.context() as m:
        m.setattr(bot_exits, "_frozen", lambda deps: True)
        [item] = bot_rearm.rearm_trade(deps, run_id, dry_run=False)
        assert item.status == "refused" and "Freeze" in item.detail
    with factory() as session:
        session.get(BotRun, run_id).live = False
        session.commit()
    [item] = bot_rearm.rearm_trade(deps, run_id, dry_run=False)
    assert item.status == "refused" and "preview" in item.detail


def test_restore_all_covers_every_open_trade(deps, factory):
    first = trade(deps, factory)
    fail_stop(factory, deps)
    at(deps, "11:00")
    quotes(deps, factory, "11:00")
    items = bot_rearm.rearm_all(deps, dry_run=True)
    assert [(i.run_id, i.kind, i.status) for i in items] == [(first, "stop", "missing")]
    items = bot_rearm.rearm_all(deps, dry_run=False)
    assert [(i.run_id, i.status) for i in items] == [(first, "placed")]
    assert bot_rearm.rearm_all(deps, dry_run=True) == []


def test_a_stop_not_known_gone_at_the_broker_is_never_doubled(deps, factory):
    """A cancelled stop whose last word isn't the broker's: Restore won't place a
    second one on top of it."""
    run_id = trade(deps, factory)
    stop = rows(factory, "stop", 0)[0]
    with factory() as session:
        row = session.get(BotExit, stop.id)
        row.state = "cancelled"
        row.log = [*row.log, {"event": "something the app can't vouch for"}]
        session.commit()
    [item] = bot_rearm.rearm_trade(deps, run_id, dry_run=True)
    assert item.status == "held back" and "isn't known to be gone" in item.detail
    sent = len(placed(deps))
    [item] = bot_rearm.rearm_trade(deps, run_id, dry_run=False)
    assert item.status == "refused" and len(placed(deps)) == sent


# --- The final review's fixes ------------------------------------------------------------


def test_a_stop_that_failed_on_an_unclear_send_is_never_restored(deps, factory):
    """rearm writes "failed" for any refusal, an Unclear answer too: that stop may be
    live at the broker, so Restore won't place a second one."""
    run_id = trade(deps, factory)
    stop = fail_stop(factory, deps, cancel=False)
    with factory() as session:
        row = session.get(BotExit, stop.id)
        row.log = [*row.log, {"event": "re-armed", "result": "Unclear: the broker didn't say"}]
        session.commit()
    [item] = bot_rearm.rearm_trade(deps, run_id, dry_run=True)
    assert item.status == "held back" and "isn't known to be gone" in item.detail
    at(deps, "11:00")
    quotes(deps, factory, "11:00")
    sent = len(stops_sent(deps))
    [item] = bot_rearm.rearm_trade(deps, run_id, dry_run=False)
    assert item.status == "refused" and len(stops_sent(deps)) == sent


def test_a_refused_restore_is_left_failed_and_never_placed_later(deps, factory):
    """The monitor places a "pending" stop without the live check: a refused Restore
    must leave the row "failed", so nothing places it on its own."""
    run_id = trade(deps, factory)
    fail_stop(factory, deps)
    at(deps, "11:00")
    quotes(deps, factory, "11:00")
    deps.tasty.errors["place"] = RuntimeError("the broker said no")
    [item] = bot_rearm.rearm_trade(deps, run_id, dry_run=False)
    assert item.status == "refused" and "nothing will place it later" in item.detail
    assert rows(factory, "stop", 0)[0].state == "failed"
    del deps.tasty.errors["place"]
    sent = len(stops_sent(deps))
    quotes(deps, factory, "11:00", short_mid="9.00")  # through the stop by now
    bot_exits.monitor_pass(deps)
    assert len(stops_sent(deps)) == sent and rows(factory, "stop", 0)[0].state == "failed"


def test_restore_writes_a_new_target_row_over_one_that_closed_part(deps, factory):
    run_id = trade(deps, factory)
    target = rows(factory, "target", 1)[0]
    with factory() as session:
        row = session.get(BotExit, target.id)
        row.state, row.fill_price = "reduced", D("0.40")
        row.log = [*row.log, {"event": "closed 1 of 2"}]
        session.commit()
    [item] = bot_rearm.rearm_trade(deps, run_id, dry_run=False)
    assert item.status == "placed"
    old, new = rows(factory, "target", 1)
    assert (old.id, old.state, old.fill_price) == (target.id, "reduced", D("0.40"))
    assert new.state == "armed" and new.limit == target.limit


def debit_trade(deps, factory):
    """A live call debit spread's trade: one group the app watches on its P&L."""
    from tests.services.bot_fakes import make_shape

    bot_id = make_shape(factory, "call_debit_spread", live=True, stop=D("50"), target=D("50"))
    return bot_entry.fire(deps, bot_id, "09:47")


def group_quotes(deps, factory, run_id, hhmm="11:00"):
    from optionsjourney.db.models import BotRun
    from tests.services.test_bot_manage import ny

    with factory() as session:
        fills = session.get(BotRun, run_id).filled_legs
    for f in fills:
        price = D(f["fill_price"])
        deps.hub.set(f["symbol"], price - D("0.05"), price + D("0.05"), ny(hhmm))


def test_restore_watches_a_watched_groups_stop_again_and_sends_nothing(deps, factory):
    run_id = debit_trade(deps, factory)
    stop = rows(factory, "stop", 0)[0]
    assert stop.state == "watching"
    with factory() as session:
        session.get(BotExit, stop.id).state = "failed"
        session.commit()
    at(deps, "11:00")
    group_quotes(deps, factory, run_id)
    sent = len(placed(deps))
    [item] = bot_rearm.rearm_trade(deps, run_id, dry_run=False)
    assert (item.status, item.detail) == ("placed", "watched by the app"), item
    assert rows(factory, "stop", 0)[0].state == "watching" and len(placed(deps)) == sent


def test_restore_leaves_a_watched_stop_at_its_3_fires_a_day_to_you(deps, factory):
    from optionsjourney.services.bot_exits import WATCHED_STOP_BY
    from tests.services.test_bot_manage import ny

    run_id = debit_trade(deps, factory)
    stop = rows(factory, "stop", 0)[0]
    with factory() as session:
        row = session.get(BotExit, stop.id)
        row.state = "failed"
        row.log = [*row.log, {"event": "reached again after 3 closes today; left to you"}]
        for _ in range(3):
            session.add(BotExit(
                run_id=run_id, group_position=0, kind="close", state="cancelled",
                short_symbol="", long_symbol="", legs=row.legs, contracts=1, rearms=0,
                log=[{"event": "a close of 1 for the stop", "by": WATCHED_STOP_BY}],
                created_at=ny("10:00"), updated_at=ny("10:00"),
            ))  # fmt: skip
        session.commit()
    at(deps, "11:00")
    [item] = bot_rearm.rearm_trade(deps, run_id, dry_run=True)
    assert item.status == "set it by hand" and "3 times today" in item.detail
    [item] = bot_rearm.rearm_trade(deps, run_id, dry_run=False)
    assert item.status == "refused" and rows(factory, "stop", 0)[0].state == "failed"


def test_restore_places_a_resting_target_again_at_tastytrade(deps, factory):
    from tests.services.bot_fakes import make_shape, rest_the_target

    bot_id = make_shape(factory, "put_credit_spread", live=True, stop=D("95"), target=D("50"))
    run_id = bot_entry.fire(deps, bot_id, "09:47")
    _, target_id = rest_the_target(deps, factory, run_id)
    with factory() as session:
        row = session.get(BotExit, target_id)
        deps.tasty.cancel_order("5WX01234", row.broker_order_id)
        row.state = "failed"
        session.commit()
    before = [c for c in deps.tasty.calls if c[0] == "place" and c[2]["time-in-force"] == "GTC"]
    items = bot_rearm.rearm_trade(deps, run_id, dry_run=False)
    assert [i.kind for i in items if i.status == rearm.PLACED] == ["target"]
    after = [c for c in deps.tasty.calls if c[0] == "place" and c[2]["time-in-force"] == "GTC"]
    assert len(after) == len(before) + 1
    with factory() as session:
        assert session.get(BotExit, target_id).state == "working"


def test_a_new_row_restore_places_for_a_resting_target_says_it_rests(deps, factory):
    """P5: a resting target Restore places on a new row (the old one closed part of the
    group) logs that it rests, so once that row ends Restore places it again, never arms it."""
    from tests.services.bot_fakes import make_shape, rest_the_target

    bot_id = make_shape(factory, "put_credit_spread", live=True, stop=D("95"), target=D("50"))
    run_id = bot_entry.fire(deps, bot_id, "09:47")
    _, target_id = rest_the_target(deps, factory, run_id)
    with factory() as session:
        row = session.get(BotExit, target_id)
        deps.tasty.cancel_order("5WX01234", row.broker_order_id)
        row.state, row.fill_price = "cancelled", D("1.00")  # it closed part, so it's kept
        session.commit()
    bot_rearm.rearm_trade(deps, run_id, dry_run=False)
    new = rows(factory, "target", 0)[-1]
    assert new.id != target_id and new.state == "working"
    assert {"rests": "target"} in new.log
    with factory() as session:
        row = session.get(BotExit, new.id)
        deps.tasty.cancel_order("5WX01234", row.broker_order_id)
        row.state = "failed"
        session.commit()
    before = [c for c in deps.tasty.calls if c[0] == "place" and c[2]["time-in-force"] == "GTC"]
    items = bot_rearm.rearm_trade(deps, run_id, dry_run=False)
    assert [i.kind for i in items if i.status == rearm.PLACED] == ["target"]
    after = [c for c in deps.tasty.calls if c[0] == "place" and c[2]["time-in-force"] == "GTC"]
    assert len(after) == len(before) + 1
    assert rows(factory, "target", 0)[-1].state == "working"


def test_an_unclear_restore_of_a_resting_target_holds_the_group(deps, factory):
    """Final review C1: a Restore whose send got no clear answer may have left the target
    resting: it's held (no close is sent on the group), never just "not restored"."""
    from tests.services.bot_fakes import make_shape, rest_the_target
    from tests.services.test_bot_exits import target_sends_unclear

    bot_id = make_shape(factory, "put_credit_spread", live=True, stop=D("95"), target=D("50"))
    run_id = bot_entry.fire(deps, bot_id, "09:47")
    _, target_id = rest_the_target(deps, factory, run_id)
    with factory() as session:
        row = session.get(BotExit, target_id)
        deps.tasty.cancel_order("5WX01234", row.broker_order_id)
        row.state = "failed"
        session.commit()
    target_sends_unclear(deps)
    items = bot_rearm.rearm_trade(deps, run_id, dry_run=False)
    assert [i.status for i in items if i.kind == "target"] == [rearm.REFUSED]
    with factory() as session:
        row = session.get(BotExit, target_id)
        assert row.state == "failed" and row.log[-1]["event"] == bot_exits.TARGET_UNCLEAR
        side = list(session.query(BotExit).filter(BotExit.run_id == run_id))
        assert bot_exits.target_may_rest(side)
