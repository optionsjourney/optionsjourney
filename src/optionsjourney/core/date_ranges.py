"""The Journal's date presets (#284; Tracker2's daterangepicker ranges). Pure: a preset
and today's New York date in, the first and last opening day out. A preset is kept by
name, so a saved view rolls on with the calendar."""

from datetime import date, timedelta

RANGES = {
    "yesterday": "Yesterday",
    "7d": "Last 7 Days",
    "14d": "Last 14 Days",
    "30d": "Last 30 Days",
    "month": "This Month",
    "last_month": "Last Month",
    "ytd": "Year to Date",
}


def window(key: str, today: date) -> tuple[date, date]:
    """The days a preset covers, both ends included."""
    if key == "yesterday":
        day = today - timedelta(days=1)
        return day, day
    if key in ("7d", "14d", "30d"):
        return today - timedelta(days=int(key[:-1]) - 1), today
    if key == "month":
        return today.replace(day=1), today
    if key == "last_month":
        end = today.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end
    if key == "ytd":
        return today.replace(month=1, day=1), today
    raise KeyError(key)
