"""The pre-open market data check's answers (#327, decision 0130): from what each broker
returned, will today's entries be placed (ok), placed from a backup (degraded), or not
placed at all (blocked)? Pure."""

from dataclasses import replace
from datetime import date

from optionsjourney.bots import market_check
from optionsjourney.bots.definition import EntryTimeDef, iron_condor_skeleton
from optionsjourney.bots.market_check import Listing, Result
from optionsjourney.trading.orders import Side

DAY = date(2026, 11, 16)  # a Monday
DAYS = (DAY, date(2026, 11, 17))
OFF = Listing(connected=False)


def listed(*days, error=None):
    return Listing(connected=True, days=tuple(days), error=error)


# --- expirations -----------------------------------------------------------------------


def test_both_brokers_listing_is_quiet():
    result, days = market_check.expirations("SPX", listed(*DAYS), listed(*DAYS))
    assert result == Result("SPX") and days == DAYS
    assert result.ok


def test_tastytrade_failing_with_schwab_listing_is_degraded_and_uses_schwabs_dates():
    result, days = market_check.expirations(
        "SPX", listed(error="the response couldn't be read"), listed(*DAYS)
    )
    assert days == DAYS and not result.blocked
    assert result.degraded == [
        "TastyTrade couldn't list expirations (the response couldn't be read); entries will "
        "use Schwab's list"
    ]


def test_tastytrade_listing_nothing_counts_as_failing():
    result, _ = market_check.expirations("SPX", listed(), listed(*DAYS))
    assert result.degraded == [
        "TastyTrade couldn't list expirations (none listed); entries will use Schwab's list"
    ]


def test_schwab_failing_leaves_no_backup_today():
    result, days = market_check.expirations("SPX", listed(*DAYS), listed(error="timed out"))
    assert days == DAYS and not result.blocked
    assert result.degraded == [
        "Schwab couldn't list expirations (timed out): there's no backup if TastyTrade fails today"
    ]


def test_neither_broker_listing_blocks_the_entries():
    result, days = market_check.expirations("SPX", listed(error="refused"), listed())
    assert days == () and not result.degraded
    assert result.blocked == [
        "no expirations from TastyTrade (refused) or Schwab (none listed): entries need them"
    ]


def test_a_broker_that_isnt_connected_is_never_a_problem():
    # One broker only (and the demo, whose pretend Schwab lists no expirations)
    assert market_check.expirations("SPX", listed(*DAYS), OFF) == (Result("SPX"), DAYS)
    assert market_check.expirations("SPX", OFF, listed(*DAYS)) == (Result("SPX"), DAYS)


def test_no_broker_connected_blocks_the_entries():
    result, _ = market_check.expirations("SPX", OFF, OFF)
    assert result.blocked == ["no broker is connected to list expirations"]


def test_a_long_error_is_made_one_short_line():
    result, _ = market_check.expirations("SPX", listed(error="bad\n  thing " * 50), listed(*DAYS))
    (said,) = result.degraded
    assert "\n" not in said and len(said) < 260


# --- the chain -------------------------------------------------------------------------


def test_nearest_listed_expiration_today_or_later():
    assert market_check.nearest((date(2026, 11, 13), *DAYS), DAY) == DAY
    assert market_check.nearest((date(2026, 11, 13),), DAY) is None


def test_a_chain_from_the_first_broker_with_deltas_is_quiet():
    result = Result("SPX")
    market_check.chain(result, DAY, found=True, backup=False, deltas=True)
    assert result.ok


def test_no_chain_blocks():
    result = Result("SPX")
    market_check.chain(result, DAY, found=False, backup=False, deltas=None)
    assert result.blocked == ["no 11-16 option chain from your brokers"]


def test_a_chain_without_deltas_blocks_the_delta_entries():
    result = Result("SPX")
    market_check.chain(result, DAY, found=True, backup=False, deltas=False)
    assert result.blocked == [
        "the 11-16 option chain has no deltas: entries that pick strikes by delta won't be placed"
    ]


def test_deltas_not_needed_are_not_checked():
    result = Result("SPX")
    market_check.chain(result, DAY, found=True, backup=False, deltas=None)
    assert result.ok


def test_a_chain_from_the_backup_broker_is_degraded():
    result = Result("SPX")
    market_check.chain(result, DAY, found=True, backup=True, deltas=True)
    assert result.degraded == [
        "Schwab's option chain failed; entries will price from TastyTrade's live quotes"
    ]


def test_no_expiration_ahead_blocks():
    result = Result("SPX")
    market_check.chain(result, None, found=False, backup=False, deltas=None)
    assert result.blocked == ["no expiration today or later is listed"]


# --- what's due today ------------------------------------------------------------------


def bot(**changes):
    base = iron_condor_skeleton("IC")
    return replace(base, **({"enabled": True, "entry_times": (EntryTimeDef("09:47"),)} | changes))


def test_due_takes_each_underlying_an_armed_bot_trades_today_once():
    bots = [bot(), bot(underlying="SPY"), bot(underlying="SPX", live=True)]
    assert sorted(market_check.due(bots, DAY, "09:00")) == ["SPX", "SPY"]


def test_due_leaves_out_a_bot_not_trading_today():
    weekly = bot(frequency="weekly", entry_days=(2,))  # Tuesdays only
    assert market_check.due([weekly], DAY, "09:00") == {}
    blacked = bot(use_blackout_days=True, blackout_days=(DAY.isoformat(),))
    assert market_check.due([blacked], DAY, "09:00") == {}


def test_due_leaves_out_a_bot_whose_entries_today_are_done():
    assert market_check.due([bot()], DAY, "09:48") == {}
    off = bot(entry_times=(EntryTimeDef("09:47", enabled=False),))
    assert market_check.due([off], DAY, "09:00") == {}


def test_due_takes_a_floating_window_until_it_ends():
    floating = bot(use_floating_entry=True, floating_start="10:00", floating_end="11:00",
                   entry_times=())  # fmt: skip
    assert "SPX" in market_check.due([floating], DAY, "10:30")
    assert market_check.due([floating], DAY, "11:01") == {}


def test_due_leaves_out_futures():
    assert market_check.due([bot(underlying="/ES")], DAY, "09:00") == {}


def test_due_says_whether_any_bot_on_it_picks_by_delta():
    by_premium = bot()  # the skeleton: shorts by premium, wings by offset from them
    by_delta = bot(legs=tuple(
        replace(leg, pick="delta") if leg.side is Side.SELL else leg for leg in by_premium.legs
    ))  # fmt: skip
    assert market_check.due([by_premium], DAY, "09:00") == {"SPX": False}
    assert market_check.due([by_premium, by_delta], DAY, "09:00") == {"SPX": True}


def test_chain_checked_pre_open_only_for_cash_indexes():
    assert market_check.chain_due("SPX", "09:00") and market_check.chain_due("XSP", "09:00")
    assert not market_check.chain_due("SPY", "09:00")
    assert market_check.chain_due("SPY", "09:30")  # the regular session has opened


# --- the alert -------------------------------------------------------------------------


def test_nothing_wrong_says_nothing():
    assert market_check.alert([Result("SPX"), Result("SPY")]) is None


def test_a_blocked_underlying_says_not_placed_with_every_detail():
    spx = Result("SPX", blocked=["no expirations"])
    spy = Result("SPY", degraded=["Schwab's down"])
    title, detail = market_check.alert([spx, spy, Result("QQQ")])
    assert title == "Market data check: SPX entries will NOT be placed"
    assert detail == (
        "SPX: no expirations. SPY: Schwab's down. Check your broker connections on the "
        "Settings page."
    )


def test_only_degraded_says_a_backup_is_covering():
    title, detail = market_check.alert([Result("SPX", degraded=["Schwab's down"])])
    assert title == "Market data degraded: entries will use a backup source"
    assert detail == "SPX: Schwab's down."
