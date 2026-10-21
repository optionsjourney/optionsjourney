"""A floating entry window (bots slice 10; Tracker2's floating.py, Option Omega's
"Use Floating Entry Time"): instead of fixed entry times, the bot is checked once a
minute from floating_start to floating_end (HH:MM New York, both included) and
enters at most once per window a day. Pure: the clock and bot_entry pass the minute
in, and keep the last check's reason themselves."""

from datetime import date

from optionsjourney.bots.definition import BotDef

CLOSE_SUFFIX = "close"
# A floating run in one of these ends the day's window (Tracker2's FLOATING_STOPS plus
# "pending", our in-progress claim, and "error": a refused order or a broken chain is
# never tried again every minute). "missed" and "skipped" let the next minute try.
STOPS = ("filled", "partial", "running", "pending", "preview", "error")


def window(bot: BotDef) -> tuple[str, str] | None:
    """(start, end) as HH:MM, or None when floating is off or unset."""
    if not bot.use_floating_entry or not bot.floating_start or not bot.floating_end:
        return None
    return bot.floating_start, bot.floating_end


def in_window(bot: BotDef, hhmm: str) -> bool:
    found = window(bot)
    return bool(found) and found[0] <= hhmm <= found[1]


def is_final_minute(bot: BotDef, hhmm: str) -> bool:
    found = window(bot)
    return bool(found) and hhmm == found[1]


def label(bot: BotDef) -> str:
    start, end = window(bot) or ("", "")
    return f"{start}–{end}"


def prefix(bot_id: int, day: date) -> str:
    """What every floating run of the bot that day starts with."""
    return f"bot-{bot_id}-float-{day:%Y%m%d}-"


def minute_key(bot_id: int, day: date, hhmm: str) -> str:
    """One claim per bot, day and minute: the same minute can't trade twice."""
    return prefix(bot_id, day) + hhmm.replace(":", "")


def close_key(bot_id: int, day: date) -> str:
    """The one skipped row a window that never tried anything leaves behind."""
    return prefix(bot_id, day) + CLOSE_SUFFIX
