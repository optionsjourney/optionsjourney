from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from optionsjourney.bots.definition import EntryTimeDef
from optionsjourney.brokers.http import BrokerError, RequestUnclear
from optionsjourney.db.models import Bot, BotChase, BotEntryTime, BotExit, BotRun, OrderLog
from optionsjourney.services import bot_entry, bot_exits, bot_loops, bot_store, orders
from optionsjourney.trading.orders import BrokerLeg, BrokerOrder, OrderStatus
from tests.services.bot_fakes import (
    OPEN_TIME,
    FakeChain,
    FakeHub,
    FakeSchwab,
    FakeTasty,
    force_contracts,
    make,
    make_schwab,
    mid_for,
    stream_chain,
    sym,
)

D = Decimal


@pytest.fixture
def deps(factory, monkeypatch):
    tasty = FakeTasty()
    placed = []
    monkeypatch.setattr(bot_entry, "place_protections", lambda deps, run_id: placed.append(run_id))
    d = bot_entry.BotDeps(
        factory,
        lambda: [tasty],
        FakeHub(),
        FakeChain(),
        notify=lambda e: None,
        sleep=lambda s: None,
        now=lambda: OPEN_TIME,
        background=lambda fn, *args: fn(*args),
    )
    d.tasty, d.placed = tasty, placed
    stream_chain(d.hub)  # a Paper bot's pretend fill reads the stream
    return d


def run_of(factory, run_id):
    with factory() as session:
        return session.get(BotRun, run_id)


def test_a_paper_run_picks_strikes_checks_and_pretend_fills(deps, factory):
    run_id = bot_entry.fire(deps, make(factory), "09:47")
    run = run_of(factory, run_id)
    assert run.status == "filled" and run.paper and not run.live
    assert [leg["strike"] for leg in run.legs] == ["5910", "5985", "5690", "5615"]
    assert [c[0] for c in deps.tasty.calls] == ["preview"]
    assert deps.placed == []


def test_a_run_records_the_broker_it_fired_at(deps, factory):
    run_id = bot_entry.fire(deps, make(factory, live=True), "09:47")
    assert run_of(factory, run_id).broker == "tastytrade"


def test_a_second_fire_of_the_same_time_does_nothing(deps, factory):
    bot_id = make(factory)
    assert bot_entry.fire(deps, bot_id, "09:47") is not None
    assert bot_entry.fire(deps, bot_id, "09:47") is None


def test_a_live_run_fills_and_hands_off_to_exits(deps, factory):
    run_id = bot_entry.fire(deps, make(factory, live=True), "09:47")
    run = run_of(factory, run_id)
    assert run.status == "filled" and run.fill_credit is not None and run.broker_order_id
    assert len(run.filled_legs) == 4 and run.contracts == 1
    assert deps.placed == [run_id]
    with factory() as session:
        assert (
            session.scalar(select(OrderLog.bot_run_id).where(OrderLog.action == "send")) == run_id
        )


# --- #205: a live entry re-reads its legs' quotes before it is priced (Tracker2 dc8dcad6)

IC_PUT = "SPXW  261013P05690000"  # the iron condor's short put on bot_fakes.rows (mid 2.50)


def _tasty_rest(deps, quotes, calls=None):
    """TastyTrade's REST market data: {symbol: (bid, ask)} for the options asked."""

    def market_data(by_type):
        if calls is not None:
            calls.append(by_type)
        wanted = by_type.get("equity-option", [])
        return [{"symbol": s, "bid": str(b), "ask": str(a)} for s, (b, a) in quotes.items()
                if s in wanted]  # fmt: skip

    deps.tasty.market_data = market_data


IC_LEGS = ((5910, "call"), (5985, "call"), (5690, "put"), (5615, "put"))


def _fair():
    """Each leg's own quote on the chain (bot_fakes.mid_for ± 0.05)."""
    return {sym(k, r): (mid_for(k, r) - D("0.05"), mid_for(k, r) + D("0.05")) for k, r in IC_LEGS}


def _chain_target(factory, bot_id):
    """The run's target from the chain's mids: 2.50 - 0.05 + 2.50 - 0.05, less the offset."""
    return D("4.90") - bot_store.load(factory, bot_id).entry_chase.start_offset


def test_a_live_entry_prices_from_fresh_leg_quotes(deps, factory):
    bot_id = make(factory, live=True)
    _tasty_rest(deps, _fair() | {IC_PUT: (D("2.95"), D("3.15"))})
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert run.status == "filled"
    assert [leg["mid"] for leg in run.legs][2] == "3.05"
    assert run.target_credit == _chain_target(factory, bot_id) + D("0.55")
    notes = next(e["strikes"] for e in run.chase_log if "strikes" in e)
    assert notes == ["leg 3 requoted at entry: 2.95 x 3.15, mid 3.05 (the chain had 2.50)"]


def test_a_leg_without_a_fresh_quote_keeps_the_chains_prices(deps, factory):
    bot_id = make(factory, live=True)
    _tasty_rest(deps, {IC_PUT: (D("2.95"), D("3.15"))})  # the other three legs: nothing
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert run.status == "filled" and run.legs[2]["mid"] == "2.50"
    assert run.target_credit == _chain_target(factory, bot_id)
    assert not any("strikes" in e for e in run.chase_log)


def test_a_quote_outage_never_blocks_the_entry(deps, factory):
    bot_id = make(factory, live=True)

    def down(by_type):
        raise BrokerError("TastyTrade", "down")

    deps.tasty.market_data = down
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert run.status == "filled" and run.target_credit == _chain_target(factory, bot_id)


class _SchwabQuotes:
    """Schwab's REST quotes only (decision 0080: the backup when TastyTrade can't answer)."""

    def __init__(self, quotes):
        self.quotes_by, self.asked = quotes, []

    def quotes(self, symbols):
        self.asked.append(list(symbols))
        return {s: {"quote": {"bidPrice": float(b), "askPrice": float(a)}}
                for s, (b, a) in self.quotes_by.items() if s in symbols}  # fmt: skip


def test_schwab_answers_the_legs_tastytrade_did_not(deps, factory):
    bot_id = make(factory, live=True)
    _tasty_rest(deps, {s: q for s, q in _fair().items() if s != IC_PUT})
    schwab = _SchwabQuotes({IC_PUT: (D("2.95"), D("3.15"))})
    tasty = deps.tasty
    deps.clients = lambda: [tasty, schwab]
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert run.legs[2]["mid"] == "3.05"
    assert [IC_PUT] in schwab.asked


def test_a_paper_entry_is_not_requoted(deps, factory):
    calls = []
    _tasty_rest(deps, {IC_PUT: (D("2.95"), D("3.15"))}, calls)
    bot_entry.fire(deps, make(factory), "09:47")
    bot_entry.fire(deps, make(factory, live=True, enabled=False), None, manual=True)  # Preview
    assert calls == []


def test_a_live_run_chases_then_misses(deps, factory):
    deps.tasty.entry = "work"
    run_id = bot_entry.fire(deps, make(factory, live=True), "09:47")
    run = run_of(factory, run_id)
    assert run.status == "missed"
    replaces = [c for c in deps.tasty.calls if c[0] == "replace"]
    assert len(replaces) == 5  # 6 tries: the first send and 5 replaces
    assert deps.tasty.calls[-1][0] == "cancel"


def test_a_partial_fill_keeps_what_filled(deps, factory):
    deps.tasty.entry = "partial"
    bot_id = make(factory, live=True)
    force_contracts(factory, bot_id, 2)
    run_id = bot_entry.fire(deps, bot_id, "09:47")
    run = run_of(factory, run_id)
    assert run.status == "filled" and run.contracts == 1 and deps.placed == [run_id]


@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        (lambda d, f, b: orders.set_switch(f, "no_new_trades", True), "No new trades"),
        (lambda d, f, b: bot_store.set_armed(f, b, False, OPEN_TIME), "isn't armed"),
        (lambda d, f, b: setattr(d.chain, "exps", ["2026-10-14"]), "No expiration today"),
        (lambda d, f, b: setattr(d.chain, "chain", {}), "No option chain"),
    ],
)
def test_guards_record_a_skip_with_the_reason(deps, factory, setup, reason):
    bot_id = make(factory, live=True)
    setup(deps, factory, bot_id)
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert run.status in ("skipped", "error") and reason in run.reason
    assert [c for c in deps.tasty.calls if c[0] == "place"] == []


def test_the_market_must_be_open(deps, factory):
    deps.now = lambda: datetime(2026, 10, 17, 14, 0, tzinfo=UTC)  # Saturday
    run = run_of(factory, bot_entry.fire(deps, make(factory, live=True), "09:47"))
    assert run.status == "skipped" and "market" in run.reason


def test_an_entry_in_the_last_two_minutes_is_skipped(deps, factory):
    deps.now = lambda: datetime(2026, 10, 13, 19, 59, tzinfo=UTC)  # 15:59 New York
    run = run_of(factory, bot_entry.fire(deps, make(factory, live=True), "09:47"))
    assert run.status == "skipped" and "close" in run.reason
    assert [c for c in deps.tasty.calls if c[0] == "place"] == []


def test_preview_now_never_sends_even_for_a_live_bot(deps, factory):
    run_id = bot_entry.fire(deps, make(factory, live=True, enabled=False), None, manual=True)
    run = run_of(factory, run_id)
    assert run.status == "preview" and run.key.startswith("bot-")
    assert [c[0] for c in deps.tasty.calls] == ["preview"]


def _itm_diagonal(factory, long_points="-50"):
    """#206 (Tracker2 run #340): a call bought at SPX - 50, one sold 10 points above it,
    in the money; one group with no exits."""
    from optionsjourney.bots.definition import GroupDef, LegDef, iron_condor_skeleton
    from optionsjourney.db.models import Tag
    from optionsjourney.trading.orders import Side

    with factory() as session:
        tag = session.scalar(select(Tag.id).where(Tag.name == "0DTE"))
    legs = (
        LegDef(0, Side.BUY, "call", pick="spot", target=D(long_points)),
        LegDef(1, Side.SELL, "call", pick="offset", target=D("10"), link=0),
    )
    bot = replace(
        iron_condor_skeleton("Diagonal"), tag_id=tag, account_number="5WX01234",
        max_per_day=3, legs=legs, groups=(GroupDef(0, "Diagonal"),),
        entry_times=(EntryTimeDef("09:47"),),
    )  # fmt: skip
    bot_id, problems = bot_store.save(factory, bot, OPEN_TIME)
    assert problems == {}, problems
    bot_store.set_live(factory, bot_id, True, OPEN_TIME)
    return bot_id


def test_an_offset_short_in_the_money_is_entered_with_its_delta_checked(deps, factory):
    deps.chain.chain[5760.0]["call"]["delta"] = 0.62  # Schwab's snapshot sends deltas
    run_id = bot_entry.fire(deps, _itm_diagonal(factory), None, manual=True)
    run = run_of(factory, run_id)
    assert run.status == "preview", run.reason
    assert [leg["strike"] for leg in run.legs] == ["5750", "5760"]
    assert run.legs[1]["delta"] == "0.62"  # read for the ITM short (strikes.offset_short)


def test_a_stale_offset_short_deep_in_the_money_is_an_error(deps, factory):
    deps.chain.chain[5520.0]["call"]["delta"] = 0.97
    run_id = bot_entry.fire(deps, _itm_diagonal(factory, "-290"), None, manual=True)
    run = run_of(factory, run_id)
    assert run.status == "error" and "delta of 0.97" in run.reason
    assert [c for c in deps.tasty.calls if c[0] in ("preview", "place")] == []


def test_an_offset_short_in_the_money_with_no_delta_is_skipped(deps, factory):
    # the fake chain's 5760 call is priced at its intrinsic value: no delta can be worked out
    run_id = bot_entry.fire(deps, _itm_diagonal(factory), None, manual=True)
    run = run_of(factory, run_id)
    assert run.status == "skipped" and "has no delta" in run.reason
    assert [c for c in deps.tasty.calls if c[0] in ("preview", "place")] == []


def test_an_unclear_send_leaves_the_run_running(deps, factory):
    deps.tasty.errors["place"] = RequestUnclear("Lost the connection")
    run = run_of(factory, bot_entry.fire(deps, make(factory, live=True), "09:47"))
    assert run.status == "running" and "check your broker" in run.reason.lower()


def resize(factory, bot_id, **changes):
    bot = bot_store.load(factory, bot_id)
    _, problems = bot_store.save(factory, replace(bot, **changes), OPEN_TIME)
    assert problems == {}


def test_a_partly_filled_entry_is_cancelled_never_replaced(deps, factory):
    deps.tasty.entry = "partial"
    bot_id = make(factory, live=True)
    force_contracts(factory, bot_id, 2)
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert run.status == "filled" and run.contracts == 1
    first = next(c[1] for c in deps.tasty.calls if c[0] == "place")
    assert ("cancel", first) in deps.tasty.calls
    assert not [c for c in deps.tasty.calls if c[0] == "replace" and c[1] == first]
    assert {abs(v) for v in deps.tasty.held.values()} == {run.contracts}


def test_a_bot_at_its_daily_limit_still_chases_all_its_tries(deps, factory):
    deps.tasty.entry = "work"
    bot_id = make(factory, live=True)
    resize(factory, bot_id, max_per_day=1)
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert run.status == "missed" and run.reason == "Not filled after the last try."
    assert len([c for c in deps.tasty.calls if c[0] == "replace"]) == 5


def test_a_refused_replace_says_why_instead_of_claiming_every_try(deps, factory):
    deps.tasty.entry = "work"
    bot_id = make(factory, live=True)
    # No new trades goes on once the order is out: the first sleep comes after the send.
    deps.sleep = lambda s: orders.set_switch(factory, "no_new_trades", True)
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert run.status == "missed" and "Stopped chasing" in run.reason
    assert [c[0] for c in deps.tasty.calls if c[0] == "replace"] == []


def test_a_read_error_after_the_send_leaves_the_run_running(deps, factory):
    deps.tasty.entry = "work"
    deps.tasty.errors["get_order"] = RuntimeError("boom")
    run = run_of(factory, bot_entry.fire(deps, make(factory, live=True), "09:47"))
    assert run.status == "running" and "check your broker" in run.reason.lower()


def test_an_unexpected_failure_after_the_send_does_not_hide_the_order(deps, factory, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(bot_entry, "record_fill", broken)
    run = run_of(factory, bot_entry.fire(deps, make(factory, live=True), "09:47"))
    assert run.status == "running" and "check your broker" in run.reason.lower()


def test_an_unclear_replace_does_not_record_the_old_order(deps, factory):
    deps.tasty.entry = "work"
    deps.tasty.errors["replace"] = RequestUnclear("Lost the connection")
    run = run_of(factory, bot_entry.fire(deps, make(factory, live=True), "09:47"))
    assert run.status == "running" and run.broker_order_id is None


def test_exits_that_fail_to_place_leave_the_fill_and_alert(deps, factory, monkeypatch):
    def broken(deps, run_id):
        raise RuntimeError("boom")

    alerts = []
    deps.notify = alerts.append
    monkeypatch.setattr(bot_entry, "place_protections", broken)
    run = run_of(factory, bot_entry.fire(deps, make(factory, live=True), "09:47"))
    assert run.status == "filled" and "Exits not placed" in run.reason
    assert any("exits not placed" in a.title for a in alerts)


def test_an_unclear_chase_alerts(deps, factory):
    alerts = []
    deps.notify = alerts.append
    deps.tasty.errors["place"] = RequestUnclear("Lost the connection")
    run = run_of(factory, bot_entry.fire(deps, make(factory, live=True), "09:47"))
    assert run.status == "running"
    unclear = [a for a in alerts if "entry unclear" in a.title]
    assert unclear and "recovery will settle it" in unclear[0].detail


def test_a_credit_below_the_floor_is_skipped_and_never_sent(deps, factory):
    bot_id = make(factory, live=True)
    with factory() as session:  # past validation's $2.00 cap: the engine's own floor still holds
        row = session.scalar(
            select(BotChase).where(BotChase.bot_id == bot_id, BotChase.phase == "entry")
        )
        row.start_offset = Decimal("100")
        session.commit()
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert run.status == "skipped" and run.reason == "The credit is below $0.05; not entered."
    assert deps.tasty.calls == []


def test_a_run_records_the_account_it_fired_in(deps, factory):
    run = run_of(factory, bot_entry.fire(deps, make(factory, live=True), "09:47"))
    assert run.account_number == "5WX01234"


def test_a_live_entry_whose_bot_goes_to_preview_mid_chase_stops_and_cancels(deps, factory):
    deps.tasty.entry = "work"
    bot_id = make(factory, live=True)
    deps.sleep = lambda s: bot_store.set_live(factory, bot_id, False, OPEN_TIME)
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert run.status == "missed" and "Stopped chasing" in run.reason
    assert [c[0] for c in deps.tasty.calls if c[0] in ("place", "replace", "cancel")] == [
        "place",
        "cancel",
    ]


def add_run(factory, bot_id, key, status, parent=None):
    with factory() as session:
        run = BotRun(
            bot_id=bot_id,
            key=key,
            trading_date=OPEN_TIME.date(),
            entry_time="09:30",
            status=status,
            live=True,
            legs=[],
            filled_legs=[],
            chase_log=[],
            contracts=1,
            parent_run_id=parent,
            created_at=OPEN_TIME,
            updated_at=OPEN_TIME,
        )
        session.add(run)
        session.commit()
        return run.id


def test_entries_per_day_counts_roots_not_tranches(deps, factory):
    bot_id = make(factory, live=True)
    resize(factory, bot_id, max_entries_per_day=2)
    root = add_run(factory, bot_id, "a", "filled")
    add_run(factory, bot_id, "a2", "filled", parent=root)  # a tranche of the same entry
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert run.status == "filled"  # one root so far; the cap is 2
    add_run(factory, bot_id, "b", "running")
    run = run_of(factory, bot_entry.fire(deps, bot_id, None, manual=True))
    assert run.status == "preview"  # Preview now ignores the cap
    # a (and its tranche: one root), the filled run and b make three roots
    resize(factory, bot_id, entry_times=(EntryTimeDef("09:47"), EntryTimeDef("09:48")))
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:48"))
    assert run.status == "skipped" and run.reason == "already at 3 entries today"


def test_preview_now_runs_alone_never_reach_a_cap(deps, factory):
    bot_id = make(factory, live=True)
    resize(factory, bot_id, max_entries_per_day=1)
    for _ in range(2):
        deps.now = lambda: OPEN_TIME + timedelta(seconds=len(deps.tasty.calls))
        assert run_of(factory, bot_entry.fire(deps, bot_id, None, manual=True)).status == "preview"
    assert run_of(factory, bot_entry.fire(deps, bot_id, "09:47")).status == "filled"


def test_a_blackout_day_at_the_daily_limit_says_blackout(deps, factory):
    bot_id = make(factory, live=True)
    resize(factory, bot_id, use_blackout_days=True, blackout_days=("2026-10-13",), max_per_day=1)
    with factory() as session:
        session.add(
            OrderLog(
                broker="tastytrade", account_number="5WX01234", origin="bot", action="send",
                opening=True, quantity=1, order_type="limit", order={}, ok=True,
                result="Sent.", created_at=OPEN_TIME, bot_id=bot_id,
            )
        )  # fmt: skip
        session.commit()
    assert orders.used_today(factory, bot_id, OPEN_TIME) == 1
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert run.reason == "today is a blackout day"


class OverlappingChain(FakeChain):
    """While the first entry is still in progress (its run "pending"), the clock
    fires the next entry time."""

    def __init__(self, fire_next):
        super().__init__()
        self.fire_next, self.second = fire_next, None

    def option_rows(self, clients, hub, ticker, expiration):
        if self.second is None:
            self.second = -1
            self.second = self.fire_next()
        return super().option_rows(clients, hub, ticker, expiration)


def test_a_scheduled_preview_run_in_progress_counts_toward_entries_per_day(deps, factory):
    bot_id = make(factory)  # preview mode
    resize(factory, bot_id, max_entries_per_day=1,
           entry_times=(EntryTimeDef("09:47"), EntryTimeDef("09:48")))  # fmt: skip
    deps.chain = OverlappingChain(lambda: bot_entry.fire(deps, bot_id, "09:48"))
    first = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    second = run_of(factory, deps.chain.second)
    assert first.status == "filled"
    assert second.status == "skipped" and second.reason == "already at 1 entries today"


def test_a_pending_preview_now_run_never_counts(deps, factory):
    bot_id = make(factory)
    resize(factory, bot_id, max_entries_per_day=1)
    add_run(factory, bot_id, f"{bot_id}-now-x", "pending")
    assert run_of(factory, bot_entry.fire(deps, bot_id, "09:47")).status == "filled"


def test_a_trade_page_entry_never_counts_toward_entries_per_day(deps, factory):
    # Bots slice 15, question 5: like Preview now and a trade handed over.
    bot_id = make(factory, live=True)
    resize(factory, bot_id, max_entries_per_day=1)
    add_run(factory, bot_id, f"ticket-{bot_id}-0123456789ab", "filled")
    assert run_of(factory, bot_entry.fire(deps, bot_id, "09:47")).status == "filled"


def test_entries_per_day_ignores_skipped_missed_and_error_runs(deps, factory):
    bot_id = make(factory, live=True)
    resize(factory, bot_id, max_entries_per_day=1)
    for i, status in enumerate(("skipped", "missed", "error")):
        add_run(factory, bot_id, f"x{i}", status)
    assert run_of(factory, bot_entry.fire(deps, bot_id, "09:47")).status == "filled"


# Max open trades counts the app's own runs and exits, never the journal.


def ic_legs(expiry="261013"):
    return [
        {"symbol": f"SPXW  {expiry}C05910000", "side": "sell", "ratio": 1, "group": 0},
        {"symbol": f"SPXW  {expiry}C05985000", "side": "buy", "ratio": 1, "group": 0},
        {"symbol": f"SPXW  {expiry}P05690000", "side": "sell", "ratio": 1, "group": 1},
        {"symbol": f"SPXW  {expiry}P05615000", "side": "buy", "ratio": 1, "group": 1},
    ]


def held_run(factory, bot_id, key, exits=(), expiry="261013", legs=None):
    """A filled run holding an iron condor, with the given (side, kind, state) exits."""
    with factory() as session:
        run = BotRun(
            bot_id=bot_id, key=key, trading_date=OPEN_TIME.date(), entry_time="09:30",
            status="filled", live=True, legs=ic_legs(expiry) if legs is None else legs,
            filled_legs=[{"symbol": "x", "side": "sell", "fill_price": "1", "contracts": 1}],
            chase_log=[], contracts=1, created_at=OPEN_TIME, updated_at=OPEN_TIME,
        )  # fmt: skip
        session.add(run)
        session.flush()
        for side, kind, state in exits:
            session.add(BotExit(
                run_id=run.id, group_position=side, kind=kind, state=state,
                short_symbol="s", long_symbol="l", contracts=1, rearms=0, log=[],
                created_at=OPEN_TIME, updated_at=OPEN_TIME,
            ))  # fmt: skip
        session.commit()
        return run.id


def open_cap_bot(factory, cap, split=True):
    bot_id = make(factory, live=True)
    resize(factory, bot_id, max_open_trades=cap, split_iron_condors=split)
    return bot_id


def test_two_runs_filled_today_hit_the_open_trades_cap_with_nothing_synced(deps, factory):
    bot_id = open_cap_bot(factory, 2, split=False)
    times = (EntryTimeDef("09:47"), EntryTimeDef("09:48"), EntryTimeDef("09:49"))
    resize(factory, bot_id, entry_times=times)
    for hhmm in ("09:47", "09:48"):
        assert run_of(factory, bot_entry.fire(deps, bot_id, hhmm)).status == "filled"
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:49"))
    assert run.status == "skipped" and run.reason == "already holding 2 open trades"
    assert len([c for c in deps.tasty.calls if c[0] == "place"]) == 2


def test_a_split_condor_counts_as_two_open_trades(deps, factory):
    bot_id = open_cap_bot(factory, 2)
    held_run(factory, bot_id, "a")
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert run.status == "skipped" and run.reason == "already holding 2 open trades"


def test_a_run_whose_sides_are_all_closed_is_not_an_open_trade(deps, factory):
    bot_id = open_cap_bot(factory, 1)
    held_run(factory, bot_id, "a", [(0, "target", "filled"), (1, "stop", "expired")])
    held_run(factory, bot_id, "b", [(0, "stop", "filled"), (0, "wing", "filled"),
                                    (1, "target", "filled")])  # fmt: skip
    assert run_of(factory, bot_entry.fire(deps, bot_id, "09:47")).status == "filled"


@pytest.mark.parametrize("split", [True, False])
def test_a_stopped_side_with_its_wing_sold_leaves_one_open_trade(deps, factory, split):
    bot_id = open_cap_bot(factory, 1, split=split)
    held_run(factory, bot_id, "a", [(0, "stop", "filled"), (0, "wing", "filled"),
                                    (1, "stop", "working")])  # fmt: skip
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert run.status == "skipped" and run.reason == "already holding 1 open trades"


def test_a_run_that_expired_yesterday_is_not_an_open_trade(deps, factory):
    bot_id = open_cap_bot(factory, 1)
    held_run(factory, bot_id, "a", expiry="261012")
    assert run_of(factory, bot_entry.fire(deps, bot_id, "09:47")).status == "filled"


def test_a_one_plus_two_tranche_entry_counts_once(deps, factory):
    deps.tasty.fills = [1, 2]  # 1 of 3 fills, then the tranche's 2
    bot_id = tranche_bot(factory)
    resize(factory, bot_id, entry_times=(EntryTimeDef("09:47"), EntryTimeDef("09:48")))
    first = bot_entry.fire(deps, bot_id, "09:47")
    assert [(r.status, r.contracts) for r in runs(factory)] == [("filled", 1), ("filled", 2)]
    assert runs(factory)[1].parent_run_id == first
    resize(factory, bot_id, max_open_trades=3)  # split: the entry holds 2, not 4
    assert run_of(factory, bot_entry.fire(deps, bot_id, "09:48")).status == "filled"


def test_the_one_plus_two_tranche_entry_is_at_a_cap_of_two(deps, factory):
    deps.tasty.fills = [1, 2]
    bot_id = tranche_bot(factory)
    resize(factory, bot_id, entry_times=(EntryTimeDef("09:47"), EntryTimeDef("09:48")))
    bot_entry.fire(deps, bot_id, "09:47")
    resize(factory, bot_id, max_open_trades=2)
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:48"))
    assert run.status == "skipped" and run.reason == "already holding 2 open trades"


def test_a_run_that_cant_be_read_fails_closed(deps, factory):
    bot_id = open_cap_bot(factory, 5)
    held_run(factory, bot_id, "a", legs=[{"no symbol": True}])
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert run.status == "skipped"
    assert run.reason == "Couldn't count this bot's open trades; not entered."
    assert [c for c in deps.tasty.calls if c[0] == "place"] == []


def test_a_failed_read_fails_closed(deps, factory, monkeypatch):
    bot_id = open_cap_bot(factory, 5)

    def boom(*args):
        raise RuntimeError("database locked")

    monkeypatch.setattr(bot_entry.caps, "open_trades", boom)
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert run.reason == "Couldn't count this bot's open trades; not entered."


def test_the_open_trades_cap_is_not_checked_for_preview_now(deps, factory):
    bot_id = open_cap_bot(factory, 1)
    held_run(factory, bot_id, "a")
    assert run_of(factory, bot_entry.fire(deps, bot_id, None, manual=True)).status == "preview"


def test_premium_bounds_skip_an_entry_outside_them(deps, factory):
    bot_id = make(factory, live=True)
    resize(factory, bot_id, use_premium_bounds=True, premium_min=D("50"), premium_max=D("90"))
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert run.status == "skipped" and run.reason == "net credit 4.90 < min 50.00"
    assert [c for c in deps.tasty.calls if c[0] == "place"] == []


def test_premium_bounds_let_an_entry_inside_them_through(deps, factory):
    bot_id = make(factory, live=True)
    resize(factory, bot_id, use_premium_bounds=True, premium_min=D("0.05"), premium_max=D("99"))
    assert run_of(factory, bot_entry.fire(deps, bot_id, "09:47")).status == "filled"


def test_a_blackout_day_is_a_skipped_run(deps, factory):
    bot_id = make(factory, live=True)
    resize(factory, bot_id, use_blackout_days=True, blackout_days=("2026-10-13",))
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert run.status == "skipped" and run.reason == "today is a blackout day"
    manual = run_of(factory, bot_entry.fire(deps, bot_id, None, manual=True))
    assert manual.status == "preview"


def sent_quantity(deps):
    place = next(c for c in deps.tasty.calls if c[0] == "place")
    return {int(leg["quantity"]) for leg in place[2]["legs"]}


def test_an_entry_time_quantity_is_what_gets_sent(deps, factory):
    bot_id = make(factory, live=True)
    force_contracts(factory, bot_id, 3)
    resize(factory, bot_id, entry_times=(EntryTimeDef("09:47", quantity=2),))
    assert run_of(factory, bot_entry.fire(deps, bot_id, "09:47")).status == "filled"
    assert sent_quantity(deps) == {2}


def test_a_quantity_above_the_per_trade_limit_is_clamped_and_logged(deps, factory):
    bot_id = make(factory, live=True)
    force_contracts(factory, bot_id, 2)
    with factory() as session:
        row = session.scalars(select(BotEntryTime).where(BotEntryTime.bot_id == bot_id)).first()
        row.quantity = 5
        session.commit()
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert sent_quantity(deps) == {2}
    assert {"capped": 5, "to": 2} in run.chase_log


# Tranches: partial fills on multi-contract entries (Tracker2's chase_entry / _tranche_run).


def runs(factory):
    with factory() as session:
        return list(session.scalars(select(BotRun).order_by(BotRun.id)))


def tranche_bot(factory, contracts=3):
    bot_id = make(factory, live=True)
    force_contracts(factory, bot_id, contracts)
    return bot_id


def test_a_partial_fill_becomes_tranches_each_with_its_own_stops(deps, factory, monkeypatch):
    monkeypatch.setattr(bot_entry, "place_protections", bot_exits.place_protections)
    deps.tasty.fills = [1, 2]  # the first order fills 1 of 3; the next fills its 2
    pricier = {sym: D("3.00") for sym in ("SPXW  261013C05910000", "SPXW  261013P05690000")}
    exits_while_chasing = []

    def sleep(seconds):
        deps.tasty.prices.update(pricier)  # the second order fills higher
        with factory() as session:
            exits_while_chasing.append(len(session.scalars(select(BotExit)).all()))

    deps.sleep = sleep
    root_id = bot_entry.fire(deps, tranche_bot(factory), "09:47")
    # No exits while an order of the entry was working (Tracker2: protect after the chase)
    assert len(exits_while_chasing) == 2 and set(exits_while_chasing) == {0}
    root, child = runs(factory)
    assert root.id == root_id and (root.status, root.contracts) == ("filled", 1)
    assert child.key == f"{root.key}#t2" and child.parent_run_id == root.id
    assert (child.status, child.contracts) == ("filled", 2)
    assert child.legs == root.legs and child.target_credit == root.target_credit
    assert (child.account_number, child.live, child.trading_date, child.entry_time) == (
        root.account_number, root.live, root.trading_date, root.entry_time,
    )  # fmt: skip
    first, second = [
        c for c in deps.tasty.calls if c[0] == "place" and c[2]["order-type"] == "Limit"
    ]
    assert {int(leg["quantity"]) for leg in second[2]["legs"]} == {2}  # a fresh order for the rest
    assert not [c for c in deps.tasty.calls if c[0] == "replace" and c[1] == first[1]]
    with factory() as session:
        sends = session.scalars(
            select(OrderLog).where(OrderLog.action == "send", OrderLog.opening.is_(True))
        ).all()
        stops = session.scalars(
            select(BotExit).where(BotExit.kind == "stop").order_by(BotExit.id)
        ).all()
    assert [(s.bot_run_id, s.quantity) for s in sends] == [(root.id, 3), (child.id, 2)]
    # Each tranche's stops sit at its own fill: 2.50 x 1.95 = 4.875 -> 4.80; 3.00 x 1.95 -> 5.80
    assert [(s.run_id, s.contracts, s.trigger, s.state) for s in stops] == [
        (root.id, 1, D("4.80"), "working"),
        (root.id, 1, D("4.80"), "working"),
        (child.id, 2, D("5.80"), "working"),
        (child.id, 2, D("5.80"), "working"),
    ]


def test_tries_running_out_after_a_partial_is_short(deps, factory):
    alerts = []
    deps.notify = alerts.append
    deps.tasty.entry, deps.tasty.fills = "work", [1]  # 1 of 3 fills, then nothing more
    root_id = bot_entry.fire(deps, tranche_bot(factory), "09:47")
    root, child = runs(factory)
    assert (root.status, root.contracts) == ("filled", 1) and deps.placed == [root_id]
    assert child.status == "missed" and child.reason == "Not filled after the last try."
    # One attempt counter for the whole entry: 6 tries are 1 read of the first order,
    # then the fresh send, 4 replaces and the last read of the second order.
    assert len([c for c in deps.tasty.calls if c[0] == "replace"]) == 4
    assert deps.tasty.calls[-1] == ("cancel", child.broker_order_id)
    assert [a.detail for a in alerts if "short" in a.title] == [
        "Entry short: 1 of 3 filled — rest given up"
    ]


def test_the_root_is_missed_when_only_a_later_tranche_filled(deps, factory, monkeypatch):
    # The real chase only starts a tranche after a fill, so drive bot_entry's
    # settling straight from a chase outcome where the root filled nothing.
    from optionsjourney.services.bot_chase import Entered

    def chase_entry(deps_, request, first, plan, next_price, on_tranche, on_fill):
        child = on_tranche(2, request.order.quantity)
        legs = tuple(
            BrokerLeg(leg.symbol, leg.side, True, 3, 0, D("1")) for leg in request.order.legs
        )
        found = BrokerOrder("777", OrderStatus.FILLED, legs)
        on_fill(child, found)
        return Entered("filled", [(child, found)], None, None, [], "", 3, 3)

    monkeypatch.setattr(bot_entry, "chase_entry", chase_entry)
    root_id = bot_entry.fire(deps, tranche_bot(factory), "09:47")
    root, child = runs(factory)
    assert root.id == root_id and root.status == "missed" and "filled in tranche 2" in root.reason
    assert (child.status, child.contracts, child.broker_order_id) == ("filled", 3, "777")
    assert deps.placed == [child.id]


def test_an_unconfirmed_cancel_mid_tranche_leaves_that_run_running(deps, factory):
    alerts = []
    deps.notify = alerts.append
    deps.tasty.fills = [1, 1]  # 1 of 3, then 1 of the 2 left
    deps.tasty.stuck = {"102"}  # the second order's cancel is never confirmed
    seen = []
    deps.sleep = lambda s: seen.append(set(bot_entry.ACTIVE))
    root_id = bot_entry.fire(deps, tranche_bot(factory), "09:47")
    root, child = runs(factory)
    assert (root.status, root.contracts) == ("filled", 1) and deps.placed == [root_id]
    assert child.status == "running" and child.broker_order_id == "102"
    assert "check your broker" in child.reason.lower()
    assert any("unclear" in a.title for a in alerts)
    assert {root.id, child.id} in seen  # both chased runs are left alone by recovery...
    assert not bot_entry.ACTIVE & {root.id, child.id}  # ...until fire returns


def uneven_reads(deps, filled):
    """Make order 101 read back with each leg's filled contracts from `filled`."""
    read = deps.tasty.get_order

    def get_order(account, order_id):
        found = read(account, order_id)
        if order_id != "101":
            return found
        legs = tuple(
            replace(leg, remaining=leg.quantity - n, fill_price=D("2.50") if n else None)
            for leg, n in zip(found.legs, filled, strict=True)
        )
        status = OrderStatus.PARTIAL if found.may_be_working else found.status
        return replace(found, status=status, legs=legs)

    deps.tasty.get_order = get_order


def test_legs_filled_without_a_whole_unit_need_you_and_recovery_skips_them(deps, factory):
    alerts = []
    deps.notify = alerts.append
    deps.tasty.entry = "work"
    uneven_reads(deps, (1, 0, 0, 0))  # only the first leg filled: no whole unit
    bot_entry.fire(deps, tranche_bot(factory), "09:47")
    (root,) = runs(factory)
    assert root.status == "running" and root.reason.startswith(bot_entry.NEEDS_YOU)
    assert "filled unevenly" in root.reason and root.contracts == 0 and deps.placed == []
    assert [c[0] for c in deps.tasty.calls if c[0] in ("place", "replace")] == ["place"]
    assert [a.title for a in alerts if "needs you" in a.title] == ["IC: a run needs you"]
    calls = len(deps.tasty.calls)
    bot_loops.reconcile(deps)  # recovery never records or protects a run that needs you
    (root,) = runs(factory)
    assert root.status == "running" and root.filled_legs == [] and deps.placed == []
    assert len(deps.tasty.calls) == calls


def test_uneven_legs_record_the_whole_units_alert_and_tranche_the_rest(deps, factory):
    alerts = []
    deps.notify = alerts.append
    deps.tasty.fills = [1, 2]  # the first order is read back as 2/1/1/1 of 3
    uneven_reads(deps, (2, 1, 1, 1))
    root_id = bot_entry.fire(deps, tranche_bot(factory), "09:47")
    root, child = runs(factory)
    assert (root.status, root.contracts) == ("filled", 1)
    assert (child.status, child.contracts) == ("filled", 2)
    assert deps.placed == [root_id, child.id]
    assert [a.title for a in alerts if "unevenly" in a.title] == ["IC: legs filled unevenly"]


def test_a_tranche_rest_does_not_count_again_against_the_daily_limit(deps, factory):
    deps.tasty.fills = [1, 2]
    bot_id = tranche_bot(factory)
    bot_entry.fire(deps, bot_id, "09:47")
    assert orders.used_today(factory, bot_id, OPEN_TIME) == 3  # the entry's 3, counted once


def test_a_tranche_needing_replaces_fills_at_a_daily_limit_of_its_size(deps, factory):
    deps.tasty.fills = [1, 0, 2]  # 1 of 3; the rest's first order fills nothing; its replace 2
    bot_id = tranche_bot(factory)
    with factory() as session:
        session.get(Bot, bot_id).max_per_day = 3
        session.commit()
    bot_entry.fire(deps, bot_id, "09:47")
    root, child = runs(factory)
    assert (root.status, root.contracts) == ("filled", 1)
    assert (child.status, child.contracts) == ("filled", 2)
    assert len([c for c in deps.tasty.calls if c[0] == "replace"]) == 1
    assert orders.used_today(factory, bot_id, OPEN_TIME) == 3


def test_a_failure_after_the_chase_still_protects_the_recorded_fills(deps, factory, monkeypatch):
    def broken(*args):
        raise RuntimeError("boom")

    monkeypatch.setattr(bot_entry, "_settle", broken)
    deps.tasty.entry, deps.tasty.fills = "work", [1]
    root_id = bot_entry.fire(deps, tranche_bot(factory), "09:47")
    root, child = runs(factory)
    assert (root.status, root.contracts) == ("filled", 1) and deps.placed == [root_id]
    assert child.status == "running"  # the rest's order may be out: left for recovery


def test_a_child_run_keeps_only_its_own_part_of_the_chase_log(deps, factory):
    deps.tasty.fills = [1, 2]
    bot_entry.fire(deps, tranche_bot(factory), "09:47")
    root, child = runs(factory)
    assert child.chase_log and len(child.chase_log) < len(root.chase_log)
    assert child.chase_log == root.chase_log[-len(child.chase_log) :]
    assert child.chase_log[0]["tranche"] == 2


def test_entries_per_day_counts_a_tranched_entry_once(deps, factory):
    deps.tasty.fills = [1, 2]
    bot_id = tranche_bot(factory)
    resize(
        factory, bot_id, max_entries_per_day=2,
        entry_times=(EntryTimeDef("09:47"), EntryTimeDef("09:48"), EntryTimeDef("09:49")),
    )  # fmt: skip
    bot_entry.fire(deps, bot_id, "09:47")
    assert len(runs(factory)) == 2  # the root and its tranche
    assert run_of(factory, bot_entry.fire(deps, bot_id, "09:48")).status == "filled"
    third = run_of(factory, bot_entry.fire(deps, bot_id, "09:49"))
    assert third.status == "skipped" and third.reason == "already at 2 entries today"


# Slice 5: the market filters, the last guards before the chain


@pytest.fixture
def feed(deps):
    from optionsjourney.services import bot_filters
    from tests.services.market_fakes import FakeFeed

    bot_filters.reset_alerts()
    found = FakeFeed()
    deps.market = bot_filters.Market(found)
    return found


def test_a_filter_miss_skips_with_its_reason_and_no_alert(deps, factory, feed):
    alerts = []
    deps.notify = alerts.append
    feed.trade("VIX", "21.3", OPEN_TIME - timedelta(seconds=5))
    bot_id = make(factory)
    resize(factory, bot_id, use_vix_level=True, vix_max=D("20"))
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert run.status == "skipped" and run.reason == "VIX level filter: VIX 21.3; above max 20"
    assert alerts == [] and deps.tasty.calls == []


def test_a_filter_that_passes_lets_the_entry_go_ahead(deps, factory, feed):
    feed.trade("VIX", "18", OPEN_TIME - timedelta(seconds=5))
    bot_id = make(factory)
    resize(factory, bot_id, use_vix_level=True, vix_max=D("20"))
    assert run_of(factory, bot_entry.fire(deps, bot_id, "09:47")).status == "filled"


def test_a_filter_that_cannot_be_read_skips_and_alerts_once_a_day(deps, factory, feed):
    alerts = []
    deps.notify = alerts.append
    bot_id = make(factory)
    resize(factory, bot_id, use_vix_level=True, vix_max=D("20"),
           entry_times=(EntryTimeDef("09:47"), EntryTimeDef("10:30")))  # fmt: skip
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert run.status == "skipped"
    assert run.reason == "VIX level unavailable: no VIX price from TastyTrade. Not entered."
    assert [a.title for a in alerts] == ["IC: entry skipped, VIX level unavailable"]
    assert alerts[0].detail == run.reason and alerts[0].link == f"/automation/{bot_id}/runs"
    second = run_of(factory, bot_entry.fire(deps, bot_id, "10:30"))
    assert second.status == "skipped" and len(alerts) == 1  # once a day
    assert deps.tasty.calls == []


def test_no_market_source_fails_closed(deps, factory):
    deps.market = None
    bot_id = make(factory)
    resize(factory, bot_id, use_vix_level=True, vix_max=D("20"))
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert run.status == "skipped" and "VIX level unavailable" in run.reason


def test_preview_now_ignores_the_market_filters(deps, factory, feed):
    bot_id = make(factory)
    resize(factory, bot_id, use_vix_level=True, vix_max=D("20"))
    run = run_of(factory, bot_entry.fire(deps, bot_id, None, manual=True))
    assert run.status == "preview" and feed.calls == []


def test_the_filters_run_after_the_bots_own_guards(deps, factory, feed):
    bot_id = make(factory)
    resize(factory, bot_id, use_vix_level=True, vix_max=D("20"), use_blackout_days=True,
           blackout_days=("2026-10-13",))  # fmt: skip
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert run.reason == "today is a blackout day" and feed.calls == []


# Slice 5: named date lists in blackout days


def fomc_today(factory, refreshed=OPEN_TIME):
    from optionsjourney.db.models import EconomicEvent, EconomicEventSource

    with factory() as session:
        session.add(EconomicEvent(event_date=OPEN_TIME.date(), title="FOMC", impact="high",
                                  key="FOMC", source="fed"))  # fmt: skip
        session.add(EconomicEventSource(source="fed", refreshed_at=refreshed))
        session.commit()


def test_a_blackout_list_day_is_a_skipped_run_naming_the_list(deps, factory):
    fomc_today(factory)
    bot_id = make(factory)
    resize(factory, bot_id, use_blackout_days=True, blackout_date_sets=("FOMC",))
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert run.status == "skipped" and run.reason == "today is a blackout day (FOMC)"


def test_an_unavailable_blackout_list_skips_the_entry(deps, factory):
    fomc_today(factory, refreshed=OPEN_TIME - timedelta(days=30))  # too old to trust
    bot_id = make(factory)
    resize(factory, bot_id, use_blackout_days=True, blackout_date_sets=("FOMC", "EOM"))
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert run.status == "skipped"
    assert run.reason == "FOMC dates unavailable today, so the blackout can't be checked."
    assert deps.tasty.calls == []


def test_a_list_day_before_expiration_is_a_skipped_run_naming_it(deps, factory):
    """#207: Before expiration looks at the days after today through the expiry estimate;
    a 0DTE trade has none."""
    from optionsjourney.db.models import EconomicEvent, EconomicEventSource

    with factory() as session:
        session.add(EconomicEvent(event_date=date(2026, 10, 15), title="FOMC", impact="high",
                                  key="FOMC", source="fed"))  # fmt: skip
        session.add(EconomicEventSource(source="fed", refreshed_at=OPEN_TIME))
        session.commit()
    bot_id = make(factory)
    resize(factory, bot_id, use_blackout_days=True, blackout_dte_sets=("FOMC", "HOLIDAY"))
    assert run_of(factory, bot_entry.fire(deps, bot_id, "09:47")).status == "filled"
    bot = bot_store.load(factory, bot_id)
    resize(factory, bot_id, legs=tuple(replace(leg, dte=7) for leg in bot.legs))
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:48"))
    assert run.status == "skipped"
    assert run.reason == "today is a blackout day (FOMC on 2026-10-15, before expiration)"


def test_a_blackout_list_without_today_lets_the_entry_go_ahead(deps, factory):
    bot_id = make(factory)
    resize(factory, bot_id, use_blackout_days=True, blackout_date_sets=("EOM",))
    assert run_of(factory, bot_entry.fire(deps, bot_id, "09:47")).status == "filled"


def test_the_cutoff_and_switches_are_checked_again_after_the_filters(deps, factory, feed):
    late = datetime(2026, 10, 13, 19, 58, 30, tzinfo=UTC)  # 15:58:30 New York
    feed.trade("VIX", "18", OPEN_TIME - timedelta(seconds=5))
    feed.on_read = lambda: setattr(deps, "now", lambda: late)  # a slow read
    bot_id = make(factory)
    resize(factory, bot_id, use_vix_level=True, vix_max=D("20"))
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert run.status == "skipped" and run.reason.startswith("Too close to the close")
    deps.now = lambda: OPEN_TIME
    feed.on_read = lambda: orders.set_switch(factory, "freeze", True, OPEN_TIME)
    resize(factory, bot_id, entry_times=(EntryTimeDef("09:47"), EntryTimeDef("09:48")))
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:48"))
    assert run.reason == "Orders are frozen." and deps.tasty.calls == []


def test_the_entry_guard_refuses_an_archived_bot(deps, factory):
    """Archived bots never fire, even if one is somehow still armed (belt and braces
    behind the clock's armed list and Arm's refusal), and Preview now refuses too."""
    bot_id = make(factory, live=True)
    with factory() as session:
        session.get(Bot, bot_id).archived_at = OPEN_TIME  # archived, yet still armed
        session.commit()
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert run.status == "skipped" and "archived" in run.reason
    run = run_of(factory, bot_entry.fire(deps, bot_id, None, manual=True))
    assert run.status == "skipped" and "archived" in run.reason
    assert deps.tasty.calls == []


def test_a_run_records_the_entry_spot(deps, factory):
    run_id = bot_entry.fire(deps, make(factory), "09:47")
    assert run_of(factory, run_id).entry_spot == Decimal("5800")


def test_an_alert_carries_its_bot_run_and_whether_it_is_routine(deps):
    sent = []
    deps.notify = sent.append
    bot_entry.alert(deps, "IC: close filled", "Closed.", "/automation/7/runs", run_id=42,
                    routine=True)  # fmt: skip
    bot_entry.alert(deps, "Holiday calendar out of date", "Update.", "/automation")
    assert [(e.bot_id, e.run_id, e.routine) for e in sent] == [(7, 42, True), (None, None, False)]


def test_with_no_notify_an_alert_is_queued_never_sent_on_the_bots_thread(deps, monkeypatch):
    from optionsjourney.services import notify

    queued = []
    monkeypatch.setattr(notify, "send_soon", lambda factory, event: queued.append(event))
    monkeypatch.setattr(notify, "send", lambda *a, **k: pytest.fail("sent on the bot's thread"))
    deps.notify = None
    bot_entry.alert(deps, "IC: entry failed", "boom", "/automation/3/runs")
    assert [e.title for e in queued] == ["IC: entry failed"]


def test_the_running_bots_queue_their_alerts(factory, monkeypatch):
    from types import SimpleNamespace

    from optionsjourney.services import notify

    queued = []
    monkeypatch.setattr(notify, "send_soon", lambda f, event: queued.append((f, event)))
    services = SimpleNamespace(session_factory=factory, broker_clients=lambda: [],
                               quote_hub=FakeHub(), chain=FakeChain())  # fmt: skip
    deps = bot_loops.deps_for(services)
    bot_entry.alert(deps, "IC: entry failed", "boom", "/automation/3/runs")
    assert [(f, e.title) for f, e in queued] == [(factory, "IC: entry failed")]


# Slice 10: strikes by delta and by spot offset

from tests.services.bot_fakes import SPOT, rows  # noqa: E402


def delta_rows(at=None):
    """The fake chain with Schwab-style deltas (|delta| 0.5 less 0.002 a point out)."""
    out = rows()
    for strike, row in out.items():
        for right, side in row.items():
            distance = (strike - float(SPOT)) if right == "call" else (float(SPOT) - strike)
            size = max(0.5 - distance * 0.002, 0.005)
            side["delta"] = size if right == "call" else -size
            if at is not None:
                side["at"] = at
    return out


def market_bot(factory, picks, **more):
    """An IC bot whose shorts pick by `picks` ({position: (pick, target)})."""
    bot_id = make(factory)
    bot = bot_store.load(factory, bot_id)
    legs = tuple(replace(leg, pick=picks[leg.position][0], target=D(picks[leg.position][1]))
                 if leg.position in picks else leg for leg in bot.legs)  # fmt: skip
    resize(factory, bot_id, legs=legs, **more)
    return bot_id


DELTAS = {0: ("delta", "0.30"), 2: ("delta", "0.36")}


def test_a_delta_bot_previews_the_strikes_nearest_its_deltas(deps, factory):
    deps.chain.chain = delta_rows()
    run = run_of(factory, bot_entry.fire(deps, market_bot(factory, DELTAS), "09:47"))
    assert run.status == "filled", run.reason
    assert [leg["strike"] for leg in run.legs] == ["5900", "5975", "5730", "5655"]
    assert [leg.get("delta") for leg in run.legs] == ["0.3", "0.15", "-0.36", "-0.21"]


def test_missing_deltas_skip_the_entry_and_alert_once_a_day(deps, factory):
    from optionsjourney.services import bot_filters

    bot_filters.reset_alerts()
    alerts = []
    deps.notify = alerts.append
    stale = OPEN_TIME - timedelta(seconds=60)
    deps.chain.chain = {
        k: {r: {**side, "at": stale} for r, side in row.items()} for k, row in rows().items()
    }  # TastyTrade quotes, too old to work a delta out from, and no Schwab deltas
    bot_id = market_bot(factory, DELTAS,
                        entry_times=(EntryTimeDef("09:47"), EntryTimeDef("10:30")))  # fmt: skip
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert run.status == "skipped" and run.reason.startswith("Deltas unavailable for the calls")
    assert [a.title for a in alerts] == ["IC: entry skipped, deltas unavailable"]
    assert run_of(factory, bot_entry.fire(deps, bot_id, "10:30")).status == "skipped"
    assert len(alerts) == 1 and deps.tasty.calls == []


def test_quiet_tastytrade_quotes_on_a_healthy_stream_give_worked_out_deltas(deps, factory):
    # Far 0DTE quotes that haven't changed for 5 minutes, SPX streaming a second ago
    quiet = OPEN_TIME - timedelta(minutes=5)
    deps.chain.chain = {
        k: {r: {**side, "at": quiet} for r, side in row.items()} for k, row in rows().items()
    }
    deps.hub.set_last("SPX", 5800, OPEN_TIME - timedelta(seconds=1))
    run = run_of(factory, bot_entry.fire(deps, market_bot(factory, DELTAS), "09:47"))
    assert run.status == "filled", run.reason
    assert all("delta" in leg for leg in run.legs)


@pytest.mark.parametrize("dead", ["not connected", "no spx", "spx from schwab"])
def test_a_dead_stream_gives_no_deltas(deps, factory, dead):
    from optionsjourney.services import bot_filters

    bot_filters.reset_alerts()
    deps.chain.chain = {
        k: {r: {**side, "at": OPEN_TIME} for r, side in row.items()} for k, row in rows().items()
    }
    if dead == "not connected":
        deps.hub.streams = {"tastytrade": "not connected"}
        deps.hub.set_last("SPX", 5800, OPEN_TIME - timedelta(seconds=1))
    if dead == "spx from schwab":  # TastyTrade says streaming, but SPX only comes from Schwab
        from optionsjourney.core.quotes import Quote

        moment = OPEN_TIME - timedelta(seconds=1)
        deps.hub.quotes["SPX"] = Quote("SPX", None, None, D("5800"), "schwab", moment, moment)
    run = run_of(factory, bot_entry.fire(deps, market_bot(factory, DELTAS), "09:47"))
    assert run.status == "skipped" and run.reason.startswith("Deltas unavailable")


def test_a_spot_bot_previews_the_strikes_its_points_away(deps, factory):
    picks = {0: ("spot", "100"), 2: ("spot", "-100")}
    deps.chain.spot = 5801.0
    run = run_of(factory, bot_entry.fire(deps, market_bot(factory, picks), "09:47"))
    assert run.status == "filled"
    # SPX 5801: +100 is 5901 and -100 is 5701; shorts round out of the money
    assert [leg["strike"] for leg in run.legs] == ["5905", "5980", "5700", "5625"]


def test_a_stale_spx_price_skips_a_market_pick_and_alerts(deps, factory):
    from optionsjourney.services import bot_filters

    bot_filters.reset_alerts()
    alerts = []
    deps.notify = alerts.append
    deps.hub.set_last("SPX", 5800, OPEN_TIME - timedelta(seconds=31))
    bot_id = market_bot(factory, {0: ("spot", "100"), 2: ("spot", "-100")})
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert run.status == "skipped" and "SPX's price is stale" in run.reason
    assert [a.title for a in alerts] == ["IC: entry skipped, SPX price unavailable"]
    deps.hub.set_last("SPX", 5800, OPEN_TIME - timedelta(seconds=5))
    resize(factory, bot_id, entry_times=(EntryTimeDef("09:47"), EntryTimeDef("09:48")))
    assert run_of(factory, bot_entry.fire(deps, bot_id, "09:48")).status == "filled"


def test_a_premium_bot_reads_no_deltas_and_checks_no_freshness(deps, factory):
    chain = delta_rows()
    for row in chain.values():
        for side in row.values():
            side["delta"] = 0.97 if side["delta"] > 0 else -0.97  # would be refused if read
    deps.chain.chain = chain
    deps.hub.set_last("SPX", 5800, OPEN_TIME - timedelta(hours=1))  # stale: never asked
    run = run_of(factory, bot_entry.fire(deps, make(factory), "09:47"))
    assert run.status == "filled"
    assert [leg["strike"] for leg in run.legs] == ["5910", "5985", "5690", "5615"]
    assert all("delta" not in leg for leg in run.legs)


# Slice 10: the floating entry window

from optionsjourney.bots import floating  # noqa: E402

DAY = OPEN_TIME.date()


def window_bot(factory, start="09:45", end="09:50", live=False, **more):
    bot_id = make(factory, live=live)
    resize(factory, bot_id, use_floating_entry=True, floating_start=start, floating_end=end,
           **more)  # fmt: skip
    return bot_id


def at(deps, hhmm):
    hour, minute = (int(x) for x in hhmm.split(":"))
    moment = OPEN_TIME.replace(hour=hour + 4, minute=minute, second=5)  # New York is UTC-4
    deps.now = lambda: moment


def float_runs(factory, bot_id):
    with factory() as session:
        return [
            (r.key, r.status, r.reason)
            for r in session.scalars(
                select(BotRun).where(BotRun.bot_id == bot_id).order_by(BotRun.id)
            )
        ]


def test_a_minute_that_passes_enters_under_its_own_key(deps, factory):
    bot_id = window_bot(factory)
    at(deps, "09:46")
    run = run_of(factory, bot_entry.fire_floating(deps, bot_id, "09:46"))
    assert (run.key, run.status, run.entry_time) == (
        floating.minute_key(bot_id, DAY, "09:46"), "filled", "09:46",
    )  # fmt: skip


def test_a_minute_outside_the_window_does_nothing(deps, factory):
    bot_id = window_bot(factory)
    at(deps, "09:51")
    assert bot_entry.fire_floating(deps, bot_id, "09:51") is None
    assert float_runs(factory, bot_id) == []


def test_a_miss_leaves_no_row_and_is_remembered(deps, factory):
    bot_id = window_bot(factory, use_premium_bounds=True, premium_min=D("50"))
    at(deps, "09:46")
    assert bot_entry.fire_floating(deps, bot_id, "09:46") is None
    assert float_runs(factory, bot_id) == []
    minute, reason = bot_entry.floating_last(bot_id, DAY)
    assert minute == "09:46" and "min" in reason


def test_a_guard_miss_leaves_no_row(deps, factory):
    bot_id = window_bot(factory, use_blackout_days=True, blackout_days=("2026-10-13",))
    at(deps, "09:46")
    assert bot_entry.fire_floating(deps, bot_id, "09:46") is None
    assert float_runs(factory, bot_id) == []
    assert bot_entry.floating_last(bot_id, DAY) == ("09:46", "today is a blackout day")


def test_the_last_minute_writes_one_close_row_when_nothing_was_tried(deps, factory):
    bot_id = window_bot(factory, end="09:47", use_blackout_days=True,
                        blackout_days=("2026-10-13",))  # fmt: skip
    for hhmm in ("09:46", "09:47", "09:47"):
        at(deps, hhmm)
        bot_entry.fire_floating(deps, bot_id, hhmm)
    assert float_runs(factory, bot_id) == [(
        floating.close_key(bot_id, DAY), "skipped",
        "Window 09:45–09:47 closed; last check 09:47: today is a blackout day",
    )]  # fmt: skip


def test_no_close_row_after_an_attempt(deps, factory):
    deps.tasty.entry = "work"  # never fills: the chase misses
    bot_id = window_bot(factory, end="09:47", live=True)
    at(deps, "09:46")
    assert run_of(factory, bot_entry.fire_floating(deps, bot_id, "09:46")).status == "missed"
    resize(factory, bot_id, use_blackout_days=True, blackout_days=("2026-10-13",))
    at(deps, "09:47")
    assert bot_entry.fire_floating(deps, bot_id, "09:47") is None
    assert [r[1] for r in float_runs(factory, bot_id)] == ["missed"]


def test_a_missed_chase_lets_the_next_minute_try(deps, factory):
    deps.tasty.entry = "work"
    bot_id = window_bot(factory, live=True)
    at(deps, "09:46")
    bot_entry.fire_floating(deps, bot_id, "09:46")
    deps.tasty.entry = "fill"
    at(deps, "09:47")
    assert run_of(factory, bot_entry.fire_floating(deps, bot_id, "09:47")).status == "filled"
    assert [r[1] for r in float_runs(factory, bot_id)] == ["missed", "filled"]


def test_a_filled_window_never_enters_again(deps, factory):
    bot_id = window_bot(factory, live=True)
    at(deps, "09:46")
    assert run_of(factory, bot_entry.fire_floating(deps, bot_id, "09:46")).status == "filled"
    sent = len(deps.tasty.calls)
    for hhmm in ("09:47", "09:48", "09:50"):
        at(deps, hhmm)
        assert bot_entry.fire_floating(deps, bot_id, hhmm) is None
    assert [r[1] for r in float_runs(factory, bot_id)] == ["filled"]
    assert len(deps.tasty.calls) == sent


@pytest.mark.parametrize("ended", ["preview", "pending", "running", "partial"])
def test_a_window_with_an_entry_in_progress_or_previewed_waits(deps, factory, ended):
    bot_id = window_bot(factory)
    at(deps, "09:46")
    run_id = bot_entry.fire_floating(deps, bot_id, "09:46")
    bot_entry.update_run(deps, run_id, status=ended)
    at(deps, "09:47")
    assert bot_entry.fire_floating(deps, bot_id, "09:47") is None
    assert len(float_runs(factory, bot_id)) == 1


def test_an_error_ends_the_window_for_the_day(deps, factory, monkeypatch):
    from optionsjourney.bots import strikes

    real = bot_entry._pick

    def unsafe(*args):
        raise strikes.Unsafe("The short put at 5805 is in the money.")

    monkeypatch.setattr(bot_entry, "_pick", unsafe)
    bot_id = window_bot(factory, live=True)
    at(deps, "09:46")
    assert run_of(factory, bot_entry.fire_floating(deps, bot_id, "09:46")).status == "error"
    monkeypatch.setattr(bot_entry, "_pick", real)
    at(deps, "09:47")
    assert bot_entry.fire_floating(deps, bot_id, "09:47") is None
    assert [r[1] for r in float_runs(factory, bot_id)] == ["error"]
    assert not any(c[0] == "place" for c in deps.tasty.calls)


def test_an_entry_time_quantity_never_applies_to_a_window(deps, factory):
    bot_id = window_bot(factory, live=True)
    force_contracts(factory, bot_id, 3)
    resize(factory, bot_id, contracts=1, entry_times=(EntryTimeDef("09:46", quantity=2),))
    at(deps, "09:46")
    assert run_of(factory, bot_entry.fire_floating(deps, bot_id, "09:46")).status == "filled"
    assert sent_quantity(deps) == {1}


def test_the_fixed_entry_path_ignores_a_floating_bot_left_unchanged(deps, factory):
    # fire() for a fixed time still works as before on a bot with floating off
    bot_id = make(factory)
    assert run_of(factory, bot_entry.fire(deps, bot_id, "09:47")).status == "filled"


def test_a_failed_chain_read_is_a_miss_the_next_minute_retries(deps, factory):
    from optionsjourney.services import bot_filters

    bot_filters.reset_alerts()
    alerts = []
    deps.notify = alerts.append
    good_chain = deps.chain.chain
    deps.chain.chain = {}
    bot_id = window_bot(factory, live=True)
    for hhmm in ("09:46", "09:47"):
        at(deps, hhmm)
        assert bot_entry.fire_floating(deps, bot_id, hhmm) is None
    assert float_runs(factory, bot_id) == []
    assert bot_entry.floating_last(bot_id, DAY) == (
        "09:47", "No option chain or price for the underlying.",
    )  # fmt: skip
    assert [a.title for a in alerts] == ["IC: entry skipped, option chain unavailable"]
    deps.chain.chain = good_chain
    at(deps, "09:48")
    assert run_of(factory, bot_entry.fire_floating(deps, bot_id, "09:48")).status == "filled"


def test_a_fixed_entry_without_a_chain_is_still_an_error(deps, factory):
    deps.chain.chain = {}
    assert run_of(factory, bot_entry.fire(deps, make(factory), "09:47")).status == "error"


def test_a_claim_left_pending_by_a_crash_no_longer_blocks_the_window(deps, factory):
    bot_id = window_bot(factory)
    at(deps, "09:46")
    stuck = bot_entry.fire_floating(deps, bot_id, "09:46")
    old = OPEN_TIME.replace(hour=13, minute=46)
    bot_entry.update_run(deps, stuck, status="pending")  # as if the app died mid-entry
    with factory() as session:
        session.get(BotRun, stuck).created_at = old
        session.commit()
    at(deps, "09:47")  # a minute later: too soon to call it stuck
    assert bot_entry.fire_floating(deps, bot_id, "09:47") is None
    at(deps, "09:49")
    entered = bot_entry.fire_floating(deps, bot_id, "09:49")
    assert run_of(factory, entered).status == "filled"
    left = run_of(factory, stuck)
    assert left.status == "skipped" and "never finished" in left.reason


def test_a_pending_claim_still_being_worked_is_left_alone(deps, factory):
    bot_id = window_bot(factory)
    at(deps, "09:46")
    stuck = bot_entry.fire_floating(deps, bot_id, "09:46")
    bot_entry.update_run(deps, stuck, status="pending")
    with factory() as session:
        session.get(BotRun, stuck).created_at = OPEN_TIME.replace(hour=13, minute=40)
        session.commit()
    bot_entry.ACTIVE.add(stuck)
    try:
        at(deps, "09:49")
        assert bot_entry.fire_floating(deps, bot_id, "09:49") is None
        assert run_of(factory, stuck).status == "pending"
    finally:
        bot_entry.ACTIVE.discard(stuck)


def test_a_schwab_bot_enters_through_schwab(deps, factory):
    schwab = FakeSchwab()
    deps.clients = lambda: [deps.tasty, schwab]
    run_id = bot_entry.fire(deps, make_schwab(factory, live=True), "09:47")
    run = run_of(factory, run_id)
    assert run.status == "filled" and run.broker == "schwab"
    placed = [c for c in schwab.calls if c[0] == "place"]
    assert len(placed) == 1 and placed[0][2]["complexOrderStrategyType"] == "IRON_CONDOR"
    assert [c[0] for c in deps.tasty.calls] == []
    assert deps.placed == [run_id]


def test_a_schwab_delta_bot_uses_schwabs_chain_deltas_without_tastytrades_stream(deps, factory):
    """Slice 11 with slice 10: a Schwab bot's delta pick needs no TastyTrade stream. The
    analyzer's chain is Schwab's snapshot when Schwab is connected (rows with no "at"),
    and its deltas are read whatever TastyTrade's stream is doing."""
    schwab = FakeSchwab()
    deps.clients = lambda: [schwab]
    deps.hub.streams = {"tastytrade": "not connected"}
    deps.chain.chain = delta_rows()  # Schwab-style: deltas, no quote time
    bot_id = market_bot(factory, DELTAS)
    with factory() as session:
        session.get(Bot, bot_id).broker = "schwab"
        session.commit()
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert run.status == "filled", run.reason
    assert run.broker == "schwab"
    assert [leg["strike"] for leg in run.legs] == ["5900", "5975", "5730", "5655"]
    assert [c[0] for c in schwab.calls] == ["preview"]


def test_a_schwab_tranche_trades_at_schwab_and_finishes(deps, factory):
    """A tranche run keeps the root's broker: its order passes the order path's account
    check and its exits trade at Schwab (final review I-1)."""
    schwab = FakeSchwab()
    schwab.fills = [1, 2]  # 1 of 3 fills, then the tranche's 2
    deps.clients = lambda: [deps.tasty, schwab]
    bot_id = make_schwab(factory, live=True)
    force_contracts(factory, bot_id, 3)
    first = bot_entry.fire(deps, bot_id, "09:47")
    found = runs(factory)
    assert [(r.status, r.contracts, r.broker) for r in found] == [
        ("filled", 1, "schwab"),
        ("filled", 2, "schwab"),
    ]
    assert found[1].parent_run_id == first and deps.tasty.calls == []


# --- Slice 13: any structure -------------------------------------------------------------

from tests.services.bot_fakes import SHAPE_MATRIX, make_shape  # noqa: E402


def places(deps):
    return [c for c in deps.tasty.calls if c[0] == "place"]


def sent_prices(deps):
    return [
        D(str(c[-1]["price"])) * (1 if c[-1]["price-effect"] == "Credit" else -1)
        for c in deps.tasty.calls
        if c[0] in ("place", "replace")
    ]


def test_a_bot_enters_whichever_side_it_prices_on(deps, factory):
    credit = make_shape(factory, "put_credit_spread", live=True)
    deps.chain.chain = _dearer_long(deps.chain.chain, "put", 5615)  # the wing costs more
    run = run_of(factory, bot_entry.fire(deps, credit, "09:47"))
    assert run.status == "filled" and run.target_credit < 0, run.reason
    assert run.priced.startswith("Priced as a debit of ")
    deps.chain.chain = _dearer_long(bot_chain(), "call", 5915)  # the sold wing is worth more
    debit = make_shape(factory, "call_debit_spread", live=True)
    run = run_of(factory, bot_entry.fire(deps, debit, "09:47"))
    assert run.status == "filled" and run.target_credit > 0, run.reason
    assert run.priced.startswith("Priced as a credit of ")


def bot_chain():
    from tests.services.bot_fakes import rows

    return rows()


def _dearer_long(chain, right, strike, cheaper=False):
    """Make one strike's quote worth far more (or almost nothing)."""
    chain = {k: {r: dict(q) for r, q in v.items()} for k, v in chain.items()}
    row = chain[float(strike)][right]
    if cheaper:
        row["bid"], row["ask"] = 0.0, 0.10
    else:
        row["bid"], row["ask"] = 40.0, 40.2
    return chain


@pytest.mark.parametrize("mid", ["0", "0.03", "-0.04"])
def test_a_price_too_close_to_even_is_never_entered(deps, factory, monkeypatch, mid):
    bot_id = make_shape(factory, "put_credit_spread", live=True)
    monkeypatch.setattr(bot_entry.strikes, "entry_price", lambda picked, offset: D(mid))
    words = f"Priced too close to even ({abs(D(mid)).quantize(D('0.01'))}); not entered."
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert run.status == "skipped" and run.reason == words
    assert deps.tasty.calls == []
    run = run_of(factory, bot_entry.fire(deps, bot_id, None, manual=True))  # Preview now too
    assert run.status == "skipped" and run.reason == words


def test_premium_bounds_take_a_debit_by_its_size(deps, factory):
    bot_id = make_shape(factory, "long_call", live=True)  # mid 2.50, a debit
    resize(factory, bot_id, use_premium_bounds=True, premium_min=D("1"), premium_max=D("3"))
    assert run_of(factory, bot_entry.fire(deps, bot_id, "09:47")).status == "filled"
    other = make_shape(factory, "long_call", live=True)
    resize(factory, other, use_premium_bounds=True, premium_min=D("3"))
    run = run_of(factory, bot_entry.fire(deps, other, "09:47"))
    assert run.status == "skipped" and run.reason == "net debit 2.50 < min 3.00"


def test_preview_now_records_the_side_and_what_each_group_would_set(deps, factory):
    bot_id = make_shape(factory, "long_call", stop=D("50"), target=D("50"))
    run = run_of(factory, bot_entry.fire(deps, bot_id, None, manual=True))
    assert run.status == "preview"
    assert run.priced == ("Priced as a debit of 2.50. Long call: target 3.75 rests at "
                          "TastyTrade; stop watched by the bot at P&L −1.25.")  # fmt: skip


def test_a_debit_entry_has_no_minimum_and_chases_by_paying_more(deps, factory):
    deps.tasty.entry = "work"
    bot_id = make_shape(factory, "long_call", live=True)
    resize(factory, bot_id, entry_chase=replace(bot_store.load(factory, bot_id).entry_chase,
                                                max_tries=3))  # fmt: skip
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert run.status == "missed"
    prices = sent_prices(deps)
    assert prices == [D("-2.5"), D("-2.55"), D("-2.6")]


def test_a_debit_chase_stops_at_the_premium_maximum(deps, factory):
    deps.tasty.entry = "work"
    bot_id = make_shape(factory, "long_call", live=True)
    chase = replace(bot_store.load(factory, bot_id).entry_chase, max_tries=6)
    resize(factory, bot_id, entry_chase=chase, use_premium_bounds=True,
           premium_min=D("1"), premium_max=D("2.55"))  # fmt: skip
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert run.status == "missed"
    assert sent_prices(deps) == [D("-2.5"), D("-2.55")]
    # a start already past the maximum begins at it: 2.60 asked, 2.55 sent
    deps.tasty.calls.clear()
    other = make_shape(factory, "long_call", live=True)
    resize(factory, other, entry_chase=replace(chase, start_offset=D("0.10")),
           use_premium_bounds=True, premium_max=D("2.55"))  # fmt: skip
    deps.tasty.entry = "work"
    run = run_of(factory, bot_entry.fire(deps, other, "09:47"))
    assert run.status == "missed", run.reason
    assert sent_prices(deps)[0] == D("-2.55")


def test_a_ratio_entry_fills_in_whole_units(deps, factory):
    bot_id = make_shape(factory, "long_call_butterfly", live=True)
    force_contracts(factory, bot_id, 2)
    with factory() as session:  # room for 2 units of the ratio-2 leg
        session.get(Bot, bot_id).max_per_trade = 4
        session.commit()
    deps.tasty.fills = [1]
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert run.status == "filled" and run.contracts == 1  # one tranche of 1 of the 2 units
    place = places(deps)[0][2]
    assert sorted(int(leg["quantity"]) for leg in place["legs"]) == [2, 2, 4]


@pytest.mark.parametrize("key", SHAPE_MATRIX)
def test_every_matrix_shape_enters_on_its_side(deps, factory, key):
    bot_id = make_shape(factory, key, live=True)
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert run.status == "filled", run.reason
    assert run.priced.startswith("Priced as a " + ("credit" if run.fill_credit > 0 else "debit"))
    assert run.fill_credit != 0
    shape_legs = bot_store.load(factory, bot_id).legs
    assert [(leg["ratio"], leg["group"]) for leg in run.legs] == [
        (leg.ratio, leg.group) for leg in shape_legs
    ]


def test_a_dime_tick_debit_chase_never_pays_past_its_rounded_cap(deps, factory):
    deps.tasty.entry = "work"
    bot_id = make_shape(factory, "long_call", live=True)
    bot = bot_store.load(factory, bot_id)
    legs = (replace(bot.legs[0], target=D("20")),)  # a $20 call: the dime tick
    chase = replace(bot.entry_chase, max_tries=6)
    resize(factory, bot_id, legs=legs, entry_chase=chase, use_premium_bounds=True,
           premium_min=D("1"), premium_max=D("20.05"))  # fmt: skip
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert run.status == "missed"
    prices = sent_prices(deps)
    assert prices and all(-p <= D("20.00") for p in prices)  # the cap rounds to 20.00
    assert prices[0] == D("-20.00")


def test_a_nickel_combo_debit_chase_never_pays_past_its_rounded_cap(deps, factory):
    deps.tasty.entry = "work"
    bot_id = make_shape(factory, "call_debit_spread", live=True)
    bot = bot_store.load(factory, bot_id)
    legs = (replace(bot.legs[0], target=D("2.50")), bot.legs[1])
    chase = replace(bot.entry_chase, start_offset=D("0.10"), max_tries=4)
    resize(factory, bot_id, legs=legs, entry_chase=chase, use_premium_bounds=True,
           premium_min=D("0.05"), premium_max=D("2.52"))  # fmt: skip
    bot_entry.fire(deps, bot_id, "09:47")
    prices = sent_prices(deps)
    assert prices and all(-p <= D("2.50") for p in prices), prices


def test_a_chase_steps_by_at_least_the_tick(deps, factory):
    deps.tasty.entry = "work"
    bot_id = make_shape(factory, "long_call", live=True)
    bot = bot_store.load(factory, bot_id)
    chase = replace(bot.entry_chase, max_tries=3, step=D("0.05"))
    resize(factory, bot_id, legs=(replace(bot.legs[0], target=D("20")),), entry_chase=chase)
    bot_entry.fire(deps, bot_id, "09:47")
    assert sent_prices(deps) == [D("-20.00"), D("-20.10"), D("-20.20")]
    # a credit above $3 on a single leg is a dime tick too
    deps.tasty.calls.clear()
    credit = make_shape(factory, "short_put", live=True)
    cb = bot_store.load(factory, credit)
    resize(factory, credit, legs=(replace(cb.legs[0], target=D("12")),),
           entry_chase=replace(cb.entry_chase, max_tries=3, step=D("0.05")))  # fmt: skip
    bot_entry.fire(deps, credit, "09:47")
    prices = sent_prices(deps)
    assert [prices[0] - prices[1], prices[1] - prices[2]] == [D("0.10"), D("0.10")]


def test_max_open_trades_counts_a_closed_naked_short_as_closed(deps, factory):
    # A naked short put whose stop filled holds nothing: there's no wing to sell after it.
    bot_id = make_shape(factory, "short_put", live=True)
    resize(factory, bot_id, max_open_trades=1,
           entry_times=(EntryTimeDef("09:47"), EntryTimeDef("09:48")))  # fmt: skip
    first = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert first.status == "filled"
    (short,) = first.legs
    leg = {"symbol": short["symbol"], "side": "sell", "ratio": 1}
    with factory() as session:
        stop = BotExit(
            run_id=first.id, group_position=0, kind="stop", state="working",
            short_symbol=short["symbol"], long_symbol="", legs=[leg],
            anchor_symbol=short["symbol"], contracts=1, rearms=0, log=[],
            created_at=OPEN_TIME, updated_at=OPEN_TIME,
        )  # fmt: skip
        session.add(stop)
        session.commit()
        stop_id = stop.id
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:48"))
    assert run.status == "skipped" and run.reason == "already holding 1 open trades"
    with factory() as session:
        session.get(BotExit, stop_id).state = "filled"
        session.commit()
    resize(factory, bot_id, entry_times=(EntryTimeDef("09:47"), EntryTimeDef("09:49")))
    assert run_of(factory, bot_entry.fire(deps, bot_id, "09:49")).status == "filled"


def _inside_put_wing(factory, live=True):
    """The test iron condor with its put wing 5 points INSIDE its short (5695 over the
    5690 short): the put group prices as a 1.25 debit while the whole trade is still a
    1.20 credit."""
    bot_id = make(factory, live=live)
    bot = bot_store.load(factory, bot_id)
    legs = tuple(replace(leg, target=D("5")) if leg.position == 3 else leg for leg in bot.legs)
    assert bot_store.save(factory, replace(bot, legs=legs), OPEN_TIME)[1] == {}
    return bot_id


def test_a_credit_group_that_prices_as_a_debit_is_refused_and_nothing_is_sent(deps, factory):
    """Final review I1 (controller's ruling): slice 12's inverted-side refusal, group by
    group. A resting stop would sit on a short that rises while that group wins."""
    run = run_of(factory, bot_entry.fire(deps, _inside_put_wing(factory), "09:47"))
    assert run.status == "error"
    assert run.reason == "Inverted: the Put side prices as a debit."
    assert deps.tasty.calls == [] and deps.placed == []


def test_a_normal_condor_still_enters(deps, factory):
    run = run_of(factory, bot_entry.fire(deps, make(factory, live=True), "09:47"))
    assert run.status == "filled" and deps.placed == [run.id]


# --- Bots slice 14: legs in other expirations -----------------------------------------


def _calendar_deps(deps, exps=None):
    from tests.services.bot_fakes import HELD_EXPIRATIONS

    deps.chain.exps = list(exps or HELD_EXPIRATIONS)
    return deps


def test_a_calendar_enters_as_one_order_across_two_expirations(deps, factory):
    """resolver.resolve_trade: each DTE resolved to a listed date, a chain for each, each
    leg on its own; one combo order."""
    from tests.services.bot_fakes import FAR, NEAR, make_calendar, mid_for, sym

    _calendar_deps(deps)
    run_id = bot_entry.fire(deps, make_calendar(factory, live=True), "09:47")
    run = run_of(factory, run_id)
    assert run.status == "filled", run.reason
    assert [leg["symbol"] for leg in run.legs] == [sym(5690, "put", NEAR), sym(5690, "put", FAR)]
    assert [(leg["expiration"], leg["dte"]) for leg in run.legs] == [(NEAR, 7), (FAR, 14)]
    assert sorted(set(deps.chain.asked)) == [NEAR, FAR]
    debit = mid_for(5690, "put", NEAR) - mid_for(5690, "put", FAR)
    assert run.target_credit == debit  # signed: a debit
    sends = [c for c in deps.tasty.calls if c[0] == "place"]
    assert len(sends) == 1
    body = sends[0][-1]
    assert {leg["symbol"] for leg in body["legs"]} == {sym(5690, "put", NEAR),
                                                       sym(5690, "put", FAR)}  # fmt: skip


def test_exact_dte_missing_skips_and_sends_nothing(deps, factory):
    from tests.services.bot_fakes import FAR, TODAY, make_calendar

    _calendar_deps(deps, (TODAY, "2026-10-21", FAR))
    run_id = bot_entry.fire(deps, make_calendar(factory, live=True), "09:47")
    run = run_of(factory, run_id)
    assert run.status == "skipped"
    assert run.reason == "No expiration at exactly 7 DTE (the nearest is 2026-10-21, 8 DTE)."
    assert deps.tasty.calls == []


def test_not_exact_takes_the_next_listed_expiration(deps, factory):
    from tests.services.bot_fakes import FAR, TODAY, make_calendar

    _calendar_deps(deps, (TODAY, "2026-10-21", FAR))
    run_id = bot_entry.fire(deps, make_calendar(factory, live=True, exact=False), "09:47")
    run = run_of(factory, run_id)
    assert run.status == "filled", run.reason
    assert [leg["expiration"] for leg in run.legs] == ["2026-10-21", FAR]


def test_a_missing_far_chain_is_an_error_and_sends_nothing(deps, factory):
    from tests.services.bot_fakes import FAR, make_calendar

    _calendar_deps(deps)
    deps.chain.chains[FAR] = {}
    run_id = bot_entry.fire(deps, make_calendar(factory, live=True), "09:47")
    run = run_of(factory, run_id)
    assert run.status == "error" and run.reason == "No option chain or price for the underlying."
    assert deps.tasty.calls == []


def test_a_monthly_spx_leg_refuses_the_entry(deps, factory):
    from tests.services.bot_fakes import FAR, make_calendar, rows

    _calendar_deps(deps)
    monthly = rows(FAR)
    for row in monthly.values():
        for side in row.values():
            side["symbol"] = side["symbol"].replace("SPXW ", "SPX  ")
    deps.chain.chains[FAR] = monthly
    run_id = bot_entry.fire(deps, make_calendar(factory, live=True), "09:47")
    run = run_of(factory, run_id)
    assert run.status == "error" and "monthly SPX option" in run.reason
    assert deps.tasty.calls == []


def test_a_delta_leg_is_modelled_to_its_own_expiration(deps, factory, monkeypatch):
    """The model delta runs to the leg's own expiration's close."""
    from optionsjourney.bots import strikes as strikes_mod
    from tests.services.bot_fakes import FAR, NEAR, make_calendar

    _calendar_deps(deps)
    seen = []
    real = strikes_mod.chain_from_rows

    def spy(rows, spot, **kw):
        seen.append(kw["expires_at"])
        return real(rows, spot, **kw)

    monkeypatch.setattr(strikes_mod, "chain_from_rows", spy)
    bot_entry.fire(deps, make_calendar(factory, live=True), "09:47")
    days = sorted(at.astimezone(bot_entry.MARKET_TZ).date().isoformat() for at in seen)
    assert days == [NEAR, FAR]


def test_a_same_day_iron_condor_enters_as_before(deps, factory):
    """Review focus 5: a same-day bot asks for today's chain only and enters as slice 13
    did; its legs record today's expiration at 0 DTE."""
    run_id = bot_entry.fire(deps, make(factory, live=True), "09:47")
    run = run_of(factory, run_id)
    assert run.status == "filled"
    assert [leg["strike"] for leg in run.legs] == ["5910", "5985", "5690", "5615"]
    assert {(leg["expiration"], leg["dte"]) for leg in run.legs} == {("2026-10-13", 0)}
    assert deps.chain.asked == ["2026-10-13"]
    assert deps.placed == [run_id]


def test_a_far_expiration_past_1100_dte_is_never_taken(deps, factory):
    """Review 1: exact off never reaches past MAX_DTE, where the held-trade passes stop
    looking."""
    from tests.services.bot_fakes import TODAY, make_calendar

    _calendar_deps(deps, (TODAY, "2029-10-07", "2029-11-11"))  # 1090 and 1125 DTE
    bot_id = make_calendar(factory, live=True, near=1085, far=1095, exact=False)
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert run.status == "skipped" and "1125 DTE: more than 1100" in run.reason
    assert deps.tasty.calls == []


def test_dte_0_with_exact_off_never_opens_tomorrows_trade(deps, factory):
    """Review 2."""
    bot_id = make(factory, live=True)
    with factory() as session:
        session.get(Bot, bot_id).use_exact_dte = False
        session.commit()
    deps.chain.exps = ["2026-10-14"]
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert run.status == "skipped" and "exactly 0 DTE" in run.reason
    assert deps.tasty.calls == []


# --- Bots slice 19: dollar sizing --------------------------------------------------------


def _dollars(factory, amount, *, per_trade=10, per_day=10, **changes):
    """A live long call (mid 2.50, 6 tries of 0.05: worst 2.75 a unit) sized by dollars."""
    bot_id = make_shape(factory, "long_call", live=True)
    resize(factory, bot_id, sizing="dollars", max_per_entry_usd=D(amount),
           max_per_trade=per_trade, max_per_day=per_day, **changes)  # fmt: skip
    return bot_id


def test_a_buying_power_entry_sends_what_the_budget_buys_at_the_worst_price(deps, factory):
    """Decision 0050: the dry run of one unit at the worst price (no margin on a long call,
    so the 2.75 paid) says what a unit needs; 600 / 275 = 2."""
    run = run_of(factory, bot_entry.fire(deps, _dollars(factory, "600"), "09:47"))
    assert run.status == "filled" and run.contracts == 2
    assert int(places(deps)[0][2]["legs"][0]["quantity"]) == 2
    asked = [c[1] for c in deps.tasty.calls if c[0] == "preview"][0]
    assert asked["price"] == "2.75" and asked["price-effect"] == "Debit"
    assert asked["legs"][0]["quantity"] == 1
    assert run.chase_log[0] == {
        "sizing": "buying_power", "broker": "TastyTrade", "budget": "600.00", "worst": "-2.75",
        "unit_cost": "275.00", "asked": 2, "contracts": 2,
    }  # fmt: skip


def test_a_buying_power_entry_is_capped_at_max_per_trade(deps, factory):
    run = run_of(factory, bot_entry.fire(deps, _dollars(factory, "3000", per_trade=3), "09:47"))
    assert run.contracts == 3 and run.chase_log[0]["asked"] == 10


def test_a_buying_power_entry_too_small_is_skipped_and_alerted(deps, factory):
    said = []
    deps.notify = said.append
    run = run_of(factory, bot_entry.fire(deps, _dollars(factory, "200"), "09:47"))
    assert run.status == "skipped"
    assert run.reason == (
        "One contract needs $275.00 of buying power at the worst price -2.75, more than the "
        "$200.00 budget. Not entered."
    )
    assert places(deps) == []
    assert any("budget buys nothing" in e.title and "$275.00" in e.detail for e in said)


def test_a_buying_power_entry_past_the_daily_limit_is_skipped(deps, factory):
    # 1200 buys 4 units; Max per trade 5 allows them, Max per day 3 doesn't: skipped, not cut
    run = run_of(factory, bot_entry.fire(deps, _dollars(factory, "1200", per_trade=5,
                                                        per_day=3), "09:47"))  # fmt: skip
    assert run.status == "skipped" and "limit of 3 a day" in run.reason
    assert places(deps) == []


def test_a_buying_power_entry_with_no_tick_table_is_refused(deps, factory, monkeypatch):
    bot_id = _dollars(factory, "600")

    def broken(*args, **kwargs):
        raise RuntimeError("no table")

    monkeypatch.setattr(bot_entry.orders, "tick_tables", broken)
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert run.status == "skipped"
    assert run.reason == f"{bot_entry.UNKNOWN_WORST} Not entered."
    assert deps.tasty.calls == []


def test_a_buying_power_bot_priced_as_a_credit_is_sized_by_its_margin(deps, factory):
    """Decision 0050: no fallback to contracts and no skip. A unit needs its isolated
    margin less the credit received, plus fees."""
    deps.tasty.margin, deps.tasty.fees = D("500"), D("2.30")
    bot_id = make_shape(factory, "put_credit_spread", live=True)
    resize(factory, bot_id, sizing="dollars", max_per_entry_usd=D("2000"), contracts=7,
           max_per_trade=10, max_per_day=10)  # fmt: skip
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    sized = run.chase_log[0]
    worst = D(sized["worst"])
    assert worst > 0  # a credit
    assert D(sized["unit_cost"]) == D("500") - worst * 100 + D("2.30")
    assert run.status == "filled" and run.contracts == sized["contracts"] == int(
        D("2000") // D(sized["unit_cost"])
    )  # never the 7 contracts it would take by count


def test_a_schwab_bot_sized_by_buying_power_asks_schwab_for_one_and_two(deps, factory):
    schwab = FakeSchwab()
    deps.clients = lambda: [deps.tasty, schwab]
    bot_id = _dollars(factory, "600")
    with factory() as session:
        session.get(Bot, bot_id).broker = "schwab"
        session.commit()
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    asked = [c[1]["orderLegCollection"][0]["quantity"] for c in schwab.calls if c[0] == "preview"]
    assert asked[:2] == [1, 2] and deps.tasty.calls == []
    assert run.status == "filled" and run.contracts == 2  # 600 / 275
    assert run.chase_log[0]["broker"] == "Schwab"


def test_a_refused_preview_skips_the_entry_and_alerts(deps, factory):
    said = []
    deps.notify = said.append
    deps.tasty.errors["preview"] = BrokerError(
        "TastyTrade refused it (422): not enough buying power"
    )
    run = run_of(factory, bot_entry.fire(deps, _dollars(factory, "600"), "09:47"))
    assert run.status == "skipped"
    assert run.reason.startswith("TastyTrade's preview of one contract was refused:")
    assert places(deps) == []
    assert any("couldn't be worked out" in e.title for e in said)


def test_a_trade_page_entry_is_never_sized_by_dollars(deps, factory):
    """enter_picked sizes only the bot's own entries (question 5)."""
    bot_id = _dollars(factory, "600")
    bot = bot_store.load(factory, bot_id)
    run_id = bot_entry._claim(deps, bot, "ticket-x", "09:47", True)
    from optionsjourney.bots import strikes

    chain = strikes.chain_from_rows(deps.chain.chain, 5800.0)
    picked = strikes.pick_legs(bot.legs, chain)
    bot_entry.enter_picked(deps, bot, run_id, picked, 1, True, [run_id], set(), lambda r: None,
                           scheduled=False, dtes={0: 0}, spot=5800.0)  # fmt: skip
    assert run_of(factory, run_id).contracts == 1


# --- Bots slice 19: Trade now -------------------------------------------------------------


def test_trade_now_refuses_without_typing_trade(deps, factory):
    bot_id = make(factory, live=True)
    for typed in ("", "trade", "TRADE ", "yes"):
        run_id, problem = bot_entry.trade_now(deps, bot_id, typed)
        assert run_id is None and "Type TRADE" in problem
    with factory() as session:
        assert session.scalar(select(BotRun.id)) is None  # nothing claimed
    assert deps.tasty.calls == []


def test_trade_now_is_for_a_live_bot_that_isnt_archived_or_futures(deps, factory):
    preview = make(factory, live=False)
    assert "Preview now" in bot_entry.trade_now(deps, preview, "TRADE")[1]
    archived = make(factory, live=True)
    with factory() as session:
        session.get(Bot, archived).archived_at = OPEN_TIME
        session.commit()
    assert "archived" in bot_entry.trade_now(deps, archived, "TRADE")[1]
    assert bot_entry.trade_now(deps, 999, "TRADE") == (None, "There's no such bot.")
    assert deps.tasty.calls == []


def test_trade_now_sends_a_live_entry_off_schedule(deps, factory):
    bot_id = make(factory, live=True)
    run_id, problem = bot_entry.trade_now(deps, bot_id, "TRADE")
    assert problem is None
    run = run_of(factory, run_id)
    assert run.status == "filled" and run.live and run.key.startswith(f"bot-{bot_id}-trade-")
    assert bot_entry.is_trade_now(run)
    assert deps.placed == [run_id]


def test_trade_now_ignores_arming_blackouts_and_filters_and_counts_as_an_entry(deps, factory):
    bot_id = make(factory, live=True, enabled=False)
    resize(factory, bot_id, use_blackout_days=True, blackout_days=("2026-10-13",),
           max_entries_per_day=1, use_vix_level=True, vix_max=D("10"))  # fmt: skip
    run = run_of(factory, bot_entry.trade_now(deps, bot_id, "TRADE")[0])
    assert run.status == "filled", run.reason  # deps.market is None: a filter would fail
    bot_store.set_armed(factory, bot_id, True, OPEN_TIME)
    resize(factory, bot_id, use_blackout_days=False, use_vix_level=False)
    later = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert later.status == "skipped" and "1 entries today" in later.reason


def test_trade_now_keeps_freeze_the_daily_limit_and_open_trades(deps, factory):
    bot_id = make(factory, live=True)
    resize(factory, bot_id, max_open_trades=2)
    assert run_of(factory, bot_entry.trade_now(deps, bot_id, "TRADE")[0]).status == "filled"
    deps.now = lambda: OPEN_TIME + timedelta(seconds=5)
    run = run_of(factory, bot_entry.trade_now(deps, bot_id, "TRADE")[0])
    assert run.status == "skipped" and "open trades" in run.reason
    resize(factory, bot_id, max_open_trades=None, max_per_day=1)
    deps.now = lambda: OPEN_TIME + timedelta(seconds=9)
    run = run_of(factory, bot_entry.trade_now(deps, bot_id, "TRADE")[0])
    assert run.status == "skipped" and "limit of 1 a day" in run.reason
    resize(factory, bot_id, max_per_day=3)
    orders.set_switch(factory, "freeze", True, OPEN_TIME)
    deps.now = lambda: OPEN_TIME + timedelta(seconds=12)
    run = run_of(factory, bot_entry.trade_now(deps, bot_id, "TRADE")[0])
    assert run.status == "skipped" and run.reason == "Orders are frozen."


# --- Bots slice 19: the entry chase's time limit -----------------------------------------


def _ticking(deps):
    """A clock that moves on by each sleep, so the chase's own waits pass time."""
    clock = {"at": OPEN_TIME}
    deps.now = lambda: clock["at"]

    def sleep(seconds):
        clock["at"] += timedelta(seconds=seconds)

    deps.sleep = sleep
    return clock


def test_the_entry_window_ends_the_chase_and_cancels_the_order(deps, factory):
    deps.tasty.entry = "work"
    _ticking(deps)
    bot_id = make(factory, live=True)
    chase = replace(bot_store.load(factory, bot_id).entry_chase, interval_seconds=20,
                    max_tries=10, window_minutes=1)  # fmt: skip
    resize(factory, bot_id, entry_chase=chase)
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert run.status == "missed"
    assert run.reason == "Stopped: the 1-minute entry window closed."
    # sent, then replaced after the 20 s and 40 s reads; the 60 s read is past the window
    assert len(sent_prices(deps)) == 3
    last = [c for c in deps.tasty.calls if c[0] in ("place", "replace")][-1]
    assert ("cancel", last[2] if last[0] == "replace" else last[1]) in deps.tasty.calls


def test_a_fill_inside_the_entry_window_is_kept(deps, factory):
    _ticking(deps)
    bot_id = make(factory, live=True)
    chase = replace(bot_store.load(factory, bot_id).entry_chase, window_minutes=1)
    resize(factory, bot_id, entry_chase=chase)
    assert run_of(factory, bot_entry.fire(deps, bot_id, "09:47")).status == "filled"


# --- Bots slice 19: the underlying's price at the fill ------------------------------------


def _moves(factory, **changes):
    bot_id = make(factory, live=True, **changes)
    resize(factory, bot_id, use_underlying_exit=True, underlying_down_pct=D("1"))
    return bot_id


def test_the_fill_stamps_the_live_underlying_price(deps, factory):
    deps.hub.set_last("SPX", "5812.5", OPEN_TIME - timedelta(seconds=5))
    run = run_of(factory, bot_entry.fire(deps, _moves(factory), "09:47"))
    assert run.status == "filled" and run.entry_spot == D("5812.5")
    assert "SPX" in deps.hub.subscribed


def test_a_stale_price_or_the_rule_off_keeps_the_chains_spot(deps, factory):
    deps.hub.set_last("SPX", "5812.5", OPEN_TIME - timedelta(seconds=31))
    assert run_of(factory, bot_entry.fire(deps, _moves(factory), "09:47")).entry_spot == D("5800")
    deps.hub.set_last("SPX", "5812.5", OPEN_TIME)
    plain = make(factory, live=True)  # the underlying move rule off: nothing reads it
    assert run_of(factory, bot_entry.fire(deps, plain, "09:47")).entry_spot == D("5800")


def test_an_xsp_fill_stamps_spx_over_ten(deps, factory):
    from tests.services.bot_fakes import on_underlying

    changes = on_underlying(deps, "XSP")
    deps.chain.spot = 5800.0
    deps.hub.set_last("SPX", "5812.5", OPEN_TIME)
    run = run_of(factory, bot_entry.fire(deps, _moves(factory, **changes), "09:47"))
    assert run.status == "filled", run.reason
    assert run.entry_spot == D("581.25")


# --- Bots slice 19: the strike pickers at entry ------------------------------------------


def test_moving_conflicted_strikes_reads_the_positions_once_and_logs_the_move(deps, factory):
    from tests.services.bot_fakes import sym

    bot_id = make(factory, live=True)
    resize(factory, bot_id, move_conflicted_strikes=True)
    deps.tasty.held[sym(5910, "call").replace(" ", "")] = 1  # a long 5910 call held already
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert run.status == "filled"
    assert [leg["strike"] for leg in run.legs] == ["5915", "5985", "5690", "5615"]
    moved = "strike 5910 call is held the other way; moved to 5915"
    assert run.chase_log[0] == {"strikes": [moved]}


def test_unreadable_positions_skip_the_held_check_with_a_note(deps, factory):
    bot_id = make(factory, live=True)
    resize(factory, bot_id, move_conflicted_strikes=True)
    deps.tasty.errors["positions"] = RuntimeError("down")
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert run.status == "filled"
    assert "weren't checked" in run.chase_log[0]["strikes"][0]


def test_a_bot_without_the_pickers_never_reads_positions(deps, factory):
    deps.tasty.errors["positions"] = RuntimeError("would break")
    run = run_of(factory, bot_entry.fire(deps, make(factory, live=True), "09:47"))
    assert run.status == "filled" and "strikes" not in run.chase_log[0]


def test_the_min_short_width_skips_an_entry_it_cant_widen(deps, factory):
    bot_id = make(factory, live=True)
    resize(factory, bot_id, min_short_width=D("1000"))
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert run.status == "skipped" and "no strikes left to widen" in run.reason
    assert deps.tasty.calls == []


# --- Bots slice 19 final review -----------------------------------------------------------

DAY = OPEN_TIME.date()


def test_two_trade_now_presses_at_once_send_one_order(deps, factory):
    bot_id = make(factory, live=True)
    queued = []
    deps.background = lambda fn, *args: queued.append((fn, args))  # neither has started yet
    first, problem = bot_entry.trade_now(deps, bot_id, "TRADE")
    assert problem is None
    deps.now = lambda: OPEN_TIME + timedelta(microseconds=1)  # the second tab, a moment later
    second, problem = bot_entry.trade_now(deps, bot_id, "TRADE")
    assert second is None and "still being entered" in problem
    assert len(queued) == 1
    for fn, args in queued:
        fn(*args)
    assert run_of(factory, first).status == "filled"
    assert len(places(deps)) == 1
    bot_entry.ACTIVE.clear()


def test_a_trade_now_being_entered_counts_against_max_open_trades(deps, factory):
    bot_id = make(factory, live=True)
    with factory() as session:  # a Trade now run still chasing
        session.add(BotRun(bot_id=bot_id, key=f"bot-{bot_id}-trade-1", trading_date=DAY,
                           entry_time="09:40", status="running", live=True,
                           account_number="5WX01234", legs=[], filled_legs=[], chase_log=[],
                           contracts=1, created_at=OPEN_TIME, updated_at=OPEN_TIME))  # fmt: skip
        session.commit()
    resize(factory, bot_id, max_open_trades=1)
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert run.status == "skipped" and "still being entered" in run.reason
    assert deps.tasty.calls == []


def test_trade_now_that_cant_start_says_so(deps, factory):
    bot_id = make(factory, live=True)

    def broken(fn, *args):
        raise RuntimeError("no thread")

    deps.background = broken
    run_id, problem = bot_entry.trade_now(deps, bot_id, "TRADE")
    assert run_id is None and "couldn't start" in problem
    bot_entry.ACTIVE.clear()


def test_a_part_fill_at_the_windows_close_stops_without_the_rest(deps, factory):
    _ticking(deps)
    bot_id = make(factory, live=True)
    force_contracts(factory, bot_id, 2)
    chase = replace(bot_store.load(factory, bot_id).entry_chase, interval_seconds=60,
                    max_tries=10, window_minutes=1)  # fmt: skip
    resize(factory, bot_id, entry_chase=chase, max_per_trade=2, max_per_day=20)
    deps.tasty.fills = [1]  # 1 of 2 fills on the first read, at the window's close
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert run.status == "filled" and run.contracts == 1
    assert len(places(deps)) == 1  # the rest is never sent: the window had closed
    assert any(e.get("event") == "window closed" for e in run.chase_log)


# --- Bots slice 20: the run page's timeline, read from what the real writers log --------


def _events(run, exits=()):
    from optionsjourney.bots import timeline

    return [s.event for s in timeline.order_timeline(run, list(exits))]


def test_the_timeline_reads_chase_entrys_tranches(deps, factory):
    deps.tasty.entry, deps.tasty.fills = "work", [0, 1, 2]  # 1 of 3, then the other 2
    bot_entry.fire(deps, tranche_bot(factory), "09:47")
    root, child = runs(factory)
    assert _events(root) == [
        "Placed", "Replaced", "Cancelled", "Partly filled", "Placed for the rest (tranche 2)",
        "Filled",
    ]  # fmt: skip
    assert _events(child) == ["Placed for the rest (tranche 2)", "Filled"]


def test_the_timeline_reads_the_untimed_lead_entries(deps, factory):
    bot_id = make(factory, live=True)
    force_contracts(factory, bot_id, 2)
    with factory() as session:
        row = session.scalars(select(BotEntryTime).where(BotEntryTime.bot_id == bot_id)).first()
        row.quantity = 5
        session.commit()
    run = run_of(factory, bot_entry.fire(deps, bot_id, "09:47"))
    assert _events(run) == ["Capped", "Placed", "Filled"]
    sized = run_of(factory, bot_entry.fire(deps, _dollars(factory, "600"), "09:47"))
    assert _events(sized) == ["Sized by buying power", "Placed", "Filled"]
