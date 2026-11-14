"""#328: whether a bot's previous filled trade hit a stop (services/bot_loss_boost.py)."""

from dataclasses import replace
from datetime import date, timedelta

from optionsjourney.bots.loss_boost import Boost
from optionsjourney.db.models import BotExit, BotRun, PaperFill
from optionsjourney.services import bot_loss_boost, bot_store
from tests.services.bot_fakes import OPEN_TIME, make

D_TODAY = date(2026, 10, 13)  # OPEN_TIME's New York day
YESTERDAY = D_TODAY - timedelta(days=1)


def boosted(factory, bot_id, **changes):
    """The bot as saved with the boost on (and `changes`)."""
    bot = bot_store.load(factory, bot_id)
    bot = replace(bot, use_loss_boost=True, **changes)
    _, problems = bot_store.save(factory, bot, OPEN_TIME)
    assert problems == {}
    return bot_store.load(factory, bot_id)


def past_run(factory, bot_id, *, day=YESTERDAY, paper=False, status="filled", key=None,
             parent=None, exits=(), fills=()) -> int:  # fmt: skip
    """A run of the bot on `day`, with exits [(kind, state, by)] or paper fills
    [(kind, reason)]."""
    with factory() as session:
        run = BotRun(
            bot_id=bot_id, key=key or f"bot-{bot_id}-0947-{day:%Y%m%d}-{parent or 0}",
            trading_date=day, entry_time="09:47", status=status, live=not paper, paper=paper,
            account_number="5WX01234", legs=[], filled_legs=[], chase_log=[], contracts=1,
            parent_run_id=parent, created_at=OPEN_TIME, updated_at=OPEN_TIME,
        )  # fmt: skip
        session.add(run)
        session.flush()
        for kind, state, by in exits:
            session.add(BotExit(
                run_id=run.id, group_position=0, kind=kind, state=state, short_symbol="",
                long_symbol="", contracts=1, log=[{"by": by}] if by else [],
                created_at=OPEN_TIME, updated_at=OPEN_TIME,
            ))  # fmt: skip
        for kind, reason in fills:
            session.add(PaperFill(run_id=run.id, kind=kind, group=0, price=0, contracts=1,
                                  reason=reason, legs=[], at=OPEN_TIME))  # fmt: skip
        session.commit()
        return run.id


def boost(factory, bot):
    return bot_loss_boost.boost_for(factory, bot, D_TODAY)


STOPPED = (("stop", "filled", None),)


def test_a_stop_on_an_earlier_day_boosts_the_next_entry(factory):
    bot = boosted(factory, make(factory, live=True), loss_boost_multiplier=3)
    run_id = past_run(factory, bot.id, exits=STOPPED)
    assert boost(factory, bot) == Boost(3, run_id, YESTERDAY)


def test_no_boost_while_switched_off(factory):
    bot = bot_store.load(factory, make(factory, live=True))
    past_run(factory, bot.id, exits=STOPPED)
    assert boost(factory, bot) is None


def test_a_stop_today_doesnt_boost_todays_entries(factory):
    bot = boosted(factory, make(factory, live=True))
    past_run(factory, bot.id, day=D_TODAY, exits=STOPPED)
    assert boost(factory, bot) is None


def test_a_target_doesnt_boost(factory):
    bot = boosted(factory, make(factory, live=True))
    past_run(factory, bot.id, exits=(("stop", "cancelled", None), ("target", "filled", None)))
    assert boost(factory, bot) is None


def test_only_the_most_recent_filled_trade_counts(factory):
    bot = boosted(factory, make(factory, live=True))
    past_run(factory, bot.id, day=YESTERDAY - timedelta(days=1), exits=STOPPED)
    past_run(factory, bot.id, exits=(("target", "filled", None),))
    assert boost(factory, bot) is None


def test_skipped_and_error_runs_are_passed_over(factory):
    bot = boosted(factory, make(factory, live=True))
    stopped = past_run(factory, bot.id, day=YESTERDAY - timedelta(days=1), exits=STOPPED)
    past_run(factory, bot.id, status="skipped", key="skipped")
    past_run(factory, bot.id, status="error", key="error")
    assert boost(factory, bot).after_run == stopped


def test_a_stop_on_a_tranche_counts(factory):
    bot = boosted(factory, make(factory, live=True))
    root = past_run(factory, bot.id, exits=(("target", "filled", None),))
    past_run(factory, bot.id, parent=root, key="tranche", exits=STOPPED)
    assert boost(factory, bot).after_run == root


def test_a_watched_stops_close_counts(factory):
    bot = boosted(factory, make(factory, live=True))
    past_run(factory, bot.id, exits=(("stop", "watching", None), ("close", "filled", "stop")))
    assert boost(factory, bot) is not None


def test_a_close_you_asked_for_doesnt_count(factory):
    bot = boosted(factory, make(factory, live=True))
    past_run(factory, bot.id, exits=(("close", "filled", None),))
    assert boost(factory, bot) is None


def test_a_trade_page_entry_is_passed_over(factory):
    bot = boosted(factory, make(factory, live=True))
    past_run(factory, bot.id, key=f"ticket-{bot.id}-abc", exits=STOPPED)
    assert boost(factory, bot) is None


def test_a_taken_over_trade_counts(factory):
    bot = boosted(factory, make(factory, live=True))
    past_run(factory, bot.id, key=f"adopt-{bot.id}-123", exits=STOPPED)
    assert boost(factory, bot) is not None


def test_a_paper_bot_looks_at_its_paper_trades(factory):
    bot = boosted(factory, make(factory))  # Paper
    past_run(factory, bot.id, paper=True, fills=(("entry", "entry"), ("exit", "stop (last price)")))
    assert boost(factory, bot) is not None


def test_a_paper_target_doesnt_boost(factory):
    bot = boosted(factory, make(factory))
    past_run(factory, bot.id, paper=True, fills=(("entry", "entry"), ("exit", "target")))
    assert boost(factory, bot) is None


def test_a_live_bot_doesnt_count_a_paper_stop(factory):
    bot = boosted(factory, make(factory, live=True))
    past_run(factory, bot.id, paper=True, fills=(("exit", "stop"),))
    assert boost(factory, bot) is None


def test_a_paper_bot_doesnt_count_a_live_stop(factory):
    bot = boosted(factory, make(factory))
    past_run(factory, bot.id, exits=STOPPED)
    assert boost(factory, bot) is None
