"""Sending alerts beyond the app (#28): Discord through a webhook, Telegram
through a bot, when the user has filled them in under Settings ->
Notifications. A failed send is reported and logged; it never stops the
app, and the alert still shows in the app.

Bots slice 9: each alert is laid out for a phone's lock screen
(core/alert_format), with a bot's trade lines (services/bot_alerts.context). A
routine alert from a bot goes to the bot's channel; everything else, and any
alert that reads as a safety problem, goes to every channel (decision 0035).
send_soon queues an alert for one background sender, so a bot never waits."""

import logging
import queue
import threading
import time

import httpx

from optionsjourney.core import alert_format
from optionsjourney.services import bot_alerts, settings
from optionsjourney.services.user_alerts import Fired

logger = logging.getLogger(__name__)
TIMEOUT_S = 10
TEST = Fired(
    0, "Test from OptionsJourney", "If you can read this, alerts will reach you here.", "/alerts"
)


def send(
    session_factory, event: Fired, http: httpx.Client | None = None, retry: bool = False
) -> list[tuple[str, str | None]]:
    """Send to the channels this alert goes to (every one that's switched on,
    unless it's a bot's routine alert). Returns (channel, problem or None) for each.
    Each channel on its own: one breaking never skips the other. `retry` (the
    background sender's) tries once more after a rate limit or a server error."""
    values = settings.get_all(session_factory)
    configured = []
    if values.get("discord_webhook"):
        configured.append("Discord")
    if values.get("telegram_bot_token") and values.get("telegram_chat_id"):
        configured.append("Telegram")
    if not configured:
        return []
    bot = bot_alerts.bot_view(session_factory, event.bot_id)
    lines = _context(session_factory, event, bot)
    # What happened (never the bot's own name), the detail and the trade's lines: a
    # side with "stop NONE" makes even a routine alert go everywhere.
    words = "\n".join(
        [event.detail or ""] + [text for label, text in lines if text and label != "Trade"]
    )  # the Trade line holds the bot's name
    safety = alert_format.is_safety(
        alert_format.what_happened(event.title, bot.name if bot else None), words
    )
    targets = alert_format.channels_for(
        configured, bot.channel if bot else None, event.routine, safety
    )
    out = _layout(event, bot, safety, lines)
    http = http or _client()
    results = []
    for channel, deliver in (("Discord", _discord), ("Telegram", _telegram)):
        if channel not in targets:
            continue
        try:
            problem = deliver(http, values, out, retry)
        except Exception as e:  # noqa: BLE001 -- the other channel still goes
            problem = f"failed ({e.__class__.__name__})"
        results.append((channel, problem))
    for channel, problem in results:
        if problem:
            logger.warning("couldn't send alert to %s: %s", channel, problem)
    return results


def _discord(http, values, out, retry) -> str | None:
    # allowed_mentions: a bot's name or detail can never ping @everyone
    body = {"content": alert_format.to_discord(out), "allowed_mentions": {"parse": []}}
    return _post(http, values["discord_webhook"], body, retry)


def _telegram(http, values, out, retry) -> str | None:
    url = f"https://api.telegram.org/bot{values['telegram_bot_token']}/sendMessage"
    chat = values["telegram_chat_id"]
    html = {"chat_id": chat, "text": alert_format.to_telegram(out), "parse_mode": "HTML"}
    problem = _post(http, url, html, retry)
    if problem and problem.startswith("answered 400"):
        # The formatting was refused: the same words as plain text, never lost.
        problem = _post(http, url, {"chat_id": chat, "text": alert_format.to_plain(out)}, retry)
    return problem


def _context(session_factory, event: Fired, bot) -> list:
    if bot is None:
        return []
    try:
        return bot_alerts.context(session_factory, bot, event.run_id)
    except Exception:  # noqa: BLE001 -- the alert goes without them
        logger.exception("alert context failed")
        return []


def _layout(event: Fired, bot, safety: bool, lines) -> alert_format.Layout:
    """The lock-screen layout, or the plain title and detail should it fail
    (alerts._render): an alert is never lost to its layout."""
    try:
        icon = alert_format.icon_for(
            bot=bot is not None and event.id == 0, routine=event.routine, safety=safety
        )
        return alert_format.layout(
            event.title, event.detail, icon=icon, bot_name=bot.name if bot else None,
            alert_name=bot.alert_name if bot else None, context=lines,
        )  # fmt: skip
    except Exception:  # noqa: BLE001
        logger.exception("alert layout failed; sending the plain text")
        return alert_format.Layout(event.title, event.detail or "")


_http: httpx.Client | None = None
_http_lock = threading.Lock()
MAX_RETRY_WAIT = 10  # seconds: a rate limit asking longer waits this long, then once more
_sleep = time.sleep


def _client() -> httpx.Client:
    """One client for every send (its connections reused), made on first use."""
    global _http  # noqa: PLW0603 -- one per process
    with _http_lock:
        if _http is None:
            _http = httpx.Client(timeout=TIMEOUT_S)
        return _http


def _wait_for(response: httpx.Response) -> float:
    """How long a 429 asks to wait (Discord's retry_after, Telegram's
    parameters.retry_after, or Retry-After), capped."""
    asked = None
    try:
        data = response.json()
        if isinstance(data, dict):
            asked = data.get("retry_after") or (data.get("parameters") or {}).get("retry_after")
    except ValueError:
        pass
    if asked is None:
        asked = response.headers.get("Retry-After")
    try:
        return min(max(float(asked), 0), MAX_RETRY_WAIT)
    except TypeError, ValueError:
        return 1


def _post(http: httpx.Client, url: str, body: dict, retry: bool = False) -> str | None:
    """None, or what went wrong, in words that never hold the address (Telegram's
    holds the bot token). With `retry`, once more after a 429 (waiting as asked,
    capped) or a 5xx (after a second)."""
    try:
        response = http.post(url, json=body)
    except httpx.HTTPError as e:
        return f"couldn't reach it ({e.__class__.__name__})"
    if retry and (response.status_code == 429 or response.status_code >= 500):
        _sleep(_wait_for(response) if response.status_code == 429 else 1)
        return _post(http, url, body)
    if response.status_code >= 300:
        reason = ""
        try:
            data = response.json()
            if isinstance(data, dict):
                reason = str(data.get("message") or data.get("description") or "")
        except ValueError:
            pass
        return f"answered {response.status_code} {reason}".strip()
    return None


# --- Sending off the caller's thread ---------------------------------------------------

_queue: queue.Queue = queue.Queue()  # never full: an alert is never dropped
_sender: threading.Thread | None = None
_sender_lock = threading.Lock()


def send_soon(session_factory, event: Fired, http: httpx.Client | None = None) -> None:
    """Queue the alert for the background sender and return at once. Alerts go in
    the order they were queued, each send with its timeout."""
    global _sender  # noqa: PLW0603 -- one sender per process
    _queue.put((session_factory, event, http))
    with _sender_lock:
        if _sender is None or not _sender.is_alive():
            _sender = threading.Thread(target=_run, name="alert-sender", daemon=True)
            _sender.start()


def sender(session_factory):
    """A bot's `notify`: queue each alert (bots slice 9)."""
    return lambda event: send_soon(session_factory, event)


def _run() -> None:
    while True:
        session_factory, event, http = _queue.get()
        try:
            send(session_factory, event, http=http, retry=True)
        except Exception as e:  # noqa: BLE001 -- the sender never stops
            logger.error("couldn't send an alert (%s)", e.__class__.__name__)
        finally:
            _queue.task_done()


def wait_idle(timeout: float) -> bool:
    """True once every queued alert has been handled (for tests and shutdown)."""
    deadline = time.monotonic() + timeout
    while _queue.unfinished_tasks:
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)
    return True
