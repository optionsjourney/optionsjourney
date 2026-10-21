from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from optionsjourney.bots.definition import EntryTimeDef, iron_condor_skeleton
from optionsjourney.db import make_engine, make_session_factory, migrate
from optionsjourney.db.models import Bot, BotLeg, Setting, Tag
from optionsjourney.services import bot_store

NOW = datetime(2026, 10, 12, 20, 0, tzinfo=UTC)
D = Decimal


@pytest.fixture
def factory(tmp_path):
    engine = make_engine(tmp_path / "t.db")
    migrate(engine)
    return make_session_factory(engine)


def tag_id(factory):
    with factory() as session:
        return session.scalar(select(Tag.id).where(Tag.name == "0DTE"))


def good(factory):
    bot = iron_condor_skeleton("IC")
    targets = {0: D("2.70"), 1: D("75"), 2: D("2.90"), 3: D("-75")}
    return replace(
        bot, tag_id=tag_id(factory), account_number="5WX01234",
        legs=tuple(replace(leg, target=targets[leg.position]) for leg in bot.legs),
        groups=tuple(replace(g, stop_pct=D("95"), target_pct=D("50")) for g in bot.groups),
        entry_times=(EntryTimeDef("09:47"), EntryTimeDef("12:03", False)), live=True, enabled=True,
    )  # fmt: skip


def test_new_bot_is_live_and_disarmed(factory):
    bot_id, problems = bot_store.save(factory, good(factory), NOW)
    assert problems == {}
    loaded = bot_store.load(factory, bot_id)
    assert loaded.live is True and loaded.enabled is False  # new bots: Live, disarmed
    assert loaded.legs == good(factory).legs and loaded.groups == good(factory).groups
    assert loaded.entry_times == (EntryTimeDef("09:47"), EntryTimeDef("12:03", False))


def test_slice_1_settings_round_trip(factory):
    bot = replace(
        good(factory), contracts=2, max_per_trade=5, max_per_day=5, max_entries_per_day=3,
        max_open_trades=2, use_premium_bounds=True, premium_min=D("1.5"),
        premium_max=D("4"), frequency="weekly", entry_days=(1, 3, 5), use_blackout_days=True,
        blackout_days=("2026-12-25",), specific_dates=("2026-10-14",), monthly_week=2,
        monthly_weekday=4,
        entry_times=(EntryTimeDef("09:47", True, 4, "open"), EntryTimeDef("12:03", False)),
        # groups that stop on the entry price lost (they read the same on a debit fill)
        groups=tuple(replace(g, stop_basis="group_entry") for g in good(factory).groups),
    )  # fmt: skip
    bot_id, problems = bot_store.save(factory, bot, NOW)
    assert problems == {}
    loaded = bot_store.load(factory, bot_id)
    for name in ("max_entries_per_day", "max_open_trades", "use_premium_bounds",
                 "premium_min", "premium_max", "frequency", "entry_days", "use_blackout_days",
                 "blackout_days", "specific_dates", "monthly_week", "monthly_weekday",
                 "entry_times"):  # fmt: skip
        assert getattr(loaded, name) == getattr(bot, name), name


def test_a_new_bot_loads_with_todays_defaults(factory):
    bot_id, _ = bot_store.save(factory, good(factory), NOW)
    loaded = bot_store.load(factory, bot_id)
    assert loaded.frequency == "daily" and loaded.entry_days == () and loaded.specific_dates == ()
    assert loaded.max_entries_per_day is None and not loaded.use_premium_bounds


def test_invalid_bots_write_nothing(factory):
    bot_id, problems = bot_store.save(factory, replace(good(factory), name=""), NOW)
    assert bot_id is None and "name" in problems
    with factory() as session:
        assert session.scalar(select(Bot)) is None


def test_saving_again_replaces_legs_and_keeps_mode(factory):
    bot_id, _ = bot_store.save(factory, good(factory), NOW)
    bot_store.set_live(factory, bot_id, True, NOW)
    bot_store.set_armed(factory, bot_id, True, NOW)
    edited = replace(bot_store.load(factory, bot_id), name="IC 2", live=False, enabled=False)
    bot_store.save(factory, edited, NOW)
    loaded = bot_store.load(factory, bot_id)
    assert loaded.name == "IC 2" and loaded.live and loaded.enabled
    with factory() as session:
        assert len(session.scalars(select(BotLeg).where(BotLeg.bot_id == bot_id)).all()) == 4


def test_armed_and_hide(factory):
    bot_id, _ = bot_store.save(factory, good(factory), NOW)
    assert bot_store.armed(factory) == []
    bot_store.set_armed(factory, bot_id, True, NOW)
    assert [b.id for b in bot_store.armed(factory)] == [bot_id]
    bot_store.hide(factory, bot_id, NOW)
    assert bot_store.armed(factory) == [] and bot_store.listing(factory) == []


def test_parse_form():
    form = {
        "name": "IC",
        "tag_id": "3",
        "account": "tastytrade:5WX01234",
        "underlying": "SPX",
        "contracts": "1",
        "max_per_trade": "1",
        "max_per_day": "2",
        "split": "on",
        "times": "2",
        "time-0": "09:47",
        "time-0-on": "on",
        "time-1": "",
        "time-1-on": "on",
        "group-0-name": "Call side",
        "group-0-stop": "95",
        "group-0-target": "",
        "group-1-name": "Put side",
        "group-1-stop": "95",
        "group-1-target": "50",
        "chase-entry-start": "0",
        "chase-entry-step": "0.05",
        "chase-entry-interval": "10",
        "chase-entry-tries": "6",
        "chase-exit-start": "0",
        "chase-exit-step": "0.05",
        "chase-exit-interval": "5",
        "chase-exit-tries": "6",
    }
    for i, (side, right, pick, target, link, group) in enumerate([
        ("sell", "call", "premium", "2.70", "", "0"), ("buy", "call", "offset", "75", "0", "0"),
        ("sell", "put", "premium", "2.90", "", "1"), ("buy", "put", "offset", "-75", "2", "1"),
    ]):  # fmt: skip
        form |= {f"leg-{i}-side": side, f"leg-{i}-right": right, f"leg-{i}-ratio": "1",
                 f"leg-{i}-pick": pick, f"leg-{i}-target": target, f"leg-{i}-link": link,
                 f"leg-{i}-exact": "on", f"leg-{i}-group": group}  # fmt: skip
    bot = bot_store.parse_form(form)
    assert bot.account_number == "5WX01234" and bot.tag_id == 3
    assert bot.entry_times == (EntryTimeDef("09:47"),)  # blank rows are dropped
    assert bot.groups[0].target_pct is None and bot.groups[1].target_pct == D("50")
    assert bot.legs[1].link == 0 and bot.legs[3].target == D("-75")


@pytest.mark.parametrize("bad", ["NaN", "sNaN", "Infinity", "-Infinity"])
def test_non_finite_numbers_become_problems_not_exceptions(factory, bad):
    form = {"name": "IC", "account": "tastytrade:5WX01234", "contracts": "1", "times": "1"}
    form |= {"time-0": "09:47", "time-0-on": "on", "tag_id": str(tag_id(factory))}
    for i in range(4):
        form |= {f"leg-{i}-target": bad, f"leg-{i}-link": "", f"leg-{i}-group": str(i // 2)}
    for g in range(2):
        form |= {f"group-{g}-stop": bad, f"group-{g}-target": bad}
    for phase in ("entry", "exit"):
        form |= {f"chase-{phase}-{f}": bad for f in ("start", "step", "interval", "tries")}
    bot = bot_store.parse_form(form)
    assert bot_store.save(factory, bot, NOW)[0] is None


BEFORE_CLOSE = datetime(2026, 10, 12, 14, 0, tzinfo=UTC)  # Mon 10:00 New York
AFTER_CLOSE = datetime(2026, 10, 12, 21, 0, tzinfo=UTC)  # Mon 17:00 New York


def armed_bot(factory, **changes):
    bot_id, problems = bot_store.save(factory, replace(good(factory), **changes), NOW)
    assert problems == {}
    bot_store.set_armed(factory, bot_id, True, NOW)
    return bot_id


def test_skip_day_needs_an_armed_bot(factory):
    bot_id, _ = bot_store.save(factory, good(factory), NOW)  # saved disarmed
    assert bot_store.skip_day(factory, bot_id, BEFORE_CLOSE) is None
    assert bot_store.load(factory, bot_id).blackout_days == ()
    assert bot_store.skip_day(factory, 999, BEFORE_CLOSE) is None


def test_skip_day_skips_today_before_the_close_and_the_next_trading_day_after(factory):
    bot_id = armed_bot(factory)
    assert bot_store.skip_day(factory, bot_id, BEFORE_CLOSE) == date(2026, 10, 12)
    assert bot_store.skip_day(factory, bot_id, AFTER_CLOSE) == date(2026, 10, 13)
    loaded = bot_store.load(factory, bot_id)
    assert loaded.use_blackout_days and loaded.enabled
    assert loaded.blackout_days == ("2026-10-12", "2026-10-13")
    friday = datetime(2026, 10, 16, 21, 0, tzinfo=UTC)
    assert bot_store.skip_day(factory, bot_id, friday) == date(2026, 10, 19)  # over the weekend


def test_skip_day_clears_dormant_dates_and_prunes_past_ones(factory):
    dormant = armed_bot(factory, blackout_days=("2026-12-25",), use_blackout_days=False)
    bot_store.skip_day(factory, dormant, BEFORE_CLOSE)
    assert bot_store.load(factory, dormant).blackout_days == ("2026-10-12",)
    live = armed_bot(
        factory, name="B", blackout_days=("2026-10-01", "2026-12-25"), use_blackout_days=True
    )
    bot_store.skip_day(factory, live, BEFORE_CLOSE)
    assert bot_store.load(factory, live).blackout_days == ("2026-10-12", "2026-12-25")


def test_undo_skip_turns_the_toggle_off_when_empty(factory):
    bot_id = armed_bot(factory)
    bot_store.skip_day(factory, bot_id, BEFORE_CLOSE)
    bot_store.skip_day(factory, bot_id, AFTER_CLOSE)
    bot_store.undo_skip(factory, bot_id, date(2026, 10, 12), NOW)
    loaded = bot_store.load(factory, bot_id)
    assert loaded.blackout_days == ("2026-10-13",) and loaded.use_blackout_days
    bot_store.undo_skip(factory, bot_id, date(2026, 10, 13), NOW)
    loaded = bot_store.load(factory, bot_id)
    assert loaded.blackout_days == () and not loaded.use_blackout_days


def test_skip_many_splits_skipped_from_not_armed(factory):
    one = armed_bot(factory)
    two, _ = bot_store.save(factory, replace(good(factory), name="Off"), NOW)
    assert bot_store.skip_many(factory, [one, two, 999], BEFORE_CLOSE) == ([one], [two, 999])


def full_form():
    form = {
        "name": "IC", "tag_id": "3", "account": "tastytrade:5WX01234", "underlying": "SPX",
        "contracts": "2", "max_per_trade": "5", "max_per_day": "6", "split": "on",
        "max_entries_per_day": "3", "max_open_trades": " 2 ",
        "times": "2", "time-0": "09:47", "time-0-on": "on", "time-0-qty": "4",
        "time-0-label": "open", "time-1": "12:03", "time-1-qty": "", "time-1-label": " ",
        "frequency": "weekly", "entry_days": ["1", "3", "5"], "monthly_week": "2",
        "monthly_weekday": "4", "specific_dates": "2026-10-14, 2026-10-14\n2026-10-15",
        "use_blackout_days": "on", "blackout_days": "2026-12-25 2026-12-24",
        "use_premium_bounds": "on", "premium_min": "1.5",
        "premium_max": "4",
        "group-0-name": "Call side", "group-0-stop": "95", "group-0-target": "",
        "group-1-name": "Put side", "group-1-stop": "95", "group-1-target": "50",
        # groups that stop on the entry price lost (they read the same on a debit fill)
        "group-0-stop-style": "group_entry", "group-1-stop-style": "group_entry",
        "chase-entry-start": "0", "chase-entry-step": "0.05", "chase-entry-interval": "10",
        "chase-entry-tries": "6", "chase-exit-start": "0", "chase-exit-step": "0.05",
        "chase-exit-interval": "5", "chase-exit-tries": "6",
    }  # fmt: skip
    for i, (side, right, pick, target, link, group) in enumerate([
        ("sell", "call", "premium", "2.70", "", "0"), ("buy", "call", "offset", "75", "0", "0"),
        ("sell", "put", "premium", "2.90", "", "1"), ("buy", "put", "offset", "-75", "2", "1"),
    ]):  # fmt: skip
        form |= {f"leg-{i}-side": side, f"leg-{i}-right": right, f"leg-{i}-ratio": "1",
                 f"leg-{i}-pick": pick, f"leg-{i}-target": target, f"leg-{i}-link": link,
                 f"leg-{i}-exact": "on", f"leg-{i}-group": group}  # fmt: skip
    return form


class Form(dict):
    """A form where a list value is several fields of the same name, like werkzeug's."""

    def getlist(self, key):
        value = self.get(key, [])
        return value if isinstance(value, list) else [value]


def test_parse_form_reads_every_new_field():
    bot = bot_store.parse_form(Form(full_form()))
    assert (bot.max_entries_per_day, bot.max_open_trades) == (3, 2)
    assert bot.entry_times == (
        EntryTimeDef("09:47", True, 4, "open"),
        EntryTimeDef("12:03", False, None, None),
    )
    assert (bot.frequency, bot.entry_days) == ("weekly", (1, 3, 5))
    assert (bot.monthly_week, bot.monthly_weekday) == (2, 4)
    assert bot.specific_dates == ("2026-10-14", "2026-10-15")
    assert bot.use_blackout_days and bot.blackout_days == ("2026-12-24", "2026-12-25")
    assert bot.use_premium_bounds
    assert (bot.premium_min, bot.premium_max) == (D("1.5"), D("4"))


def test_parse_form_defaults_when_the_new_fields_are_absent():
    form = full_form()
    for name in ("max_entries_per_day", "max_open_trades", "frequency", "entry_days",
                 "use_blackout_days", "use_premium_bounds"):  # fmt: skip
        del form[name]
    bot = bot_store.parse_form(Form(form))
    assert bot.max_entries_per_day is None and bot.max_open_trades is None
    assert bot.frequency == "daily" and bot.entry_days == ()
    assert not bot.use_blackout_days and not bot.use_premium_bounds


def test_bad_dates_become_problems_that_name_them(factory):
    form = full_form() | {"tag_id": str(tag_id(factory)), "frequency": "specific_dates"}
    form |= {"specific_dates": "2026-10-14 tomorrow", "blackout_days": "2026-13-45"}
    bot = bot_store.parse_form(Form(form))
    bot_id, problems = bot_store.save(factory, bot, NOW)
    assert bot_id is None
    assert "tomorrow" in problems["frequency"] and "2026-13-45" in problems["blackout"]


@pytest.mark.parametrize("qty", ["abc", "2.5", "0", " 0 "])
def test_an_unreadable_entry_time_quantity_is_a_problem_not_blank(factory, qty):
    form = full_form() | {"tag_id": str(tag_id(factory)), "time-0-qty": qty}
    bot, form_problems = bot_store.read_form(Form(form))
    assert bot.entry_times[0].quantity is None
    bot_id, problems = bot_store.save(factory, bot, NOW, form_problems)
    assert bot_id is None
    assert problems["times"] == (
        f"entry time 09:47: quantity {qty.strip()!r} isn't a whole number from 1 to 100; "
        "leave it blank for the bot's contracts"
    )


@pytest.mark.parametrize("field, word", [("premium_min", "minimum"), ("premium_max", "maximum")])
def test_an_unreadable_premium_bound_is_a_problem_not_blank(factory, field, word):
    form = full_form() | {"tag_id": str(tag_id(factory)), field: "1,50"}
    bot, form_problems = bot_store.read_form(Form(form))
    bot_id, problems = bot_store.save(factory, bot, NOW, form_problems)
    assert bot_id is None
    assert problems["premium"] == f"The {word} premium '1,50' isn't a number."


def test_blank_quantities_and_bounds_are_not_problems(factory):
    form = full_form() | {"tag_id": str(tag_id(factory)), "premium_max": " "}
    bot, form_problems = bot_store.read_form(Form(form))
    assert form_problems == {} and bot.premium_max is None
    assert bot_store.parse_form(Form(form)) == bot
    assert bot_store.save(factory, bot, NOW, form_problems)[1] == {}


def test_stop_styles_units_and_the_wing_round_trip(factory):
    bot = good(factory)
    call = replace(bot.groups[0], stop_basis="closing_order", stop_pct=D("0.20"),
                   target_unit="usd", target_pct=None, target_usd=D("1.00"),
                   on_stop_remainder="close_limit", remainder_limit=D("0.10"))  # fmt: skip
    put = replace(bot.groups[1], stop_unit="usd", stop_pct=None, stop_usd=D("2.00"),
                  on_stop_remainder="leave")  # fmt: skip
    bot = replace(bot, groups=(call, put), stop_co_mode="tighter", stop_co_amount=D("0.15"))
    bot_id, problems = bot_store.save(factory, bot, NOW)
    assert problems == {}
    loaded = bot_store.load(factory, bot_id)
    assert loaded.groups == bot.groups
    assert (loaded.stop_co_mode, loaded.stop_co_amount) == ("tighter", D("0.15"))


def test_the_form_writes_one_stop_box_to_the_column_its_style_names():
    bot, problems = bot_store.read_form({
        "group-0-stop": "2.00", "group-0-stop-style": "usd",
        "group-0-target": "1.00", "group-0-target-unit": "usd",
        "group-1-stop": "0.20", "group-1-stop-style": "closing_order",
        "group-1-remainder": "close_chase", "group-1-remainder-limit": "",
        "stop_co_mode": "both", "stop_co_amount": "0.50",
    })  # fmt: skip
    call, put = bot.groups
    assert (call.stop_unit, call.stop_usd, call.stop_pct, call.stop_basis) == (
        "usd",
        D("2.00"),
        None,
        "leg_premium",
    )
    assert (call.target_unit, call.target_usd, call.target_pct) == ("usd", D("1.00"), None)
    assert (put.stop_unit, put.stop_basis, put.stop_pct) == ("pct", "closing_order", D("0.20"))
    assert put.on_stop_remainder == "close_chase" and call.on_stop_remainder == "close_market"
    assert (bot.stop_co_mode, bot.stop_co_amount) == ("both", D("0.50"))
    assert problems == {}


def test_an_unreadable_amount_is_a_problem_never_no_stop():
    _, problems = bot_store.read_form({"group-0-stop": "9S", "stop_co_amount": "x"})
    assert "'9S'" in problems["group-0"] and "'x'" in problems["closing_order"]
    _, problems = bot_store.read_form({"group-1-remainder-limit": "ten"})
    assert "'ten'" in problems["group-1"]


def test_leave_is_honoured_until_paused_and_a_pause_can_lapse(factory):
    assert not bot_store.leave_paused(factory, NOW)
    bot_store.pause_leave(factory, None, NOW)
    assert bot_store.leave_paused(factory, NOW + timedelta(days=30))
    assert bot_store.leave_pause(factory, NOW) == (True, None)
    until = NOW + timedelta(hours=1)
    bot_store.pause_leave(factory, until, NOW)
    assert bot_store.leave_pause(factory, NOW) == (True, until)
    assert not bot_store.leave_paused(factory, NOW + timedelta(hours=2))
    bot_store.resume_leave(factory, NOW)
    assert not bot_store.leave_paused(factory, NOW)


def test_an_unreadable_pause_counts_as_paused(factory):
    with factory() as session:
        session.add(Setting(key=bot_store.LEAVE_PAUSE_KEY, value="garbled", updated_at=NOW))
        session.commit()
    assert bot_store.leave_paused(factory, NOW)

    def broken():
        raise RuntimeError("the database is gone")

    assert bot_store.leave_paused(broken, NOW)


def test_leave_is_in_use_from_a_group_or_from_one_trade(factory):
    from optionsjourney.db.models import BotTradeOverride

    bot_id, _ = bot_store.save(factory, good(factory), NOW)
    assert not bot_store.leave_in_use(factory)
    run_id = add_run(factory, bot_id, "r1")
    with factory() as session:
        session.add(BotTradeOverride(run_id=run_id, group_position=0, on_stop_remainder="leave",
                                     changed_at=NOW, log=[]))  # fmt: skip
        session.commit()
    assert bot_store.leave_in_use(factory)
    with factory() as session:
        session.query(BotTradeOverride).delete()
        session.commit()
    bot = good(factory)
    groups = (replace(bot.groups[0], on_stop_remainder="leave"), *bot.groups[1:])
    bot_store.save(factory, replace(bot, name="IC 2", groups=groups), NOW)
    assert bot_store.leave_in_use(factory)


def test_exhaust_and_natural_window_round_trip_through_the_form(factory):
    bot = good(factory)
    chase = replace(bot.exit_chase, exhaust_mode="give_up", natural_minutes=5)
    bot_id, problems = bot_store.save(factory, replace(bot, exit_chase=chase), NOW)
    assert problems == {}
    loaded = bot_store.load(factory, bot_id).exit_chase
    assert (loaded.exhaust_mode, loaded.natural_minutes) == ("give_up", 5)
    form = {"chase-exit-exhaust": "natural_then_leg_out", "chase-exit-natural": "3"}
    parsed = bot_store.parse_form(form).exit_chase
    assert (parsed.exhaust_mode, parsed.natural_minutes) == ("natural_then_leg_out", 3)


def test_an_unreadable_natural_window_is_a_form_problem():
    _, problems = bot_store.read_form({"chase-exit-natural": "two"})
    assert "two" in problems["chase_exhaust"]


def test_a_bot_with_no_exit_chase_row_gives_up_after_its_last_try(factory):
    from optionsjourney.db.models import BotChase

    bot_id, problems = bot_store.save(factory, good(factory), NOW)
    assert problems == {}
    with factory() as session:
        for row in session.scalars(select(BotChase).where(BotChase.bot_id == bot_id)):
            if row.phase == "exit":
                session.delete(row)
        session.commit()
    assert bot_store.load(factory, bot_id).exit_chase.exhaust_mode == "give_up"


# Slice 5: market filters and date lists


def filters_form():
    return full_form() | {
        "use_vix_level": "on", "vix_min": "12", "vix_max": "20",
        "use_vix_overnight": "on", "vix_on_max_up": "3", "vix_on_min_down": " ",
        "use_vix9d_ratio": "on", "vix9d_ratio_max": "0.95",
        "use_technicals": "on", "sma_mode": "above", "sma_days_a": "5", "sma_days_b": "",
        "ema_mode": "compare", "ema_minutes_a": "5", "ema_minutes_b": "20",
        "use_intraday_move": "on", "intraday_min_up": "0.3",
        "frequency": "specific_dates", "specific_date_sets": ["FOMC", "EOM"],
        "blackout_date_sets": ["CPI"], "blackout_dte_sets": ["HOLIDAY", "FOMC"],
    }  # fmt: skip


def test_market_filters_and_date_lists_round_trip_through_the_form(factory):
    from optionsjourney.bots.definition import MoveLimits

    form = filters_form() | {"tag_id": str(tag_id(factory))}
    bot, form_problems = bot_store.read_form(Form(form))
    assert form_problems == {}
    assert (bot.use_vix_level, bot.vix_min, bot.vix_max) == (True, D("12"), D("20"))
    assert bot.use_vix_overnight and bot.vix_overnight == MoveLimits(max_up=D("3"))
    assert (bot.use_vix9d_ratio, bot.vix9d_ratio_min, bot.vix9d_ratio_max) == (
        True,
        None,
        D("0.95"),
    )
    assert (bot.sma_mode, bot.sma_days_a, bot.sma_days_b) == ("above", 5, None)
    assert (bot.ema_mode, bot.ema_minutes_a, bot.ema_minutes_b) == ("compare", 5, 20)
    assert bot.use_intraday_move and bot.intraday == MoveLimits(min_up=D("0.3"))
    assert (bot.specific_date_sets, bot.blackout_date_sets) == (("EOM", "FOMC"), ("CPI",))
    assert bot.blackout_dte_sets == ("FOMC", "HOLIDAY")
    bot_id, problems = bot_store.save(factory, bot, NOW, form_problems)
    assert problems == {}
    loaded = bot_store.load(factory, bot_id)
    for name in ("use_vix_level", "vix_min", "vix_max", "use_vix_overnight", "vix_overnight",
                 "use_vix9d_ratio", "vix9d_ratio_min", "vix9d_ratio_max", "use_technicals",
                 "sma_mode", "sma_days_a", "sma_days_b", "ema_mode", "ema_minutes_a",
                 "ema_minutes_b", "use_intraday_move", "intraday", "specific_date_sets",
                 "blackout_date_sets", "blackout_dte_sets"):  # fmt: skip
        assert getattr(loaded, name) == getattr(bot, name), name


def test_an_unticked_filter_keeps_its_numbers(factory):
    form = filters_form() | {"tag_id": str(tag_id(factory))}
    del form["use_vix_level"]
    bot, form_problems = bot_store.read_form(Form(form))
    bot_id, problems = bot_store.save(factory, bot, NOW, form_problems)
    assert problems == {}
    loaded = bot_store.load(factory, bot_id)
    assert not loaded.use_vix_level and (loaded.vix_min, loaded.vix_max) == (D("12"), D("20"))


@pytest.mark.parametrize(
    "field, key, words",
    [
        ("vix_max", "vix_level", "maximum VIX"),
        ("vix_on_max_up", "vix_overnight", "VIX overnight max up %"),
        ("vix9d_ratio_min", "vix9d_ratio", "minimum ratio"),
        ("intraday_max_down", "intraday_move", "intraday max down %"),
    ],
)
def test_an_unreadable_filter_number_is_a_problem_never_blank(factory, field, key, words):
    form = filters_form() | {"tag_id": str(tag_id(factory)), field: "1,5"}
    bot, form_problems = bot_store.read_form(Form(form))
    bot_id, problems = bot_store.save(factory, bot, NOW, form_problems)
    assert bot_id is None
    assert problems[key] == f"The {words} '1,5' isn't a number."


def test_an_unreadable_period_is_a_problem():
    form = filters_form() | {"sma_days_a": "five"}
    _, problems = bot_store.read_form(Form(form))
    assert problems["technicals"] == "The SMA days 'five' isn't a whole number."


def test_skip_day_drops_dormant_blackout_lists_and_undo_keeps_live_ones(factory):
    """Tracker2's _add_blackout_day / _remove_blackout_day: turning blackout days on
    for a skip must not bring ticked lists into force."""
    dormant = armed_bot(factory, blackout_date_sets=("FOMC",), blackout_dte_sets=("CPI",),
                        use_blackout_days=False)  # fmt: skip
    bot_store.skip_day(factory, dormant, BEFORE_CLOSE)
    loaded = bot_store.load(factory, dormant)
    assert loaded.blackout_date_sets == () and loaded.blackout_days == ("2026-10-12",)
    assert loaded.blackout_dte_sets == ()
    live = armed_bot(factory, name="B", blackout_date_sets=("CPI",), use_blackout_days=True)
    bot_store.skip_day(factory, live, BEFORE_CLOSE)
    bot_store.undo_skip(factory, live, date(2026, 10, 12), NOW)
    loaded = bot_store.load(factory, live)
    assert loaded.blackout_date_sets == ("CPI",) and loaded.use_blackout_days  # still on
    later = armed_bot(factory, name="C", blackout_dte_sets=("HOLIDAY",), use_blackout_days=True)
    bot_store.skip_day(factory, later, BEFORE_CLOSE)
    bot_store.undo_skip(factory, later, date(2026, 10, 12), NOW)
    loaded = bot_store.load(factory, later)
    assert loaded.blackout_dte_sets == ("HOLIDAY",) and loaded.use_blackout_days  # still on


# Slice 4: timed close and time actions


def test_timed_close_and_time_actions_round_trip_through_the_form(factory):
    from optionsjourney.bots.definition import TimeActionDef

    rows = (TimeActionDef("dte", 0, "14:00", D("150"), None),
            TimeActionDef("dit", 1, "10:30", None, D("25")))  # fmt: skip
    bot = replace(good(factory), close_time="15:45", close_dte=1, use_time_actions=True,
                  time_actions=rows)  # fmt: skip
    bot_id, problems = bot_store.save(factory, bot, NOW)
    assert problems == {}
    loaded = bot_store.load(factory, bot_id)
    assert (loaded.close_time, loaded.close_dte, loaded.use_time_actions) == ("15:45", 1, True)
    assert loaded.time_actions == rows
    form = {
        "close_time": " 15:30 ", "close_dte": "2", "use_time_actions": "on", "actions": "3",
        "ta-0-type": "dte", "ta-0-days": "0", "ta-0-at": "14:00", "ta-0-stop": "150",
        "ta-1-type": "dit", "ta-1-days": "", "ta-1-at": "", "ta-1-stop": "", "ta-1-target": "",
        "ta-2-type": "dit", "ta-2-days": "1", "ta-2-at": "10:30", "ta-2-target": "25",
    }  # fmt: skip
    parsed = bot_store.parse_form(form)
    assert (parsed.close_time, parsed.close_dte, parsed.use_time_actions) == ("15:30", 2, True)
    assert parsed.time_actions == rows  # the blank row is dropped


def test_a_blank_close_time_is_off():
    parsed = bot_store.parse_form({"close_time": "  ", "close_dte": ""})
    assert (parsed.close_time, parsed.close_dte, parsed.time_actions) == (None, 0, ())


def test_unreadable_time_action_numbers_are_form_problems():
    _, problems = bot_store.read_form(
        {
            "actions": "1",
            "ta-0-type": "dte",
            "ta-0-days": "one",
            "ta-0-at": "14:00",
            "ta-0-target": "50",
        }  # fmt: skip
    )
    assert "one" in problems["time_actions"]
    _, problems = bot_store.read_form(
        {
            "actions": "1",
            "ta-0-type": "dte",
            "ta-0-days": "0",
            "ta-0-at": "14:00",
            "ta-0-stop": "lots",
        }  # fmt: skip
    )
    assert "lots" in problems["time_actions"]
    _, problems = bot_store.read_form({"close_time": "15:45", "close_dte": "x"})
    assert "x" in problems["timed_close"]


# --- Slice 8: tags, clone, archive, import -------------------------------------------

LEGS = [
    {"symbol": "SPXW  261013C05910000", "side": "sell", "ratio": 1, "group": 0},
    {"symbol": "SPXW  261013C05985000", "side": "buy", "ratio": 1, "group": 0},
]


def add_run(factory, bot_id, key, status="filled", exits=(), legs=None):
    from optionsjourney.db.models import BotExit, BotRun

    with factory() as session:
        run = BotRun(
            bot_id=bot_id, key=key, trading_date=NOW.date(), entry_time="09:47", status=status,
            live=False, legs=LEGS if legs is None else legs, chase_log=[], contracts=1,
            filled_legs=[{"symbol": "x"}] if status in ("filled", "partial") else [],
            created_at=NOW, updated_at=NOW,
        )  # fmt: skip
        session.add(run)
        session.flush()
        for kind, state in exits:
            session.add(BotExit(
                run_id=run.id, group_position=0, kind=kind, state=state,
                short_symbol=LEGS[0]["symbol"], long_symbol=LEGS[1]["symbol"], contracts=1,
                rearms=0, log=[], created_at=NOW, updated_at=NOW,
            ))  # fmt: skip
        session.commit()
        return run.id


def test_tags_round_trip_through_the_form(factory):
    data = {"tags": " fomc week, 0DTE,, Armed, 0dte "}
    assert bot_store.read_form(data)[0].tags == ("0DTE", "fomc week")
    bot_id, problems = bot_store.save(factory, replace(good(factory), tags=("Mine", "0DTE")), NOW)
    assert problems == {} and bot_store.load(factory, bot_id).tags == ("0DTE", "Mine")


def test_clone_is_disarmed_and_live_with_a_copy_name(factory):
    bot_id, _ = bot_store.save(factory, replace(good(factory), tags=("Mine",)), NOW)
    bot_store.set_live(factory, bot_id, True, NOW)
    bot_store.set_armed(factory, bot_id, True, NOW)
    add_run(factory, bot_id, "k1")
    copy_id, problems = bot_store.clone(factory, bot_id, NOW)
    assert problems == {} and copy_id != bot_id
    copy, source = bot_store.load(factory, copy_id), bot_store.load(factory, bot_id)
    assert copy.name == "IC (copy)" and copy.live and not copy.enabled
    assert (copy.legs, copy.groups, copy.tags) == (source.legs, source.groups, source.tags)
    assert copy.account_number == source.account_number and copy.contracts == source.contracts
    assert bot_store.runs(factory, copy_id) == []
    assert bot_store.load(factory, bot_store.clone(factory, bot_id, NOW)[0]).name == "IC (copy 2)"
    assert bot_store.load(factory, bot_store.clone(factory, copy_id, NOW)[0]).name == "IC (copy 3)"


def test_a_bot_that_fails_validation_is_not_cloned(factory):
    bot_id, _ = bot_store.save(factory, good(factory), NOW)
    with factory() as session:
        session.get(Bot, bot_id).contracts = 0  # a rule the bot no longer passes
        session.commit()
    copy_id, problems = bot_store.clone(factory, bot_id, NOW)
    assert copy_id is None and "contracts" in problems
    assert len(bot_store.listing(factory)) == 1


def test_archive_is_refused_while_a_trade_is_open(factory):
    bot_id, _ = bot_store.save(factory, good(factory), NOW)
    run_id = add_run(factory, bot_id, "k1", exits=[("stop", "failed")])
    reasons = bot_store.archive(factory, bot_id, NOW)
    assert reasons == [f"1 open trade (run {run_id})"]
    assert not bot_store.load(factory, bot_id).archived
    # the option has expired: nothing open any more
    later = datetime(2026, 10, 14, 15, 0, tzinfo=UTC)
    assert bot_store.archive(factory, bot_id, later) == []
    assert bot_store.load(factory, bot_id).archived


def test_archive_is_refused_while_an_exit_or_entry_is_live(factory):
    bot_id, _ = bot_store.save(factory, good(factory), NOW)
    closed = add_run(factory, bot_id, "k1", exits=[("target", "filled"), ("stop", "working")])
    entering = add_run(factory, bot_id, "k2", status="running")
    assert bot_store.archive(factory, bot_id, NOW) == [
        f"1 exit still working (run {closed})",
        f"1 entry in progress (run {entering})",
    ]


def test_an_archived_bot_is_not_armed_and_cannot_be_armed(factory):
    bot_id, _ = bot_store.save(factory, good(factory), NOW)
    bot_store.set_armed(factory, bot_id, True, NOW)
    assert bot_store.archive(factory, bot_id, NOW) == []
    bot = bot_store.load(factory, bot_id)
    assert bot.archived and not bot.enabled
    assert bot_store.set_armed(factory, bot_id, True, NOW) is False
    assert not bot_store.load(factory, bot_id).enabled
    assert bot_store.armed(factory) == [] and bot_store.listing(factory) == []
    assert [b.id for b in bot_store.listing(factory, archived=True)] == [bot_id]


def test_unarchive_brings_it_back_disarmed(factory):
    bot_id, _ = bot_store.save(factory, good(factory), NOW)
    bot_store.archive(factory, bot_id, NOW)
    bot_store.unarchive(factory, bot_id, NOW)
    bot = bot_store.load(factory, bot_id)
    assert not bot.archived and not bot.enabled
    assert [b.id for b in bot_store.listing(factory)] == [bot_id]
    assert bot_store.set_armed(factory, bot_id, True, NOW) is True


def test_saving_never_changes_archived(factory):
    bot_id, _ = bot_store.save(factory, good(factory), NOW)
    bot_store.archive(factory, bot_id, NOW)
    bot_store.save(factory, replace(bot_store.load(factory, bot_id), archived=False), NOW)
    assert bot_store.load(factory, bot_id).archived


def test_an_imported_bot_is_disarmed_and_live_on_the_chosen_account(factory):
    exported = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    bot = replace(good(factory), id=None, live=True, enabled=True, account_number="5WX09999")
    bot_id, problems = bot_store.import_bot(factory, bot, exported, NOW)
    assert problems == {}
    loaded = bot_store.load(factory, bot_id)
    assert loaded.live and not loaded.enabled and loaded.account_number == "5WX09999"
    assert bot_store.imported(factory, bot_id) == (exported, None)
    assert bot_store.imported(factory, bot_store.save(factory, good(factory), NOW)[0]) == (
        None,
        None,
    )


def test_an_import_from_a_link_keeps_the_link(factory):
    link = "https://example.com/bots/spx-ic.json"
    bot_id, _ = bot_store.import_bot(factory, replace(good(factory), id=None), None, NOW,
                                     source_url=link)  # fmt: skip
    assert bot_store.imported(factory, bot_id) == (NOW, link)
    # A later save keeps it
    bot_store.save(factory, bot_store.load(factory, bot_id), NOW)
    assert bot_store.imported(factory, bot_id)[1] == link


def test_an_import_that_fails_validation_saves_nothing(factory):
    bot = replace(good(factory), id=None, account_number="")
    bot_id, problems = bot_store.import_bot(factory, bot, None, NOW)
    assert bot_id is None and "broker" in problems
    bot_id, problems = bot_store.import_bot(factory, good(factory), None, NOW, {"x": "bad"})
    assert bot_id is None and problems == {"x": "bad"}
    assert bot_store.listing(factory) == []


def test_an_import_never_overwrites_a_bot(factory):
    bot_id, _ = bot_store.save(factory, good(factory), NOW)
    new_id, _ = bot_store.import_bot(factory, replace(good(factory), id=bot_id), None, NOW)
    assert new_id != bot_id and len(bot_store.listing(factory)) == 2


def test_an_imported_name_never_clobbers_a_bot(factory):
    assert bot_store.imported_name(factory, "IC") == "IC"
    bot_store.save(factory, good(factory), NOW)
    assert bot_store.imported_name(factory, "IC") == "IC (imported)"
    bot_store.save(factory, replace(good(factory), name="IC (imported)"), NOW)
    assert bot_store.imported_name(factory, "IC") == "IC (imported 2)"
    assert len(bot_store.imported_name(factory, "x" * 100)) <= 100


def test_archive_counts_a_wing_left_open_as_an_open_trade(factory):
    bot_id, _ = bot_store.save(factory, good(factory), NOW)
    run_id = add_run(factory, bot_id, "k1", exits=[("stop", "filled"), ("wing", "left")])
    assert bot_store.archive(factory, bot_id, NOW) == [f"1 open trade (run {run_id})"]
    later = datetime(2026, 10, 14, 15, 0, tzinfo=UTC)  # the wing has expired
    assert bot_store.archive(factory, bot_id, later) == []


def test_watch_rules_and_profit_actions_round_trip_through_the_form(factory):
    from optionsjourney.bots.definition import ProfitActionDef, TimeActionDef

    base = good(factory)
    side = replace(base.groups[0], target_pct=D("80"), use_profit_actions=True,
                   profit_actions=(ProfitActionDef(D("25"), 33, D("0")),
                                   ProfitActionDef(D("50"), 0, D("-20"))))  # fmt: skip
    bot = replace(
        base, groups=(side, base.groups[1]), use_sl_ratio_exit=True, sl_ratio_below=D("0.78"),
        use_delta_exit=True, position_delta_below=D("-30"), position_delta_above=D("30"),
        use_underlying_exit=True, underlying_down_pct=D("1.5"), short_call_tested_pts=D("-5"),
        use_time_actions=True, time_actions=(TimeActionDef("dte", 0, "14:00", None, None, 50),),
    )  # fmt: skip
    bot_id, problems = bot_store.save(factory, bot, NOW)
    assert problems == {}
    loaded = bot_store.load(factory, bot_id)
    for name in bot_store.WATCH_FIELDS:
        assert getattr(loaded, name) == getattr(bot, name), name
    assert loaded.groups[0].profit_actions == side.profit_actions
    assert loaded.groups[0].use_profit_actions and not loaded.groups[1].use_profit_actions
    assert loaded.time_actions[0].close_pct == 50
    form = {
        "use_sl_ratio_exit": "on", "sl_ratio_below": "0.78", "sl_ratio_above": "",
        "use_delta_exit": "on", "position_delta_below": "-30", "position_delta_above": "30",
        "use_underlying_exit": "on", "underlying_down_pct": "1.5",
        "short_call_tested_pts": "-5",
        "group-0-use-profit": "on", "group-0-profits": "3",
        "pa-0-0-at": "25", "pa-0-0-close": "33", "pa-0-0-stop": "0",
        "pa-0-1-at": "50", "pa-0-1-close": "", "pa-0-1-stop": "-20",
        "pa-0-2-at": "", "pa-0-2-close": "", "pa-0-2-stop": "",
        "use_time_actions": "on", "actions": "1", "ta-0-type": "dte", "ta-0-days": "0",
        "ta-0-at": "14:00", "ta-0-close": "50",
    }  # fmt: skip
    parsed = bot_store.parse_form(form)
    for name in bot_store.WATCH_FIELDS:
        assert getattr(parsed, name) == getattr(bot, name), name
    assert parsed.groups[0].use_profit_actions and parsed.groups[0].profit_actions == (
        side.profit_actions
    )
    assert not parsed.groups[1].use_profit_actions
    assert parsed.time_actions[0].close_pct == 50


def test_unreadable_watch_and_profit_numbers_are_form_problems():
    _, problems = bot_store.read_form({
        "sl_ratio_below": "abc", "group-1-profits": "1", "pa-1-0-at": "x",
        "actions": "1", "ta-0-type": "dte", "ta-0-days": "0", "ta-0-at": "14:00",
        "ta-0-close": "half",
    })  # fmt: skip
    assert "'abc'" in problems["watch_rules"]
    assert "'x'" in problems["profit-1"]
    assert "'half'" in problems["time_actions"]


def test_alert_settings_round_trip_through_the_store_and_the_form(factory):
    from optionsjourney.bots.definition import DefaultAlertDef as A

    rules = (A("pnl_pct", "above", D("15")), A("pnl_pct", "below", D("-50")),
             A("strike_offset", points=D("10")))  # fmt: skip
    bot = replace(good(factory), alert_name="IF Joint", alert_channel="discord",
                  use_default_alerts=True, default_alerts=rules,
                  default_alerts_in_app_only=True)  # fmt: skip
    bot_id, problems = bot_store.save(factory, bot, NOW)
    assert problems == {}
    loaded = bot_store.load(factory, bot_id)
    for name in bot_store.ALERT_FIELDS:
        assert getattr(loaded, name) == getattr(bot, name), name
    form = {
        "alert_name": " IF Joint ", "alert_channel": "discord", "use_default_alerts": "on",
        "default_alerts_in_app_only": "on", "alerts": "4",
        "da-0-type": "pnl_pct", "da-0-direction": "above", "da-0-value": "15",
        "da-1-type": "pnl_pct", "da-1-direction": "below", "da-1-value": "-50",
        "da-2-type": "strike_offset", "da-2-points": "10",
        "da-3-type": "pnl_pct", "da-3-direction": "above", "da-3-value": "",
    }  # fmt: skip
    parsed = bot_store.parse_form(form)
    for name in bot_store.ALERT_FIELDS:
        assert getattr(parsed, name) == getattr(bot, name), name
    blank = bot_store.parse_form({"alert_name": "  "})
    assert (blank.alert_name, blank.alert_channel, blank.default_alerts) == (None, "all", ())


def test_an_unreadable_default_alert_number_is_a_form_problem():
    _, problems = bot_store.read_form({
        "alerts": "1", "da-0-type": "pnl_pct", "da-0-direction": "above", "da-0-value": "ten",
    })  # fmt: skip
    assert "'ten'" in problems["default_alerts"]


def test_the_form_reads_a_delta_typed_either_way_and_a_spot_leg():
    form = full_form() | {"leg-0-pick": "delta", "leg-0-target": "16",
                          "leg-2-pick": "spot", "leg-2-target": "-50"}  # fmt: skip
    bot = bot_store.parse_form(Form(form))
    assert (bot.legs[0].pick, bot.legs[0].target) == ("delta", D("0.16"))
    assert (bot.legs[2].pick, bot.legs[2].target) == ("spot", D("-50"))
    form["leg-0-target"] = "0.16"
    assert bot_store.parse_form(Form(form)).legs[0].target == D("0.16")


def test_the_form_reads_the_floating_window_and_saves_it(factory):
    form = full_form() | {"tag_id": str(tag_id(factory)), "use_floating_entry": "on",
                          "floating_start": " 11:30 ", "floating_end": "11:45"}  # fmt: skip
    bot = bot_store.parse_form(Form(form))
    assert (bot.use_floating_entry, bot.floating_start, bot.floating_end) == (
        True, "11:30", "11:45",
    )  # fmt: skip
    bot_id, problems = bot_store.save(factory, bot, NOW)
    assert problems == {}
    loaded = bot_store.load(factory, bot_id)
    assert (loaded.use_floating_entry, loaded.floating_start, loaded.floating_end) == (
        True, "11:30", "11:45",
    )  # fmt: skip
    assert [t.time for t in loaded.entry_times] == ["09:47", "12:03"]  # kept
    off = bot_store.parse_form(Form(full_form() | {"floating_start": "", "floating_end": ""}))
    assert (off.use_floating_entry, off.floating_start, off.floating_end) == (False, None, None)


def test_the_form_reads_times_typed_without_a_colon(factory):
    """#114 (Tracker2's normalize_time): 945 is 09:45; entry times may carry seconds."""
    form = full_form() | {"time-0": "947", "time-1": "93015", "use_floating_entry": "on",
                          "floating_start": "1130", "floating_end": "11:45:15"}  # fmt: skip
    bot = bot_store.parse_form(Form(form))
    assert [t.time for t in bot.entry_times] == ["09:47", "09:30:15"]
    assert (bot.floating_start, bot.floating_end) == ("11:30", "11:45:15")  # as typed: refused
    bot_id, problems = bot_store.save(factory, replace(bot, floating_end="11:45",
                                      tag_id=tag_id(factory)), NOW)  # fmt: skip
    assert problems == {}
    assert [t.time for t in bot_store.load(factory, bot_id).entry_times] == ["09:30:15", "09:47"]


def test_broker_change_drops_to_paper(factory):
    """Final review I-2 (slice 11): a broker change needs the new broker's own arming
    and live steps (a Schwab bot's stops are watched by the app)."""
    from tests.services.bot_fakes import OPEN_TIME, make

    bot_id = make(factory, live=True, enabled=True)
    bot = bot_store.load(factory, bot_id)
    saved, problems = bot_store.save(factory, replace(bot, account_number="5WX09999"), OPEN_TIME)
    assert problems == {}
    same = bot_store.load(factory, bot_id)
    assert same.enabled and same.live  # another account at the same broker: unchanged
    moved = replace(same, broker="schwab", account_number="12345678")
    bot_store.save(factory, moved, OPEN_TIME)
    after = bot_store.load(factory, bot_id)
    assert after.broker == "schwab" and not after.enabled and not after.live


def test_clone_keeps_the_sources_mode_and_is_disarmed(factory):
    bot_id, _ = bot_store.save(factory, good(factory), NOW)
    live_copy, _ = bot_store.clone(factory, bot_id, NOW)
    bot_store.set_mode(factory, bot_id, "paper", NOW)
    paper_copy, _ = bot_store.clone(factory, bot_id, NOW)
    live, paper = bot_store.load(factory, live_copy), bot_store.load(factory, paper_copy)
    assert live.live is True and live.enabled is False
    assert paper.live is False and paper.enabled is False


def test_set_mode_switches_between_live_and_paper(factory):
    bot_id, _ = bot_store.save(factory, good(factory), NOW)
    bot_store.set_mode(factory, bot_id, "paper", NOW)
    assert bot_store.load(factory, bot_id).live is False
    bot_store.set_mode(factory, bot_id, "live", NOW)
    assert bot_store.load(factory, bot_id).live is True


def test_a_change_that_makes_the_app_watch_a_bots_stops_disarms_it(factory):
    """Final review I3 (controller's ruling): an armed, live iron condor edited so that
    a group's stop is watched by the app (here: its targets rest now) is switched
    off and put in preview, as a broker change is, so the "watched by the app" box is
    ticked again before it trades."""
    from optionsjourney.bots.definition import app_watched
    from tests.services.bot_fakes import OPEN_TIME, make

    bot_id = make(factory, live=True, enabled=True)
    bot = bot_store.load(factory, bot_id)
    assert not app_watched(bot)
    groups = tuple(replace(g, stop_basis="group_entry", target_pct=D("50")) for g in bot.groups)
    watched = replace(bot, groups=tuple(replace(g, broker_exit="target") for g in groups))
    assert app_watched(watched)
    assert bot_store.save(factory, watched, OPEN_TIME)[1] == {}
    after = bot_store.load(factory, bot_id)
    assert after.groups[0].broker_exit == "target" and not after.enabled and not after.live


def test_an_edit_to_a_stop_only_group_that_rests_its_target_disarms_it(factory):
    """Fix round 1: an armed, live iron condor edited to a call debit spread with a stop and
    no target: its target rests by choice, so its stop is watched by the app on any debit
    fill. The save switches it off and puts it in preview."""
    from optionsjourney.bots.definition import app_watched, shape
    from tests.services.bot_fakes import OPEN_TIME, make

    bot_id = make(factory, live=True, enabled=True)
    bot = bot_store.load(factory, bot_id)
    spread = shape("call_debit_spread", bot)
    legs = (replace(spread.legs[0], target=D("2.70")), replace(spread.legs[1], target=D("10")))
    group = replace(spread.groups[0], stop_pct=D("50"))
    edited = replace(spread, legs=legs, groups=(group,))
    assert group.broker_exit == "target" and group.target_value is None
    assert app_watched(edited)
    assert bot_store.save(factory, edited, OPEN_TIME)[1] == {}
    after = bot_store.load(factory, bot_id)
    assert after.groups[0].broker_exit == "target" and not after.enabled and not after.live


def test_a_change_that_leaves_app_watching_as_it_was_keeps_arming(factory):
    from tests.services.bot_fakes import OPEN_TIME, make, make_shape

    bot_id = make(factory, live=True, enabled=True)  # resting stops, before and after
    bot = bot_store.load(factory, bot_id)
    assert bot_store.save(factory, replace(bot, name="IC 2"), OPEN_TIME)[1] == {}
    after = bot_store.load(factory, bot_id)
    assert after.enabled and after.live
    straddle = make_shape(factory, "long_straddle", live=True, stop=Decimal("50"))
    bot = bot_store.load(factory, straddle)  # watched before and after
    assert bot_store.save(factory, replace(bot, name="Straddle 2"), OPEN_TIME)[1] == {}
    after = bot_store.load(factory, straddle)
    assert after.enabled and after.live


def test_a_wing_row_with_no_short_stops_blocking_archive_once_its_leg_expires(factory):
    # A long call fly's leg-out wing row (bots slice 13): no short column, its one leg
    # in `legs`. Working, it blocks; past its leg's expiry it can't be live any more.
    from optionsjourney.db.models import BotExit

    bot_id, _ = bot_store.save(factory, good(factory), NOW)
    run_id = add_run(factory, bot_id, "k1", exits=[("target", "filled")])
    wing = "SPXW  261013C05985000"
    with factory() as session:
        session.add(BotExit(
            run_id=run_id, group_position=0, kind="wing", state="working",
            short_symbol="", long_symbol=wing, anchor_symbol=None,
            legs=[{"symbol": wing, "side": "buy", "ratio": 1}], contracts=1, rearms=0,
            log=[], created_at=NOW, updated_at=NOW,
        ))  # fmt: skip
        session.commit()
    assert bot_store.open_trade_reasons(factory, bot_id, NOW) == [
        f"1 exit still working (run {run_id})"
    ]
    later = datetime(2026, 10, 14, 15, 0, tzinfo=UTC)
    assert bot_store.open_trade_reasons(factory, bot_id, later) == []


def test_split_is_saved_only_for_an_iron_condor(factory):
    # Spec question 7: "Split iron condors" applies only to an iron condor; a straddle
    # saved with the box ticked is one trade in the journal.
    from tests.services.bot_fakes import make_shape

    straddle_id = make_shape(factory, "long_straddle")
    straddle = bot_store.load(factory, straddle_id)
    assert bot_store.save(factory, replace(straddle, split_iron_condors=True), NOW)[1] == {}
    assert bot_store.load(factory, straddle_id).split_iron_condors is False
    condor_id, problems = bot_store.save(factory, replace(good(factory), split_iron_condors=True),
                                         NOW)  # fmt: skip
    assert problems == {}
    assert bot_store.load(factory, condor_id).split_iron_condors is True


def _counted(legs: int, groups: int) -> dict:
    form = full_form() | {"leg_count": str(legs), "group_count": str(groups)}
    for i in range(4):
        form[f"leg-{i}-group"] = str(i % groups)
    for g in range(4):
        form.setdefault(f"group-{g}-name", "")
    return form


def test_read_form_reads_as_many_legs_and_groups_as_it_says():
    one, _ = bot_store.read_form(Form(_counted(1, 1)))
    assert len(one.legs) == 1 and len(one.groups) == 1
    assert one.groups[0].name == "Call side"
    four, _ = bot_store.read_form(Form(_counted(4, 4) | {"group-3-stop": "x"}))
    assert [leg.group for leg in four.legs] == [0, 1, 2, 3]
    assert [g.name for g in four.groups] == ["Call side", "Put side", "Group 3", "Group 4"]
    assert [g.position for g in four.groups] == [0, 1, 2, 3]
    _, problems = bot_store.read_form(Form(_counted(4, 4) | {"group-3-stop": "x"}))
    assert "group-3" in problems  # a group past the second is checked too
    # Without the counts (a form from before slice 13): four legs in two groups.
    old, _ = bot_store.read_form(Form(full_form()))
    assert (len(old.legs), len(old.groups)) == (4, 2)
    # Never more than a bot can hold, whatever the count says.
    many, _ = bot_store.read_form(Form(_counted(4, 4) | {"leg_count": "9", "group_count": "9"}))
    assert (len(many.legs), len(many.groups)) == (4, 4)


def test_a_blank_group_name_is_a_side_only_on_an_iron_condor():
    """Review M2."""
    form = full_form() | {"group-0-name": "", "group-1-name": ""}
    assert [g.name for g in bot_store.parse_form(Form(form)).groups] == ["Call side", "Put side"]
    spread = form | {"leg_count": "2", "group_count": "2", "leg-1-group": "1"}
    assert [g.name for g in bot_store.parse_form(Form(spread)).groups] == ["Group 1", "Group 2"]


def test_an_unreadable_leg_target_is_a_form_problem():
    _, problems = bot_store.read_form(Form(full_form() | {"leg-2-target": "2,90"}))
    assert problems["leg-2"] == "The target '2,90' isn't a number."


def test_the_form_reads_each_legs_dte_and_exact_dte_and_saves_them(factory):
    """Bots slice 14: a DTE per leg (blank is 0) and Exact DTE only; a form without the
    box (from before this slice) keeps exact DTE on."""
    form = full_form() | {"tag_id": str(tag_id(factory)), "leg-1-dte": "14",
                          "leg-3-dte": " 14 ", "leg-0-dte": "7", "leg-2-dte": "7",
                          "exact_dte_shown": "1", "close_time": "15:45"}  # fmt: skip
    bot = bot_store.parse_form(Form(form))
    assert [leg.dte for leg in bot.legs] == [7, 14, 7, 14]
    assert bot.use_exact_dte is False
    bot = replace(bot, groups=tuple(replace(g, stop_basis="group_entry") for g in bot.groups))
    bot_id, problems = bot_store.save(factory, bot, NOW)
    assert problems == {}
    loaded = bot_store.load(factory, bot_id)
    assert [leg.dte for leg in loaded.legs] == [7, 14, 7, 14]
    assert loaded.use_exact_dte is False
    assert bot_store.parse_form(Form(full_form())).use_exact_dte is True
    assert bot_store.parse_form(Form(full_form())).legs[0].dte == 0
    on = bot_store.parse_form(Form(full_form() | {"exact_dte_shown": "1", "use_exact_dte": "on"}))
    assert on.use_exact_dte is True


def test_an_unreadable_dte_is_a_form_problem():
    _, problems = bot_store.read_form(Form(full_form() | {"leg-1-dte": "7d"}))
    assert problems["leg-1"] == "Days to expiration '7d' isn't a whole number, 0 or more."


def _calendar_without_timed_close(factory):
    """An iron condor whose bought legs are 14 DTE: two multi-expiry groups."""
    bot = good(factory)
    legs = tuple(replace(leg, dte=14) if leg.position in (1, 3) else leg for leg in bot.legs)
    return replace(bot, legs=legs, close_time=None)


TIMED = "A calendar or diagonal must close before its near legs expire: set a timed close."


def test_a_calendar_without_a_timed_close_is_not_cloned(factory):
    """Review: the rule holds for clone, not just the form."""
    bot_id, problems = bot_store.save(
        factory, replace(_calendar_without_timed_close(factory), close_time="15:45"), NOW
    )
    assert problems == {}
    with factory() as session:
        session.get(Bot, bot_id).close_time = None  # written some other way
        session.commit()
    copy_id, problems = bot_store.clone(factory, bot_id, NOW)
    assert copy_id is None and problems["timed_close"] == TIMED


def test_a_calendar_without_a_timed_close_is_not_imported(factory):
    import json

    from optionsjourney.bots import bot_file

    raw = json.dumps(bot_file.to_file(_calendar_without_timed_close(factory), NOW))
    imported = bot_file.parse(raw).bot
    bot = replace(imported, tag_id=good(factory).tag_id, account_number="5WX01234")
    bot_id, problems = bot_store.import_bot(factory, bot, None, NOW)
    assert bot_id is None and problems["timed_close"] == TIMED
    assert bot_store.listing(factory) == []


def test_slice_19_settings_round_trip_through_the_store(factory):
    """Dollar sizing, the chase's window and market switch, the leg deltas, the min short
    width, moving conflicted strikes and a profit action's $ stop are saved and loaded."""
    from optionsjourney.bots.definition import ProfitActionDef

    base = good(factory)
    legs = list(base.legs)
    legs[0] = replace(legs[0], exit_delta_below=D("-40"), exit_delta_above=D("10"))
    legs[3] = replace(legs[3], pick="premium", target=D("0.50"), link=2, max_offset=D("-25"))
    side = replace(base.groups[0], target_pct=D("80"), use_profit_actions=True,
                   profit_actions=(ProfitActionDef(D("25"), 0, None, D("0.40")),))  # fmt: skip
    bot = replace(
        base, legs=tuple(legs), groups=(side, base.groups[1]), use_delta_exit=True,
        min_short_width=D("20"), move_conflicted_strikes=True,
        entry_chase=replace(base.entry_chase, window_minutes=3),
        exit_chase=replace(base.exit_chase, switch_to_market=True),
    )  # fmt: skip
    bot_id, problems = bot_store.save(factory, bot, NOW)
    assert problems == {}
    loaded = bot_store.load(factory, bot_id)
    assert loaded.legs == bot.legs
    assert loaded.groups[0].profit_actions == side.profit_actions
    assert (loaded.min_short_width, loaded.move_conflicted_strikes) == (D("20"), True)
    assert (loaded.entry_chase.window_minutes, loaded.exit_chase.switch_to_market) == (3, True)
    debit = replace(
        bot, id=bot_id, sizing="dollars", max_per_entry_usd=D("300"),
        groups=tuple(replace(g, stop_basis="group_entry", use_profit_actions=False)
                     for g in bot.groups),
    )  # fmt: skip
    assert bot_store.save(factory, debit, NOW)[1] == {}
    loaded = bot_store.load(factory, bot_id)
    assert (loaded.sizing, loaded.max_per_entry_usd) == ("dollars", D("300"))


def test_all_tags_offers_every_bot_tag_but_a_deleted_bots(factory):
    bot_store.save(factory, replace(good(factory), tags=("mornings", "Fridays")), NOW)
    old, _ = bot_store.save(factory, replace(good(factory), name="Old", tags=("Late",)), NOW)
    gone, _ = bot_store.save(factory, replace(good(factory), name="Gone", tags=("Secret",)), NOW)
    bot_store.save(factory, replace(good(factory), name="Twin", tags=("Mornings",)), NOW)
    bot_store.archive(factory, old, NOW)
    bot_store.hide(factory, gone, NOW)
    offered = set(bot_store.all_tags(factory)) - _journal_tags(factory)
    assert offered == {"Fridays", "mornings", "Late"}


def _journal_tags(factory):
    from optionsjourney.db.models import Tag

    with factory() as session:
        return set(session.scalars(select(Tag.name)))


def test_all_tags_offers_the_journals_tags_too(factory):
    from optionsjourney.db.models import Tag

    seeded = _journal_tags(factory)  # the starter tags
    assert seeded and seeded <= set(bot_store.all_tags(factory))
    bot_store.save(factory, replace(good(factory), tags=("mornings",)), NOW)
    with factory() as session:
        session.add_all([Tag(name="Mornings"), Tag(name="Earnings"), Tag(name="Live")])
        session.commit()
    offered = bot_store.all_tags(factory)
    assert {"Earnings", "mornings"} <= set(offered)  # a bot's spelling wins
    assert "Mornings" not in offered and "Live" not in offered  # a state word never is


def test_tag_many_changes_only_the_tags_and_names_a_bot_that_would_pass_the_limit(factory):
    first, _ = bot_store.save(factory, replace(good(factory), tags=("Beta",)), NOW)
    many = tuple(f"t{i}" for i in range(20))
    full, _ = bot_store.save(factory, replace(good(factory), name="Full", tags=many), NOW)
    with factory() as session:
        row = session.get(Bot, first)
        row.live, row.enabled = True, True  # armed and live: tags still change
        session.commit()
    changed, too_many = bot_store.tag_many(factory, [first, full, 999], add=["Wings"], remove=[])
    assert changed == [first] and too_many == ["Full"]
    bot = bot_store.load(factory, first)
    assert bot.tags == ("Beta", "Wings") and bot.live and bot.enabled  # nothing else moved
    assert len(bot_store.load(factory, full).tags) == 20
    changed, _ = bot_store.tag_many(factory, [first], add=[], remove=["beta"])
    assert changed == [first] and bot_store.load(factory, first).tags == ("Wings",)
    unchanged, _ = bot_store.tag_many(factory, [first], add=["wings"], remove=[])
    assert unchanged == []  # already tagged: nothing to change


def test_a_groups_pairing_is_read_from_the_form():
    """Bots slice 21: "When its stop fills, also close" (group-G-pair)."""
    from optionsjourney.services.bot_store import _group

    assert _group({"group-0-name": "Call side", "group-0-pair": "1"}, 0).close_on_stop == 1
    assert _group({"group-0-name": "Call side", "group-0-pair": ""}, 0).close_on_stop is None


def test_rests_at_the_broker_round_trips_per_group(factory):
    bot = good(factory)
    groups = (replace(bot.groups[0], broker_exit="target"), bot.groups[1])
    bot_id, problems = bot_store.save(factory, replace(bot, groups=groups), NOW)
    assert problems == {}
    assert [g.broker_exit for g in bot_store.load(factory, bot_id).groups] == ["target", "stop"]


def test_the_form_reads_each_groups_rests_at_the_broker():
    bot = bot_store.parse_form(Form(full_form() | {"group-0-rests": "target"}))
    # decision 0054: an iron condor's sides are two legs each, so both rest their targets
    assert [g.broker_exit for g in bot.groups] == ["target", "target"]


def test_the_form_reads_a_stops_unit_and_basis():
    """#124 (Tracker2's selects): % / basis with its basis, or $ P&L; an older form's one
    style still reads."""
    read = lambda **f: bot_store.parse_form(Form(full_form() | f)).groups[0]  # noqa: E731
    g = read(**{"group-0-stop": "0.20", "group-0-stop-unit": "pct",
                "group-0-stop-basis": "closing_order"})  # fmt: skip
    assert (g.stop_unit, g.stop_basis, g.stop_pct) == ("pct", "closing_order", Decimal("0.20"))
    g = read(**{"group-0-stop": "2", "group-0-stop-unit": "usd",
                "group-0-stop-basis": "entry_premium"})  # fmt: skip
    assert (g.stop_unit, g.stop_usd, g.stop_pct) == ("usd", Decimal("2"), None)
    g = read(**{"group-0-stop": "50", "group-0-stop-style": "group_entry"})
    assert (g.stop_unit, g.stop_basis) == ("pct", "group_entry")


def test_the_form_reads_the_resting_stop_box():
    """#126: where the box was shown, ticked rests the stop and unticked the target."""
    read = lambda **f: bot_store._rests(Form(f), 0)  # noqa: E731
    shown = {"group-0-rest-shown": "1"}
    assert read(**shown, **{"group-0-rest-stop": "on"}) == "stop"
    assert read(**shown) == "target"
    assert read(**{"group-0-rests": "target"}) == "target"  # hidden, or a form from before
    assert read() == "stop"


def test_ignore_bidless_longs_round_trips_on_the_exit_chase(factory):
    bot = good(factory)
    chase = replace(bot.exit_chase, ignore_bidless_longs=True)
    bot_id, problems = bot_store.save(factory, replace(bot, exit_chase=chase), NOW)
    assert problems == {}
    loaded = bot_store.load(factory, bot_id)
    assert loaded.exit_chase.ignore_bidless_longs and not loaded.entry_chase.ignore_bidless_longs


def test_the_form_reads_ignore_bidless_longs_on_the_exit_chase_only():
    assert bot_store.parse_form({"chase-exit-bidless": "on"}).exit_chase.ignore_bidless_longs
    assert not bot_store.parse_form({}).exit_chase.ignore_bidless_longs
    assert not bot_store.parse_form({"chase-entry-bidless": "on"}).entry_chase.ignore_bidless_longs


def test_an_existing_bot_row_loads_with_ignore_bidless_longs_off(factory):
    from optionsjourney.db.models import BotChase

    bot_id, _ = bot_store.save(factory, good(factory), NOW)
    with factory() as session:
        for row in session.scalars(select(BotChase).where(BotChase.bot_id == bot_id)):
            row.ignore_bidless_longs = False
        session.commit()
    loaded = bot_store.load(factory, bot_id)
    assert not loaded.exit_chase.ignore_bidless_longs


def test_split_is_kept_for_a_condor_with_a_group_per_leg(factory):
    # #165: Tracker2's MEIC bots put each leg in its own group; still an iron condor.
    from tests.bots.test_definition import per_leg

    meic = per_leg(replace(good(factory), split_iron_condors=True))
    # stops on the sold legs; the bought ones are left to expire
    meic = replace(meic, groups=tuple(replace(g, stop_pct=D("95") if g.position in (0, 2) else None)
                                      for g in meic.groups))  # fmt: skip
    bot_id, problems = bot_store.save(factory, meic, NOW)
    assert problems == {}
    saved = bot_store.load(factory, bot_id)
    assert saved.split_iron_condors is True and len(saved.groups) == 4
    from optionsjourney.services.bot_entry import entering_weight

    assert entering_weight(saved) == 2  # max open trades: each side is a trade
    assert entering_weight(replace(saved, split_iron_condors=False)) == 1


def test_a_split_meic_bots_halves_match_by_side(factory):
    # #165: the journal shows the condor as a put spread and a call spread; the bot's
    # pattern reads its sides from the legs' types, not from its per-leg groups.
    from optionsjourney.services.bot_match import pattern_of
    from tests.bots.test_definition import per_leg

    two = good(factory)
    assert sorted(pattern_of(per_leg(two)).group_shapes) == sorted(pattern_of(two).group_shapes)
    assert len(pattern_of(per_leg(two)).group_shapes) == 2
    assert pattern_of(per_leg(replace(two, split_iron_condors=False))).group_shapes == ()
