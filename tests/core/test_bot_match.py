"""Scoring a past trade against a bot (#164; Tracker2's strategy_matcher._score_match)."""

from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from itertools import count

from optionsjourney.core.bot_match import Pattern, position_shape, score, shape_of
from optionsjourney.core.orders import group_fills
from optionsjourney.core.positions import build_positions
from optionsjourney.core.symbols import occ_symbol
from optionsjourney.core.transactions import Effect, Fill, InstrumentType, Kind, OptionDetails, Side

AT = datetime(2026, 10, 12, 14, 31, tzinfo=UTC)  # Monday 10:31 New York
EXP = date(2026, 10, 12)
_ids = count(1)
S, B = Side.SELL, Side.BUY
PCS = shape_of([("sell", "put", 1), ("buy", "put", 1)])


def fill(side, right, strike, *, qty=1, order="1", at=AT, account="5WX01234", root="SPXW"):
    option = OptionDetails(right, Decimal(strike), EXP)
    return Fill(
        broker="tastytrade", account_number=account, transaction_id=str(next(_ids)), leg=0,
        executed_at=at, kind=Kind.TRADE, instrument_type=InstrumentType.OPTION,
        symbol=occ_symbol(root, option), underlying=root.rstrip("W"), option=option, side=side,
        effect=Effect.OPEN, quantity=Decimal(qty), price=Decimal("1"), value=Decimal("100"),
        fees=Decimal("1"), order_id=order,
    )  # fmt: skip


def spread(qty=1, **kw):
    [p] = build_positions(
        group_fills([fill(S, "PUT", 5800, qty=qty, **kw), fill(B, "PUT", 5790, qty=qty, **kw)])
    ).positions
    return p


BOT = Pattern("tastytrade", "5WX01234", "SPX", PCS, entry_times=("10:30",))


def test_shape_ignores_size_and_strikes():
    assert position_shape(spread(qty=3)) == PCS
    assert shape_of([("SELL", "PUT", 2), ("buy", "put", 1)]) != PCS


def test_a_strong_match_has_every_reason():
    match = score(replace(BOT, days=frozenset({1})), spread())
    assert match.strong and match.score == 1 + 2 + 3 + 1
    assert match.reason == "same legs · opened 10:31 · Monday"


def test_hard_filters():
    assert score(BOT, spread(account="other")) is None
    assert score(replace(BOT, underlying="QQQ"), spread()) is None
    assert score(replace(BOT, entry_times=("10:28",)), spread()) is None  # 3 minutes off
    assert score(replace(BOT, entry_times=("10:29",)), spread()) is not None  # within 2
    assert score(replace(BOT, days=frozenset({2})), spread()) is None
    assert score(replace(BOT, dates=frozenset({EXP})), spread()) is not None
    other = shape_of([("sell", "call", 1), ("buy", "call", 1)])
    assert score(replace(BOT, shape=other), spread()) is None


def test_without_entry_times_it_is_only_a_guess():
    match = score(replace(BOT, entry_times=()), spread())
    assert not match.strong and match.score == 3 and match.reason == "same legs"


def test_a_split_half_matches_the_bots_side_only():
    condor = shape_of([("sell", "put", 1), ("buy", "put", 1), ("sell", "call", 1),
                       ("buy", "call", 1)])  # fmt: skip
    bot = replace(BOT, shape=condor, group_shapes=(PCS,))
    half = spread()
    assert score(bot, half) is None  # a spread opened on its own
    half.split_from = "tastytrade:5WX01234:1"
    assert score(bot, half).strong


# --- #169: Match all bots gives each trade to its best-scoring bot -----------------


def test_best_is_the_top_score_and_its_ties():
    from optionsjourney.core.bot_match import Match, best

    weak, strong = Match(3, False, ("same legs",)), Match(6, True, ("same legs", "opened 10:31"))
    assert best([]) == []
    assert best([("a", weak), ("b", strong)]) == ["b"]
    assert best([("a", strong), ("b", weak), ("c", strong)]) == ["a", "c"]  # a tie
