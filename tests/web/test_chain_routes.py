"""The Trade page's option chain JSON (#90), and where Analyze's old addresses go (#223)."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from optionsjourney.app import create_app
from optionsjourney.core.quotes import Quote


class FakeTasty:
    name = "TastyTrade"
    broker = "tastytrade"

    def accounts(self):
        return []

    def quote_token(self):
        return "t", "wss://x"

    def option_chain(self, symbol):
        strikes = [
            {
                "strike-price": f"{k}.0",
                "call": f"SPXW  261120C0{k}000",
                "put": f"SPXW  261120P0{k}000",
            }
            for k in (5750, 5800, 5850)
        ]
        return [
            {
                "root-symbol": "SPXW",
                "expirations": [{"expiration-date": "2026-11-20", "strikes": strikes}],
            }
        ]


class FakeSchwab:
    name = "Schwab"
    broker = "schwab"

    def accounts(self):
        return []

    def option_expirations(self, symbol):
        from datetime import date

        return [date(2026, 11, 20)]

    def option_chain(self, symbol, expiration, strike_count):
        def side(right, k, bid, ask):
            return [
                {
                    "symbol": f"SPXW  261120{right}0{k}000",
                    "bid": bid,
                    "ask": ask,
                    "delta": -0.4 if right == "P" else 0.6,
                    "openInterest": 10,
                    "totalVolume": 5,
                },
                {"symbol": f"SPX   261120{right}0{k}000", "bid": 0, "ask": 0},
            ]

        calls = {
            f"{k}.0": side("C", k, c, c + 0.4) for k, c in ((5750, 80), (5800, 45), (5850, 20))
        }
        puts = {f"{k}.0": side("P", k, p, p + 0.4) for k, p in ((5750, 25), (5800, 40), (5850, 70))}
        return {
            "callExpDateMap": {"2026-11-20:41": calls},
            "putExpDateMap": {"2026-11-20:41": puts},
        }


class FakeHub:
    def subscribe(self, keys):
        pass

    def latest(self, key):
        marks = {"SPX": "5810"} | {
            f"SPXW  261120{right}0{k}000": price
            for right, k, price in (
                ("C", 5750, "80"), ("C", 5800, "45"), ("C", 5850, "20"),
                ("P", 5750, "25"), ("P", 5800, "40"), ("P", 5850, "70"),
            )
        }  # fmt: skip
        m = marks.get(key)
        return (
            None
            if m is None
            else Quote(key, Decimal(m), Decimal(m), Decimal(m), "tastytrade", datetime.now(UTC))
        )

    def status(self):
        return {"tastytrade": "streaming"}


@pytest.fixture
def client(settings, fake_credentials):
    app = create_app(
        settings,
        credentials=fake_credentials,
        broker_clients=lambda: [FakeTasty()],
        quote_hub=FakeHub(),
    )
    app.config["TESTING"] = True
    return app.test_client()


def test_analyze_is_gone_and_its_links_go_to_the_trade_page(client):
    # #223: Analyze was removed; bookmarks and old alert links still land somewhere useful
    assert client.get("/analyze").location.endswith("/trade")
    assert client.get("/analyze?ticker=spy&chain=1&embed=1").location.endswith("/trade?ticker=SPY")
    assert client.get("/tools/analyzer/?ticker=SPX").location.endswith("/trade?ticker=SPX")
    for old in ("/analyze?trade=k1", "/analyze?position=k1"):
        assert client.get(old).location.endswith("/risk-graph?trade=k1")
    assert client.get("/tools/analyzer/chart?frame=m5").status_code == 404
    assert client.get("/api/analyzer/expirations?ticker=SPX").status_code == 404


def test_expirations_from_tastytrade(client):
    body = client.get("/api/trade/expirations?ticker=spx").get_json()
    assert body == {"status": "success", "ticker": "SPX", "expirations": ["2026-11-20"]}
    assert client.get("/api/trade/expirations?ticker=S P X").status_code == 400


@pytest.fixture
def schwab_client(settings, fake_credentials):
    app = create_app(
        settings,
        credentials=fake_credentials,
        broker_clients=lambda: [FakeSchwab()],
        quote_hub=FakeHub(),
    )
    app.config["TESTING"] = True
    return app.test_client()


def test_chain_table(schwab_client):
    body = schwab_client.get("/api/trade/chain-table?ticker=SPX&expiration=2026-11-20")
    body = body.get_json()
    assert body["source"] == "schwab" and body["spot"] == 5810.0
    row = next(r for r in body["rows"] if r["strike"] == 5800.0)
    assert row["put"] == {
        "bid": 40.0,
        "ask": 40.4,
        "delta": -0.4,
        "oi": 10,
        "volume": 5,
        "stream_symbol": ".SPXW261120P5800",
    }


@pytest.fixture
def both_client(settings, fake_credentials):
    app = create_app(
        settings,
        credentials=fake_credentials,
        broker_clients=lambda: [FakeTasty(), FakeSchwab()],
        quote_hub=FakeHub(),
    )
    app.config["TESTING"] = True
    return app.test_client()


def test_chain_table_follows_the_tickets_broker(both_client):
    url = "/api/trade/chain-table?ticker=SPX&expiration=2026-11-20"
    assert both_client.get(url).get_json()["source"] == "schwab"  # no preference: Schwab first
    assert both_client.get(url + "&broker=tastytrade").get_json()["source"] == "tastytrade"
    assert both_client.get(url + "&broker=schwab").get_json()["source"] == "schwab"


def test_chain_table_falls_back_to_the_other_broker(client):
    url = "/api/trade/chain-table?ticker=SPX&expiration=2026-11-20&broker=schwab"
    assert client.get(url).get_json()["source"] == "tastytrade"  # no Schwab connected


def test_held_legs(client, monkeypatch):
    # The Trade page chain's Pos column (#210), from the journal's open trades
    from optionsjourney.services import journal

    legs = [
        {"account_id": 1, "expiration": "2026-11-20", "strike": 5800.0, "type": "put", "qty": -1}
    ]
    seen = []
    monkeypatch.setattr(journal, "held_legs", lambda f, t, **kw: seen.append((t, kw)) or legs)
    body = client.get("/api/trade/held-legs?ticker=spx").get_json()
    assert body == {"status": "success", "ticker": "SPX", "legs": legs}
    assert seen[0][0] == "SPX" and seen[0][1]["cache"] is not None
    assert client.get("/api/trade/held-legs?ticker=S P X").status_code == 400


def test_held_legs_with_no_trades(client):
    body = client.get("/api/trade/held-legs?ticker=SPX").get_json()
    assert body == {"status": "success", "ticker": "SPX", "legs": []}
