from dataclasses import replace
from decimal import Decimal as D

from optionsjourney.bots.definition import (
    DEFAULT_ENTRY_CHASE,
    MAX_CONTRACTS,
    MAX_GROUPS,
    MAX_LEGS,
    SHAPES,
    EntryTimeDef,
    GroupDef,
    LegDef,
    add_group,
    add_leg,
    add_linked_leg,
    app_watched,
    contracts_for,
    day_cap,
    group_kind,
    iron_condor_skeleton,
    is_iron_condor,
    largest_ratio,
    limits,
    lone_shorts_rest_stops,
    move_leg,
    one_group,
    remove_group,
    remove_leg,
    rests_stop,
    shape,
    trade_cap,
    watched_groups,
)
from optionsjourney.bots.validation import validate
from optionsjourney.trading.orders import Side


def lone_shorts():
    """A short strangle as two groups of one sold leg: each can rest its stop (0054)."""
    bot = iron_condor_skeleton("x")
    sold = [leg for leg in bot.legs if leg.side is Side.SELL]
    legs = tuple(replace(leg, position=i, group=i, link=None) for i, leg in enumerate(sold))
    groups = tuple(replace(g, broker_exit="stop") for g in bot.groups)
    return replace(bot, legs=legs, groups=groups)


def test_the_skeleton_is_an_iron_condor_with_blank_targets():
    bot = iron_condor_skeleton("SPX 0DTE IC")
    assert [(leg.side, leg.right, leg.pick) for leg in bot.legs] == [
        (Side.SELL, "call", "premium"), (Side.BUY, "call", "offset"),
        (Side.SELL, "put", "premium"), (Side.BUY, "put", "offset"),
    ]  # fmt: skip
    assert all(leg.target is None for leg in bot.legs)
    assert bot.legs[1].link == 0 and bot.legs[3].link == 2 and all(leg.exact for leg in bot.legs)
    assert [g.name for g in bot.groups] == ["Call side", "Put side"]
    assert [leg.position for leg in bot.legs_in(1)] == [2, 3]
    assert not bot.live and not bot.enabled and bot.split_iron_condors
    assert bot.broker == "tastytrade" and bot.entry_chase == DEFAULT_ENTRY_CHASE


def test_the_skeleton_keeps_todays_behavior_in_the_new_fields():
    bot = iron_condor_skeleton("x")
    assert bot.frequency == "daily" and bot.entry_days == () and bot.specific_dates == ()
    assert bot.max_entries_per_day is None and bot.max_open_trades is None
    assert not bot.use_premium_bounds
    assert bot.premium_min is None and bot.premium_max is None
    assert not bot.use_blackout_days and bot.blackout_days == ()
    assert bot.entry_times == () and MAX_CONTRACTS == 100


def test_an_entry_time_defaults_to_on_with_the_bots_contracts():
    entry = EntryTimeDef("09:47")
    assert entry.enabled and entry.quantity is None and entry.label is None


def test_contracts_for_uses_the_bots_contracts_unless_the_time_overrides():
    bot = replace(
        iron_condor_skeleton("x"), contracts=2, max_per_trade=5,
        entry_times=(EntryTimeDef("09:47"), EntryTimeDef("10:30", quantity=4)),
    )  # fmt: skip
    assert contracts_for(bot, "09:47") == (2, None)
    assert contracts_for(bot, "10:30") == (4, None)
    assert contracts_for(replace(bot, max_per_trade=3), "10:30") == (3, 4)  # clamped
    assert contracts_for(bot, "11:00") == (2, None)  # not a listed time: the bot's contracts


def test_a_side_reads_its_stop_and_target_by_unit():
    side = GroupDef(0, "Call side", stop_pct=D("95"), target_pct=D("50"),
                    stop_usd=D("2"), target_usd=D("1"))  # fmt: skip
    assert (side.stop_value, side.target_value) == (D("95"), D("50"))
    usd = replace(side, stop_unit="usd", target_unit="usd")
    assert (usd.stop_value, usd.target_value) == (D("2"), D("1"))
    assert side.stop_basis == "leg_premium" and side.on_stop_remainder == "close_market"
    assert side.remainder_limit is None
    bot = iron_condor_skeleton("IC")
    assert bot.stop_co_mode == "off" and bot.stop_co_amount is None


def test_tags_are_normalized():
    from optionsjourney.bots.definition import TAG_MAX_LEN, normalize_tags

    tags = normalize_tags(["  fomc  week ", "0DTE", "fomc week", "", "x" * 60, "Iron"])
    assert tags == ("0DTE", "fomc week", "Iron", "x" * TAG_MAX_LEN)


def test_reserved_tags_are_dropped():
    from optionsjourney.bots.definition import normalize_tags

    assert normalize_tags(["Armed", "off", "LIVE", "paper", "Mine"]) == ("Mine",)


def test_normalize_delta_reads_both_ways_of_writing_it():
    from decimal import Decimal as D

    from optionsjourney.bots.definition import normalize_delta

    for typed, stored in (("16", "0.16"), ("-16", "0.16"), ("0.16", "0.16"), ("5", "0.05"),
                          ("0.05", "0.05"), ("1", "1"), ("100", "1")):  # fmt: skip
        assert normalize_delta(D(typed)) == D(stored), typed
    for typed in ("0", "101"):
        assert normalize_delta(D(typed)) is None
    assert normalize_delta(None) is None


def test_group_kind_is_single_short_only_with_exactly_one_sell():
    assert group_kind(["sell", "buy"]) == "single_short"
    assert group_kind(["sell"]) == "single_short"
    assert group_kind([Side.BUY, Side.SELL, Side.BUY]) == "single_short"  # a 1x2x1 fly
    for sides in (["buy"], ["buy", "buy"], ["sell", "sell"], ["sell", "sell", "buy", "buy"]):
        assert group_kind(sides) == "watched", sides


def test_a_bot_reads_a_groups_kind_from_its_legs():
    bot = iron_condor_skeleton("x")
    assert bot.kind(0) == bot.kind(1) == "single_short"
    assert shape("long_call", bot).kind(0) == "watched"
    # Decision 0048: a group's kind is its sold legs alone; the fill decides the rest
    assert shape("call_debit_spread", bot).kind(0) == "single_short"
    assert shape("long_call_butterfly", bot).kind(0) == "single_short"  # one sold leg (x2)
    assert shape("put_credit_spread", bot).kind(0) == "single_short"
    # A debit shape's groups default to the stop that works for them.
    for key in ("call_debit_spread", "put_debit_spread", "long_call", "long_call_butterfly"):
        assert all(g.stop_basis == "group_entry" for g in SHAPES[key].groups), key
    assert SHAPES["put_credit_spread"].groups[0].stop_basis == "leg_premium"


def test_is_iron_condor_needs_a_put_pair_and_a_call_pair():
    bot = iron_condor_skeleton("x")
    assert is_iron_condor(bot)
    assert not is_iron_condor(shape("short_straddle", bot))
    assert not is_iron_condor(shape("long_call_butterfly", bot))
    assert not is_iron_condor(shape("iron_butterfly", bot))
    doubled = replace(bot, legs=tuple(replace(leg, ratio=2) for leg in bot.legs))
    assert not is_iron_condor(doubled)
    one_group = replace(bot, legs=tuple(replace(leg, group=0) for leg in bot.legs),
                        groups=bot.groups[:1])  # fmt: skip
    assert not is_iron_condor(one_group)
    two_puts = replace(bot, legs=tuple(replace(leg, right="put") for leg in bot.legs))
    assert not is_iron_condor(two_puts)
    assert not is_iron_condor(replace(bot, legs=bot.legs[:3]))


def test_largest_ratio_is_one_with_no_legs():
    bot = iron_condor_skeleton("x")
    assert largest_ratio(bot) == 1 and largest_ratio(replace(bot, legs=(), groups=())) == 1
    assert largest_ratio(shape("long_call_butterfly", bot)) == 2
    wide = replace(bot, legs=(replace(bot.legs[0], ratio=10), *bot.legs[1:]))
    assert largest_ratio(wide) == 10


def test_the_app_watches_only_for_a_watched_group_with_a_stop():
    bot = iron_condor_skeleton("x")
    assert not app_watched(bot)
    # Decision 0060: Schwab follows TastyTrade's rules, so the broker alone says nothing
    assert app_watched(replace(bot, broker="schwab")) == app_watched(bot)
    long_call = shape("long_call", bot)
    assert not app_watched(long_call)  # nothing to watch without a stop
    stopped = replace(long_call, groups=(replace(long_call.groups[0], stop_pct=D("50")),))
    assert app_watched(stopped)
    # A debit shape rests its target: with a stop, even one with no target set, the app
    # watches that stop on any debit fill, so the bot is app-watched.
    spread = shape("call_debit_spread", bot)
    assert not app_watched(spread)
    stop_only = replace(spread, groups=(replace(spread.groups[0], stop_pct=D("50")),))
    assert app_watched(stop_only)
    both = replace(stop_only, groups=(replace(stop_only.groups[0], target_pct=D("50")),))
    assert app_watched(both)
    shorts = lone_shorts()
    resting = replace(shorts, groups=(replace(shorts.groups[0], stop_pct=D("95")),
                                      shorts.groups[1]))  # fmt: skip
    assert not app_watched(resting)
    # decision 0054: the iron condor shape's sides rest their targets
    condor = shape("iron_condor", bot)
    condor = replace(condor, groups=(replace(condor.groups[0], stop_pct=D("95")),
                                     condor.groups[1]))  # fmt: skip
    assert app_watched(condor)


def _filled(bot):
    """The shape with made-up numbers where a target is blank, ready to save."""
    legs = []
    for leg in bot.legs:
        if leg.target is None:
            if leg.pick == "premium":
                target = D("2.00")
            elif leg.pick == "offset":
                target = D("75") if leg.right == "call" else D("-75")
                if any(
                    o.pick == "offset" and o.link == leg.link and o.target == target for o in legs
                ):  # a second wing on one body: the other side
                    target = -target
            else:  # spot
                target = D("50") if leg.right == "call" else D("-50")
            leg = replace(leg, target=target)
        legs.append(leg)
    calendar = any(len({leg.dte for leg in legs if leg.group == g.position}) > 1
                   for g in bot.groups)  # fmt: skip
    return replace(bot, legs=tuple(legs), tag_id=1, account_number="5WX01234",
                   max_per_trade=10, max_per_day=10,
                   close_time="15:45" if calendar else bot.close_time,
                   entry_times=(EntryTimeDef("09:47"),))  # fmt: skip


def test_every_shape_validates_once_its_targets_are_filled():
    assert len(SHAPES) >= 15
    for key in SHAPES:
        bot = _filled(shape(key, iron_condor_skeleton("x")))
        assert validate(bot, {1}) == {}, key


def test_every_shape_keeps_the_bots_settings_and_swaps_the_structure():
    base = replace(iron_condor_skeleton("Mine"), contracts=3, max_per_trade=3, max_per_day=3,
                   close_time="15:45", tags=("a",))  # fmt: skip
    for key, definition in SHAPES.items():
        out = shape(key, base)
        assert (out.name, out.contracts, out.close_time, out.tags) == ("Mine", 3, "15:45", ("a",))
        assert out.legs == definition.legs and out.groups == definition.groups, key
        assert definition.label and definition.label[0].isupper()


def test_only_a_lone_short_shape_rests_its_stop():
    """Decision 0048, and 0054: a group of more than one leg rests its target."""
    lone = ["short_put", "short_call", "blank"]
    credit = ["put_credit_spread", "call_credit_spread", "iron_condor", "iron_butterfly",
              "short_straddle", "short_strangle"]  # fmt: skip
    debit = ["put_debit_spread", "call_debit_spread", "long_put", "long_call",
             "long_call_butterfly", "long_straddle", "condor", "reverse_iron_condor",
             "calendar", "diagonal", "double_calendar", "double_diagonal"]  # fmt: skip
    assert set(SHAPES) == set(lone) | set(credit) | set(debit)
    for key in lone:
        assert {g.broker_exit for g in SHAPES[key].groups} == {"stop"}, key
    for key in credit + debit:
        assert {g.broker_exit for g in SHAPES[key].groups} == {"target"}, key
    assert SHAPES["put_credit_spread"].label == "Put credit spread"
    blank = SHAPES["blank"]
    assert [(leg.side, leg.right) for leg in blank.legs] == [(Side.SELL, "call")]
    assert len(blank.groups) == 1


def test_a_new_group_stops_above_the_shorts_fill_and_rests_its_stop():
    """Final review I3 (the controller's ruling): a new group starts on today's style, %
    above the short's fill. "% of entry price lost" counts a credit spread's wing as
    worthless on its short, so as a default it would fire early or be refused."""
    added = add_group(iron_condor_skeleton("x")).groups[-1]
    assert (added.stop_basis, added.broker_exit) == ("leg_premium", "stop")


def test_a_group_whose_target_rests_has_its_stop_watched_by_the_app():
    bot = lone_shorts()
    groups = (replace(bot.groups[0], stop_pct=D("95"), target_pct=D("50"), broker_exit="target"),
              replace(bot.groups[1], stop_pct=D("95")))  # fmt: skip
    assert [g.name for g in watched_groups(replace(bot, groups=groups))] == ["Call side"]


def test_shape_legs_and_groups_are_consistent():
    for key, definition in SHAPES.items():
        positions = [leg.position for leg in definition.legs]
        assert positions == list(range(len(positions))), key
        assert {leg.group for leg in definition.legs} <= {g.position for g in definition.groups}
        assert len({g.name for g in definition.groups}) == len(definition.groups), key
        assert all(isinstance(leg, LegDef) for leg in definition.legs)
    fly = SHAPES["iron_butterfly"]
    shorts = [leg for leg in fly.legs if leg.side is Side.SELL]
    assert [(leg.pick, leg.target) for leg in shorts] == [("spot", D(0))] * 2
    body = SHAPES["long_call_butterfly"].legs[0]
    assert (body.side, body.pick, body.ratio) == (Side.SELL, "premium", 2)


def test_contracts_for_caps_at_the_limit_over_the_largest_ratio():
    base = iron_condor_skeleton("x")
    legs = (replace(base.legs[0], ratio=2), *base.legs[1:])
    bot = replace(base, legs=legs, contracts=5, max_per_trade=5)
    assert contracts_for(bot, "09:47") == (2, 5)  # 5 // 2
    assert contracts_for(replace(bot, contracts=2), "09:47") == (2, None)
    ratio_one = replace(base, contracts=5, max_per_trade=3)
    assert contracts_for(ratio_one, "09:47") == (3, 5)  # unchanged for ratio 1


def test_every_shape_fits_the_default_limits():
    """Task 3 ruling: picking a shape raises the limits to contracts x its largest ratio,
    keeping any larger value, so the shape saves with the limits it came with."""
    base = iron_condor_skeleton("x")
    fly = shape("long_call_butterfly", base)
    assert (fly.max_per_trade, fly.max_per_day) == (None, None)  # 0058: worked out
    assert (trade_cap(fly), day_cap(fly)) == (2, 2)
    bigger = shape("long_call_butterfly", replace(base, contracts=3, max_per_trade=10,
                                                  max_per_day=1))  # fmt: skip
    assert (bigger.max_per_trade, bigger.max_per_day) == (10, 6)
    for key in SHAPES:
        bot = shape(key, base)
        filled = _filled(bot)
        assert validate(replace(filled, max_per_trade=bot.max_per_trade,
                                max_per_day=bot.max_per_day), {1}) == {}, key  # fmt: skip


def test_removing_a_leg_renumbers_and_fixes_links():
    bot = iron_condor_skeleton("x")  # legs 1 and 3 are offsets from 0 and 2
    out = remove_leg(bot, 0)
    assert [leg.position for leg in out.legs] == [0, 1, 2]
    assert [(leg.side, leg.right) for leg in out.legs] == [
        (Side.BUY, "call"), (Side.SELL, "put"), (Side.BUY, "put"),
    ]  # fmt: skip
    assert out.legs[0].link is None  # it was measured from the removed leg
    assert out.legs[2].link == 1  # it was measured from leg 2, now leg 1
    later = remove_leg(bot, 3)
    assert [leg.link for leg in later.legs] == [None, 0, None]
    assert remove_leg(bot, 9) == bot
    one = shape("long_call", bot)
    assert remove_leg(one, 0) == one  # the last leg stays


def test_removing_a_group_moves_its_legs_to_the_first_group():
    bot = add_group(iron_condor_skeleton("x"))
    bot = replace(bot, legs=(*bot.legs[:3], replace(bot.legs[3], group=2)))
    out = remove_group(bot, 0)
    assert [g.name for g in out.groups] == ["Put side", "Group 3"]
    assert [g.position for g in out.groups] == [0, 1]
    assert [leg.group for leg in out.legs] == [0, 0, 0, 1]  # the call legs joined Put side
    middle = remove_group(bot, 1)
    assert [g.name for g in middle.groups] == ["Call side", "Group 3"]
    assert [leg.group for leg in middle.legs] == [0, 0, 0, 1]
    one = shape("long_call", iron_condor_skeleton("x"))
    assert remove_group(one, 0) == one  # the last group stays
    assert remove_group(bot, 7) == bot


def test_add_stops_at_the_limits():
    bot = shape("long_call", iron_condor_skeleton("x"))
    more = add_leg(bot)
    new = more.legs[-1]
    assert (new.position, new.side, new.right, new.group) == (1, Side.SELL, "call", 0)
    assert new.target is None and new.pick == "premium"
    full = add_leg(add_leg(add_leg(bot)))
    assert len(full.legs) == MAX_LEGS and add_leg(full) == full
    groups = add_group(bot)
    assert [g.name for g in groups.groups] == ["Long call", "Group 2"]
    assert add_leg(groups).legs[-1].group == 1  # a new leg joins the last group
    many = add_group(add_group(add_group(bot)))
    assert len(many.groups) == MAX_GROUPS and add_group(many) == many
    assert [g.position for g in many.groups] == [0, 1, 2, 3]
    renamed = replace(groups, groups=(groups.groups[0], replace(groups.groups[1], name="Group 3")))
    assert add_group(renamed).groups[-1].name == "Group 4"  # never a name already used


def test_watched_groups_are_those_the_app_watches_with_a_stop():
    bot = iron_condor_skeleton("x")
    mixed = replace(
        bot,
        legs=(*bot.legs[:3], replace(bot.legs[3], side=Side.SELL)),  # the put group: 2 sells
        groups=tuple(replace(g, stop_pct=D("50"), stop_basis="group_entry") for g in bot.groups),
    )
    assert [g.name for g in watched_groups(mixed)] == ["Put side"]
    assert watched_groups(bot) == ()  # no stops
    shorts = lone_shorts()
    stopped = replace(shorts, groups=tuple(replace(g, stop_pct=D("95")) for g in shorts.groups))
    assert watched_groups(stopped) == ()


def test_holds_overnight_when_any_leg_expires_after_today():
    """Bots slice 14."""
    from optionsjourney.bots.definition import holds_overnight

    bot = iron_condor_skeleton("IC")
    assert not holds_overnight(bot)
    far = replace(bot.legs[1], dte=7)
    assert holds_overnight(replace(bot, legs=(bot.legs[0], far, *bot.legs[2:])))
    assert bot.use_exact_dte  # Tracker2's default


def test_the_held_shapes_sell_near_and_buy_far():
    """Bots slice 14: Calendar, Diagonal and their doubles, near 7 DTE sold and far 14
    DTE bought; a calendar's far leg on the near leg's strike (offset 0)."""
    cal = SHAPES["calendar"]
    assert [(leg.side, leg.right, leg.dte) for leg in cal.legs] == [
        (Side.SELL, "put", 7), (Side.BUY, "put", 14),
    ]  # fmt: skip
    assert (cal.legs[1].pick, cal.legs[1].target, cal.legs[1].link) == ("offset", D(0), 0)
    assert SHAPES["diagonal"].legs[1].target is None  # the points are yours
    double = SHAPES["double_diagonal"]
    assert [g.name for g in double.groups] == ["Put diagonal", "Call diagonal"]
    assert [leg.group for leg in double.legs] == [0, 0, 1, 1]
    assert [g.name for g in SHAPES["double_calendar"].groups] == ["Put calendar", "Call calendar"]
    for key in ("calendar", "diagonal", "double_calendar", "double_diagonal", "condor",
                "reverse_iron_condor"):  # fmt: skip
        assert {g.broker_exit for g in SHAPES[key].groups} == {"target"}, key
        assert all(g.stop_basis == "group_entry" for g in SHAPES[key].groups), key
    condor = SHAPES["condor"]
    assert [(leg.side, leg.right) for leg in condor.legs] == [
        (Side.BUY, "call"), (Side.SELL, "call"), (Side.SELL, "call"), (Side.BUY, "call"),
    ]  # fmt: skip


def test_retag_adds_and_removes_with_the_bots_own_spelling_winning():
    from optionsjourney.bots.definition import retag

    assert retag(("Beta", "Wings"), add=("beta", "New"), remove=()) == ("Beta", "New", "Wings")
    assert retag(("Beta", "Wings"), add=(), remove=("WINGS",)) == ("Beta",)
    assert retag(("Beta",), add=("Live", " spaced   out "), remove=()) == ("Beta", "spaced out")


def test_retag_cuts_a_long_removal_as_it_cuts_a_tag():
    from optionsjourney.bots.definition import TAG_MAX_LEN, retag

    long = "x" * (TAG_MAX_LEN + 10)
    kept = retag((), add=(long,), remove=())
    assert retag(kept, add=(), remove=(long,)) == ()


def test_removing_a_group_renumbers_or_drops_a_pairing():
    """Bots slice 21: a pairing follows its group when the groups move up, and goes when
    the group it named is removed."""
    from dataclasses import replace as swap

    from optionsjourney.bots import definition

    bot = definition.add_group(definition.iron_condor_skeleton("IC"))  # groups 0, 1, 2
    groups = (swap(bot.groups[0], close_on_stop=2), swap(bot.groups[1], close_on_stop=0),
              bot.groups[2])  # fmt: skip
    bot = swap(bot, groups=groups)
    left = definition.remove_group(bot, 1)
    assert [g.close_on_stop for g in left.groups] == [1, None]
    gone = definition.remove_group(bot, 2)
    assert [g.close_on_stop for g in gone.groups] == [None, 0]


def test_a_linked_leg_goes_below_its_leg_on_the_other_side():
    """#115 (Tracker2's addChildLeg): the other side, same type, ratio, DTE and group,
    an exact offset 5 points out (puts below, calls above), measured from it."""
    bot = shape("short_put", iron_condor_skeleton("x"))
    bot = replace(bot, legs=(replace(bot.legs[0], ratio=2, dte=1),))
    out = add_linked_leg(bot, 0)
    child = out.legs[1]
    assert (child.position, child.side, child.right, child.ratio, child.dte) == (
        1, Side.BUY, "put", 2, 1)
    assert (child.pick, child.target, child.link, child.exact) == ("offset", D(-5), 0, True)
    assert child.group == bot.legs[0].group
    call = add_linked_leg(shape("long_call", bot), 0).legs[1]
    assert (call.side, call.target) == (Side.SELL, D(5))


def test_a_linked_leg_moves_the_later_legs_and_their_links_down():
    bot = iron_condor_skeleton("x")  # legs 1 and 3 are offsets from 0 and 2
    bot = replace(bot, legs=bot.legs[:3])  # room for one more
    out = add_linked_leg(bot, 0)
    assert [leg.position for leg in out.legs] == [0, 1, 2, 3]
    assert [leg.link for leg in out.legs] == [None, 0, 0, None]
    assert out.legs[2] == replace(bot.legs[1], position=2)


def test_a_linked_leg_is_never_added_to_an_offset_or_past_the_limit():
    bot = iron_condor_skeleton("x")
    assert add_linked_leg(bot, 0) == bot  # four legs already
    three = replace(bot, legs=bot.legs[:3])
    assert add_linked_leg(three, 1) == three  # leg 2 is itself an offset
    assert add_linked_leg(three, 9) == three


def test_one_group_puts_every_leg_in_the_first_group_with_its_exits():
    """#116 (Tracker2's Use leg groups off): one stop and target for the whole trade."""
    bot = add_group(iron_condor_skeleton("x"))
    bot = replace(bot, groups=(replace(bot.groups[0], stop_pct=D(95), close_on_stop=1),
                               *bot.groups[1:]))  # fmt: skip
    out = one_group(bot)
    assert len(out.groups) == 1 and out.groups[0].name == "Whole trade"
    assert out.groups[0].stop_value == D(95) and out.groups[0].close_on_stop is None
    assert {leg.group for leg in out.legs} == {0}
    single = shape("long_call", bot)
    assert one_group(single) == single


def test_only_a_group_of_one_sold_leg_rests_its_stop():
    """Decision 0054: a group of more than one leg never rests its stop at the broker."""
    assert rests_stop(["sell"]) and rests_stop([Side.SELL])
    for sides in (["sell", "buy"], ["buy"], ["buy", "sell", "buy"], ["sell", "sell"]):
        assert not rests_stop(sides), sides
    bot = iron_condor_skeleton("x")
    assert not bot.rests_stop(0) and not bot.rests_stop(1)
    assert shape("short_put", bot).rests_stop(0)


def test_shapes_rest_a_target_on_every_group_of_more_than_one_leg():
    bot = iron_condor_skeleton("x")
    assert [g.broker_exit for g in shape("iron_condor", bot).groups] == ["target", "target"]
    assert shape("put_credit_spread", bot).groups[0].broker_exit == "target"
    assert shape("short_put", bot).groups[0].broker_exit == "stop"
    # how its stop is measured doesn't change: on the short's fill
    assert shape("put_credit_spread", bot).groups[0].stop_basis == "leg_premium"


def test_a_bot_coming_in_rests_the_target_of_any_group_of_more_than_one_leg():
    """Decision 0054, on the form and from a bot file: corrected, not refused."""
    condor = iron_condor_skeleton("x")  # both sides set to Stop, as bots before 0054
    assert {g.broker_exit for g in lone_shorts_rest_stops(condor).groups} == {"target"}
    alone = shape("short_put", condor)
    assert lone_shorts_rest_stops(alone).groups[0].broker_exit == "stop"


def test_a_leg_moves_to_the_group_above_or_below():
    """#120 (OptionOmega's arrows): never past the first or the last group."""
    bot = iron_condor_skeleton("x")  # legs 0, 1 in group 0; 2, 3 in group 1
    down = move_leg(bot, 1, +1)
    assert [leg.group for leg in down.legs] == [0, 1, 1, 1]
    assert move_leg(down, 1, -1) == bot
    assert move_leg(bot, 0, -1) == bot and move_leg(bot, 3, +1) == bot
    emptied = move_leg(move_leg(bot, 0, +1), 1, +1)
    assert [leg.group for leg in emptied.legs] == [1, 1, 1, 1] and len(emptied.groups) == 2


# Decision 0058 (#139): blank contract limits are worked out from the bot


def _six_entries(**changes):
    times = tuple(EntryTimeDef(t) for t in ("09:47", "12:23", "13:03", "13:43", "14:23", "15:03"))
    return replace(iron_condor_skeleton("IC"), entry_times=times, **changes)


def test_blank_limits_are_worked_out_from_the_size_and_entry_times():
    bot = _six_entries()
    assert (bot.max_per_trade, bot.max_per_day) == (None, None)
    assert (trade_cap(bot), day_cap(bot)) == (1, 6)
    bot = replace(bot, contracts=3)
    assert (trade_cap(bot), day_cap(bot)) == (3, 18)


def test_an_entry_times_own_size_and_off_times_count_as_they_trade():
    times = (EntryTimeDef("09:47", quantity=4), EntryTimeDef("12:00"),
             EntryTimeDef("13:00", enabled=False, quantity=9))  # fmt: skip
    bot = replace(iron_condor_skeleton("IC"), contracts=2, entry_times=times)
    assert (trade_cap(bot), day_cap(bot)) == (4, 6)


def test_most_entries_a_day_keeps_the_biggest_entries():
    times = (EntryTimeDef("09:47", quantity=4), EntryTimeDef("12:00"), EntryTimeDef("13:00"))
    bot = replace(iron_condor_skeleton("IC"), entry_times=times, max_entries_per_day=2)
    assert day_cap(bot) == 5


def test_ratios_count_in_the_largest_leg():
    bot = _six_entries(contracts=2)
    bot = replace(bot, legs=tuple(replace(leg, ratio=2) if leg.position == 0 else leg
                                  for leg in bot.legs))  # fmt: skip
    assert (trade_cap(bot), day_cap(bot)) == (4, 24)


def test_a_floating_window_enters_once_a_day():
    bot = _six_entries(use_floating_entry=True, floating_start="10:00", floating_end="11:00")
    assert (trade_cap(bot), day_cap(bot)) == (1, 1)


def test_buying_power_sizing_is_capped_by_the_contract_ceiling():
    bot = _six_entries(sizing="dollars", max_per_entry_usd=D("2000"))
    assert (trade_cap(bot), day_cap(bot)) == (MAX_CONTRACTS, 6 * MAX_CONTRACTS)


def test_a_bot_with_no_entry_times_on_can_still_make_one_entry():
    bot = replace(iron_condor_skeleton("IC"), entry_times=(EntryTimeDef("09:47", enabled=False),))
    assert (trade_cap(bot), day_cap(bot)) == (1, 1)


def test_limits_you_set_are_used_as_they_are():
    bot = _six_entries(max_per_trade=2, max_per_day=3)
    assert (trade_cap(bot), day_cap(bot)) == (2, 3)
    bot = _six_entries(max_per_trade=2)
    assert (trade_cap(bot), day_cap(bot)) == (2, 6)


def test_entries_you_start_yourself_are_capped_by_the_ceiling_only_when_blank():
    """Trade now, the Trade page and taking over a trade: blank limits don't hold them
    to the bot's own size, and there's no worked-out daily cap for them."""
    bot = _six_entries()
    assert limits(bot, by_hand=False) == (1, 6)
    assert limits(bot, by_hand=True) == (MAX_CONTRACTS, None)
    bot = _six_entries(max_per_trade=2, max_per_day=3)
    assert limits(bot, by_hand=True) == (2, 3)


def test_contracts_for_uses_the_worked_out_limit():
    bot = replace(_six_entries(contracts=2), entry_times=(EntryTimeDef("09:47", quantity=5),))
    assert contracts_for(bot, "09:47") == (5, None)
    assert contracts_for(replace(bot, max_per_trade=3), "09:47") == (3, 5)


def test_a_shape_keeps_blank_limits_blank():
    bot = shape(next(iter(SHAPES)), _six_entries())
    assert (bot.max_per_trade, bot.max_per_day) == (None, None)


def per_leg(bot):
    """#165: a Tracker2 MEIC-style condor, each leg in its own group."""
    return replace(bot, legs=tuple(replace(leg, group=leg.position) for leg in bot.legs),
                   groups=tuple(GroupDef(p, f"Group {p + 1}") for p in range(4)))  # fmt: skip


def test_an_iron_condor_shape_ignores_how_its_legs_are_grouped():
    from optionsjourney.bots.definition import is_iron_condor_shape

    bot = iron_condor_skeleton("x")
    assert is_iron_condor_shape(bot) and is_iron_condor_shape(per_leg(bot))
    assert not is_iron_condor(per_leg(bot))  # the two-group structure is still its own test
    # a group that mixes puts and calls can't be split into a put side and a call side
    mixed = replace(per_leg(bot), legs=tuple(replace(leg, group=0 if leg.side == Side.SELL
                                                     else leg.position) for leg in bot.legs),
                    groups=tuple(GroupDef(p, "") for p in (0, 1, 3)))  # fmt: skip
    assert not is_iron_condor_shape(mixed)
    assert not is_iron_condor_shape(replace(bot, legs=bot.legs[:3]))


def test_a_condor_with_uneven_sides_splits_but_not_one_across_expirations():
    """#166: Tracker2 splits any sold+bought put and sold+bought call at one expiration."""
    from optionsjourney.bots.definition import is_iron_condor_shape

    bot = iron_condor_skeleton("x")
    ern = replace(bot, legs=tuple(replace(leg, ratio=3 if leg.right == "call" else 1)
                                  for leg in bot.legs))  # fmt: skip
    assert is_iron_condor_shape(ern) and is_iron_condor_shape(per_leg(ern))
    assert not is_iron_condor_shape(shape("iron_butterfly", bot))  # one group mixes both
    lopsided = replace(bot, legs=tuple(replace(leg, ratio=2 if leg.position == 0 else 1)
                                       for leg in bot.legs))  # fmt: skip
    assert is_iron_condor_shape(lopsided)
    calendar = replace(bot, legs=tuple(replace(leg, dte=7 if leg.right == "call" else 0)
                                       for leg in bot.legs))  # fmt: skip
    assert not is_iron_condor_shape(calendar)
    assert not is_iron_condor_shape(replace(bot, legs=bot.legs[:3]))
