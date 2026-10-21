from datetime import UTC, datetime, timedelta

import pytest

from optionsjourney.services import bot_audit, bot_entry, bot_loops
from tests.services.bot_fakes import FakeChain, FakeHub, FakeTasty

MORNING = datetime(2026, 10, 13, 13, 0, tzinfo=UTC)  # Tue 09:00 New York
START = MORNING - timedelta(hours=1)


@pytest.fixture(autouse=True)
def fresh():
    bot_audit.reset()
    yield
    bot_audit.reset()


def all_running(now):
    for name in bot_audit.LIMITS:
        bot_audit.started(name, START)
        bot_audit.beat(name, now)


def deps_at(when, sent):
    return bot_entry.BotDeps(
        None, lambda: [FakeTasty()], FakeHub(), FakeChain(), notify=sent.append,
        sleep=lambda s: None, now=lambda: when,
    )  # fmt: skip


def test_not_checked_when_the_loops_are_not_running():
    result = bot_audit.audit(MORNING)
    assert (result.checked, result.ok, result.problems) == (False, True, [])


def test_all_running_is_ok():
    all_running(MORNING - timedelta(seconds=5))
    result = bot_audit.audit(MORNING)
    assert result.checked and result.ok and result.problems == []
    assert result.last["bot-clock"] == MORNING - timedelta(seconds=5)


def test_each_stopped_loop_is_named():
    all_running(MORNING)
    later = MORNING + timedelta(seconds=61)
    problems = {p.loop: p.text for p in bot_audit.audit(later).problems}
    assert list(problems) == ["bot-clock"]  # the watch has 90 s, the monitor 180 s
    assert problems["bot-clock"] == "The bot clock has stopped since 09:00: no bot will fire."
    later = MORNING + timedelta(seconds=91)
    problems = {p.loop: p.text for p in bot_audit.audit(later).problems}
    assert list(problems) == ["bot-clock", "bot-watch"]
    assert "targets won't fire" in problems["bot-watch"]
    later = MORNING + timedelta(seconds=181)
    assert "stops and closes aren't being watched" in bot_audit.audit(later).problems[2].text
    assert not bot_audit.audit(later).ok


def test_a_loop_that_never_passed_counts_from_its_start():
    bot_audit.started("bot-clock", START)
    [problem] = bot_audit.audit(START + timedelta(seconds=61)).problems
    assert "since the app started" in problem.text
    assert bot_audit.audit(START + timedelta(seconds=59)).ok


def every_minute(start, minutes, sent, beat=None):
    """Audit passes a minute apart, as the bot-audit loop runs them."""
    for n in range(minutes):
        at = start + timedelta(minutes=n)
        if beat:
            beat(at)
        bot_audit.audit_pass(deps_at(at, sent))


def test_a_stopped_clock_alerts_once_a_day_from_0920():
    bot_audit.started("bot-clock", START)
    bot_audit.beat("bot-clock", START)
    sent = []
    every_minute(MORNING + timedelta(minutes=15), 5, sent)  # 09:15 to 09:19
    assert sent == []
    every_minute(MORNING + timedelta(minutes=20), 1, sent)  # 09:20: stale once, not yet
    assert sent == []
    every_minute(MORNING + timedelta(minutes=21), 120, sent)  # stale twice in a row: alert
    assert [e.title for e in sent] == ["Bots will NOT fire"]
    assert "no bot will fire" in sent[0].detail and "restart" in sent[0].detail
    every_minute(MORNING + timedelta(days=1, minutes=20), 3, sent)  # Wednesday (after a gap)
    assert len(sent) == 2


def test_a_loop_stale_on_one_pass_only_does_not_alert():
    bot_audit.started("bot-clock", START)
    sent = []
    at = MORNING + timedelta(minutes=30)
    bot_audit.beat("bot-clock", at - timedelta(seconds=70))
    bot_audit.audit_pass(deps_at(at, sent))  # stale: 70 s
    bot_audit.beat("bot-clock", at + timedelta(seconds=30))
    bot_audit.audit_pass(deps_at(at + timedelta(minutes=1), sent))  # running again
    bot_audit.beat("bot-clock", at + timedelta(seconds=40))
    bot_audit.audit_pass(deps_at(at + timedelta(minutes=2), sent))
    assert sent == []


def test_no_alert_just_after_the_computer_wakes():
    """Asleep, every loop goes quiet together: the first passes after waking would
    all read stale. The audit skips a round when its own gap shows it was asleep."""
    bot_audit.started("bot-clock", START)

    def clock_runs(at):
        bot_audit.beat("bot-clock", at)

    sent = []
    every_minute(MORNING + timedelta(minutes=30), 3, sent, clock_runs)  # 09:30-09:32
    woke = MORNING + timedelta(hours=2)  # asleep until 11:00
    bot_audit.audit_pass(deps_at(woke, sent))  # everything looks stale: skipped
    every_minute(woke + timedelta(minutes=1), 3, sent, clock_runs)  # the clock is back
    assert sent == []


def test_no_audit_alerts_on_a_holiday_or_after_the_close():
    bot_audit.started("bot-clock", START)
    sent = []
    thanksgiving = datetime(2026, 11, 26, 15, 0, tzinfo=UTC)
    bot_audit.audit_pass(deps_at(thanksgiving, sent))
    bot_audit.audit_pass(deps_at(datetime(2026, 10, 13, 20, 5, tzinfo=UTC), sent))  # 16:05
    assert sent == []


def test_the_audit_pass_never_raises(monkeypatch):
    def broken(now):
        raise RuntimeError("boom")

    monkeypatch.setattr(bot_audit, "audit", broken)
    bot_audit.audit_pass(deps_at(MORNING, []))


def test_the_loop_beats_only_on_a_good_pass(monkeypatch):
    monkeypatch.setattr(bot_loops, "_utcnow", lambda: MORNING)
    bot_loops._pass("bot-clock", lambda: None)
    assert bot_audit.audit(MORNING).last.get("bot-clock") is None  # not started: not watched
    bot_audit.started("bot-clock", START)

    def broken():
        raise RuntimeError("boom")

    bot_loops._pass("bot-clock", broken)
    assert bot_audit.audit(MORNING).last["bot-clock"] == MORNING  # the good pass before
    monkeypatch.setattr(bot_loops, "_utcnow", lambda: MORNING + timedelta(seconds=30))
    bot_loops._pass("bot-clock", broken)
    assert bot_audit.audit(MORNING + timedelta(seconds=30)).last["bot-clock"] == MORNING
