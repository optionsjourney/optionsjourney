from dataclasses import replace
from decimal import Decimal

from optionsjourney.bots.definition import EntryTimeDef, iron_condor_skeleton
from optionsjourney.bots.strategy_rows import OFF_GROUP, base_name, by_strategy, rows

BOT = iron_condor_skeleton("x")
ACCOUNTS = {"A1": "Beta ••0001", "A2": "Roth IRA ••0002", "A3": "Joint ••0003"}
STRATEGIES = {1: "0DTE", 2: "Calendars"}


def bot(id_, name, account="A1", tag_id=1, **changes):
    return replace(BOT, id=id_, name=name, account_number=account, tag_id=tag_id, **changes)


def build(bots, pl=None):
    return rows(bots, lambda b: ACCOUNTS[b.account_number], STRATEGIES, pl)


def test_a_suffix_naming_the_bots_own_account_is_dropped():
    assert base_name("Iron Condor - Beta", "Beta ••0001") == "Iron Condor"
    assert base_name("Friday Calendar - IRA", "Roth IRA ••0002") == "Friday Calendar"


def test_a_suffix_naming_something_else_stays():
    assert base_name("Butterfly - XSP", "Beta ••0001") == "Butterfly - XSP"
    assert base_name("Iron Condor - IRA", "Joint ••0003") == "Iron Condor - IRA"  # not this bot's account
    assert base_name("Iron Condor - Beta", None) == "Iron Condor - Beta"
    assert base_name(" - Beta", "Beta") == "- Beta"


def test_account_variants_of_one_strategy_are_one_row():
    a = bot(1, "Iron Condor - Beta", "A1", contracts=2)
    b = bot(2, "Iron Condor - IRA", "A2", contracts=1)
    c = bot(3, "Butterfly - XSP", "A3")
    got = build([a, b, c])
    assert [(r.name, r.key) for r in got] == [("Butterfly - XSP", "3"), ("Iron Condor", "1-2")]
    meic = got[1]
    assert meic.multi and meic.lots == 3 and not meic.lots_partial
    assert [m.account for m in meic.members] == ["Beta ••0001", "Roth IRA ••0002"]


def test_bots_under_different_strategies_never_merge():
    got = build([bot(1, "Iron Condor - Beta", "A1", tag_id=1), bot(2, "Iron Condor - IRA", "A2", tag_id=2)])
    assert [(r.strategy, r.key) for r in got] == [("0DTE", "1"), ("Calendars", "2")]


def test_pl_adds_up_real_and_paper_apart_and_not_when_records_overlap():
    a, b = bot(1, "Iron Condor - Beta", "A1"), bot(2, "Iron Condor - IRA", "A2")
    row = build([a, b], {1: (Decimal("100"), False), 2: (Decimal("-30"), False)})[0]
    assert row.pl == Decimal("70") and row.paper_pl is None and not row.pl_shared
    row = build([a, b], {1: (Decimal("100"), False), 2: (Decimal("5"), True)})[0]
    assert (row.pl, row.paper_pl) == (Decimal("100"), Decimal("5"))
    twin = bot(3, "MEIC", "A1")  # same name, same account and tag as a
    assert build([bot(1, "MEIC", "A1"), twin])[0].pl_shared


def test_armed_live_and_paper():
    on_live = bot(1, "Iron Condor - Beta", "A1", enabled=True, live=True)
    on_paper = bot(2, "Iron Condor - IRA", "A2", enabled=True, live=False)
    off_live = bot(2, "Iron Condor - IRA", "A2", enabled=False, live=True)
    row = build([on_live, on_paper])[0]
    assert row.armed and row.live and not row.paper
    row = build([replace(on_live, enabled=False), on_paper])[0]
    assert row.armed and row.paper and not row.live
    row = build([replace(on_live, enabled=False), off_live])[0]
    assert not row.armed and not row.live and not row.paper


def test_days_and_times():
    a = bot(1, "Iron Condor - Beta", "A1", frequency="weekly", entry_days=(2, 4),
            entry_times=(EntryTimeDef("10:30"), EntryTimeDef("09:47")))  # fmt: skip
    b = bot(2, "Iron Condor - IRA", "A2", frequency="weekly", entry_days=(4,),
            entry_times=(EntryTimeDef("10:30"), EntryTimeDef("09:47")))  # fmt: skip
    row = build([a, b])[0]
    assert row.weekly and row.days == (2, 4) and row.shared_times
    assert row.first_time == "09:47" and row.days_label == "Tue, Thu / Thu"
    c = replace(b, entry_times=(EntryTimeDef("11:00"),))
    assert not build([a, c])[0].shared_times
    monthly = bot(3, "Monthly", frequency="monthly", monthly_week=1, monthly_weekday=5)
    assert not build([monthly])[0].weekly and build([bot(4, "D")])[0].days == (1, 2, 3, 4, 5)


def test_dollar_sized_bots_arent_counted_in_lots():
    sized = bot(1, "Iron Condor - Beta", "A1", contracts=2)
    row = build([sized, bot(2, "Iron Condor - IRA", "A2", sizing="dollars")])[0]
    assert row.lots == 2 and row.lots_partial
    assert build([bot(3, "D", sizing="dollars")])[0].lots is None


def test_groups_by_strategy_with_the_off_ones_last():
    got = by_strategy(build([
        bot(1, "Cal", tag_id=2, enabled=True), bot(2, "IC", tag_id=1, enabled=True),
        bot(3, "Idle", tag_id=1), bot(4, "Untagged", tag_id=None, enabled=True),
    ]))  # fmt: skip
    assert [(g, [r.name for r in rs]) for g, rs in got] == [
        ("0DTE", ["IC"]), ("Calendars", ["Cal"]), ("No strategy", ["Untagged"]),
        (OFF_GROUP, ["Idle"]),
    ]  # fmt: skip


def test_rows_sort_armed_first_then_day_then_time():
    early = bot(1, "B early", enabled=True, entry_times=(EntryTimeDef("09:35"),))
    late = bot(2, "A late", enabled=True, entry_times=(EntryTimeDef("15:00"),))
    off = bot(3, "C off", entry_times=(EntryTimeDef("09:31"),))
    tue = bot(4, "D tue", enabled=True, frequency="weekly", entry_days=(2,))
    names = [r.name for r in build([off, tue, late, early])]
    assert names == ["B early", "A late", "D tue", "C off"]
