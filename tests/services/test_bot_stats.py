from dataclasses import replace
from decimal import Decimal

from sqlalchemy import select

from optionsjourney.db.models import Tag, TradeTag
from optionsjourney.services import bot_stats
from optionsjourney.services.sync import sync_broker
from tests.services.bot_fakes import make
from tests.services.test_journal import CALL_KEY, NOW, PUT_KEY, FakeBroker
from tests.services.test_journal import session_factory as session_factory  # noqa: F401


def file_under(factory, key, tag):
    """Tag the trade (decision 0072: a bot's record follows its tag)."""
    with factory() as session:
        tag_id = session.scalar(select(Tag.id).where(Tag.name == tag))
        session.add(TradeTag(position_key=key, tag_id=tag_id))
        session.commit()


def test_stats_cover_the_tag_on_the_bots_account(session_factory):  # noqa: F811
    factory = session_factory
    file_under(factory, PUT_KEY, "0DTE")
    file_under(factory, CALL_KEY, "0DTE")
    bot_id = make(factory, live=True)  # files under 0DTE on 5WX01234
    from optionsjourney.services import bot_store

    bot = bot_store.load(factory, bot_id)
    other = replace(bot, id=99, account_number="5WX09999")
    stats = bot_stats.summaries(factory, [bot, other])
    mine = stats[bot_id].summary
    assert mine.trades == 2 and mine.winners == 1 and stats[bot_id].closed == 1
    assert mine.closed_pl == Decimal("100") and mine.win_rate == 100
    assert stats[99].summary.trades == 0 and stats[99].summary.win_rate is None
    # #208: the P/L graph is the same trades, closed ones only, from $0
    closed_at = bot_stats.record(factory, bot).closed[0].position.closed_at
    assert [(p.at, p.pl, p.cumulative) for p in stats[bot_id].curve] == [
        (None, None, Decimal("0")), (closed_at, Decimal("100"), Decimal("100")),
    ]  # fmt: skip
    assert stats[99].curve == []
    record = bot_stats.record(factory, bot)
    assert [e.position.key for e in record.closed] == [PUT_KEY]
    assert [e.position.key for e in record.open] == [CALL_KEY]
    assert record.summary == mine


def test_a_trade_filed_elsewhere_is_not_in_the_record(session_factory):  # noqa: F811
    factory = session_factory
    sync_broker(FakeBroker(), factory)
    from optionsjourney.services import bot_store

    bot = bot_store.load(factory, make(factory))
    assert bot_stats.record(factory, bot).summary.trades == 0


def test_a_bots_record_is_its_linked_trades_and_unlinked_ones_with_its_tag(
    session_factory,  # noqa: F811
):
    """#169, decision 0071: bots sharing a tag don't share each other's trades."""
    from optionsjourney.services import bot_match, bot_store

    factory = session_factory
    file_under(factory, PUT_KEY, "0DTE")
    file_under(factory, CALL_KEY, "0DTE")
    bot = bot_store.load(factory, make(factory, live=True))
    twin = bot_store.load(factory, make(factory, live=True))  # same tag and account
    bot_match.file(factory, bot, [PUT_KEY])

    def keys(b):
        record = bot_stats.record(factory, b)
        return sorted(e.position.key for e in record.open + record.closed)

    assert keys(bot) == sorted([PUT_KEY, CALL_KEY])  # its own, and the unlinked one
    assert keys(twin) == [CALL_KEY]  # not the trade linked to the other bot
    stats = bot_stats.summaries(factory, [bot, twin])
    assert stats[bot.id].summary.trades == 2 and stats[twin.id].summary.trades == 1


def test_an_unlinked_trade_counts_for_each_bot_whose_tag_it_has(session_factory):  # noqa: F811
    """Decision 0072: a trade can carry several tags, so it can be in several bots' records
    while no bot is linked to it; other tags don't matter."""
    from optionsjourney.services import bot_store, journal

    factory = session_factory
    sync_broker(FakeBroker(), factory)
    journal.edit_many(factory, [PUT_KEY], add_tags="0DTE, Wheel, earnings")
    zero = bot_store.load(factory, make(factory, live=True))
    with factory() as session:
        wheel = session.scalar(select(Tag.id).where(Tag.name == "Wheel"))
    other = replace(zero, id=None, name="W", tag_id=wheel)
    wheel_id, problems = bot_store.save(factory, other, NOW)
    assert not problems
    wheel_bot = bot_store.load(factory, wheel_id)
    stats = bot_stats.summaries(factory, [zero, wheel_bot])
    assert stats[zero.id].summary.trades == 1 and stats[wheel_id].summary.trades == 1
