import json

import httpx
import pytest

from optionsjourney.core.settings import SettingError, spec
from optionsjourney.db import make_engine, make_session_factory, migrate
from optionsjourney.services import notify, settings
from optionsjourney.services.user_alerts import Fired

EVENT = Fired(1, "SPX went below 7,700.00", "now 7,690.00 · add hedges", "/charts/pane?symbol=SPX")
WEBHOOK = "https://discord.com/api/webhooks/123/abc"
TOKEN = "123456789:" + "A" * 35


@pytest.fixture
def factory(tmp_path):
    engine = make_engine(tmp_path / "t.db")
    migrate(engine)
    return make_session_factory(engine)


def recorder():
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(204 if "discord" in str(request.url) else 200, json={"ok": True})

    return sent, httpx.Client(transport=httpx.MockTransport(handler))


def test_nothing_is_sent_until_switched_on(factory):
    sent, http = recorder()
    assert notify.send(factory, EVENT, http=http) == []
    assert sent == []


def test_discord(factory):
    settings.save_section(
        factory,
        "notifications",
        {"discord_webhook": WEBHOOK, "telegram_bot_token": "", "telegram_chat_id": ""},
    )
    sent, http = recorder()
    assert notify.send(factory, EVENT, http=http) == [("Discord", None)]
    assert str(sent[0].url) == WEBHOOK
    assert (
        json.loads(sent[0].content)["content"]
        == "**🔔 SPX went below 7,700.00**\nnow 7,690.00 · add hedges"
    )


def test_telegram(factory):
    settings.save_section(
        factory,
        "notifications",
        {"discord_webhook": "", "telegram_bot_token": TOKEN, "telegram_chat_id": "42"},
    )
    sent, http = recorder()
    assert notify.send(factory, EVENT, http=http) == [("Telegram", None)]
    assert str(sent[0].url) == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    assert json.loads(sent[0].content) == {
        "chat_id": "42",
        "text": "<b>🔔 SPX went below 7,700.00</b>\nnow 7,690.00 · add hedges",
        "parse_mode": "HTML",
    }


def test_a_failed_send_is_reported_not_raised(factory):
    settings.save_section(
        factory,
        "notifications",
        {"discord_webhook": WEBHOOK, "telegram_bot_token": "", "telegram_chat_id": ""},
    )
    http = httpx.Client(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(404, json={"message": "Unknown Webhook"})
        )
    )
    [(channel, problem)] = notify.send(factory, EVENT, http=http)
    assert channel == "Discord" and "404" in problem


def test_settings_check_the_formats():
    assert spec("discord_webhook").clean("") == ""
    with pytest.raises(SettingError, match="discord.com"):
        spec("discord_webhook").clean("https://example.com/hook")
    with pytest.raises(SettingError, match="BotFather"):
        spec("telegram_bot_token").clean("not-a-token")
    with pytest.raises(SettingError, match="number"):
        spec("telegram_chat_id").clean("@me")


# --- Bots slice 9: routing, the lock-screen layout, sending off the bots' threads ---

import logging  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from datetime import UTC, datetime  # noqa: E402
from decimal import Decimal  # noqa: E402

from optionsjourney.db.models import Bot, BotExit, BotLegGroup, BotRun  # noqa: E402
from tests.services import bot_fakes  # noqa: E402

NOW = datetime(2026, 10, 13, 15, 0, tzinfo=UTC)


def both(factory):
    settings.save_section(
        factory,
        "notifications",
        {"discord_webhook": WEBHOOK, "telegram_bot_token": TOKEN, "telegram_chat_id": "42"},
    )


def bot_on(factory, channel="all", alert_name=None):
    bot_id = bot_fakes.make(factory)
    with factory() as session:
        bot = session.get(Bot, bot_id)
        bot.alert_channel, bot.alert_name = channel, alert_name
        session.commit()
    return bot_id


def channels(results):
    return sorted(channel for channel, _ in results)


SAFETY = [
    ("IC: a stop was not placed", "The put side has no stop. Check your broker."),
    ("An order's outcome is unclear", "TastyTrade didn't answer."),
    ("IC: stops waiting for Freeze", "Freeze all orders is on, so the stops weren't sent."),
    ("IC: a side is held back", "Its close may still be live at your broker."),
    ("IC: close didn't fill", "The stop was put back."),
    ("IC: timed close failed", "Nothing was closed."),
]


@pytest.mark.parametrize(("title", "detail"), SAFETY)
def test_safety_alerts_reach_every_channel_whatever_the_bot_chose(factory, title, detail):
    both(factory)
    bot_id = bot_on(factory, "discord")
    sent, http = recorder()
    event = Fired(0, title, detail, f"/automation/{bot_id}/runs", bot_id, None, routine=True)
    assert channels(notify.send(factory, event, http=http)) == ["Discord", "Telegram"]
    assert len(sent) == 2


def test_a_routine_alert_goes_only_to_the_bots_channel(factory):
    both(factory)
    bot_id = bot_on(factory, "telegram")
    _, http = recorder()
    done = Fired(0, "IC: close filled", "The side closed at 0.55.", "", bot_id, routine=True)
    assert channels(notify.send(factory, done, http=http)) == ["Telegram"]
    # Not marked routine: everywhere, as before
    plain = Fired(0, "IC: close filled", "The side closed at 0.55.", "", bot_id)
    assert channels(notify.send(factory, plain, http=http)) == ["Discord", "Telegram"]


def test_a_routine_alert_for_a_channel_not_set_up_goes_everywhere(factory):
    settings.save_section(
        factory,
        "notifications",
        {"discord_webhook": WEBHOOK, "telegram_bot_token": "", "telegram_chat_id": ""},
    )
    bot_id = bot_on(factory, "telegram")
    _, http = recorder()
    done = Fired(0, "IC: close filled", "Closed at 0.55.", "", bot_id, routine=True)
    assert channels(notify.send(factory, done, http=http)) == ["Discord"]


def test_an_alert_for_a_bot_that_is_gone_still_goes_everywhere(factory):
    both(factory)
    _, http = recorder()
    done = Fired(0, "IC: close filled", "Closed at 0.55.", "", 999, routine=True)
    assert channels(notify.send(factory, done, http=http)) == ["Discord", "Telegram"]


def add_run(factory, bot_id):
    legs = [
        {"symbol": bot_fakes.sym(5700, "put"), "strike": "5700", "right": "put",
         "side": "sell", "ratio": 1, "group": 0},
        {"symbol": bot_fakes.sym(5675, "put"), "strike": "5675", "right": "put",
         "side": "buy", "ratio": 1, "group": 0},
        {"symbol": bot_fakes.sym(5800, "call"), "strike": "5800", "right": "call",
         "side": "sell", "ratio": 1, "group": 1},
        {"symbol": bot_fakes.sym(5825, "call"), "strike": "5825", "right": "call",
         "side": "buy", "ratio": 1, "group": 1},
    ]  # fmt: skip
    with factory() as session:
        run = BotRun(bot_id=bot_id, key="k-42", trading_date=NOW.date(), entry_time="09:47",
                     status="filled", live=True, account_number="5WX06789", legs=legs,
                     filled_legs=[{"symbol": leg["symbol"]} for leg in legs], contracts=2,
                     fill_credit=Decimal("2.00"), chase_log=[], filled_at=NOW, created_at=NOW,
                     updated_at=NOW)  # fmt: skip
        session.add(run)
        session.flush()
        for position, short, long, state in ((0, 5700, 5675, "working"),
                                             (1, 5800, 5825, "pending")):  # fmt: skip
            right = "put" if position == 0 else "call"
            session.add(BotExit(run_id=run.id, group_position=position, kind="stop", state=state,
                                short_symbol=bot_fakes.sym(short, right),
                                long_symbol=bot_fakes.sym(long, right),
                                contracts=2, trigger=Decimal("3.2"), log=[], created_at=NOW,
                                updated_at=NOW))  # fmt: skip
        session.commit()
        return run.id


def test_a_bot_alert_reads_well_on_a_lock_screen(factory):
    settings.save_section(
        factory,
        "notifications",
        {"discord_webhook": WEBHOOK, "telegram_bot_token": "", "telegram_chat_id": ""},
    )
    bot_id = bot_on(factory, alert_name="IC Joint")
    run_id = add_run(factory, bot_id)
    sent, http = recorder()
    event = Fired(0, "IC: stops waiting for Freeze", "Freeze is on.", "", bot_id, run_id)
    notify.send(factory, event, http=http)
    with factory() as session:
        names = [g.name for g in session.query(BotLegGroup).filter_by(bot_id=bot_id)
                 .order_by(BotLegGroup.position)]  # fmt: skip
    assert json.loads(sent[0].content)["content"] == (
        "**🔴 stops waiting for Freeze · IC Joint**\nFreeze is on.\n\n"
        f"**Trade:** IC · ••6789 · run {run_id}\n"
        "**Legs:** −2 SPX 13 Oct 5700P · +2 5675P · −2 5800C · +2 5825C\n"
        f"**Protection:** {names[0]} stop 3.20 · {names[1]} stop NONE"
    )
    assert "5WX0" not in sent[0].content.decode()


def test_the_trade_lines_are_left_out_when_they_cant_be_read(factory, monkeypatch):
    from optionsjourney.services import bot_alerts

    settings.save_section(
        factory,
        "notifications",
        {"discord_webhook": WEBHOOK, "telegram_bot_token": "", "telegram_chat_id": ""},
    )
    bot_id = bot_on(factory)

    def broken(*args):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(bot_alerts, "_legs", broken)
    run_id = add_run(factory, bot_id)
    sent, http = recorder()
    notify.send(factory, Fired(0, "IC: stop filled", "Lost 1.20.", "", bot_id, run_id), http=http)
    content = json.loads(sent[0].content)["content"]
    assert "**Trade:**" in content and "**Legs:**" not in content and "**Protection:**" in content


def test_telegram_falls_back_to_plain_text_when_the_html_is_refused(factory):
    settings.save_section(
        factory,
        "notifications",
        {"discord_webhook": "", "telegram_bot_token": TOKEN, "telegram_chat_id": "42"},
    )
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        if "parse_mode" in sent[-1]:
            return httpx.Response(400, json={"description": "can't parse entities"})
        return httpx.Response(200, json={"ok": True})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    assert notify.send(factory, EVENT, http=http) == [("Telegram", None)]
    assert sent[1] == {
        "chat_id": "42",
        "text": "🔔 SPX went below 7,700.00\nnow 7,690.00 · add hedges",
    }


def test_a_failed_telegram_send_never_logs_the_token(factory, caplog):
    settings.save_section(
        factory,
        "notifications",
        {"discord_webhook": "", "telegram_bot_token": TOKEN, "telegram_chat_id": "42"},
    )

    def handler(request):
        raise httpx.ConnectError(f"couldn't reach {request.url}")

    http = httpx.Client(transport=httpx.MockTransport(handler))
    with caplog.at_level(logging.DEBUG):
        [(channel, problem)] = notify.send(factory, EVENT, http=http)
        notify.send_soon(factory, EVENT, http=http)
        assert notify.wait_idle(5)
    assert channel == "Telegram" and TOKEN not in problem
    assert TOKEN not in caplog.text and "couldn't send alert to Telegram" in caplog.text


def test_send_soon_returns_at_once_and_still_delivers(factory):
    settings.save_section(
        factory,
        "notifications",
        {"discord_webhook": WEBHOOK, "telegram_bot_token": "", "telegram_chat_id": ""},
    )
    release, delivered = threading.Event(), []

    def handler(request):
        release.wait(5)  # Discord hangs until released
        delivered.append(json.loads(request.content)["content"])
        return httpx.Response(204)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    started = time.monotonic()
    notify.send_soon(factory, EVENT, http=http)
    notify.send_soon(factory, Fired(0, "Second", "", ""), http=http)
    assert time.monotonic() - started < 0.5
    assert delivered == []
    release.set()
    assert notify.wait_idle(5)
    assert delivered == ["**🔔 SPX went below 7,700.00**\nnow 7,690.00 · add hedges",
                         "**🔔 Second**"]  # in order  # fmt: skip


def test_a_send_that_breaks_never_stops_the_sender(factory, monkeypatch):
    settings.save_section(
        factory,
        "notifications",
        {"discord_webhook": WEBHOOK, "telegram_bot_token": "", "telegram_chat_id": ""},
    )
    sent, http = recorder()
    real = notify.send
    calls = []

    def flaky(session_factory, event, http=None, retry=False):
        calls.append(event.title)
        if event.title == "boom":
            raise RuntimeError("broken")
        return real(session_factory, event, http=http, retry=retry)

    monkeypatch.setattr(notify, "send", flaky)
    notify.send_soon(factory, Fired(0, "boom", "", ""), http=http)
    notify.send_soon(factory, EVENT, http=http)
    assert notify.wait_idle(5)
    assert calls == ["boom", EVENT.title] and len(sent) == 1


# --- Final review fixes ---


def test_a_long_alert_still_reaches_telegram_within_its_limit(factory):
    settings.save_section(
        factory,
        "notifications",
        {"discord_webhook": "", "telegram_bot_token": TOKEN, "telegram_chat_id": "42"},
    )
    sent = []

    def handler(request):
        body = json.loads(request.content)
        sent.append(body)
        return httpx.Response(400 if len(body["text"]) > 4096 else 200, json={"ok": True})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    event = Fired(0, "IC: stop not re-placed", "x" * 5000, "")
    assert notify.send(factory, event, http=http) == [("Telegram", None)]
    assert sent[0]["text"].startswith("<b>🔴 IC: stop not re-placed</b>\nxxx")


def test_a_bot_named_like_a_problem_keeps_its_routine_alerts_on_its_channel(factory):
    both(factory)
    bot_id = bot_on(factory, "telegram")
    with factory() as session:
        session.get(Bot, bot_id).name = "Failsafe paused IC"
        session.commit()
    _, http = recorder()
    done = Fired(0, "Failsafe paused IC: close filled", "Closed at 0.55.", "", bot_id,
                 routine=True)  # fmt: skip
    assert channels(notify.send(factory, done, http=http)) == ["Telegram"]


def test_a_trade_with_a_side_without_a_stop_makes_its_routine_alert_go_everywhere(factory):
    both(factory)
    bot_id = bot_on(factory, "discord")
    run_id = add_run(factory, bot_id)  # the call side's stop is pending: "stop NONE"
    _, http = recorder()
    done = Fired(0, "IC: profit action 1", "Put side: the stop moved.", "", bot_id, run_id,
                 routine=True)  # fmt: skip
    assert channels(notify.send(factory, done, http=http)) == ["Discord", "Telegram"]


def test_one_channel_breaking_never_skips_the_other(factory):
    both(factory)
    sent = []

    def handler(request):
        sent.append(str(request.url))
        if "discord" in str(request.url):
            return httpx.Response(500, json=["not", "a", "dict"])
        return httpx.Response(200, json={"ok": True})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    results = dict(notify.send(factory, EVENT, http=http))
    assert "500" in results["Discord"] and results["Telegram"] is None


def test_discord_never_pings_anyone(factory):
    settings.save_section(
        factory,
        "notifications",
        {"discord_webhook": WEBHOOK, "telegram_bot_token": "", "telegram_chat_id": ""},
    )
    sent, http = recorder()
    notify.send(factory, Fired(0, "@everyone look", "", ""), http=http)
    assert json.loads(sent[0].content)["allowed_mentions"] == {"parse": []}


def test_the_sender_retries_once_after_a_rate_limit_or_a_server_error(factory, monkeypatch):
    both(factory)
    waits, answers = [], {"discord": [429, 204], "telegram": [502, 200]}

    def handler(request):
        which = "discord" if "discord" in str(request.url) else "telegram"
        code = answers[which].pop(0)
        if code == 429:
            return httpx.Response(429, json={"retry_after": 60}, headers={"Retry-After": "60"})
        return httpx.Response(code, json={"ok": True})

    monkeypatch.setattr(notify, "_sleep", waits.append)
    http = httpx.Client(transport=httpx.MockTransport(handler))
    assert notify.send(factory, EVENT, http=http, retry=True) == [
        ("Discord", None), ("Telegram", None),
    ]  # fmt: skip
    assert waits == [notify.MAX_RETRY_WAIT, 1]  # 60 s asked, capped


def test_a_send_without_retry_reports_a_rate_limit(factory):
    both(factory)
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(429, json={})))
    assert all("429" in problem for _, problem in notify.send(factory, EVENT, http=http))
