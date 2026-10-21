"""The market's clock: US options trade on New York time."""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

MARKET_TZ = ZoneInfo("America/New_York")


def market_date(moment: datetime) -> date:
    """The trading day `moment` falls on, in New York."""
    return moment.astimezone(MARKET_TZ).date()


# Options stop trading at 16:00 New York time on their expiration day.
EXPIRY_CLOSE = time(16, 0)


def expiry_close(day: date) -> datetime:
    """When options expiring on `day` stop trading."""
    return datetime.combine(day, EXPIRY_CLOSE, tzinfo=MARKET_TZ)
