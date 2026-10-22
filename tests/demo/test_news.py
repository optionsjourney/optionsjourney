from datetime import UTC, datetime

from optionsjourney.demo import news
from optionsjourney.services import news as real_news

NOW = datetime(2026, 10, 21, 13, 40, tzinfo=UTC)


def test_demo_news_shape_and_source():
    out = news.headlines("spx", NOW)
    assert out["symbol"] == "SPX" and 6 <= len(out["news"]) <= 10
    for item in out["news"]:
        assert set(item) == {"headline", "source", "url", "summary", "datetime"}
        assert item["source"] == "Demo headlines" and item["url"] == ""
        assert item["datetime"] <= NOW.timestamp()
    assert out == news.headlines("SPX", NOW)


def test_news_route_uses_demo_headlines_in_demo_mode(demo_app, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("went outside")

    monkeypatch.setattr(real_news, "company_news", boom)
    monkeypatch.setattr(real_news, "fetch_yahoo", boom)
    body = demo_app.test_client().get("/api/news/SPX").get_json()
    assert body["symbol"] == "SPX" and body["news"]
    assert {n["source"] for n in body["news"]} == {"Demo headlines"}
