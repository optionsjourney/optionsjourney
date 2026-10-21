"""Bots slice 15: a Trade page ticket sent as a bot's entry (Tracker2's
analyzer_entry.run_analyzer_entry). Every broker is a fake; nothing live is sent."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from optionsjourney.brokers.http import BrokerError
from optionsjourney.core.symbols import parse_occ
from optionsjourney.db.models import Bot, BotExit, BotRun
from optionsjourney.services import bot_entry, bot_store, bot_ticket, orders
from optionsjourney.trading.orders import plain
from tests.services.bot_fakes import (
    FAR,
    NEAR,
    OPEN_TIME,
    FakeChain,
    FakeHub,
    FakeTasty,
    make,
    make_calendar,
    make_shape,
    mid_for,
    sym,
)

D = Decimal
ACCOUNT = "5WX01234"
TOKEN = "0123456789ab"
CONDOR = [(5910, "call", "sell"), (5985, "call", "buy"), (5690, "put", "sell"),
          (5615, "put", "buy")]  # fmt: skip


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
    return d


def dx(strike, right, expiration="2026-10-13"):
    """The page's DXLink symbol for an SPXW option."""
    day = expiration.replace("-", "")[2:]
    return f".SPXW{day}{right[0].upper()}{strike}"


def quote(deps, symbol, at=OPEN_TIME):
    _, details = parse_occ(symbol)
    mid = mid_for(details.strike, details.put_call.lower(), details.expiration.isoformat())
    deps.hub.set(symbol, mid - D("0.05"), mid + D("0.05"), at)


def ticket(legs=CONDOR, quantity=1, expiration="2026-10-13", token=TOKEN, bot_id=None, **extra):
    """A ticket as the page posts it, its legs quoted in the hub by the test."""
    rows = []
    for item in legs:
        strike, right, side, *rest = item
        rows.append({"stream_symbol": dx(strike, right, rest[0] if rest else expiration),
                     "side": side, "ratio": 1})  # fmt: skip
    return {"legs": rows, "quantity": quantity, "token": token, "bot_id": bot_id, **extra}


def quoted(deps, body):
    from optionsjourney.core.chain import occ_from_dxlink

    for leg in body["legs"]:
        quote(deps, occ_from_dxlink(leg["stream_symbol"]))
    return body


def review_code(deps, body, account=ACCOUNT):
    """A passed review of the ticket, as Review order keeps it (trade_ticket._keep)."""
    from optionsjourney.core.chain import occ_from_dxlink
    from optionsjourney.services import trade_ticket
    from optionsjourney.trading.orders import Leg, Order

    legs = tuple(Leg(occ_from_dxlink(leg["stream_symbol"]), leg["side"], leg["ratio"])
                 for leg in body["legs"])  # fmt: skip
    order = Order(legs, body["quantity"], True, price=D("1.00"))
    return trade_ticket._keep("tastytrade", account, order, deps.now())


def send(deps, body, account=ACCOUNT):
    """Send as the bot, with a fresh review of the ticket unless the body names one."""
    if "code" not in body:
        body = {**body, "code": review_code(deps, body, account)}
    return bot_ticket.enter(deps, "tastytrade", account, body)


def runs(factory):
    with factory() as session:
        return list(session.scalars(select(BotRun).order_by(BotRun.id)))


def exits_of(factory, run_id):
    with factory() as session:
        rows = session.scalars(select(BotExit).where(BotExit.run_id == run_id).order_by(BotExit.id))
        return [(e.kind, e.state, e.trigger, e.limit, e.group_position) for e in rows]


def openings(client):
    return [c for c in client.calls if c[0] == "place"
            and c[2]["legs"][0]["action"].endswith("Open")]  # fmt: skip


# --- The picker ---------------------------------------------------------------------------


def test_the_picker_offers_matching_live_bots_and_says_why_not(deps, factory):
    good = make(factory, live=True)
    preview = make(factory, live=False)
    spread = make_shape(factory, "put_credit_spread", live=True, stop=D("95"))
    problem, offered = bot_ticket.choices(deps, "tastytrade", ACCOUNT, ticket())
    assert problem is None
    why = {bot.id: (reason, lines) for bot, reason, lines in offered}
    assert why[good][0] is None
    # The pairing the bot will use, with each group's exits
    assert why[good][1] == [
        "Call side: sell 5910C Oct 13 / buy 5985C Oct 13 · stop 95% · no target",
        "Put side: sell 5690P Oct 13 / buy 5615P Oct 13 · stop 95% · no target",
    ]
    assert why[spread][1] == ["Put spread: stop 95% · no target"]
    assert "Paper bot" in why[preview][0]
    assert "doesn't match" in why[spread][0]
    assert deps.tasty.calls == [] and runs(factory) == []


def test_the_picker_lists_only_the_tickets_account(deps, factory):
    bot_id = make(factory, live=True)
    _, offered = bot_ticket.choices(deps, "tastytrade", "5WX09999", ticket())
    assert bot_id not in [bot.id for bot, _, _ in offered]


def test_an_unreadable_ticket_says_why(deps, factory):
    problem, offered = bot_ticket.choices(deps, "tastytrade", ACCOUNT, {"legs": []})
    assert problem == "Pick a bid (sell) or ask (buy) first." and offered == []


# --- The send -----------------------------------------------------------------------------


def test_a_ticket_condor_fills_and_gets_the_bots_exits(deps, factory):
    # Review focus 3: the exits a scheduled entry of the bot gets on these strikes (they
    # are the ones it picks on the fake chain): each short filled at 2.50, a stop at 95%
    # above it, 4.875 -> 4.80 on the $0.10 tick, resting at the broker (as
    # test_bot_exits' first test).
    expected = [("stop", "working", D("4.80"), None, 0), ("stop", "working", D("4.80"), None, 1)]
    bot_id = make(factory, live=True)
    sent = send(deps, quoted(deps, ticket(bot_id=bot_id)))
    assert sent.ok, sent.message
    run = next(r for r in runs(factory) if r.id == sent.run_id)
    assert run.status == "filled" and run.live and run.key == f"ticket-{bot_id}-{TOKEN}"
    assert run.contracts == 1 and run.broker_order_id is not None
    assert run.chase_log[0]["event"] == bot_ticket.FROM_TICKET
    assert exits_of(factory, run.id) == expected
    assert len(openings(deps.tasty)) == 1
    assert run.id not in bot_entry.ACTIVE


def test_a_second_send_with_the_same_token_places_nothing(deps, factory):
    # Review focus 1: the claim comes first; a second press finds it taken.
    bot_id = make(factory, live=True)
    body = quoted(deps, ticket(bot_id=bot_id))
    first = send(deps, body)
    assert first.ok, first.message
    again = send(deps, body)
    assert not again.ok and again.run_id == first.run_id and "already sent" in again.message
    assert len(openings(deps.tasty)) == 1 and len(runs(factory)) == 1


def test_a_preview_mode_bot_is_refused_and_nothing_is_recorded(deps, factory):
    # Review focus 2
    bot_id = make(factory, live=False)
    sent = send(deps, quoted(deps, ticket(bot_id=bot_id)))
    assert not sent.ok and "Paper bot" in sent.message
    assert runs(factory) == [] and deps.tasty.calls == []


@pytest.mark.parametrize("token", [None, "", "short", "0123456789AB", "0123456789abc"])
def test_a_bad_token_is_refused(deps, factory, token):
    bot_id = make(factory, live=True)
    sent = send(deps, quoted(deps, ticket(bot_id=bot_id, token=token)))
    assert not sent.ok and "Review" in sent.message
    assert runs(factory) == [] and deps.tasty.calls == []


@pytest.mark.parametrize("switch, words", [("freeze", "frozen"), ("no_new_trades", "No new")])
def test_either_switch_refuses_before_the_claim(deps, factory, switch, words):
    bot_id = make(factory, live=True)
    orders.set_switch(factory, switch, True, OPEN_TIME)
    sent = send(deps, quoted(deps, ticket(bot_id=bot_id)))
    assert not sent.ok and words in sent.message
    assert runs(factory) == [] and deps.tasty.calls == []


def test_outside_the_session_is_refused(deps, factory):
    bot_id = make(factory, live=True)
    deps.now = lambda: datetime(2026, 10, 13, 21, 0, tzinfo=UTC)  # 17:00 New York
    sent = send(deps, ticket(bot_id=bot_id))
    assert not sent.ok and "isn't open" in sent.message and runs(factory) == []


def test_over_the_bots_limits_is_refused(deps, factory):
    bot_id = make(factory, live=True)  # 1 per trade
    sent = send(deps, quoted(deps, ticket(bot_id=bot_id, quantity=2)))
    assert not sent.ok and "per trade" in sent.message
    with factory() as session:
        row = session.get(Bot, bot_id)
        row.max_per_trade, row.max_per_day = 5, 1
        session.commit()
    first = send(deps, quoted(deps, ticket(bot_id=bot_id)))
    assert first.ok
    deps.tasty.held.clear()
    later = send(deps, quoted(deps, ticket(bot_id=bot_id, token="ba9876543210")))
    assert not later.ok and "a day" in later.message
    assert len(runs(factory)) == 1


def test_blank_limits_hold_a_trade_page_entry_to_the_ceiling_only(deps, factory):
    """Decision 0058: with both limits blank, a Trade page entry keeps the size you
    reviewed, and doesn't use up the bot's worked-out day."""
    bot_id = make(factory, live=True, max_per_trade=None, max_per_day=None)
    first = send(deps, quoted(deps, ticket(bot_id=bot_id, quantity=2)))
    assert first.ok, first.message
    deps.tasty.held.clear()
    later = send(deps, quoted(deps, ticket(bot_id=bot_id, quantity=2, token="ba9876543210")))
    assert later.ok, later.message


def test_a_bot_at_its_max_open_trades_is_refused(deps, factory):
    bot_id = make(factory, live=True)
    bot = bot_store.load(factory, bot_id)
    # A condor the bot splits is two open trades (caps.open_trades)
    bot_store.save(factory, replace(bot, max_open_trades=2, max_per_day=3), OPEN_TIME)
    bot_store.set_live(factory, bot_id, True, OPEN_TIME)
    assert send(deps, quoted(deps, ticket(bot_id=bot_id))).ok
    assert bot_entry._open_trades(deps, bot_store.load(factory, bot_id)) == 2
    deps.tasty.held.clear()
    later = send(deps, quoted(deps, ticket(bot_id=bot_id, token="ba9876543210")))
    assert not later.ok and "Max open trades" in later.message


def test_a_timed_close_already_due_is_refused(deps, factory):
    bot_id = make(factory, live=True)
    bot = bot_store.load(factory, bot_id)
    from optionsjourney.bots.definition import EntryTimeDef

    changed = replace(bot, close_time="09:45", entry_times=(EntryTimeDef("09:40"),))
    assert bot_store.save(factory, changed, OPEN_TIME)[1] == {}
    bot_store.set_live(factory, bot_id, True, OPEN_TIME)
    sent = send(deps, quoted(deps, ticket(bot_id=bot_id)))
    assert not sent.ok and "closed as soon as it's in" in sent.message
    assert runs(factory) == []


def test_a_ticket_that_would_close_a_held_position_sends_nothing(deps, factory):
    bot_id = make(factory, live=True)
    body = quoted(deps, ticket(bot_id=bot_id))
    deps.tasty.held[plain(sym(5910, "call"))] = 1  # selling it would close it
    sent = send(deps, body)
    assert sent.ok  # claimed: the positions are read after the claim
    (run,) = runs(factory)
    assert run.status == "error" and "position you hold" in run.reason
    assert openings(deps.tasty) == []
    assert [a.title for a in deps.alerts if "not sent" in a.title] == [
        "IC: entry from the Trade page not sent"
    ]


def test_unreadable_positions_send_nothing(deps, factory):
    bot_id = make(factory, live=True)
    deps.tasty.errors["positions"] = BrokerError("down")
    send(deps, quoted(deps, ticket(bot_id=bot_id)))
    (run,) = runs(factory)
    assert run.status == "error" and "positions" in run.reason
    assert openings(deps.tasty) == []


def test_a_leg_with_no_fresh_quote_sends_nothing(deps, factory):
    bot_id = make(factory, live=True)
    body = quoted(deps, ticket(bot_id=bot_id))
    deps.hub.quotes.pop(sym(5615, "put"))
    send(deps, body)
    (run,) = runs(factory)
    assert run.status == "skipped" and "No fresh quote" in run.reason
    assert openings(deps.tasty) == []


def test_a_bot_switched_to_preview_by_the_send_sends_only_a_preview(deps, factory):
    # Switched to preview between the checks and the send: preview_only, nothing live.
    bot_id = make(factory, live=True)

    def later(fn, *args):
        bot_store.set_live(factory, bot_id, False, OPEN_TIME)
        fn(*args)

    deps.background = later
    assert send(deps, quoted(deps, ticket(bot_id=bot_id))).ok
    assert openings(deps.tasty) == []
    (run,) = runs(factory)
    assert run.status == "preview" and not run.live


def test_a_calendar_ticket_into_a_calendar_bot_is_held_and_watched(deps, factory):
    bot_id = make_calendar(factory, live=True)
    legs = [(5700, "put", "sell", NEAR), (5700, "put", "buy", FAR)]
    sent = send(deps, quoted(deps, ticket(legs, bot_id=bot_id)))
    assert sent.ok, sent.message
    (run,) = runs(factory)
    assert run.status == "filled" and run.fill_credit == D("-3.50")
    assert [(leg["expiration"], leg["dte"]) for leg in run.legs] == [(NEAR, 7), (FAR, 14)]
    rows = exits_of(factory, run.id)
    assert ("stop", "watching", D("-1.75"), None, 0) in rows
    assert len(openings(deps.tasty)) == 1  # the entry; the stop rests nowhere: it's watched
    assert not [c for c in deps.tasty.calls if c[0] == "place" and c[2]["order-type"] == "Stop"]


def test_a_calendar_ticket_into_a_same_day_bot_is_refused(deps, factory):
    bot_id = make_shape(factory, "put_credit_spread", live=True, stop=D("95"))
    legs = [(5700, "put", "sell", NEAR), (5700, "put", "buy", FAR)]
    sent = send(deps, quoted(deps, ticket(legs, bot_id=bot_id)))
    assert not sent.ok and "same-day bot" in sent.message and runs(factory) == []


def test_a_ticket_on_another_account_is_refused(deps, factory):
    bot_id = make(factory, live=True)
    sent = send(deps, quoted(deps, ticket(bot_id=bot_id)), account="5WX09999")
    assert not sent.ok and "another account" in sent.message and runs(factory) == []


def test_an_entry_from_the_ticket_is_not_an_entry_today(deps, factory):
    bot_id = make(factory, live=True)
    assert send(deps, quoted(deps, ticket(bot_id=bot_id))).ok
    assert bot_entry._entries_today(deps, bot_id, OPEN_TIME.date(), run_id=-1) == 0
    assert bot_ticket.is_from_ticket(runs(factory)[0])


def test_a_scheduled_entry_picks_the_same_condor_and_exits(deps, factory):
    # The other half of review focus 3: the bot's own scheduled entry, on the same chain.
    bot_id = make(factory, live=True)
    run_id = bot_entry.fire(deps, bot_id, "09:47")
    with factory() as session:
        legs = session.get(BotRun, run_id).legs
    assert sorted((int(D(leg["strike"])), leg["right"], leg["side"]) for leg in legs) == sorted(
        CONDOR
    )
    assert exits_of(factory, run_id) == [
        ("stop", "working", D("4.80"), None, 0),
        ("stop", "working", D("4.80"), None, 1),
    ]


# --- Final review fixes -------------------------------------------------------------------

from datetime import timedelta  # noqa: E402

from optionsjourney.brokers.http import RequestUnclear  # noqa: E402
from optionsjourney.db.models import BotLeg  # noqa: E402
from optionsjourney.services import trade_ticket  # noqa: E402
from tests.services.bot_fakes import force_contracts  # noqa: E402


def test_the_bot_send_uses_up_the_review(deps, factory):
    bot_id = make(factory, live=True)
    body = quoted(deps, ticket(bot_id=bot_id))
    code = review_code(deps, body)
    assert send(deps, {**body, "code": code}).ok
    # Neither Send nor another bot send can use it again
    assert trade_ticket._take(code, "tastytrade", ACCOUNT, OPEN_TIME) is None
    again = send(deps, {**body, "code": code, "token": "ba9876543210"})
    assert not again.ok and again.message == bot_ticket.REVIEW_USED
    assert len(openings(deps.tasty)) == 1


def test_a_bot_send_needs_a_review_of_this_ticket(deps, factory):
    bot_id = make(factory, live=True)
    body = quoted(deps, ticket(bot_id=bot_id))
    sent = send(deps, {**body, "code": "nope"})
    assert not sent.ok and sent.message == bot_ticket.REVIEW_USED
    other = review_code(deps, {**body, "quantity": 2})
    sent = send(deps, {**body, "code": other})
    assert not sent.ok and sent.message == bot_ticket.REVIEW_CHANGED
    assert runs(factory) == [] and deps.tasty.calls == []


def capped(factory, bot_id, most=2):
    bot = bot_store.load(factory, bot_id)
    bot_store.save(factory, replace(bot, max_open_trades=most, max_per_day=5), OPEN_TIME)
    bot_store.set_live(factory, bot_id, True, OPEN_TIME)


def test_an_entry_still_being_entered_counts_toward_max_open_trades(deps, factory):
    bot_id = make(factory, live=True)
    capped(factory, bot_id)  # a split condor is 2 open trades
    later = []
    deps.background = lambda fn, *args: later.append((fn, args))
    first = send(deps, quoted(deps, ticket(bot_id=bot_id)))
    assert first.ok
    second = send(deps, quoted(deps, ticket(bot_id=bot_id, token="ba9876543210")))
    assert not second.ok and f"run {first.run_id} is still being entered" in second.message
    fn, args = later.pop()
    fn(*args)
    assert len(openings(deps.tasty)) == 1


def test_two_claims_that_both_passed_send_only_the_first(deps, factory, monkeypatch):
    # Two tabs pressed at once: both passed the check before either claimed. The re-check
    # in the background lets the earlier claim go ahead and ends the later one.
    bot_id = make(factory, live=True)
    capped(factory, bot_id)
    real = bot_ticket._cap_problem

    def recheck_only(d, b, run_id=None):
        return None if run_id is None else real(d, b, run_id)

    monkeypatch.setattr(bot_ticket, "_cap_problem", recheck_only)
    later = []
    deps.background = lambda fn, *args: later.append((fn, args))
    first = send(deps, quoted(deps, ticket(bot_id=bot_id)))
    second = send(deps, quoted(deps, ticket(bot_id=bot_id, token="ba9876543210")))
    assert first.ok and second.ok
    for fn, args in reversed(later):  # the later claim's thread runs first
        fn(*args)
    by_id = {r.id: r for r in runs(factory)}
    assert by_id[first.run_id].status == "filled"
    assert by_id[second.run_id].status == "error"
    assert f"run {first.run_id} is still being entered" in by_id[second.run_id].reason
    assert len(openings(deps.tasty)) == 1


def test_a_refused_order_alerts_once(deps, factory):
    bot_id = make(factory, live=True)
    deps.tasty.errors["place"] = BrokerError("rejected")
    send(deps, quoted(deps, ticket(bot_id=bot_id)))
    (run,) = runs(factory)
    titles = [a.title for a in deps.alerts]
    assert run.status in ("error", "missed") and len(titles) == 1, titles


def test_a_partly_filled_ticket_entry_becomes_tranches_each_protected(deps, factory):
    bot_id = make(factory, live=True)
    force_contracts(factory, bot_id, 2)
    deps.tasty.fills = [1, 1]  # the first order fills 1 unit, the next the other
    send(deps, quoted(deps, ticket(bot_id=bot_id, quantity=2)))
    root, child = runs(factory)
    assert child.key == f"{root.key}#t2" and child.parent_run_id == root.id
    assert (root.status, root.contracts, child.status, child.contracts) == (
        "filled",
        1,
        "filled",
        1,
    )
    for run in (root, child):
        assert [row[:2] for row in exits_of(factory, run.id)] == [("stop", "working")] * 2
    assert not bot_entry.ACTIVE


def test_an_unclear_send_is_left_for_recovery_and_alerted_once(deps, factory):
    bot_id = make(factory, live=True)
    deps.tasty.errors["place"] = RequestUnclear("Lost the connection")
    send(deps, quoted(deps, ticket(bot_id=bot_id)))
    (run,) = runs(factory)
    assert run.status == "running" and "recovery" in run.reason
    titles = [a.title for a in deps.alerts]
    assert titles.count("IC: entry unclear") == 1 and not [t for t in titles if "not sent" in t]
    assert run.id not in bot_entry.ACTIVE


def test_an_exception_inside_the_entry_finishes_the_run(deps, factory, monkeypatch):
    bot_id = make(factory, live=True)

    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(bot_entry, "enter_picked", broken)
    send(deps, quoted(deps, ticket(bot_id=bot_id)))
    (run,) = runs(factory)
    assert run.status == "error" and openings(deps.tasty) == []
    assert [a.title for a in deps.alerts] == ["IC: entry failed"]
    assert not bot_entry.ACTIVE


def test_a_raise_before_the_entry_never_leaves_the_claim_pending(deps, factory, monkeypatch):
    bot_id = make(factory, live=True)

    def broken(*args):
        raise RuntimeError("boom")

    def later(fn, *args):
        monkeypatch.setattr(bot_ticket.rules, "pair", broken)  # the reload in _send raises
        fn(*args)

    deps.background = later
    assert send(deps, quoted(deps, ticket(bot_id=bot_id))).ok
    (run,) = runs(factory)
    assert run.status == "error" and run.reason == bot_ticket.COULDNT
    assert not bot_entry.ACTIVE and openings(deps.tasty) == []
    assert [a.title for a in deps.alerts] == ["IC: entry from the Trade page not sent"]


def test_a_bot_deleted_by_the_send_sends_nothing(deps, factory, monkeypatch):
    bot_id = make(factory, live=True)

    def later(fn, *args):
        monkeypatch.setattr(bot_ticket.bot_store, "load", lambda f, i: None)
        fn(*args)

    deps.background = later
    assert send(deps, quoted(deps, ticket(bot_id=bot_id))).ok
    (run,) = runs(factory)
    assert run.status == "error" and "gone" in run.reason and openings(deps.tasty) == []
    assert len(deps.alerts) == 1


def test_a_bot_edited_by_the_send_sends_nothing(deps, factory):
    bot_id = make(factory, live=True)

    def later(fn, *args):
        with factory() as session:
            leg = session.query(BotLeg).filter_by(bot_id=bot_id, position=0).one()
            leg.right = "put"  # the call side now has a put: the ticket no longer matches
            session.commit()
        fn(*args)

    deps.background = later
    assert send(deps, quoted(deps, ticket(bot_id=bot_id))).ok
    (run,) = runs(factory)
    assert run.status == "error" and run.reason == bot_ticket.CHANGED
    assert openings(deps.tasty) == [] and not bot_entry.ACTIVE


def test_freeze_by_the_send_skips_before_the_order(deps, factory):
    bot_id = make(factory, live=True)

    def later(fn, *args):
        orders.set_switch(factory, "freeze", True, OPEN_TIME)
        fn(*args)

    deps.background = later
    send(deps, quoted(deps, ticket(bot_id=bot_id)))
    (run,) = runs(factory)
    assert run.status == "skipped" and "frozen" in run.reason and openings(deps.tasty) == []


def test_a_claim_a_crash_left_pending_is_settled(deps, factory):
    bot_id = make(factory, live=True)
    old = OPEN_TIME - timedelta(minutes=5)
    with factory() as session:
        session.add(BotRun(bot_id=bot_id, key=f"ticket-{bot_id}-aaaaaaaaaaaa",
                           trading_date=OPEN_TIME.date(), entry_time="09:42", status="pending",
                           live=True, account_number=ACCOUNT, legs=[], filled_legs=[],
                           chase_log=[], contracts=0, created_at=old, updated_at=old))  # fmt: skip
        session.commit()
    assert send(deps, quoted(deps, ticket(bot_id=bot_id))).ok
    stuck = next(r for r in runs(factory) if r.key.endswith("aaaaaaaaaaaa"))
    assert stuck.status == "skipped" and "nothing was sent" in stuck.reason


def test_a_scheduled_entry_counts_a_ticket_entry_being_entered(deps, factory):
    # A scheduled entry and a ticket can't pass Max open trades together.
    bot_id = make(factory, live=True)
    capped(factory, bot_id)
    later = []
    deps.background = lambda fn, *args: later.append((fn, args))
    first = send(deps, quoted(deps, ticket(bot_id=bot_id)))
    assert first.ok
    run_id = bot_entry.fire(deps, bot_id, "09:47")
    run = next(r for r in runs(factory) if r.id == run_id)
    said = f"run {first.run_id} (from the Trade page or Trade now)"
    assert run.status == "skipped" and said in run.reason
    assert openings(deps.tasty) == []


def test_a_ticket_not_in_lowest_terms_is_refused_before_the_claim(deps, factory):
    """Final review (slice 17): the bot sends units x its own ratios, so a reviewed 1 x
    (2/2) would go out as 2 x (1/1). Refused unless the units and every leg's ratio are
    what was reviewed; nothing is recorded or sent."""
    bot_id = make_shape(factory, "put_credit_spread", live=True, stop=D("95"))
    with factory() as session:  # room for 2 contracts, so only the ratios refuse it
        session.get(Bot, bot_id).max_per_trade, session.get(Bot, bot_id).max_per_day = 2, 4
        session.commit()
    body = ticket(legs=[(5690, "put", "sell"), (5615, "put", "buy")], bot_id=bot_id)
    body["legs"] = [dict(leg, ratio=2) for leg in body["legs"]]
    sent = send(deps, quoted(deps, body))
    assert not sent.ok and sent.message == bot_ticket.NOT_AS_REVIEWED
    assert runs(factory) == [] and openings(deps.tasty) == []


def test_a_one_by_two_ticket_passes_the_ratio_check(deps, factory):
    """The ratio check refuses only a size the bot would change: a 1x2x1 butterfly into a
    1x2x1 bot is sent as reviewed, 1 unit with the body at 2."""
    bot_id = make_shape(factory, "long_call_butterfly", live=True)
    body = ticket(legs=[(5825, "call", "buy"), (5900, "call", "sell"), (5975, "call", "buy")],
                  bot_id=bot_id)  # fmt: skip
    body["legs"][1]["ratio"] = 2
    sent = send(deps, quoted(deps, body))
    assert sent.ok, sent.message
    ((_, _, order),) = openings(deps.tasty)
    assert sorted(int(leg["quantity"]) for leg in order["legs"]) == [1, 1, 2]


def test_a_resting_entry_limit_may_be_either_side():
    from optionsjourney.bots.definition import iron_condor_skeleton
    from optionsjourney.trading.orders import Leg, Order, Side

    bot = iron_condor_skeleton("IC")
    legs = (Leg("SPXW  261013P05690000", Side.SELL), Leg("SPXW  261013P05615000", Side.BUY))

    def problem(price):
        return bot_ticket._limit_problem(bot, Order(legs, 1, True, price=D(price)))

    assert problem("-1.00") is None and problem("1.00") is None
    assert problem("0.03") == "The credit is below $0.05; not entered."
    assert problem("-0.03") == "The debit is below $0.05; not entered."
