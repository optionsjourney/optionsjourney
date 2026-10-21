from dataclasses import replace

from optionsjourney.services import bot_match, bot_stats, bot_store, journal
from tests.services.bot_fakes import make_shape
from tests.services.test_journal import CALL_KEY, PUT_KEY
from tests.services.test_journal import session_factory as session_factory  # noqa: F401


def short_put_bot(factory):
    bot = bot_store.load(factory, make_shape(factory, "short_put"))
    return replace(bot, entry_times=())  # the test trades' times are now-based


def keys(candidates):
    return [c.entry.position.key for c in candidates]


def test_it_offers_trades_of_the_bots_shape(session_factory):  # noqa: F811
    bot = short_put_bot(session_factory)
    found = bot_match.find(session_factory, bot)
    assert keys(found.offered) == [PUT_KEY] and found.filed_elsewhere == []  # not the call
    [c] = found.offered
    assert not c.match.strong and c.match.reason == "same legs"
    # with its entry time (09:47), the trade opened at another time isn't offered
    assert bot_match.find(session_factory, bot_store.load(session_factory, bot.id)).offered == []


def test_filing_puts_it_in_the_record_and_undo_puts_it_back(session_factory):  # noqa: F811
    factory = session_factory
    bot = short_put_bot(factory)
    journal.save_trade(factory, PUT_KEY, name="", tags="mine", notes="",
                       close_reason="")  # fmt: skip
    batch = bot_match.file(factory, bot, [PUT_KEY])
    assert [e.position.key for e in bot_stats.record(factory, bot).closed] == [PUT_KEY]
    assert bot_match.find(factory, bot).offered == []  # already in its record
    entry = journal.find_entry(journal.load_journal(factory), PUT_KEY)
    assert sorted(t.name for t in entry.tags) == ["0DTE", "mine"]
    assert bot_match.undo(factory, batch) == 1
    entry = journal.find_entry(journal.load_journal(factory), PUT_KEY)
    assert [t.name for t in entry.tags] == ["mine"]


def test_a_filed_trade_takes_the_bots_name_unless_you_named_it(session_factory):  # noqa: F811
    factory = session_factory
    bot = short_put_bot(factory)
    batch = bot_match.file(factory, bot, [PUT_KEY])
    entry = journal.find_entry(journal.load_journal(factory), PUT_KEY)
    assert entry.name == bot.name
    bot_match.undo(factory, batch)
    entry = journal.find_entry(journal.load_journal(factory), PUT_KEY)
    assert entry.name == entry.position.name and not entry.renamed
    journal.save_trade(factory, PUT_KEY, name="My put", tags="", notes="",
                       close_reason="")  # fmt: skip
    bot_match.undo(factory, bot_match.file(factory, bot, [PUT_KEY]))
    assert journal.find_entry(journal.load_journal(factory), PUT_KEY).name == "My put"


def test_a_trade_with_another_bots_tag_is_listed_apart(session_factory):  # noqa: F811
    """Decision 0072: a trade carrying the tag another bot files under (and not this
    bot's) is that bot's; any other tag doesn't matter."""
    factory = session_factory
    bot = short_put_bot(factory)
    journal.edit_many(factory, [PUT_KEY], add_tags="earnings")
    assert keys(bot_match.find(factory, bot).offered) == [PUT_KEY]
    other = _other_bot(factory)
    from optionsjourney.db.models import Bot

    with factory() as session:
        session.get(Bot, other.id).tag_id = journal.tag_for(factory, "Other").id
        session.commit()
    journal.edit_many(factory, [PUT_KEY], add_tags="Other")
    found = bot_match.find(factory, bot)
    assert found.offered == [] and keys(found.filed_elsewhere) == [PUT_KEY]
    assert CALL_KEY not in keys(found.filed_elsewhere)
    journal.edit_many(factory, [PUT_KEY], add_tag_ids=[bot.tag_id])  # its own tag too
    assert keys(bot_match.find(factory, bot).offered) == [PUT_KEY]


def test_it_says_which_other_bots_it_matches(session_factory, monkeypatch):  # noqa: F811
    bot = short_put_bot(session_factory)
    twin = replace(bot, id=999, name="Twin")
    monkeypatch.setattr(bot_store, "listing", lambda factory: [bot, twin])
    [c] = bot_match.find(session_factory, bot).offered
    assert c.also == ("Twin",)


# --- #166: a ticked whole condor is split when the bot splits its condors ----------


def _condor_factory(tmp_path):
    from datetime import timedelta

    from optionsjourney.db import make_engine, make_session_factory, migrate
    from optionsjourney.services.sync import sync_broker
    from tests.services import test_journal as tj

    at = tj.NOW - timedelta(days=1)
    rows = [
        tj._tasty(21, "Buy to Open", "1.00", "Debit", at, 30, "SPXW  261120P05780000"),
        tj._tasty(22, "Sell to Open", "2.00", "Credit", at, 30, "SPXW  261120P05800000"),
        tj._tasty(23, "Sell to Open", "1.50", "Credit", at, 30, "SPXW  261120C06000000"),
        tj._tasty(24, "Buy to Open", "0.50", "Debit", at, 30, "SPXW  261120C06020000"),
    ]
    for row in rows[2:]:  # an ERN-style condor: the call side x3
        row["quantity"], row["value"] = "3", str(float(row["value"]) * 3)

    class Broker(tj.FakeBroker):
        def transactions(self, number, start, end):
            return [tj.RawTransaction(str(r["id"]), journal.datetime.fromisoformat(
                r["executed-at"]), "Trade", r) for r in rows]  # fmt: skip

    engine = make_engine(tmp_path / "ic.db")
    migrate(engine)
    factory = make_session_factory(engine)
    sync_broker(Broker(), factory)
    return factory


CONDOR = "tastytrade:5WX01234:30"


def test_filing_a_whole_condor_splits_it_and_undo_combines_it(tmp_path):
    factory = _condor_factory(tmp_path)
    bot = bot_store.load(factory, make_shape(factory, "iron_condor"))
    bot = replace(bot, entry_times=(), split_iron_condors=True,
                  legs=tuple(replace(leg, ratio=3 if leg.right == "call" else 1)
                             for leg in bot.legs))  # fmt: skip
    journal.save_trade(factory, CONDOR, name="", tags="mine", notes="",
                       close_reason="")  # fmt: skip
    [c] = bot_match.find(factory, bot).offered
    assert c.entry.position.key == CONDOR and c.splits
    batch = bot_match.file(factory, bot, [CONDOR])
    view = journal.load_journal(factory)
    halves = [journal.find_entry(view, f"{CONDOR}#{side}") for side in ("put", "call")]
    assert all(h is not None and h.bot and h.bot.bot_id == bot.id for h in halves)
    assert all("0DTE" in [t.name for t in h.tags] for h in halves)
    assert journal.find_entry(view, CONDOR) is None
    bot_match.undo(factory, batch)
    entry = journal.find_entry(journal.load_journal(factory), CONDOR)
    assert entry is not None and [t.name for t in entry.tags] == ["mine"]
    # without the switch, it's filed whole
    off = replace(bot, split_iron_condors=False)
    assert not bot_match.find(factory, off).offered[0].splits
    bot_match.file(factory, off, [CONDOR])
    entry = journal.find_entry(journal.load_journal(factory), CONDOR)
    assert bot.tag_id in {t.id for t in entry.tags}


# --- #169: a matched trade is linked to the bot, not only given its tag --------------


def _other_bot(factory, name="Other bot"):
    from optionsjourney.db.models import Bot

    bot_id = make_shape(factory, "short_put")
    with factory() as session:
        session.get(Bot, bot_id).name = name
        session.commit()
    return replace(bot_store.load(factory, bot_id), entry_times=())


def test_matching_links_the_trade_to_the_bot_and_undo_unlinks_it(session_factory):  # noqa: F811
    factory = session_factory
    bot = short_put_bot(factory)
    batch = bot_match.file(factory, bot, [PUT_KEY])
    entry = journal.find_entry(journal.load_journal(factory), PUT_KEY)
    assert entry.bot is not None and entry.bot.bot_id == bot.id and entry.bot.matched
    assert entry.bot.run_id is None and entry.bot.name == bot.name
    assert bot_match.find(factory, bot).offered == []  # linked to this bot
    bot_match.undo(factory, batch)
    assert journal.find_entry(journal.load_journal(factory), PUT_KEY).bot is None


def test_a_trade_with_the_bots_tag_without_a_link_is_offered_again(session_factory):  # noqa: F811
    factory = session_factory
    bot = short_put_bot(factory)
    journal.edit_many(factory, [PUT_KEY], add_tag_ids=[bot.tag_id])  # the old #164 way
    found = bot_match.find(factory, bot)
    assert keys(found.offered) + keys(found.filed_elsewhere) == [PUT_KEY]


def test_a_trade_matched_to_another_bot_says_so_and_can_move(session_factory):  # noqa: F811
    factory = session_factory
    bot = short_put_bot(factory)
    other = _other_bot(factory)
    bot_match.file(factory, other, [PUT_KEY])
    [c] = bot_match.find(factory, bot).filed_elsewhere
    assert c.matched_to == "Other bot"
    batch = bot_match.file(factory, bot, [PUT_KEY])
    assert journal.find_entry(journal.load_journal(factory), PUT_KEY).bot.bot_id == bot.id
    bot_match.undo(factory, batch)  # back to the other bot
    assert journal.find_entry(journal.load_journal(factory), PUT_KEY).bot.bot_id == other.id


def test_a_split_condor_is_linked_whole_and_both_halves_show_the_bot(tmp_path):
    factory = _condor_factory(tmp_path)
    bot = bot_store.load(factory, make_shape(factory, "iron_condor"))
    bot = replace(bot, entry_times=(), split_iron_condors=True,
                  legs=tuple(replace(leg, ratio=3 if leg.right == "call" else 1)
                             for leg in bot.legs))  # fmt: skip
    batch = bot_match.file(factory, bot, [CONDOR])
    view = journal.load_journal(factory)
    for side in ("put", "call"):
        half = journal.find_entry(view, f"{CONDOR}#{side}")
        assert half.bot is not None and half.bot.bot_id == bot.id and half.bot.matched
    bot_match.undo(factory, batch)
    assert journal.find_entry(journal.load_journal(factory), CONDOR).bot is None


def test_match_all_gives_each_trade_to_its_best_bot(session_factory, monkeypatch):  # noqa: F811
    factory = session_factory
    bot = short_put_bot(factory)
    timed = replace(bot, id=998, name="Timed")  # the same, with a matching entry time
    opened = journal.find_entry(journal.load_journal(factory), PUT_KEY).position.opened_at
    from optionsjourney.bots.definition import EntryTimeDef as EntryTime
    from optionsjourney.core.market import MARKET_TZ

    at = opened.astimezone(MARKET_TZ).strftime("%H:%M:%S")
    timed = replace(timed, entry_times=(EntryTime(at, True),), live=True)
    monkeypatch.setattr(bot_store, "listing", lambda factory: [replace(bot, live=True), timed])
    found = bot_match.find_all(factory)
    groups = {g.bot.name: g.candidates for g in found.groups}
    assert keys(groups["Timed"]) == [PUT_KEY] and bot.name not in groups
    [c] = groups["Timed"]
    assert c.match.strong and c.ticked and c.also == ()
    twin = replace(timed, id=997, name="Twin")
    monkeypatch.setattr(bot_store, "listing", lambda factory: [timed, twin])
    found = bot_match.find_all(factory)
    [g] = found.groups  # a tie: shown under the first, unticked, naming the other
    [c] = g.candidates
    assert not c.ticked and c.also == ("Twin",)


def test_match_all_skips_paper_hidden_and_linked(session_factory, monkeypatch):  # noqa: F811
    factory = session_factory
    bot = short_put_bot(factory)
    monkeypatch.setattr(bot_store, "listing", lambda factory: [bot])  # Paper
    assert bot_match.find_all(factory).groups == []
    live = replace(bot, live=True)
    monkeypatch.setattr(bot_store, "listing", lambda factory: [live])
    assert len(bot_match.find_all(factory).groups) == 1
    batches = bot_match.file_all(factory, {live.id: [PUT_KEY]}, [live])
    assert bot_match.find_all(factory).groups == []  # linked now
    assert bot_match.undo_all(factory, batches) == 1
    assert len(bot_match.find_all(factory).groups) == 1


def test_unlink_takes_a_matched_trade_off_its_bot_for_good(session_factory):  # noqa: F811
    """#177: the link, the bot's tag and the bot's name go; your own tags stay."""
    factory = session_factory
    bot = short_put_bot(factory)
    journal.save_trade(factory, PUT_KEY, name="", tags="mine", notes="",
                       close_reason="")  # fmt: skip
    bot_match.file(factory, bot, [PUT_KEY])
    assert bot_match.unlink(factory, PUT_KEY) == bot.name
    entry = journal.find_entry(journal.load_journal(factory), PUT_KEY)
    assert entry.bot is None and [t.name for t in entry.tags] == ["mine"]
    assert entry.name == entry.position.name
    assert bot_stats.record(factory, bot).closed == []
    assert bot_match.unlink(factory, PUT_KEY) is None  # nothing left to unlink


def test_unlink_keeps_a_name_you_gave_it(session_factory):  # noqa: F811
    factory = session_factory
    bot = short_put_bot(factory)
    journal.save_trade(factory, PUT_KEY, name="My put", tags="", notes="",
                       close_reason="")  # fmt: skip
    bot_match.file(factory, bot, [PUT_KEY])
    bot_match.unlink(factory, PUT_KEY)
    assert journal.find_entry(journal.load_journal(factory), PUT_KEY).name == "My put"
