"""The schedule check: are the bot loops still finishing their passes? (spec:
bots slice 8, section 7; Tracker2's schedule_audit.py).

Tracker2 compares its scheduler's jobs with the armed bots. Here the clock reads
the armed bots from the database every pass, so there is no job list to drift;
what can fail is a loop that stops finishing passes (one that keeps raising, or
a call that hangs). bot_loops records each loop's start and each good pass;
`audit` says which loops have gone quiet, and `audit_pass` (its own loop, every
minute) alerts each problem once a day from 09:20 New York to the close on
market days. Nothing here raises."""

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta

from optionsjourney.core.market import MARKET_TZ
from optionsjourney.core.sessions import session_close
from optionsjourney.services import bot_entry

logger = logging.getLogger(__name__)

# How long each loop may go without a good pass (it runs every 10 s, 2 s, 30 s). The
# watch waits on the exits' lock while a monitor pass reads the broker, so it gets
# more than its 2 s cadence suggests: a slow broker read isn't a stopped loop.
LIMITS = {
    "bot-clock": timedelta(seconds=60),
    "bot-watch": timedelta(seconds=90),
    "bot-monitor": timedelta(seconds=180),
}
_WORDS = {
    "bot-clock": ("Bots will NOT fire", "The bot clock has stopped{since}: no bot will fire."),
    "bot-watch": ("Bot profit targets aren't being watched",
                  "The profit-target watch has stopped{since}: targets won't fire."),
    "bot-monitor": ("Bot exits aren't being watched", "The exit monitor has stopped{since}: "
                    "stops and closes aren't being watched."),
}  # fmt: skip
PREOPEN = time(9, 20)  # schedule_audit.register_preopen_audit
AUDIT_EVERY = 60  # seconds between audit passes (bot_loops' bot-audit loop)
# A gap this much longer than AUDIT_EVERY means the audit itself wasn't running (the
# computer slept): every loop reads stale for the same reason, so skip that round.
WOKE_GAP = timedelta(seconds=3 * AUDIT_EVERY)
STALE_ROUNDS = 2  # a loop must read stopped on this many passes in a row to alert

_lock = threading.Lock()
_started: dict[str, datetime] = {}
_beats: dict[str, datetime] = {}
_alerted: set[tuple] = set()  # (New York day, loop) already alerted
_stale: dict[str, int] = {}  # loop -> audit passes in a row it has read stopped
_last_audit: list[datetime] = []  # when the last audit pass ran (at most one entry)


@dataclass(frozen=True)
class Problem:
    loop: str
    title: str
    text: str


@dataclass(frozen=True)
class Audit:
    checked: bool  # False: the loops don't run in this process, so nothing can be said
    ok: bool
    problems: list[Problem] = field(default_factory=list)
    last: dict[str, datetime | None] = field(default_factory=dict)  # each loop's last good pass


def started(name: str, now: datetime) -> None:
    with _lock:
        _started[name] = now


def beat(name: str, now: datetime) -> None:
    with _lock:
        _beats[name] = now


def reset() -> None:
    """Forget everything (tests)."""
    with _lock:
        _started.clear()
        _beats.clear()
        _alerted.clear()
        _stale.clear()
        _last_audit.clear()


def audit(now: datetime) -> Audit:
    with _lock:
        watched = {name: at for name, at in _started.items() if name in LIMITS}
        beats = dict(_beats)
    if not watched:
        return Audit(False, True)
    problems, last = [], {}
    for name in LIMITS:
        if name not in watched:
            continue
        last[name] = beats.get(name)
        since = beats.get(name) or watched[name]
        if now - since <= LIMITS[name]:
            continue
        words = (
            f" since {beats[name].astimezone(MARKET_TZ):%H:%M}"
            if name in beats
            else " since the app started"
        )
        title, text = _WORDS[name]
        problems.append(Problem(name, title, text.format(since=words)))
    return Audit(True, not problems, problems, last)


def audit_pass(deps) -> None:
    """Alert each problem once a New York day, from 09:20 to the close on market days,
    once it has read stopped on two passes in a row. A pass that comes long after the
    last one (the computer slept) only notes the time."""
    try:
        now = deps.now()
        with _lock:
            previous = _last_audit[0] if _last_audit else None
            _last_audit[:] = [now]
            if previous is not None and now - previous > WOKE_GAP:
                _stale.clear()  # just woke: the loops haven't had a chance to run yet
                return
        local = now.astimezone(MARKET_TZ)
        close = session_close(local.date())
        if close is None or not PREOPEN <= local.time() < close:
            return
        problems = audit(now).problems
        with _lock:
            stopped = {problem.loop for problem in problems}
            for loop in list(_stale):
                if loop not in stopped:
                    del _stale[loop]
            for loop in stopped:
                _stale[loop] = _stale.get(loop, 0) + 1
        for problem in problems:
            key = (local.date(), problem.loop)
            with _lock:
                if _stale.get(problem.loop, 0) < STALE_ROUNDS or key in _alerted:
                    continue
                _alerted.add(key)
            bot_entry.alert(deps, problem.title,
                            f"{problem.text} Check the app's log, then restart it.",
                            "/automation")  # fmt: skip
    except Exception:  # noqa: BLE001 -- the check never takes anything down with it
        logger.exception("the bot schedule check failed")
