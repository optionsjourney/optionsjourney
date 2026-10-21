from optionsjourney.core import news


def test_only_stocks_and_etfs_have_company_news():
    for sym in ("DELL", "BRK.B", "SPY"):
        assert news.has_company_news(sym)
    for sym in ("/ES", "$SPX", "^VIX", "dell", "", None):
        assert not news.has_company_news(sym)


def test_headlines_newest_first_without_repeats_or_blanks():
    rows = [
        {"headline": "Old", "datetime": 1, "source": "A", "url": "https://a.com/1"},
        {"headline": "DELL beats", "datetime": 3, "summary": "x" * 500},
        {"headline": " dell BEATS ", "datetime": 2},
        {"headline": "", "datetime": 4},
        {"datetime": 5},
    ]
    out = news.headlines(rows)
    assert [n["headline"] for n in out] == ["DELL beats", "Old"]
    assert len(out[0]["summary"]) == 400 and out[0]["source"] == "" and out[0]["datetime"] == 3
    assert out[1] == {"headline": "Old", "source": "A", "url": "https://a.com/1",
                      "summary": "", "datetime": 1}  # fmt: skip


def test_headlines_limit_and_bad_payload():
    rows = [{"headline": f"h{i}", "datetime": i} for i in range(50)]
    assert len(news.headlines(rows)) == 40
    assert news.headlines(rows, limit=2)[0]["headline"] == "h49"
    assert news.headlines({"error": "x"}) == []


def test_links_must_be_web_addresses():
    assert news.item({"url": "javascript:alert(1)"})["url"] == ""
    assert news.item({"url": " https://x.com/a "})["url"] == "https://x.com/a"


FEED = """<?xml version="1.0"?><rss version="2.0"><channel><title>DELL News</title>
<item><title>Old story</title><link>https://y.com/old</link>
<pubDate>Mon, 05 Oct 2026 10:00:00 +0000</pubDate></item>
<item><title>Newer</title><link>https://y.com/new</link><description>d</description>
<pubDate>Wed, 21 Oct 2026 12:00:00 +0000</pubDate></item>
<item><title>Older</title><link>https://y.com/mid</link>
<pubDate>Tue, 20 Oct 2026 12:00:00 +0000</pubDate></item>
<item><title>No date</title><link>https://y.com/x</link></item>
</channel></rss>"""


def test_yahoo_feed_last_7_days_newest_first():
    now = 1_792_600_000  # 2026-10-21
    out = news.yahoo_headlines(FEED, now)
    assert [n["headline"] for n in out] == ["Newer", "Older"]
    assert out[0] == {"headline": "Newer", "source": "Yahoo Finance", "url": "https://y.com/new",
                      "summary": "d", "datetime": 1792584000}  # fmt: skip


def test_yahoo_bad_xml_raises():
    import pytest

    with pytest.raises(ValueError):
        news.yahoo_headlines("<html>", 0)
