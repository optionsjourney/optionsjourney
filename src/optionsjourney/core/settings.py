"""What can be set on the Settings page, and the check each value must pass
(docs/decisions/0021: configuration lives in the app, not in .env).

Every setting has a label, a one-line explanation (`help`, always shown) and a
fuller one (`tip`, in a tooltip), both in plain words, a default, and a
`clean` that turns what the user typed into the stored value or refuses it
with a message that says how to fix it. New settings are added here, in a
section, and the page shows them without further work."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_BACKUP_FOLDER = str(Path.home() / "Documents" / "OptionsJourney Backups")


class SettingError(ValueError):
    """One or more values the user entered can't be used. `problems` maps
    each setting's key to what's wrong with it."""

    def __init__(self, problems: Mapping[str, str]):
        self.problems = dict(problems)
        super().__init__(" ".join(self.problems.values()))


@dataclass(frozen=True)
class Setting:
    key: str
    label: str
    help: str  # one short line, shown under the label
    tip: str  # the full explanation, shown in the label's tooltip
    default: str
    choices: tuple[tuple[str, str], ...] = ()  # (value, label); empty for free text
    check: Callable[[str], str] | None = None  # turns typed text into the value, or raises
    secret: bool = False  # shown as a password field

    def clean(self, text: str | None) -> str:
        text = (text or "").strip()
        if self.choices:
            if text not in {value for value, _ in self.choices}:
                allowed = ", ".join(label for _, label in self.choices)
                raise SettingError({self.key: f"{self.label}: choose one of {allowed}."})
            return text
        return self.check(text) if self.check else text

    def default_text(self) -> str:
        return self.default


@dataclass(frozen=True)
class Section:
    key: str
    title: str
    help: str
    settings: tuple[Setting, ...] = field(default_factory=tuple)


def _folder(label: str, key: str) -> Callable[[str], str]:
    def check(text: str) -> str:
        if not text:
            raise SettingError({key: f"{label}: choose a folder."})
        path = Path(text).expanduser()
        if not path.is_absolute():
            raise SettingError({key: f"{label}: enter a full path, like {DEFAULT_BACKUP_FOLDER}."})
        return str(path)

    return check


SECTIONS: tuple[Section, ...] = (
    Section(
        key="backups",
        title="Backups",
        help=(
            "A copy of your journal (trades, edits, tags and dashboards), "
            "made while the app runs and compressed to save space."
        ),
        settings=(
            Setting(
                key="backup_folder",
                label="Backup folder",
                help="Where copies of your journal go.",
                tip=(
                    "Click Browse… to pick a folder. A folder inside iCloud Drive, Dropbox, "
                    "OneDrive or Google Drive keeps a copy off this computer. A USB drive works "
                    "if it's plugged in. Other files in the folder are never touched."
                ),
                default=DEFAULT_BACKUP_FOLDER,
                check=_folder("Backup folder", "backup_folder"),
            ),
            Setting(
                key="backup_frequency",
                label="Backups",
                help="How often a backup is made.",
                tip=(
                    "A backup missed while the app was off is made at the next start. With Off, "
                    "no backups are made on a schedule; Back up now still works."
                ),
                default="daily",
                choices=(("daily", "Every day"), ("weekly", "Every week"), ("off", "Off")),
            ),
            Setting(
                key="backup_keep",
                label="Backups to keep",
                help="How many backups to keep.",
                tip=(
                    "The newest ones are kept and older ones are deleted. Each is compressed, "
                    "typically to a tenth of the journal's size."
                ),
                default="30",
                choices=(("7", "7"), ("14", "14"), ("30", "30"), ("60", "60")),
            ),
        ),
    ),
)


def _optional(pattern: str, key: str, message: str) -> Callable[[str], str]:
    import re  # noqa: PLC0415

    compiled = re.compile(pattern)

    def check(text: str) -> str:
        if text and not compiled.match(text):
            raise SettingError({key: message})
        return text

    return check


SECTIONS = SECTIONS + (
    Section(
        key="notifications",
        title="Notifications",
        help=(
            "Alerts always show in the app. They can also be sent to Discord or Telegram, so you "
            "see them on your phone. Leave a field blank to turn that off."
        ),
        settings=(
            Setting(
                key="trade_popups",
                label="Trade popups",
                help="A popup on any page when a trade opens, rolls or closes.",
                tip=(
                    "It shows for a few seconds at the top right, with a chime, once your broker's "
                    "fill is synced (seconds for TastyTrade). Off: no popups. Either way, every "
                    "trade event is listed on the Alerts page."
                ),
                default="on",
                choices=(("on", "On"), ("off", "Off")),
            ),
            Setting(
                key="discord_webhook",
                label="Discord webhook URL",
                help="Sends alerts to a Discord channel.",
                tip=(
                    "In Discord: open a channel's settings, then Integrations → Webhooks → "
                    "New Webhook → Copy Webhook URL, and paste it here. Leave it blank to turn "
                    "Discord off."
                ),
                default="",
                secret=True,
                check=_optional(
                    r"^https://(discord|discordapp)\.com/api/webhooks/\S+$",
                    "discord_webhook",
                    "Discord webhook URL: paste the whole URL; it starts with https://discord.com/api/webhooks/.",
                ),
            ),
            Setting(
                key="telegram_bot_token",
                label="Telegram bot token",
                help="The Telegram bot that sends you alerts.",
                tip=(
                    "In Telegram: message @BotFather, send /newbot, and copy the token it gives "
                    "you. Leave it blank to turn Telegram off."
                ),
                default="",
                secret=True,
                check=_optional(
                    r"^\d+:[A-Za-z0-9_-]{30,}$",
                    "telegram_bot_token",
                    "Telegram bot token: copy it exactly as @BotFather shows it "
                    "(numbers, a colon, then letters).",
                ),
            ),
            Setting(
                key="telegram_chat_id",
                label="Telegram chat id",
                help="Your Telegram id, so the bot knows where to send.",
                tip=(
                    "Message @userinfobot in Telegram; it replies with your id. Then send your new "
                    "bot any message once, since a bot can only message people who've messaged it."
                ),
                default="",
                check=_optional(
                    r"^-?\d+$",
                    "telegram_chat_id",
                    "Telegram chat id: it's a number, like 123456789.",
                ),
            ),
        ),
    ),
)

SECTIONS = SECTIONS + (
    Section(
        key="computer",
        title="This computer",
        help="How OptionsJourney behaves on this computer when it runs from the menu bar.",
        settings=(
            Setting(
                key="keep_awake",
                label="Keep awake during market hours",
                help="Stops this computer sleeping while markets are open.",
                tip=(
                    "On weekdays from 9:15 to 4:15 New York time, this computer doesn't go to "
                    "sleep, so syncs (and later, bots) keep running."
                ),
                default="on",
                choices=(("on", "On"), ("off", "Off")),
            ),
            Setting(
                key="listen_on_network",
                label="Open from other devices",
                help="Lets your other computers and phone open the app.",
                tip=(
                    "On: other devices on your network (or your Tailscale network) can open "
                    "the app at this computer's address, followed by :2026. There's no login, so "
                    "anyone who can reach it can see your accounts and place orders: only use it "
                    "on networks you trust. Takes effect after Restart OptionsJourney in the OJ "
                    "menu."
                ),
                default="off",
                choices=(("off", "Off"), ("on", "On")),
            ),
            Setting(
                key="check_updates",
                label="Check for new versions",
                help="Once a day, asks GitHub if a newer version is out.",
                tip=(
                    "Once a day, asks GitHub whether a newer OptionsJourney is out. "
                    "Nothing about you is sent. If one is, the menu bar offers it."
                ),
                default="on",
                choices=(("on", "On"), ("off", "Off")),
            ),
        ),
    ),
)


def _whole(key: str, label: str, low: int, high: int) -> Callable[[str], str]:
    def check(text: str) -> str:
        if not text.isdigit() or not low <= int(text) <= high:
            raise SettingError({key: f"{label}: a whole number from {low} to {high}."})
        return str(int(text))

    return check


SECTIONS = SECTIONS + (
    Section(
        key="your_orders",
        title="Your orders",
        help="Limits on the orders you place yourself from the Trade page.",
        settings=(
            Setting(
                key="max_order_contracts",
                label="Largest order you can place",
                help="The most contracts in any one leg of an order you place.",
                tip=(
                    "An order with more contracts than this in any leg is refused before it "
                    "reaches your broker. Bots have their own limits on the Automation page."
                ),
                default="10",
                check=_whole("max_order_contracts", "Largest order you can place", 1, 1000),
            ),
        ),
    ),
)


SECTIONS = SECTIONS + (
    Section(
        key="market_calendar",
        title="Market calendar",
        help=(
            "Bots can skip, or only trade, FOMC days, CPI days and the last trading day of the "
            "month. The app reads those days from the Fed, NYSE and ForexFactory on its own."
        ),
        settings=(
            Setting(
                key="fred_api_key",
                label="FRED API key (optional)",
                help="Adds CPI days beyond the current week.",
                tip=(
                    "Without a key, CPI days come from ForexFactory's calendar for the current "
                    "week, which is all a bot needs on the day. With a free key from "
                    "fred.stlouisfed.org (My Account → API Keys), CPI days for the months ahead "
                    "come from the St. Louis Fed too. Leave it blank to go without."
                ),
                default="",
                secret=True,
                check=_optional(
                    r"^[A-Za-z0-9]{32}$",
                    "fred_api_key",
                    "FRED API key: paste the 32 letters and numbers FRED shows you.",
                ),
            ),
        ),
    ),
)


SECTIONS = SECTIONS + (
    Section(
        key="charts",
        title="Charts",
        help="The Charts page's News panel shows headlines for the ticker on the chart.",
        settings=(
            Setting(
                key="finnhub_api_key",
                label="Finnhub API key (optional)",
                help="Better-matched headlines in the Charts page's News panel.",
                tip=(
                    "Without a key, News reads Yahoo Finance's free feed, which sometimes "
                    "mixes in stories about other companies and could stop working. With a free "
                    "key from finnhub.io (Get free API key, then your Dashboard), headlines "
                    "come from Finnhub instead. Leave it blank to stay on Yahoo Finance."
                ),
                default="",
                secret=True,
                check=_optional(
                    r"^[A-Za-z0-9]{10,64}$",
                    "finnhub_api_key",
                    "Finnhub API key: paste the letters and numbers Finnhub shows you.",
                ),
            ),
        ),
    ),
)


SECTIONS = SECTIONS + (
    Section(
        key="sync",
        title="Syncing your trades",
        help=(
            "The app reads new trades from your brokers on its own, so your journal stays "
            "up to date. Sync now on the Transactions page still works any time."
        ),
        settings=(
            Setting(
                key="sync_market_hours",
                label="During market hours",
                help="How often to sync while the stock market is open.",
                tip=(
                    "Market hours are 9:30 to 4:00 New York time on trading days (to 1:00 on "
                    "early-close days). Every minute shows a new trade in the journal within "
                    "about a minute. Each sync asks your brokers for the last week's trades."
                ),
                default="1",
                choices=(
                    ("1", "Every minute"),
                    ("2", "Every 2 minutes"),
                    ("3", "Every 3 minutes"),
                    ("4", "Every 4 minutes"),
                    ("5", "Every 5 minutes"),
                    ("15", "Every 15 minutes"),
                    ("off", "Off"),
                ),
            ),
            Setting(
                key="sync_other_times",
                label="Other times",
                help="How often to sync when the stock market is closed.",
                tip=(
                    "Evenings, weekends and holidays: futures trades, and anything your broker "
                    "books after the close, such as expirations and assignments."
                ),
                default="60",
                choices=(("15", "Every 15 minutes"), ("60", "Every hour"), ("off", "Off")),
            ),
            Setting(
                key="sync_on_fill",
                label="When an order fills",
                help="Sync within seconds of a fill your broker reports.",
                tip=(
                    "TastyTrade reports fills on its account stream, which the app listens to. "
                    "Schwab reports them on its price stream while that's open (not in "
                    "development gateway mode). The timed syncs above catch anything missed."
                ),
                default="on",
                choices=(("on", "Sync"), ("off", "Off")),
            ),
            Setting(
                key="expiry_settle_on",  # services/expiry_settle.SWITCH_KEY (#147, decision 0109)
                label="Settle expirations at 4:15 PM",
                help="Close expiring trades the same day, at the close.",
                tip=(
                    "At 4:15 PM New York time on trading days the app syncs, then settles "
                    "trades whose options expired that day at the last price from your broker, "
                    "so 0DTE trades count in your P/L straight away. When the official close is "
                    "out, they're corrected to it, and you get an alert if your P/L changes by "
                    "$1 or more. The broker's own record replaces the settlement when it "
                    "arrives. Off: expiring trades wait for the broker's record, usually the "
                    "next day."
                ),
                default="0",
                choices=(("1", "On"), ("0", "Off")),
            ),
        ),
    ),
)

_BY_KEY = {setting.key: setting for section in SECTIONS for setting in section.settings}


def spec(key: str) -> Setting:
    return _BY_KEY[key]


def section(key: str) -> Section:
    return next(s for s in SECTIONS if s.key == key)


def clean_section(key: str, typed: Mapping[str, str]) -> dict[str, str]:
    """The values typed for a section, checked together so the user sees every
    problem at once. Settings not on the form keep their value. Raises
    SettingError."""
    values, problems = {}, {}
    for setting in section(key).settings:
        if setting.key not in typed:
            continue  # not on this form: it keeps its value
        try:
            values[setting.key] = setting.clean(typed.get(setting.key))
        except SettingError as e:
            problems.update(e.problems)
    if problems:
        raise SettingError(problems)
    return values
