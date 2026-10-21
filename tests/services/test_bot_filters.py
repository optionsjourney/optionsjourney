from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest

from optionsjourney.bots.definition import MoveLimits, iron_condor_skeleton
from optionsjourney.services import bot_filters
from tests.services.market_fakes import FakeFeed, daily_history, day_id, et, ms

D = Decimal
DAY = date(2026, 10, 13)
NOW = et(DAY, 9, 47) + timedelta(seconds=30)
BOT = replace(iron_condor_skeleton("IC"), id=7)


@pytest.fixture
def feed():
    return FakeFeed()


@pytest.fixture
def market(feed):
    return bot_filters.Market(feed)


def check(market, **changes):
    return bot_filters.check(market, replace(BOT, **changes), NOW)


def test_with_every_filter_off_nothing_is_read(market, feed):
    assert check(market, vix_min=D("1"), sma_mode="above") is None
    # Ticked with nothing set passes without reading, like Tracker2
    assert check(market, use_vix_level=True, use_technicals=True, use_intraday_move=True) is None
    assert feed.calls == []


def test_vix_level_passes_misses_and_fails_closed(market, feed):
    feed.trade("VIX", "18.5", NOW - timedelta(seconds=20))
    assert check(market, use_vix_level=True, vix_max=D("20")) is None
    miss = check(market, use_vix_level=True, vix_max=D("18"))
    assert miss.reason == "VIX level filter: VIX 18.5; above max 18" and miss.unavailable is None
    feed.trade("VIX", "18.5", NOW - timedelta(minutes=7))
    stale = check(market, use_vix_level=True, vix_max=D("20"))
    assert stale.unavailable == "VIX level"
    assert stale.reason == (
        "VIX level unavailable: VIX last printed at 09:40:30 New York, 7 min ago; "
        "too stale to use. Not entered."
    )
    feed.trades.clear()
    assert check(market, use_vix_level=True, vix_max=D("20")).reason == (
        "VIX level unavailable: no VIX price from TastyTrade. Not entered."
    )


def test_the_ratio_needs_both_prints_fresh(market, feed):
    feed.trade("VIX", "16.00", NOW - timedelta(seconds=5))
    feed.trade("VIX9D", "14.50", NOW - timedelta(seconds=5))
    on = {"use_vix9d_ratio": True, "vix9d_ratio_max": D("0.9")}
    assert check(market, **on).reason == (
        "VIX9D/VIX ratio filter: VIX9D/VIX 14.50 / 16.00 = 0.906; above max 0.9"
    )
    assert check(market, **(on | {"vix9d_ratio_max": D("1")})) is None
    feed.trade("VIX9D", "14.50", NOW - timedelta(minutes=6))
    assert check(market, **on).unavailable == "VIX9D/VIX ratio"


def overnight(feed, prior="15.00", open_="15.60"):
    feed.summaries["VIX"] = (day_id(DAY), D(prior))
    feed.bars(
        "VIX{=1m}",
        [(et(DAY, 9, 29), "15.10", "15.10"), (et(DAY, 9, 30), open_, open_),
         (et(DAY, 9, 31), "99", "99")],
    )  # fmt: skip


def test_the_overnight_move_is_from_the_prior_close_to_the_first_print(market, feed):
    overnight(feed)
    on = {"use_vix_overnight": True, "vix_overnight": MoveLimits(max_up=D("3"))}
    assert check(market, **on).reason == (
        "VIX overnight move filter: VIX 15.00 -> 15.60 (+4.00%); outside max up 3.00%"
    )
    want, wait = feed.calls[0]
    assert want.summaries == ("VIX",) and wait == 60  # waits for the 09:30 bar
    assert check(market, **(on | {"vix_overnight": MoveLimits(max_up=D("5"))})) is None
    assert len(feed.calls) == 1  # the pair is cached for the day


def test_an_old_summary_or_no_open_yet_fails_closed_and_is_not_cached(market, feed):
    on = {"use_vix_overnight": True, "vix_overnight": MoveLimits(max_up=D("3"))}
    overnight(feed)
    feed.summaries["VIX"] = (day_id(DAY) - 1, D("14"))  # yesterday's: two sessions back
    assert check(market, **on).reason == (
        "VIX overnight move unavailable: no VIX prior close from TastyTrade. Not entered."
    )
    overnight(feed)
    feed.candles["VIX{=1m}"] = [c for c in feed.candles["VIX{=1m}"] if c[1] == D("15.10")]
    assert "no VIX print at or after 09:30 yet" in check(market, **on).reason
    overnight(feed)
    assert check(market, **on).unavailable is None  # read again, and now it's there
    assert len(feed.calls) == 3


def test_no_0930_bar_never_uses_a_later_open(market, feed):
    # TastyTrade's history starts at 09:31: its open isn't 09:30's, so the filter fails
    # closed (the same rule as Schwab's, tests/services/test_market_feed.py).
    on = {"use_vix_overnight": True, "vix_overnight": MoveLimits(max_up=D("50"))}
    overnight(feed)
    feed.candles["VIX{=1m}"] = [c for c in feed.candles["VIX{=1m}"] if c[1] == D("99")]
    found = check(market, **on)
    assert found.unavailable == "VIX overnight move"
    assert found.reason == (
        "VIX overnight move unavailable: no VIX 09:30 bar from TastyTrade. Not entered."
    )


def test_no_0930_bar_is_the_intraday_filters_problem(market, feed):
    feed.trade("SPX", "5016", NOW - timedelta(seconds=3))
    minute_bars(feed)
    feed.candles["SPX{=1m}"] = [c for c in feed.candles["SPX{=1m}"] if c[0] != ms(et(DAY, 9, 30))]
    on = {"use_intraday_move": True, "intraday": MoveLimits(min_up=D("0.1"))}
    found = check(market, **on)
    assert found.unavailable == "Intraday movement"
    assert found.reason == (
        "Intraday movement unavailable: no SPX 09:30 bar from TastyTrade. Not entered."
    )


def test_sma_reads_daily_closes_once_a_day_when_complete(market, feed):
    feed.trade("SPX", "5800", NOW - timedelta(seconds=3))
    feed.bars("SPX{=1d}", daily_history(DAY, [5790] + [5700] * 250))
    on = {"use_technicals": True, "sma_mode": "above", "sma_days_a": 2}
    assert check(market, **on) is None  # 5800 > (5800 + 5790) / 2
    assert check(market, **(on | {"sma_mode": "below"})).reason == (
        "Technical indicators filter: SPX price 5800.00 > SMA(2d) 5795.00"
    )
    assert feed.calls[1][0].candles == ()  # the daily closes came from the cache


def test_short_or_incomplete_daily_history(market, feed):
    feed.trade("SPX", "5800", NOW - timedelta(seconds=3))
    feed.bars("SPX{=1d}", daily_history(DAY, [5790] * 10))
    on = {"use_technicals": True, "sma_mode": "above", "sma_days_a": 50}
    short = check(market, **on)
    assert short.unavailable == "Technical indicators"
    assert short.reason == (
        "Technical indicators unavailable: SPX has not enough history for SMA(50d). Not entered."
    )
    check(market, **on)
    assert all(want.candles for want, _ in feed.calls)  # too short to cache: read again
    feed.incomplete.add("SPX{=1d}")
    assert check(market, **on).reason == (
        "Technical indicators unavailable: SPX daily history incomplete from TastyTrade. "
        "Not entered."
    )


def minute_bars(feed, open_="5000"):
    rows = [(et(DAY - timedelta(days=1), 15, 59), "4990", "4990")]
    rows += [(et(DAY, 9, 30) + timedelta(minutes=k), open_ if k == 0 else "5010", "5010")
             for k in range(17)]  # fmt: skip
    feed.bars("SPX{=1m}", rows)


def test_technicals_and_intraday_share_one_read(market, feed):
    feed.trade("SPX", "5016", NOW - timedelta(seconds=3))
    minute_bars(feed)
    on = {"use_technicals": True, "ema_mode": "above", "ema_minutes_a": 5,
          "use_intraday_move": True, "intraday": MoveLimits(min_up=D("0.3"))}  # fmt: skip
    assert check(market, **on) is None
    assert len(feed.calls) == 1 and feed.calls[0][1] == 10
    miss = check(market, **(on | {"intraday": MoveLimits(min_up=D("0.5"))}))
    assert miss.reason == (
        "Intraday movement filter: SPX 5000 -> 5016 (+0.32%); outside min up 0.50%"
    )


def test_no_open_yet_is_the_intraday_filters_problem(market, feed):
    feed.trade("SPX", "5016", NOW - timedelta(seconds=3))
    feed.bars("SPX{=1m}", [(et(DAY - timedelta(days=1), 15, 59), "4990", "4990")])
    on = {"use_technicals": True, "ema_mode": "above", "ema_minutes_a": 2,
          "use_intraday_move": True, "intraday": MoveLimits(min_up=D("0.3"))}  # fmt: skip
    found = check(market, **on)  # EMA(2) of 4990 and 5016 passes; there's no open
    assert found.unavailable == "Intraday movement"
    assert "no SPX bar at or after 09:30 yet" in found.reason


def test_a_feed_that_raises_or_is_missing_fails_closed(market, feed):
    feed.error = RuntimeError("socket closed")
    found = check(market, use_vix_level=True, vix_max=D("20"))
    assert found.unavailable == "VIX level" and "socket closed" in found.reason
    none = bot_filters.check(None, replace(BOT, use_vix_level=True, vix_max=D("20")), NOW)
    assert none.unavailable == "VIX level"


def test_the_first_failing_filter_wins_in_tracker2s_order(market, feed):
    feed.trade("VIX", "30", NOW)
    feed.trade("VIX9D", "33", NOW)
    found = check(market, use_vix_level=True, vix_max=D("20"), use_vix9d_ratio=True,
                  vix9d_ratio_max=D("1"))  # fmt: skip
    assert found.reason.startswith("VIX level filter")


def test_an_alert_once_per_bot_filter_and_day():
    bot_filters.reset_alerts()
    assert bot_filters.first_today(7, "VIX level", DAY)
    assert not bot_filters.first_today(7, "VIX level", DAY)
    assert bot_filters.first_today(8, "VIX level", DAY)
    assert bot_filters.first_today(7, "VIX9D/VIX ratio", DAY)
    assert bot_filters.first_today(7, "VIX level", DAY + timedelta(days=1))


def test_the_first_bar_counts_only_once_the_whole_history_has_arrived(market, feed):
    """DXLink sends a candle history newest first: until its end flag, the earliest
    bar seen so far may not be 09:30's."""
    overnight(feed)
    feed.candles["VIX{=1m}"] = [c for c in feed.candles["VIX{=1m}"] if c[1] == D("99")]
    feed.incomplete.add("VIX{=1m}")  # only the 09:31 bar has arrived
    on = {"use_vix_overnight": True, "vix_overnight": MoveLimits(max_up=D("3"))}
    found = check(market, **on)
    assert found.unavailable == "VIX overnight move"
    assert "VIX 1-minute history incomplete" in found.reason
    feed.trade("SPX", "5016", NOW - timedelta(seconds=3))
    minute_bars(feed)
    feed.incomplete.add("SPX{=1m}")
    move = check(market, use_intraday_move=True, intraday=MoveLimits(min_up=D("0.3")))
    assert move.unavailable == "Intraday movement" and "incomplete" in move.reason


# Final review fixes


def test_vix_reads_wait_for_a_fresh_print_not_the_first_one(market, feed):
    from optionsjourney.services.market_feed import Got

    feed.trade("VIX", "18", NOW - timedelta(seconds=5))
    feed.trade("VIX9D", "17", NOW - timedelta(seconds=5))
    check(market, use_vix_level=True, vix_max=D("20"))
    check(market, use_vix9d_ratio=True, vix9d_ratio_max=D("2"))
    stale, fresh = Got(), Got()
    for symbol in ("VIX", "VIX9D"):
        stale.trades[symbol] = (D("18"), int((NOW - timedelta(minutes=10)).timestamp() * 1000))
        fresh.trades[symbol] = (D("18"), int((NOW - timedelta(seconds=1)).timestamp() * 1000))
    for ready in feed.readies:
        assert not ready(stale) and ready(fresh)


def test_daily_history_must_reach_the_previous_session(market, feed):
    feed.trade("SPX", "5800", NOW - timedelta(seconds=3))
    rows = daily_history(DAY, [5790] + [5700] * 250)
    feed.bars("SPX{=1d}", [rows[0]] + rows[2:])  # Monday 10/12's close is missing
    found = check(market, use_technicals=True, sma_mode="above", sma_days_a=2)
    assert found.unavailable == "Technical indicators"
    assert "ends 2026-10-09, not the previous session 2026-10-12" in found.reason


# --- Bots slice 20: the filters as they read now (_preview_vix_level, _preview_vix9d_ratio)


def preview(market, **changes):
    return bot_filters.preview(market, replace(BOT, **changes), NOW)


def test_filters_now_reads_and_flags_a_stale_vix(market, feed):
    feed.trade("VIX", "18.5", NOW - timedelta(seconds=20))
    level = preview(market, use_vix_level=True, vix_max=D("20"))
    assert level == [{"label": "VIX level", "ok": True, "detail": "VIX 18.5",
                      "as_of": "09:47:10", "stale": False}]  # fmt: skip
    feed.trade("VIX", "18.5", NOW - timedelta(minutes=7))
    stale = preview(market, use_vix_level=True, vix_max=D("18"))[0]
    assert stale["ok"] is False and stale["stale"] is True
    assert stale["detail"] == "VIX 18.5; above max 18" and stale["as_of"] == "09:40:30"
    assert all(call[1] <= bot_filters.PREVIEW_WAIT_SECONDS for call in feed.calls)


def test_filters_now_reads_the_ratio_and_says_a_missing_price(market, feed):
    feed.trade("VIX", "16.00", NOW - timedelta(seconds=5))
    on = {"use_vix9d_ratio": True, "vix9d_ratio_max": D("0.9")}
    missing = preview(market, **on)
    assert missing == [{"label": "VIX9D/VIX ratio", "error": "no VIX9D price from TastyTrade"}]
    feed.trade("VIX9D", "14.50", NOW - timedelta(seconds=5))
    ratio = preview(market, **on)[0]
    assert ratio["ok"] is False and ratio["detail"].startswith("VIX9D/VIX 14.50 / 16.00 = 0.906")


def test_filters_now_reads_nothing_for_filters_that_are_off(market, feed):
    assert preview(market, vix_min=D("1")) == []
    assert feed.calls == []


def test_filters_now_never_raises(feed):
    class Broken:
        def read(self, *a):
            raise RuntimeError("socket gone")

    out = bot_filters.preview(bot_filters.Market(Broken()),
                              replace(BOT, use_vix_level=True, vix_max=D("20")), NOW)  # fmt: skip
    assert out == [{"label": "VIX level", "error": "couldn't read the prices (socket gone)"}]


def test_filters_now_reads_vix_and_vix9d_once_for_both_filters(market, feed):
    feed.trade("VIX", "16.00", NOW - timedelta(seconds=5))
    feed.trade("VIX9D", "14.50", NOW - timedelta(seconds=5))
    out = preview(market, use_vix_level=True, vix_max=D("20"), use_vix9d_ratio=True,
                  vix9d_ratio_max=D("1"))  # fmt: skip
    assert [f["label"] for f in out] == ["VIX level", "VIX9D/VIX ratio"]
    assert [f["ok"] for f in out] == [True, True]
    assert len(feed.calls) == 1 and set(feed.calls[0][0].trades) == {"VIX", "VIX9D"}


def test_a_schwab_read_names_schwab_when_it_fails_closed(market, feed):
    feed.source = "Schwab"
    assert check(market, use_vix_level=True, vix_max=D("20")).reason == (
        "VIX level unavailable: no VIX price from Schwab. Not entered."
    )
