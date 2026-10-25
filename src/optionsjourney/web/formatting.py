"""Template filters for showing numbers and times."""

from datetime import UTC, datetime
from decimal import Decimal

from flask import Flask

from optionsjourney.core.alerts import schwab_login_alert
from optionsjourney.core.market import MARKET_TZ


def money(value: Decimal | None) -> str:
    if value is None:
        return "—"
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def dollars(value: Decimal | None) -> str:
    """Whole dollars: "$1,234"."""
    if value is None:
        return "—"
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.0f}"


def percent(value: Decimal | None, places: int = 0) -> str:
    """ "62%", "-12.3%"; a value that rounds to zero never shows as "-0%"."""
    if value is None:
        return "—"
    rounded = round(value, places)
    return f"{abs(rounded) if rounded == 0 else rounded:.{places}f}%"


def signed_money(value: Decimal | None) -> str:
    """P/L with its sign spelled out, whole dollars: "+$150", "-$1,234"."""
    if value is None:
        return "—"
    sign = "-" if value < 0 else "+" if value > 0 else ""
    return f"{sign}${abs(value):,.0f}"


def short_money(value: Decimal | None) -> str:
    """Abbreviated for a phone's calendar cell (#303): "$150", "-$1.2k", "$12k". Only a
    loss gets a sign, and $10k and up drop the decimal: the cell has no more room."""
    if value is None:
        return signed_money(value)
    sign = "-" if value < 0 else ""
    size = abs(value)
    if size >= Decimal("9950"):
        return f"{sign}${size / 1000:,.0f}k"
    return f"{sign}${size / 1000:,.1f}k" if size >= Decimal("999.5") else f"{sign}${size:,.0f}"


def number(value: Decimal | None) -> str:
    """A quantity or price without trailing zeros: 1, 2.5, 0.05."""
    if value is None:
        return "—"
    text = f"{value:,f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def premium(value: Decimal | None) -> str:
    """A per-spread price: "2.50 cr" received, "1.20 db" paid."""
    if value is None:
        return "—"
    return f"{abs(value):,.2f} {'cr' if value >= 0 else 'db'}"


def market_time(value: datetime | None) -> str:
    """e.g. "10/09/26 09:32 ET"."""
    if value is None:
        return "—"
    return value.astimezone(MARKET_TZ).strftime("%m/%d/%y %H:%M ET")


def market_day(value: datetime) -> str:
    """The New York trading day, "2026-10-16"."""
    return value.astimezone(MARKET_TZ).date().isoformat()


def market_date_label(value: datetime) -> str:
    """The New York trading day, "Mar 1, 2026" (no %-d: it isn't on Windows)."""
    day = value.astimezone(MARKET_TZ).date()
    return f"{day:%b} {day.day}, {day.year}"


def login_due(expires_at: datetime | None) -> str:
    """ "problem" once the Schwab login has run out, "warning" in the day
    before, else "": the same rule as the alert."""
    alert = schwab_login_alert(expires_at, datetime.now(UTC))
    return alert.level if alert else ""


def rich(value) -> bool:
    """A tip written as a template block ({% set %}...{% endset %}: markup, such as a
    table), not plain text (#133)."""
    return hasattr(value, "__html__")


def register(app: Flask) -> None:
    app.add_template_test(rich)
    app.add_template_filter(money)
    app.add_template_filter(signed_money)
    app.add_template_filter(dollars)
    app.add_template_filter(percent)
    app.add_template_filter(short_money)
    app.add_template_filter(number)
    app.add_template_filter(premium)
    app.add_template_filter(market_time)
    app.add_template_filter(market_date_label)
    app.add_template_filter(market_day)
    app.add_template_filter(login_due)
