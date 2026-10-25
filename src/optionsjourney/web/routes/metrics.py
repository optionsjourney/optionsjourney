"""The Metrics tab (#301, decision 0119): the P/L calendar with its day, week and month
drawer, and the trading metrics. Both use the journal's filters and dashboards."""

from datetime import UTC, date, datetime

from flask import Blueprint, abort, render_template, request, url_for

from optionsjourney.bots.validation import BROKER_NAMES
from optionsjourney.core import date_ranges
from optionsjourney.core.market import market_date
from optionsjourney.services import economic_events, journal
from optionsjourney.services.journal import Filters
from optionsjourney.web import services
from optionsjourney.web.routes.journal import dashboard_for

bp = Blueprint("metrics", __name__)


def _now():
    return datetime.now(UTC)


def _view(filters: Filters):
    return journal.load_journal(
        services().session_factory, filters, cache=services().positions_cache
    )


def _page(template: str, endpoint: str, view, filters, dashboard, **extra):
    return render_template(
        template,
        page_endpoint=endpoint,
        view=view,
        filters=filters,
        dashboard=dashboard,
        periods=journal.PERIODS,
        date_ranges=date_ranges.RANGES,
        broker_names=BROKER_NAMES,
        today=market_date(_now()),
        statuses=journal.STATUSES,
        uncategorized=journal.UNCATEGORIZED,
        **extra,
    )


@bp.route("/metrics/calendar")
def pl_page():
    """The P/L calendar and P/L over time, for the trades the filters pick."""
    dashboard, response = dashboard_for("metrics.pl_page")
    if response:
        return response
    filters = Filters.from_args(request.args)
    view = _view(filters)
    month = journal.parse_month(request.args.get("month"))
    days = journal.pl_by_day(view)
    calendar = journal.load_calendar(days, month)
    return _page(
        "pl.html",
        "metrics.pl_page",
        view,
        filters,
        dashboard,
        calendar=calendar,
        events=economic_events.events_between(
            services().session_factory, calendar.first, calendar.last
        ),
        history=journal.load_history(days),
        month_url=lambda months: _month_url(month, months),
    )


@bp.route("/metrics/calendar/period")
def pl_period():
    """The drawer for a day, week or month of the calendar: its first and last day
    (both included; not start and end, which are the filters' opening dates) plus
    the page's filters."""
    try:
        start, end = (
            date.fromisoformat(request.args["first"]),
            date.fromisoformat(request.args["last"]),
        )
    except KeyError, ValueError:
        abort(400)
    if end < start or (end - start).days > 31:
        abort(400)
    view = _view(Filters.from_args(request.args))
    events = (
        economic_events.events_between(services().session_factory, start, end).get(start, [])
        if start == end
        else []
    )
    return render_template(
        "_pl_period.html",
        period=journal.load_period(view, start, end, _now()),
        accounts=view.accounts,
        broker_names=BROKER_NAMES,
        events=events,
    )


@bp.route("/metrics")
def metrics_page():
    dashboard, response = dashboard_for("metrics.metrics_page")
    if response:
        return response
    filters = Filters.from_args(request.args)
    view = _view(filters)
    period = request.args.get("period", "week")
    if period not in journal.METRIC_PERIODS:
        period = "week"
    return _page(
        "metrics.html",
        "metrics.metrics_page",
        view,
        filters,
        dashboard,
        metrics=journal.load_metrics(view, period),
        period=period,
    )


def _month_url(month, months: int) -> str:
    """This page with the calendar moved `months` forward (or back), keeping
    the filters."""
    index = month.year * 12 + month.month - 1 + months
    args = request.args.to_dict(flat=False)
    args["month"] = f"{index // 12:04d}-{index % 12 + 1:02d}"
    return url_for("metrics.pl_page", **args)
