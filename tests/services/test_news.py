import pytest

from optionsjourney.services import news, settings

KEY = "abc123def456ghi789jk"


@pytest.fixture(autouse=True)
def _fresh_cache():
    news.clear_cache()
    yield
    news.clear_cache()


def _with_key(factory):
    settings.save_section(factory, "charts", {"finnhub_api_key": KEY})


FEED = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>DELL beats</title><link>https://finance.yahoo.com/a</link>
<description>Strong quarter</description><pubDate>Wed, 21 Oct 2026 12:24:08 +0000</pubDate></item>
</channel></rss>"""
NOW = 1_792_600_000.0  # 2026-10-21 New York


def test_without_a_key_headlines_come_from_yahoo(factory):
    def fetch(*a):
        raise AssertionError("Finnhub without a key")

    out = news.company_news(factory, "dell", fetch=fetch, fetch_feed=lambda s: FEED,
                            clock=lambda: NOW)  # fmt: skip
    assert out == {"symbol": "DELL", "news": [{
        "headline": "DELL beats", "source": "Yahoo Finance", "url": "https://finance.yahoo.com/a",
        "summary": "Strong quarter", "datetime": 1792585448}]}  # fmt: skip


def test_a_bad_feed_is_an_error(factory):
    with pytest.raises(news.NewsError, match="isn't a feed"):
        news.company_news(factory, "DELL", fetch_feed=lambda s: "<html>nope", clock=lambda: NOW)


def test_adding_a_key_switches_source_at_once(factory):
    news.company_news(factory, "DELL", fetch_feed=lambda s: FEED, clock=lambda: NOW)
    _with_key(factory)
    rows = [{"headline": "From Finnhub", "datetime": 5}]
    no_feed = lambda s: pytest.fail("Yahoo")  # noqa: E731
    out = news.company_news(factory, "DELL", fetch=lambda *a: rows, fetch_feed=no_feed,
                            clock=lambda: NOW)  # fmt: skip
    assert out["news"][0]["headline"] == "From Finnhub"


def test_futures_and_indices_are_a_note(factory):
    _with_key(factory)
    out = news.company_news(factory, "/ES", fetch=lambda *a: pytest.fail("fetched"))
    assert out["note"] == "No company news for futures or indices" and out["news"] == []


def test_fetches_the_last_7_days_and_caches_5_minutes(factory):
    _with_key(factory)
    calls = []

    def fetch(sym, start, end, key):
        calls.append((sym, start, end, key))
        return [{"headline": "DELL beats", "datetime": 10}]

    t = [1_760_000_000.0]  # 2025-10-09 New York
    out = news.company_news(factory, "DELL", fetch=fetch, clock=lambda: t[0])
    assert out == {"symbol": "DELL", "news": [{"headline": "DELL beats", "source": "", "url": "",
                                                "summary": "", "datetime": 10}]}  # fmt: skip
    assert calls == [("DELL", "2025-10-02", "2025-10-09", KEY)]
    t[0] += 299
    news.company_news(factory, "DELL", fetch=fetch, clock=lambda: t[0])
    assert len(calls) == 1
    t[0] += 2
    news.company_news(factory, "DELL", fetch=fetch, clock=lambda: t[0])
    assert len(calls) == 2


def test_a_failed_fetch_raises_and_is_not_cached(factory):
    _with_key(factory)

    def boom(*a):
        raise OSError("network down")

    with pytest.raises(news.NewsError, match="network down"):
        news.company_news(factory, "DELL", fetch=boom)
    out = news.company_news(factory, "DELL", fetch=lambda *a: [])
    assert out == {"symbol": "DELL", "news": []}


def test_yahoo_is_asked_as_a_browser(monkeypatch):
    seen = {}

    class Resp:
        status_code = 200
        content = FEED.encode()
        text = FEED

    def get(url, params, headers, timeout, follow_redirects):
        seen.update(url=url, params=params, headers=headers)
        return Resp()

    monkeypatch.setattr(news.httpx, "get", get)
    assert news.fetch_yahoo("DELL") == FEED
    assert seen["params"]["s"] == "DELL" and seen["headers"]["User-Agent"].startswith("Mozilla/5.0")


def test_the_key_goes_in_a_header_not_the_address(monkeypatch):
    seen = {}

    class Resp:
        status_code = 401

    def get(url, params, headers, timeout):
        seen.update(url=url, params=params, headers=headers)
        return Resp()

    monkeypatch.setattr(news.httpx, "get", get)
    with pytest.raises(news.NewsError, match="refused the key"):
        news.fetch_finnhub("DELL", "2026-10-14", "2026-10-21", KEY)
    assert seen["headers"] == {"X-Finnhub-Token": KEY}
    assert KEY not in seen["url"] and KEY not in str(seen["params"])
