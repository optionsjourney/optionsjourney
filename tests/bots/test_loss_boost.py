"""#328: the loss boost's rules (bots/loss_boost.py) and how it sizes and caps an entry
(bots/definition.py)."""

from dataclasses import replace
from datetime import date

import pytest

from optionsjourney.bots import loss_boost
from optionsjourney.bots.definition import (
    MAX_CONTRACTS,
    EntryTimeDef,
    contracts_for,
    day_cap,
    iron_condor_skeleton,
    limits,
    loss_boost_multiplier,
    trade_cap,
)


def bot(**changes):
    base = iron_condor_skeleton("IC")
    return replace(base, **({"entry_times": (EntryTimeDef("09:47"),), "contracts": 1} | changes))


def test_the_boost_is_off_unless_switched_on():
    assert loss_boost_multiplier(bot()) == 1
    assert loss_boost_multiplier(bot(loss_boost_multiplier=3)) == 1


def test_a_blank_multiplier_reads_2():
    assert loss_boost_multiplier(bot(use_loss_boost=True)) == 2
    assert loss_boost_multiplier(bot(use_loss_boost=True, loss_boost_multiplier=3)) == 3


def test_a_boost_multiplies_the_contracts_asked_for():
    on = bot(use_loss_boost=True, max_per_trade=10)
    assert contracts_for(on, "09:47", boost=2) == (2, None)
    assert contracts_for(on, "09:47") == (1, None)  # no stop before: no boost


def test_max_per_trade_still_caps_a_boosted_entry():
    on = bot(use_loss_boost=True, loss_boost_multiplier=3, contracts=2, max_per_trade=4)
    assert contracts_for(on, "09:47", boost=3) == (4, 6)  # 6 asked, cut to 4


def test_an_entry_times_own_quantity_is_boosted_too():
    on = bot(use_loss_boost=True, max_per_trade=10,
             entry_times=(EntryTimeDef("09:47", quantity=3),))  # fmt: skip
    assert contracts_for(on, "09:47", boost=2) == (6, None)


def test_auto_limits_make_room_for_the_boosted_size():
    """Decision 0058's Auto is the bot's own biggest entry; with the boost on it counts the
    boosted entry, or Auto would cut every boost back to the base size."""
    off = bot(contracts=2, entry_times=(EntryTimeDef("09:47"), EntryTimeDef("10:00")))
    on = replace(off, use_loss_boost=True, loss_boost_multiplier=3)
    assert (trade_cap(off), day_cap(off)) == (2, 4)
    assert (trade_cap(on), day_cap(on)) == (6, 12)
    assert contracts_for(on, "09:47", boost=3) == (6, None)
    assert limits(on, by_hand=False) == (6, 12)


def test_auto_never_passes_the_contract_ceiling():
    on = bot(contracts=60, use_loss_boost=True, loss_boost_multiplier=3)
    assert trade_cap(on) == MAX_CONTRACTS


def test_a_typed_limit_is_kept_as_typed():
    on = bot(use_loss_boost=True, max_per_trade=1, max_per_day=3)
    assert (trade_cap(on), day_cap(on)) == (1, 3)


def test_dollar_sizing_keeps_its_auto_cap():
    on = bot(sizing="dollars", use_loss_boost=True)
    assert trade_cap(on) == MAX_CONTRACTS


@pytest.mark.parametrize(
    ("kind", "state", "by", "stopped"),
    [
        ("stop", "filled", None, True),  # a resting stop that filled
        ("stop", "working", None, False),
        ("stop", "cancelled", None, False),
        ("target", "filled", None, False),
        ("timed", "filled", None, False),
        ("close", "filled", "stop", True),  # a watched stop's close
        ("close", "reduced", "stop", True),  # part of the side closed by it
        ("close", "failed", "stop", False),
        ("close", "filled", None, False),  # a close you asked for
        ("close", "filled", "delta", False),  # a watch rule's close
        ("wing", "filled", None, False),
    ],
)
def test_what_counts_as_a_stop(kind, state, by, stopped):
    assert loss_boost.stop_exit(kind, state, by) is stopped


@pytest.mark.parametrize(
    ("kind", "reason", "stopped"),
    [
        ("exit", "stop", True),
        ("exit", "stop (last price)", True),
        ("exit", "target", False),
        ("exit", "timed close", False),
        ("expiry", "expired", False),
        ("entry", "entry", False),
    ],
)
def test_what_counts_as_a_paper_stop(kind, reason, stopped):
    assert loss_boost.paper_stop(kind, reason) is stopped


def test_a_boost_record_and_its_words():
    boost = loss_boost.Boost(2, 41, date(2026, 10, 12))
    assert boost.record() == {"boost": 2, "after_run": 41, "after_day": "2026-10-12"}
    assert loss_boost.words(boost.record()) == "×2: run 41 (2026-10-12) hit its stop"


def test_the_multiplier_is_checked_only_while_the_boost_is_on():
    from optionsjourney.bots.validation import validate

    off = bot(use_loss_boost=False, loss_boost_multiplier=20)  # a leftover from a file
    assert "loss_boost" not in validate(off, {off.tag_id})
    on = bot(use_loss_boost=True, loss_boost_multiplier=20)
    assert "loss_boost" in validate(on, {on.tag_id})
