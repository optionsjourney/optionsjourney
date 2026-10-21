"""The Automation list's search (bots slice 20; Tracker2's automation.js matchesBotSearch).
Pure.

Every whitespace-separated word must match. A day word matches the bot's entry days (a
weekly bot's ticked days; any other schedule counts as Monday to Friday, as Tracker2's
empty list does), never the name; anything else is part of the bot's name, its
account's number or its underlying, ignoring case."""

from optionsjourney.bots.definition import BotDef

DAY_WORDS = {
    "mon": 1, "monday": 1,
    "tue": 2, "tues": 2, "tuesday": 2,
    "wed": 3, "weds": 3, "wednesday": 3,
    "thu": 4, "thur": 4, "thurs": 4, "thursday": 4,
    "fri": 5, "friday": 5,
}  # fmt: skip
WEEKDAYS = (1, 2, 3, 4, 5)


def text_of(bot: BotDef) -> str:
    return " ".join(filter(None, (bot.name, bot.account_number, bot.underlying))).lower()


def matches(bot: BotDef, query: str) -> bool:
    days = bot.entry_days if bot.frequency == "weekly" and bot.entry_days else WEEKDAYS
    text = text_of(bot)
    return all(
        DAY_WORDS[word] in days if word in DAY_WORDS else word in text
        for word in (query or "").lower().split()
    )
