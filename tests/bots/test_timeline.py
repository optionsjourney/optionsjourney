"""Bots slice 20: a run's orders step by step, and the watch-exit banner (Tracker2's
run_view.py and automation_watch_banner.js)."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

from optionsjourney.bots import timeline

PUT = "SPXW  261014P05800000"
WING = "SPXW  261014P05725000"
AT = "2026-10-14T14:00:0{}+00:00"  # 10:00:0x New York


def run(log, **kw):
    base = dict(
        chase_log=log, contracts=1, status="filled", fill_credit=None, filled_at=None,
        legs=[{"symbol": PUT, "side": "sell", "ratio": 1}, {"symbol": WING, "side": "buy",
                                                            "ratio": 1}],
    )  # fmt: skip
    return SimpleNamespace(**(base | kw))


def exit_row(id=1, kind="stop", state="working", log=(), **kw):
    base = dict(
        id=id, kind=kind, state=state, log=list(log), group_position=0, contracts=1,
        legs=[{"symbol": PUT, "side": "sell", "ratio": 1}], short_symbol=PUT, long_symbol="",
        fill_price=None, created_at=datetime(2026, 10, 14, 14, 0, 10, tzinfo=UTC),
        updated_at=datetime(2026, 10, 14, 14, 5, tzinfo=UTC),
    )  # fmt: skip
    return SimpleNamespace(**(base | kw))


def events(steps):
    return [s.event for s in steps]


def test_entry_steps_infer_placed_then_replaced_then_cancelled():
    log = [
        {"at": AT.format(0), "price": "1.30", "result": "Sent."},
        {"at": AT.format(1), "price": "1.25", "result": "Replaced."},
        {"at": AT.format(2), "price": "1.20", "result": "The broker's check refused it: no"},
        {"at": AT.format(3), "cancel": "77", "status": "cancelled"},
    ]
    steps = timeline.order_timeline(run(log, status="missed"), [])
    assert events(steps) == ["Placed", "Replaced", "Replace refused", "Cancelled"]
    assert steps[0].when == "10-14 10:00:00"
    assert steps[0].price == "1.30 credit"
    assert steps[3].note == "order 77: cancelled"


def test_a_debit_price_reads_as_a_debit():
    steps = timeline.order_timeline(run([{"price": "-2.05", "result": "Sent."}]), [])
    assert steps[0].price == "2.05 debit"


def test_a_preview_step_says_nothing_was_sent():
    message = "SPX IC is in preview mode: checked, not sent. Buying power 500"
    steps = timeline.order_timeline(
        run([{"at": AT.format(0), "price": "1.30", "result": message}], status="preview"), []
    )
    assert events(steps) == ["Preview"]
    assert "nothing sent" in steps[0].note


def test_tranches_say_partly_filled_then_filled_with_the_fill_credit():
    log = [
        {"at": AT.format(0), "price": "1.30", "result": "Sent."},
        {"at": AT.format(1), "cancel": "77", "status": "cancelled"},
        {"at": AT.format(2), "tranche": 1, "filled": 2},
        {"at": AT.format(3), "tranche": 2, "contracts": 1, "price": "1.30", "result": "Sent."},
        {"at": AT.format(4), "tranche": 2, "filled": 1},
    ]
    steps = timeline.order_timeline(run(log, fill_credit=Decimal("1.30")), [])
    assert events(steps) == [
        "Placed", "Cancelled", "Partly filled", "Placed for the rest (tranche 2)", "Filled",
    ]  # fmt: skip
    assert steps[2].note == "2 contracts (tranche 1)"
    assert steps[4].price == "1.30 credit"


def test_known_events_and_lead_entries_are_worded():
    log = [
        {
            "sizing": "dollars",
            "allocation": "300",
            "worst": "-1.45",
            "unit_cost": "145",
            "contracts": 2,
        },  # fmt: skip
        {"strikes": ["put short widened 5790 → 5785"]},
        {"at": AT.format(0), "price": "1.30", "result": "Sent."},
        {"at": AT.format(5), "event": "window closed"},
        {"at": AT.format(6), "order": "77", "error": "BrokerError"},
    ]
    steps = timeline.order_timeline(run(log), [])
    assert events(steps) == [
        "Sized by dollars", "Strikes moved", "Placed", "Entry window closed",
        "Couldn't read the order",
    ]  # fmt: skip
    assert steps[1].note == "put short widened 5790 → 5785"


def test_an_unknown_step_shows_its_keys():
    steps = timeline.order_timeline(run([{"at": AT.format(0), "mystery": 4, "why": "x"}]), [])
    assert events(steps) == ["Step"]
    assert steps[0].note == "mystery: 4; why: x"


def test_a_fill_no_chase_recorded_stands_in_from_the_run():
    filled = datetime(2026, 10, 14, 14, 30, tzinfo=UTC)
    handed = run([{"at": AT.format(0), "event": "a trade you opened, handed to this bot"}],
                 filled_at=filled, fill_credit=Decimal("2.10"))  # fmt: skip
    steps = timeline.order_timeline(handed, [], entry_source="Entry (handed over)")
    assert events(steps) == ["A trade you opened, handed to this bot", "Filled"]
    assert steps[1].when == "10-14 10:30:00" and steps[1].price == "2.10 credit"
    assert steps[1].source == "Entry (handed over)"


def test_entry_legs_read_as_orders_and_exit_legs_as_closes():
    steps = timeline.order_timeline(
        run([{"price": "1.3", "result": "Sent."}], contracts=2),
        [exit_row(log=[{"at": AT.format(9), "result": "Sent."}])],
    )
    assert steps[0].legs == "STO 2 5800P · BTO 2 5725P"
    assert steps[1].legs == "BTC 1 5800P"


def test_a_symbol_that_isnt_occ_is_shown_as_it_is():
    odd = run(
        [{"price": "1", "result": "Sent."}],
        legs=[{"symbol": "./ESZ6 E1AX6 6000P", "side": "sell", "ratio": 1}],
    )
    assert timeline.entry_legs(odd) == "STO 1 ./ESZ6 E1AX6 6000P"


def test_an_exit_chase_lists_its_orders_its_stage_and_its_end():
    close = exit_row(kind="close", state="filled", fill_price=Decimal("0.60"), log=[
        {"at": AT.format(5), "event": "a close of 1 for the S/L ratio exit",
         "by": "S/L ratio exit"},
        {"at": AT.format(6), "stage": "close", "priced": "mid", "start": "-0.55"},
        {"at": AT.format(9), "stage": "chase", "status": "filled", "chase": [
            {"at": AT.format(7), "price": "-0.55", "result": "Sent."},
            {"at": AT.format(8), "price": "-0.60", "result": "Replaced."},
        ]},
    ])  # fmt: skip
    steps = timeline.order_timeline(run([]), [close], {0: "Put side"})
    assert [s.source for s in steps] == ["close (S/L ratio exit) · Put side"] * 6
    assert events(steps) == [
        "A close of 1 for the S/L ratio exit", "Limit started", "Limit: Placed",
        "Limit: Replaced", "Limit chase ended: filled", "Filled",
    ]  # fmt: skip
    assert steps[2].price == "0.55 debit" and steps[-1].price == "fill 0.60"


def test_a_live_exit_marks_its_last_step_and_an_ended_one_says_its_state():
    working = exit_row(id=1, log=[{"at": AT.format(1), "stop": "95"},
                                  {"at": AT.format(2), "result": "Sent."}])  # fmt: skip
    gone = exit_row(id=2, kind="target", state="cancelled", log=[
        {"at": AT.format(3), "target": "50", "unit": "pct", "close_at": "0.65"}])  # fmt: skip
    steps = timeline.order_timeline(run([]), [working, gone])
    assert [(s.event, s.live) for s in steps] == [
        ("Worked out", False), ("Placed", True), ("Worked out", False), ("Cancelled", False),
    ]  # fmt: skip
    assert steps[2].note == "close at 0.65"


def test_steps_with_no_time_sort_right_after_the_step_before():
    log = [{"at": AT.format(5), "price": "1.30", "result": "Sent."}, {"tranche": 1, "filled": 1}]
    early = exit_row(log=[{"at": AT.format(1), "result": "Sent."}])
    steps = timeline.order_timeline(run(log), [early])
    assert events(steps) == ["Placed", "Placed", "Filled"]
    assert [s.source.split(" ")[0] for s in steps] == ["stop", "Entry", "Entry"]


def test_kind_label_names_the_rule_that_started_a_close():
    assert timeline.kind_label(exit_row(kind="close", log=[{"by": "Delta exit"}])) == (
        "close (Delta exit)"
    )
    assert timeline.kind_label(exit_row(kind="close", log=[{"event": "x"}])) == "your close"
    assert timeline.kind_label(exit_row(kind="timed")) == "timed close"


# --- The banner ----------------------------------------------------------------------


def watch(id=10, state="filled", rule="Delta exit", detail="position delta 32 above 30"):
    return SimpleNamespace(id=id, state=state, log=[{"rule": rule, "detail": detail}])


def close(state, by="Delta exit", id=20):
    return SimpleNamespace(id=id, state=state, log=[{"by": by}])


def test_no_watch_row_no_banner():
    assert timeline.watch_banner([], [close("filled")]) is None


def test_banner_is_amber_while_closing():
    banner = timeline.watch_banner([watch()], [close("firing")])
    assert banner.tone == "warning"
    assert banner.text == "Delta exit fired: position delta 32 above 30 (closing)"


def test_banner_is_quiet_once_every_close_filled():
    banner = timeline.watch_banner([watch()], [close("filled"), close("filled", id=21)])
    assert banner.tone == "quiet"
    assert banner.text.endswith(": closed")


def test_banner_is_red_when_a_watch_close_failed_or_was_cancelled():
    for state in ("failed", "cancelled"):
        banner = timeline.watch_banner([watch()], [close("filled"), close(state, id=21)])
        assert banner.tone == "danger", state
        assert banner.text == (
            "Delta exit fired (position delta 32 above 30), close failed: close it yourself"
        )


def test_a_part_close_is_amber_and_says_the_rest_is_protected():
    banner = timeline.watch_banner([watch()], [close("filled"), close("reduced", id=21)])
    assert banner.tone == "warning"
    assert banner.text.endswith("partly closed; the rest is protected")


def test_a_watch_row_still_being_started_is_not_red_for_30_seconds():
    # bot_watch._fire_side writes the row "failed" first, then "filled" once the close runs
    written = datetime(2026, 10, 14, 14, 0, tzinfo=UTC)
    starting = SimpleNamespace(id=10, state="failed", updated_at=written, log=[{
        "rule": "Delta exit", "detail": "position delta 32 above 30",
        "notes": ["starting the close"]}])  # fmt: skip
    soon = written + timedelta(seconds=20)
    assert timeline.watch_banner([starting], [], now=soon).tone == "warning"
    crashed = written + timedelta(seconds=31)  # the app died before its close was claimed
    assert timeline.watch_banner([starting], [], now=crashed).tone == "danger"


def test_a_watch_close_that_broke_is_red():
    broke = SimpleNamespace(id=10, state="failed", log=[{
        "rule": "Delta exit", "notes": ["the close broke: RuntimeError"]}])  # fmt: skip
    assert timeline.watch_banner([broke], []).tone == "danger"


def test_a_side_another_close_already_has_is_not_red():
    notes = ["the side couldn't be claimed"]
    busy = SimpleNamespace(id=10, state="failed", log=[{"rule": "Delta exit", "notes": notes}])
    banner = timeline.watch_banner([busy], [])
    assert banner.tone == "warning" and "another close" in banner.text


def test_a_watch_row_that_failed_for_another_reason_is_red():
    broke = SimpleNamespace(id=10, state="failed", log=[{"rule": "Delta exit", "detail": "d"}])
    assert timeline.watch_banner([broke], []).tone == "danger"


def test_banner_says_nothing_left_open():
    banner = timeline.watch_banner([watch()], [close("expired")])
    assert banner.tone == "quiet" and banner.text.endswith("nothing left open")


def test_banner_follows_the_latest_rule_only():
    old = watch(id=1, rule="S/L ratio exit", state="failed")
    banner = timeline.watch_banner([old, watch(id=2)], [close("firing")])
    assert banner.tone == "warning" and banner.text.startswith("Delta exit")


def test_a_rule_row_says_it_acted_not_filled():
    fired = exit_row(
        kind="watch",
        state="filled",
        log=[{"at": AT.format(1), "rule": "Delta exit", "detail": "position delta 32"}],
    )
    failed = exit_row(id=2, kind="profit", state="failed", log=[{"rule": "Profit action 1"}])
    steps = timeline.order_timeline(run([]), [fired, failed])
    assert events(steps)[1::2] == ["Acted", "Couldn't act"]


def test_a_short_entry_reads_partly_filled_not_filled():
    log = [
        {"at": AT.format(0), "price": "1.30", "result": "Sent."},
        {"at": AT.format(1), "cancel": "77", "status": "cancelled"},
        {"at": AT.format(2), "tranche": 1, "filled": 1},
        {"at": AT.format(3), "tranche": 2, "contracts": 1, "price": "1.30", "result": "Sent."},
        {"at": AT.format(4), "cancel": "78", "status": "cancelled"},
    ]
    assert events(timeline.order_timeline(run(log), []))[2] == "Partly filled"
    recovered = [{"at": AT.format(2), "tranche": 1, "filled": 1}]
    assert events(timeline.order_timeline(run(recovered, status="partial"), [])) == [
        "Partly filled"
    ]


def test_a_filled_exit_is_timed_by_its_fill_not_its_last_change():
    row = exit_row(kind="target", state="filled", fill_price=Decimal("0.60"), log=[
        {"at": AT.format(1), "result": "Sent."},
        {"at": AT.format(7), "event": "closed at the target"},
    ])  # fmt: skip
    steps = timeline.order_timeline(run([]), [row])
    assert steps[-1].event == "Filled" and steps[-1].when == "10-14 10:00:07"


def test_untimed_notes_after_a_lead_entry_keep_their_place():
    # bot_entry inserts the notes at 0, then the lead (from the Trade page) before them
    log = [
        {"at": AT.format(0), "event": "from the Trade page"},
        {"strikes": ["moved"]}, {"expirations": ["moved"]},
        {"sizing": "dollars", "allocation": "300", "worst": "-1", "unit_cost": "100",
         "contracts": 3}, {"capped": 3, "to": 2},
        {"at": AT.format(1), "price": "1.00", "result": "Sent."},
    ]  # fmt: skip
    assert events(timeline.order_timeline(run(log), [])) == [
        "From the Trade page", "Strikes moved", "Expirations moved", "Sized by dollars",
        "Capped", "Placed",
    ]  # fmt: skip


def test_a_paired_rows_kind_names_the_stop_that_started_it():
    row = exit_row(kind="paired", log=[{"by": "Call side's stop"}])
    assert timeline.kind_label(row) == "paired close (after Call side's stop)"


def test_a_dollar_bot_priced_as_a_credit_is_sized_by_its_contracts():
    from optionsjourney.bots import timeline

    step = {"sizing": "contracts", "contracts": 2, "why": "Priced as a credit, so it's sized by "
            "its contracts: dollar sizing is for a debit."}  # fmt: skip
    assert timeline._entry_event(step, None, False, False) == (
        "Sized by contracts",
        None,
        step["why"],
    )


def test_a_long_left_by_a_close_says_it_was_left_to_expire():
    """Ignore bid-less longs: the close's "left" stage reads as the leg left to expire."""
    close = exit_row(kind="target", state="filled", log=[
        {"at": AT.format(5), "stage": "left", "left": ["SPXW  261013C05985000"],
         "why": "no bid"},
    ])  # fmt: skip
    steps = timeline.order_timeline(run([]), [close], {0: "Call side"})
    assert events(steps)[0] == "Left SPXW 261013C05985000 to expire: no bid"


def test_exit_steps_say_their_prices_and_a_fill_once():
    stop = exit_row(kind="stop", state="filled", trigger=Decimal("0.32"), log=[
        {"at": "2026-10-19T19:07:15+00:00", "result": "Sent."},
        {"at": "2026-10-19T19:08:31+00:00", "event": "filled", "price": "0.34"},
        {"at": "2026-10-19T19:08:31+00:00", "event": "the group has no other legs"},
    ])  # fmt: skip
    steps = [s for _, s in timeline.exit_steps(stop)]
    assert [(s.event, s.price) for s in steps if s.price] == [("Placed", "stop 0.32"),
                                                              ("Filled", "fill 0.34")]  # fmt: skip
    assert sum(s.event == "Filled" for s in steps) == 1
    wing = exit_row(id=2, kind="wing", state="working",
                    log=[{"at": "2026-10-19T19:22:01+00:00", "one_tick": "0.01", "result": "Sent."}])
    assert timeline.exit_steps(wing)[0][1].price == "limit 0.01"


def test_a_close_ended_at_market_takes_its_price_from_the_synced_fills():
    close = exit_row(kind="close", state="filled", broker_order_id="77", log=[
        {"at": "2026-10-19T19:30:45+00:00", "stage": "market", "result": "Sent."},
        {"at": "2026-10-19T19:30:47+00:00", "event": "closed at market"},
    ])  # fmt: skip
    steps = [s for _, s in timeline.exit_steps(close, fills={"77": Decimal("0.03")})]
    assert steps[-1].event == "Filled" and steps[-1].price == "0.03 credit"
    paired = exit_row(id=3, kind="paired", state="filled")
    assert [s.event for _, s in timeline.exit_steps(paired)] == ["Handed to its close"]


def test_a_closed_group_says_its_stop_and_how_it_closed():
    stop = exit_row(kind="stop", state="filled", trigger=Decimal("0.32"), limit=None,
                    fill_price=Decimal("0.34"),
                    log=[{"at": "2026-10-19T19:08:31+00:00", "status": "filled"}])  # fmt: skip
    wing = exit_row(id=2, kind="close", state="filled", trigger=None, limit=None, group_position=1,
                    broker_order_id="9", log=[{"by": "Short Puts's stop"},
                                              {"at": "2026-10-19T19:08:34+00:00", "status": "filled"}])  # fmt: skip
    assert timeline.side_closed([stop, wing], 0) == ["Stop 0.32", "Stopped out at 0.34 (15:08)"]
    assert timeline.side_closed([stop, wing], 1, {"9": Decimal("0.02")}) == [
        "Closed after the Short Puts's stop at 0.02 credit (15:08)"]


def test_a_wing_still_selling_says_so_not_closed():
    close = exit_row(kind="close", state="filled", trigger=None, limit=None,
                     log=[{"by": "Short Calls's stop"}, {"stage": "leg_out", "handed": "XSP 774C"}])  # fmt: skip
    wing = exit_row(id=2, kind="wing", state="working", trigger=None, limit=None,
                    created_at=datetime(2026, 10, 19, 19, 22, tzinfo=UTC),
                    log=[{"one_tick": "0.01", "result": "Sent."}])  # fmt: skip
    assert timeline.side_closed([close, wing], 0) == [
        "Handed the wing to its own sell order", "Selling the wing at 0.01: working since 15:22"]
