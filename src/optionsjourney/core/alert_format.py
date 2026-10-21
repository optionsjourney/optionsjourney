"""How an alert sent to Discord or Telegram reads on a phone's lock screen, and
where it goes (spec: bots slice 9; Tracker2's services/bots/alert_format.py and
alerts.post_alert). Pure.

    🔴 stop not re-placed · Iron fly 1       line 1: icon, what happened, alert name
    The stop was refused. Check your broker. line 2: the detail

    Trade: Iron fly 1 · ••6789 · run 42
    Legs: −1 SPX 13 Oct 5700P · +1 5675P
    Protection: stop NONE

A lock screen shows plain text, about four lines, so lines 1-2 carry the alert.

Where it goes (decision 0035): a bot's channel choice applies only to an alert
marked routine, and never to one that reads as a safety problem (is_safety). A
chosen channel that isn't set up means every channel that is.
"""

import html
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

LINE1_MAX = 40  # alert_format.LINE1_MAX
DISCORD_MAX = 1900  # alerts._post_discord
TELEGRAM_MAX = 4000  # Telegram refuses more than 4,096 characters (counted once parsed)
_TRUNCATED = "\n…(truncated)"
_MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# A bot's choice for its routine alerts (bots.alert_channel)
ALERT_CHANNELS = {"all": "Every channel", "discord": "Discord only", "telegram": "Telegram only"}
_CHANNEL_NAMES = {"discord": "Discord", "telegram": "Telegram"}

# Words that mean money may be at risk or something needs you. Any of them in an
# alert's title or detail sends it everywhere, whatever a call site said. Erring
# toward too many: a routine alert sent everywhere costs nothing.
_SAFETY = re.compile(
    r"no stop|stop none|unprotected|re-placed|not placed|not sent|not set|not done|"
    r"not moved|not fully|not sold|unclear|freeze|frozen|held back|hold|fail|"
    r"couldn't|could not|can't|cannot|didn't|wasn't|weren't|isn't|missed|missing|"
    r"error|broke|cut off|refused|check your broker|needs you|uneven|left open|"
    r"dropped|waiting|paused|stale|skipped|unknown|crash",
    re.IGNORECASE,
)


def is_safety(title: str, detail: str = "") -> bool:
    """True for an alert that must reach every channel: a side with no stop, an
    unclear order, Freeze, a held-back side, a failed close, and the like."""
    return bool(_SAFETY.search(f"{title}\n{detail}"))


def what_happened(title: str, bot_name: str | None) -> str:
    """A bot alert's title without its "<bot name>: ", so the bot's own name (a
    "Failsafe IC") never reads as a safety word."""
    if bot_name and title.startswith(f"{bot_name}: "):
        return title[len(bot_name) + 2 :]
    return title


def channels_for(
    configured: list[str], bot_channel: str | None, routine: bool, safety: bool
) -> list[str]:
    """Which of the configured channels ("Discord", "Telegram") an alert goes to."""
    if not routine or safety or bot_channel not in _CHANNEL_NAMES:
        return list(configured)
    wanted = _CHANNEL_NAMES[bot_channel]
    return [wanted] if wanted in configured else list(configured)


def icon_for(*, bot: bool, routine: bool, safety: bool) -> str:
    if safety:
        return "🔴"
    if not bot:
        return "🔔"
    return "✅" if routine else "⚠️"


@dataclass
class Layout:
    line1: str
    line2: str = ""
    details: list = field(default_factory=list)  # (label or None, text)


def _cut(text: str) -> str:
    return text if len(text) <= LINE1_MAX else text[: LINE1_MAX - 1].rstrip() + "…"


def layout(
    title: str,
    detail: str,
    *,
    icon: str,
    bot_name: str | None = None,
    alert_name: str | None = None,
    context: Iterable[tuple[str, str | None]] = (),
) -> Layout:
    """A bot's "<bot name>: <what>" becomes "<what> · <alert name>"; a line 1 that
    had to be cut keeps the full title as the body's first line."""
    what = what_happened(title, bot_name)
    name = alert_name or bot_name
    full = f"{icon} {what}" + (f" · {name}" if name else "")
    out = Layout(_cut(full), (detail or "").strip())
    if len(full) > LINE1_MAX:
        out.details.append((None, title))
    out.details += [(label, text) for label, text in context if text]
    return out


def _body(lines: list[str]) -> str:
    return "\n".join(lines)


def _plain_lines(out: Layout) -> list[str]:
    lines = [out.line1] + ([out.line2] if out.line2 else [])
    if out.details:
        lines.append("")
        lines += [f"{label}: {text}" if label else text for label, text in out.details]
    return lines


def fit_telegram(out: Layout) -> Layout:
    """The layout cut to Telegram's limit, measured on the plain text (what Telegram
    counts once it has read the HTML), before any escaping, so no entity is split.
    Line 1 always stays; the detail and then the body lines are cut from the end."""
    budget = TELEGRAM_MAX - len(_TRUNCATED)
    if len(_body(_plain_lines(out))) <= TELEGRAM_MAX:
        return out
    room = budget - len(out.line1) - 1
    line2 = out.line2[: max(room, 0)]
    room -= len(line2) + 2  # the newline and the blank line
    details = []
    for label, text in out.details:
        line = f"{label}: {text}" if label else text
        if room - len(line) - 1 < 0:
            break
        details.append((label, text))
        room -= len(line) + 1
    return Layout(out.line1, line2 + _TRUNCATED if len(line2) < len(out.line2) or
                  len(details) < len(out.details) else line2, details)  # fmt: skip


def to_plain(out: Layout) -> str:
    """Plain text, within Telegram's limit."""
    return _body(_plain_lines(fit_telegram(out)))


def to_discord(out: Layout) -> str:
    lines = [f"**{out.line1}**"] + ([out.line2] if out.line2 else [])
    if out.details:
        lines.append("")
        lines += [f"**{label}:** {text}" if label else text for label, text in out.details]
    text = _body(lines)
    if len(text) > DISCORD_MAX:
        text = text[: DISCORD_MAX - len(_TRUNCATED)] + _TRUNCATED
    return text


def to_telegram(out: Layout) -> str:
    """Telegram HTML (parse_mode=HTML): line 1 and the labels bold, the text escaped."""

    def esc(text: str) -> str:
        return html.escape(text, quote=False)

    out = fit_telegram(out)

    lines = [f"<b>{esc(out.line1)}</b>"] + ([esc(out.line2)] if out.line2 else [])
    if out.details:
        lines.append("")
        lines += [
            f"<b>{esc(label)}:</b> {esc(text)}" if label else esc(text)
            for label, text in out.details
        ]
    return _body(lines)


def _num(value: Decimal) -> str:
    text = f"{Decimal(value):f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _price(value: Decimal) -> str:
    return f"{Decimal(value):.2f}"


def legs_line(legs: Iterable[tuple[int, str, date | None, Decimal, str]]) -> str:
    """(signed contracts, underlying, expiration, strike, "P"/"C") → "−2 SPX 13 Oct
    5700P · +2 5675P" (alert_format._format_legs): the date once per expiration."""
    out, previous = [], None
    for qty, symbol, expiration, strike, kind in legs:
        sign = "−" if qty < 0 else "+"
        when = f"{expiration.day} {_MONTHS[expiration.month - 1]}" if expiration else ""
        if (symbol, when) != previous:
            out.append(" ".join(p for p in (f"{sign}{abs(qty)}", symbol, when,
                                              f"{_num(strike)}{kind}") if p))  # fmt: skip
            previous = (symbol, when)
        else:
            out.append(f"{sign}{abs(qty)} {_num(strike)}{kind}")
    return " · ".join(out)


@dataclass(frozen=True)
class SideProtection:
    name: str
    stop: Decimal | None  # a working stop's trigger; None: no stop working
    target: Decimal | None  # the target's price, working or watched
    target_armed: bool = False  # watched by the app rather than resting at the broker
    closed: bool = False


def protection_line(sides: list[SideProtection]) -> str:
    """Each side's stop and target (alert_format._protection_line); "stop NONE"
    for a side whose stop isn't working. One side isn't named."""
    named = len(sides) > 1
    parts = []
    for side in sides:
        prefix = f"{side.name} " if named else ""
        if side.closed:
            parts.append(f"{prefix}closed")
            continue
        parts.append(f"{prefix}stop {_price(side.stop) if side.stop is not None else 'NONE'}")
        if side.target is not None:
            parts.append(
                f"target {_price(side.target)}" + (" (armed)" if side.target_armed else "")
            )
    return " · ".join(parts)
