"""bot_chase.chase's hooks: each order recorded as it goes, and a stop request."""

from dataclasses import replace
from decimal import Decimal

import pytest

from optionsjourney.bots.definition import ChaseDef
from optionsjourney.db.models import BotExit
from optionsjourney.services import bot_entry, orders
from optionsjourney.services.bot_chase import chase
from optionsjourney.trading.orders import Leg, Order, OrderStatus, Side
from tests.services.bot_fakes import OPEN_TIME, FakeChain, FakeHub, FakeTasty, make

D = Decimal
PLAN = ChaseDef(D("0"), D("0.05"), 5, 3)


@pytest.fixture
def deps(factory):
    tasty = FakeTasty()
    d = bot_entry.BotDeps(
        factory, lambda: [tasty], FakeHub(), FakeChain(),
        notify=lambda e: None, sleep=lambda s: None, now=lambda: OPEN_TIME,
        background=lambda fn, *args: fn(*args),
    )  # fmt: skip
    d.tasty = tasty
    return d


def close_request(deps, factory):
    """A live run's call side, and the request that closes it."""
    bot_id = make(factory, live=True)
    run_id = bot_entry.fire(deps, bot_id, "09:47")
    with factory() as session:
        stop = session.query(BotExit).filter_by(run_id=run_id, kind="stop").first()
        short, long, stop_order = stop.short_symbol, stop.long_symbol, stop.broker_order_id
    deps.tasty.cancel_order("5WX01234", stop_order)  # the stop out of the way
    order = Order((Leg(short, Side.BUY), Leg(long, Side.SELL)), 1, False, price=D("-1.00"))
    return orders.Request("tastytrade", "5WX01234", "bot", order, bot_id=bot_id, bot_run_id=run_id)


def test_each_order_is_recorded_as_it_goes(deps, factory):
    deps.tasty.close = "work"
    request = close_request(deps, factory)
    seen = []
    chase(deps, request, D("-1.00"), PLAN, lambda p, s: p - s, on_order=seen.append)
    replaces = [c[2] for c in deps.tasty.calls if c[0] == "replace"]
    first = [c[1] for c in deps.tasty.calls if c[0] == "place" and len(c[2]["legs"]) == 2]
    assert seen == first + replaces and len(replaces) == 2


def test_a_stop_request_cancels_and_confirms(deps, factory):
    deps.tasty.close = "work"
    request = close_request(deps, factory)
    seen = []
    out = chase(deps, request, D("-1.00"), PLAN, lambda p, s: p - s,
                on_order=seen.append, should_stop=lambda: True)  # fmt: skip
    assert out.status == "stopped" and out.order.status is OrderStatus.CANCELLED
    assert ("cancel", seen[-1]) in deps.tasty.calls
    assert not [c for c in deps.tasty.calls if c[0] == "replace"]


def test_a_stop_request_that_finds_the_order_filled_reports_filled(deps, factory):
    deps.tasty.close = "work"
    request = close_request(deps, factory)
    seen = []

    def stop_after_it_filled():
        deps.tasty.fill(seen[-1])
        return True

    out = chase(deps, request, D("-1.00"), PLAN, lambda p, s: p - s,
                on_order=seen.append, should_stop=stop_after_it_filled)  # fmt: skip
    assert out.status == "filled"


def test_an_unchanged_next_price_keeps_the_order_without_a_replace(deps, factory):
    deps.tasty.close = "work"
    request = close_request(deps, factory)
    out = chase(deps, request, D("-1.00"), PLAN, lambda p, s: p)
    assert out.status == "missed"
    assert not [c for c in deps.tasty.calls if c[0] == "replace"]
    assert deps.tasty.calls[-1][0] == "cancel"  # the last try ran out: cancelled


def test_the_hooks_are_optional(deps, factory):
    request = close_request(deps, factory)
    assert chase(deps, request, D("-1.00"), replace(PLAN, max_tries=1), lambda p, s: p).status == (
        "filled"
    )


# Final review: a hook that breaks never strands a live order.


class Busy(Exception):
    """A database hiccup (SQLite busy) in a hook."""


def test_a_stop_check_that_breaks_counts_as_not_asked(deps, factory):
    deps.tasty.close = "work"
    request = close_request(deps, factory)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise Busy("database is locked")
        return False

    out = chase(deps, request, D("-1.00"), PLAN, lambda p, s: p - s, should_stop=flaky)
    assert out.status == "missed"  # the chase carried on to its last try
    assert len([c for c in deps.tasty.calls if c[0] == "replace"]) == 2
    assert deps.tasty.calls[-1][0] == "cancel"


def test_recording_an_order_is_tried_twice(deps, factory):
    deps.tasty.close = "work"
    request = close_request(deps, factory)
    seen, calls = [], {"n": 0}

    def flaky(order_id):
        calls["n"] += 1
        if calls["n"] == 1:
            raise Busy("database is locked")
        seen.append(order_id)

    out = chase(deps, request, D("-1.00"), PLAN, lambda p, s: p - s, on_order=flaky)
    assert out.status == "missed" and len(seen) == 3  # the send and both replaces


def test_an_order_that_cant_be_recorded_is_cancelled_and_confirmed(deps, factory):
    deps.tasty.close = "work"
    request = close_request(deps, factory)

    def broken(order_id):
        raise Busy("database is locked")

    out = chase(deps, request, D("-1.00"), PLAN, lambda p, s: p - s, on_order=broken)
    sent = [c[1] for c in deps.tasty.calls if c[0] == "place" and len(c[2]["legs"]) == 2]
    assert ("cancel", sent[0]) in deps.tasty.calls
    assert not [c for c in deps.tasty.calls if c[0] == "replace"]
    assert out.status == "missed" and out.order.status is OrderStatus.CANCELLED
    assert "record" in out.message


def test_a_replace_that_cant_be_recorded_is_cancelled(deps, factory):
    deps.tasty.close = "work"
    request = close_request(deps, factory)
    seen = []

    def breaks_after_the_send(order_id):
        if seen:
            raise Busy("database is locked")
        seen.append(order_id)

    out = chase(deps, request, D("-1.00"), PLAN, lambda p, s: p - s, on_order=breaks_after_the_send)
    replaced = [c[2] for c in deps.tasty.calls if c[0] == "replace"]
    assert len(replaced) == 1 and ("cancel", replaced[0]) in deps.tasty.calls
    assert out.status == "missed"


def test_an_unrecorded_order_whose_cancel_isnt_confirmed_is_unclear(deps, factory):
    deps.tasty.close = "work"
    request = close_request(deps, factory)
    real_place = deps.tasty.place_order

    def stuck(account, body):
        oid = real_place(account, body)
        deps.tasty.stuck.add(oid)
        return oid

    deps.tasty.place_order = stuck

    def broken(order_id):
        raise Busy("database is locked")

    out = chase(deps, request, D("-1.00"), PLAN, lambda p, s: p - s, on_order=broken)
    assert out.status == "unclear"


def test_a_cancel_is_read_back_again_before_it_counts_as_live(deps, factory):
    deps.tasty.close = "work"
    request = close_request(deps, factory)
    real_cancel, real_get = deps.tasty.cancel_order, deps.tasty.get_order
    late = set()

    def slow_cancel(account, order_id):
        late.add(order_id)  # the broker takes a moment to cancel it

    def get(account, order_id):
        found = real_get(account, order_id)
        if order_id in late:
            late.discard(order_id)
            real_cancel(account, order_id)
        return found

    deps.tasty.cancel_order, deps.tasty.get_order = slow_cancel, get
    waits = []
    deps.sleep = waits.append
    out = chase(deps, request, D("-1.00"), replace(PLAN, max_tries=1), lambda p, s: p,
                should_stop=lambda: True)  # fmt: skip
    assert out.status == "stopped" and out.order.status is OrderStatus.CANCELLED
    assert waits == [5, 1]  # the chase's wait, then a second read 1 s later


# Issue #198 (Tracker2 e7ab1a95): the broker cancels the close at once.


def dead(deps, oid, **changes):
    """The order gone at the broker with nothing filled."""
    o = deps.tasty.orders[oid]
    legs = tuple(replace(leg, remaining=leg.quantity, fill_price=None) for leg in o.legs)
    deps.tasty.orders[oid] = replace(o, legs=legs, **changes)


def cancelled_at_once(deps, how_many=1, status=OrderStatus.CANCELLED):
    """The broker kills the first `how_many` closing sends the moment they arrive."""
    real_place = deps.tasty.place_order
    left = {"n": how_many}

    def place(account, body):
        if not (len(body["legs"]) == 2 and left["n"]):
            return real_place(account, body)
        left["n"] -= 1
        behaviour, deps.tasty.close = deps.tasty.close, "work"  # nothing of it fills
        try:
            oid = real_place(account, body)
        finally:
            deps.tasty.close = behaviour
        dead(deps, oid, status=status)
        return oid

    deps.tasty.place_order = place


def close_sends(deps):
    return [c for c in deps.tasty.calls if c[0] == "place" and len(c[2]["legs"]) == 2]


def test_a_close_the_broker_cancels_at_once_is_sent_again_once(deps, factory):
    request = close_request(deps, factory)
    cancelled_at_once(deps)
    seen = []
    out = chase(deps, request, D("-1.00"), PLAN, lambda p, s: p - s, on_order=seen.append)
    sent = close_sends(deps)
    assert out.status == "filled" and out.order.broker_order_id == sent[1][1]
    assert len(sent) == 2 and sent[1][2]["price"] == sent[0][2]["price"]  # at the same price
    assert seen == [sent[0][1], sent[1][1]]  # the new order is recorded too
    assert any("sent it again" in str(e.get("event", "")) for e in out.log)


def test_the_retry_does_not_use_up_a_try(deps, factory):
    deps.tasty.close = "work"
    request = close_request(deps, factory)
    cancelled_at_once(deps)
    out = chase(deps, request, D("-1.00"), PLAN, lambda p, s: p - s)
    assert out.status == "missed"
    assert len(close_sends(deps)) == 2
    assert len([c for c in deps.tasty.calls if c[0] == "replace"]) == 2  # as without the retry


def test_a_close_cancelled_at_once_twice_is_not_sent_a_third_time(deps, factory):
    request = close_request(deps, factory)
    cancelled_at_once(deps, how_many=2)
    out = chase(deps, request, D("-1.00"), PLAN, lambda p, s: p - s)
    assert out.status == "missed" and len(close_sends(deps)) == 2
    assert not [c for c in deps.tasty.calls if c[0] == "replace"]


def test_a_rejected_close_is_not_sent_again(deps, factory):
    request = close_request(deps, factory)
    cancelled_at_once(deps, status=OrderStatus.REJECTED)
    out = chase(deps, request, D("-1.00"), PLAN, lambda p, s: p - s)
    assert out.status == "missed" and len(close_sends(deps)) == 1


def test_a_close_cancelled_after_the_first_read_is_not_sent_again(deps, factory):
    deps.tasty.close = "work"
    request = close_request(deps, factory)
    real_get = deps.tasty.get_order
    reads = {"n": 0}

    def get(account, order_id):
        reads["n"] += 1
        if reads["n"] == 2:  # the replacement dies on its first read
            o = deps.tasty.orders[order_id]
            deps.tasty.orders[order_id] = replace(o, status=OrderStatus.CANCELLED)
        return real_get(account, order_id)

    deps.tasty.get_order = get
    out = chase(deps, request, D("-1.00"), PLAN, lambda p, s: p - s)
    assert out.status == "missed" and len(close_sends(deps)) == 1


def test_a_close_replaced_from_the_broker_app_is_not_sent_again(deps, factory):
    request = close_request(deps, factory)
    real_place = deps.tasty.place_order

    def place(account, body):
        oid = real_place(account, body)
        if len(body["legs"]) == 2:
            dead(deps, oid, status=OrderStatus.CANCELLED, replaced_by="999")
        return oid

    deps.tasty.place_order = place
    out = chase(deps, request, D("-1.00"), PLAN, lambda p, s: p - s)
    assert out.status == "missed" and len(close_sends(deps)) == 1


def test_a_close_cancelled_at_once_after_the_session_is_not_sent_again(deps, factory):
    request = close_request(deps, factory)
    cancelled_at_once(deps)
    deps.now = lambda: OPEN_TIME.replace(hour=20, minute=20)  # 16:20 New York
    out = chase(deps, request, D("-1.00"), PLAN, lambda p, s: p - s)
    assert out.status == "missed" and len(close_sends(deps)) == 1


def test_a_retry_the_central_check_refuses_ends_missed(deps, factory, monkeypatch):
    request = close_request(deps, factory)
    cancelled_at_once(deps)
    real_submit = orders.submit
    sends = {"n": 0}

    def submit(*args, **kwargs):
        sends["n"] += 1
        if sends["n"] == 2:
            return orders.Result("refuse", False, "Stop all bots is on.")
        return real_submit(*args, **kwargs)

    monkeypatch.setattr(orders, "submit", submit)
    out = chase(deps, request, D("-1.00"), PLAN, lambda p, s: p - s)
    assert out.status == "missed" and "Stop all bots" in out.message
    assert out.order.status is OrderStatus.CANCELLED and len(close_sends(deps)) == 1


# Issue #199 (Tracker2 8dce236f): the replacement is rejected because the old order filled.


def old_fills_on_replace(deps, old_status=OrderStatus.FILLED):
    """TastyTrade accepts the replace, then rejects the new order: "Received a fill on the
    original order"."""
    real_replace = deps.tasty.replace_order

    def replace_order(account, order_id, body):
        new = real_replace(account, order_id, body)
        if old_status is OrderStatus.FILLED:
            deps.tasty.fill(order_id)
        else:
            deps.tasty.orders[order_id] = replace(deps.tasty.orders[order_id], status=old_status)
        deps.tasty.orders[new] = replace(deps.tasty.orders[new], status=OrderStatus.REJECTED)
        return new

    deps.tasty.replace_order = replace_order


def test_a_rejected_replacement_reports_the_old_orders_fill(deps, factory):
    deps.tasty.close = "work"
    request = close_request(deps, factory)
    old_fills_on_replace(deps)
    out = chase(deps, request, D("-1.00"), PLAN, lambda p, s: p - s)
    old = close_sends(deps)[0][1]
    assert out.status == "filled" and out.order.broker_order_id == old
    assert ("cancel", old) not in deps.tasty.calls  # the re-read is read-only
    assert len([c for c in deps.tasty.calls if c[0] == "replace"]) == 1


def test_a_rejected_replacement_of_a_dead_order_is_still_missed(deps, factory):
    deps.tasty.close = "work"
    request = close_request(deps, factory)
    old_fills_on_replace(deps, old_status=OrderStatus.CANCELLED)
    out = chase(deps, request, D("-1.00"), PLAN, lambda p, s: p - s)
    assert out.status == "missed" and out.order.status is OrderStatus.REJECTED


def test_an_old_order_that_cant_be_read_again_leaves_the_chase_missed(deps, factory):
    deps.tasty.close = "work"
    request = close_request(deps, factory)
    old_fills_on_replace(deps)
    real_get = deps.tasty.get_order

    def get(account, order_id):
        replaced = [c[1] for c in deps.tasty.calls if c[0] == "replace"]
        if order_id in replaced:
            raise TimeoutError("read timed out")
        return real_get(account, order_id)

    deps.tasty.get_order = get
    out = chase(deps, request, D("-1.00"), PLAN, lambda p, s: p - s)
    assert out.status == "missed" and out.order.status is OrderStatus.REJECTED
