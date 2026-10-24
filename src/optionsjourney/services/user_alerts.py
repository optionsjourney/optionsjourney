"""Alerts the user sets (#27): a price, a trade's open P/L, or a spread's
price (#54, from Analyze) going above or below a level. Checked in the
background from the live prices; one that goes off is shown in the app (and
sent to Discord or Telegram if switched on, #28) until dismissed, and can be
set again."""

import logging
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import select

from optionsjourney.core.chain import WEEKLY_ROOTS
from optionsjourney.core.symbols import occ_symbol
from optionsjourney.core.transactions import OptionDetails
from optionsjourney.db.models import UserAlert
from optionsjourney.services.charts import ChartError, clean_symbol

logger = logging.getLogger(__name__)
CHECK_EVERY = 15  # seconds
SPREADS = ("spread", "bot_pl")  # valued from their legs' mids (bot_pl: a bot trade's close)
DIRECTIONS = ("above", "below")


class AlertError(ValueError):
    """An alert that can't be set; the message says why."""


@dataclass(frozen=True)
class Fired:
    """An alert to show and send. A bot's carries the bot and run it's about, and
    whether it's routine: only a routine alert follows the bot's channel choice,
    and never one that reads as a safety problem (bots slice 9, decision 0035)."""

    id: int
    title: str
    detail: str
    link: str
    bot_id: int | None = None
    run_id: int | None = None
    routine: bool = False


def _level(text) -> Decimal:
    try:
        value = Decimal(str(text).replace(",", "").replace("$", "").strip())
    except InvalidOperation:
        raise AlertError("The level must be a number.") from None
    if not value.is_finite():
        raise AlertError("The level must be a number.")
    return value


def _direction(text: str) -> str:
    if text not in DIRECTIONS:
        raise AlertError("Choose above or below.")
    return text


def _create(session_factory, kind, target, label, direction, level, note, legs=None) -> int:
    with session_factory() as session:
        alert = UserAlert(
            kind=kind,
            legs=legs,
            target=target,
            label=label,
            direction=_direction(direction),
            level=_level(level),
            note=(note or "").strip()[:500] or None,
            active=True,
            created_at=datetime.now(UTC),
            dismissed=False,
        )
        session.add(alert)
        session.commit()
        return alert.id


def create_price(session_factory, symbol: str, direction: str, level, note: str = "") -> int:
    try:
        symbol = clean_symbol(symbol)
    except ChartError as e:
        raise AlertError("Enter a symbol such as SPY or SPX.") from e
    return _create(session_factory, "price", symbol, symbol, direction, level, note)


def create_trade(
    session_factory, position_key: str, name: str, direction: str, level, note: str = ""
) -> int:
    return _create(session_factory, "trade_pl", position_key, name, direction, level, note)


MAX_SPREAD_LEGS = 12
_ISO_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def create_spread(session_factory, ticker: str, contracts, direction: str, level) -> int:
    """A spread's price: the size of its legs' net mid (qty x mid, added up),
    as Tracker2 measures it. contracts: [{expiry, strike, right, qty}] from
    Analyze's checked cards."""
    ticker = (ticker or "").strip().upper()
    if ticker.startswith("/"):
        raise AlertError("spread alerts on futures aren't supported")
    if not re.fullmatch(r"[A-Z][A-Z.]{0,9}", ticker):
        raise AlertError("Enter a symbol such as SPY or SPX.")
    if not isinstance(contracts, list) or not 0 < len(contracts) <= MAX_SPREAD_LEGS:
        raise AlertError(f"A spread needs 1-{MAX_SPREAD_LEGS} legs.")
    root = WEEKLY_ROOTS.get(ticker, ticker)
    legs = []
    for leg in contracts:
        leg = leg if isinstance(leg, dict) else {}
        expiry, strike, right, qty = (leg.get(k) for k in ("expiry", "strike", "right", "qty"))
        if (
            not isinstance(expiry, str)
            or not _ISO_DAY.match(expiry)
            or type(strike) not in (int, float)
            or strike <= 0
            or right not in ("call", "put")
            or type(qty) is not int
            or qty == 0
        ):
            raise AlertError("Each leg needs an expiration, strike, call or put, and quantity.")
        option = OptionDetails(right.upper(), Decimal(str(strike)), date.fromisoformat(expiry))
        legs.append({"key": occ_symbol(root, option), "qty": qty})
    return _create(
        session_factory, "spread", ticker, f"{ticker} spread", direction, level, "", legs
    )


def spread_value(hub, legs: list[dict]) -> Decimal | None:
    total = Decimal("0")
    for leg in legs:
        quote = hub.latest(leg["key"])
        if quote is None or quote.mark is None:
            return None
        total += leg["qty"] * quote.mark
    return abs(total)


def disable(session_factory, alert_id: int) -> None:
    _update(session_factory, alert_id, active=False)


def state(alert: UserAlert) -> str:
    if alert.active:
        return "armed"
    return "fired" if alert.triggered_at is not None else "off"


def listing(session_factory, states: set[str] | None = None) -> list[dict]:
    """Every alert as Analyze's Alerts dialog lists them, newest first."""
    with session_factory() as session:
        rows = session.scalars(select(UserAlert).order_by(UserAlert.created_at.desc()))
        return [
            {"id": a.id, "name": describe(a).title, "state": state(a), "kind": a.kind}
            for a in rows
            if states is None or state(a) in states
        ]


def active(session_factory) -> list[UserAlert]:
    with session_factory() as session:
        return list(
            session.scalars(
                select(UserAlert).where(UserAlert.active).order_by(UserAlert.created_at)
            )
        )


def triggered(session_factory) -> list[UserAlert]:
    """Ones that went off and haven't been dismissed, newest first."""
    with session_factory() as session:
        query = select(UserAlert).where(
            UserAlert.triggered_at.is_not(None), ~UserAlert.dismissed, ~UserAlert.active
        )
        return list(session.scalars(query.order_by(UserAlert.triggered_at.desc())))


def _update(session_factory, alert_id: int, **changes) -> None:
    with session_factory() as session:
        alert = session.get(UserAlert, alert_id)
        if alert is None:
            raise AlertError("That alert no longer exists.")
        for key, value in changes.items():
            setattr(alert, key, value)
        session.commit()


def dismiss(session_factory, alert_id: int) -> None:
    _update(session_factory, alert_id, dismissed=True)


def rearm(session_factory, alert_id: int) -> None:
    _update(
        session_factory,
        alert_id,
        active=True,
        dismissed=False,
        triggered_at=None,
        triggered_value=None,
    )


def delete(session_factory, alert_id: int) -> None:
    with session_factory() as session:
        alert = session.get(UserAlert, alert_id)
        if alert is not None:
            session.delete(alert)
            session.commit()


def _money(value: Decimal) -> str:
    return f"{'-' if value < 0 else ''}${abs(value):,.0f}"


def describe(alert: UserAlert, value: Decimal | None = None) -> Fired:
    if alert.bot_run_id is not None:  # a bot's default alert (bots slice 9)
        if value is None:
            now = ""
        elif alert.kind == "bot_pl":
            now = f"close price now {value:,.2f}"
        else:
            now = f"{alert.target} now {value:,.2f}"
        detail = " · ".join(part for part in (now, alert.note or "") if part)
        return Fired(alert.id, alert.label, detail, f"/automation/runs/{alert.bot_run_id}",
                     run_id=alert.bot_run_id, routine=True)  # fmt: skip
    if alert.kind in ("price", "spread"):
        title = f"{alert.label} went {alert.direction} {alert.level:,.2f}"
        now = f"now {value:,.2f}" if value is not None else ""
        link = f"/trade?ticker={alert.target}"
    else:
        title = f"{alert.label} open P/L went {alert.direction} {_money(alert.level)}"
        now = f"now {_money(value)}" if value is not None else ""
        link = f"/journal/trades/{alert.target}"
    detail = " · ".join(part for part in (now, alert.note or "") if part)
    return Fired(alert.id, title, detail, link)


def _value(alert: UserAlert, hub, open_pl: Callable[[str], Decimal | None]) -> Decimal | None:
    """What the alert watches, now: a price, a spread's or bot trade's value, or a P/L."""
    if alert.kind == "price":
        quote = hub.latest(alert.target)
        return quote.mark if quote is not None else None
    if alert.kind in SPREADS:
        return spread_value(hub, alert.legs or [])
    return open_pl(alert.target)


def _shown(alert: UserAlert, value: Decimal) -> str:
    return _money(value) if alert.kind == "trade_pl" else f"{value:,.2f}"


def condition(alert: UserAlert) -> str:
    """What it waits for: "goes below 7,700.00", "open P/L goes above $250"."""
    what = {"trade_pl": "open P/L ", "bot_pl": "close price "}.get(alert.kind, "")
    if alert.bot_run_id is not None and alert.kind == "price":
        what = f"{alert.target} "
    return f"{what}goes {alert.direction} {_shown(alert, alert.level)}"


TABLE_SHOWS = ("active", "armed", "fired", "all")  # Tracker2's filters; active: armed + fired today


def table(session_factory, show: str, hub, open_pl, now: datetime | None = None) -> list[dict]:
    """The Alerts page's Price alerts tab (#274): every alert, the user's and the bots',
    with what it watches now and its state, newest first."""
    from optionsjourney.core.market import MARKET_TZ  # noqa: PLC0415
    from optionsjourney.services import bot_alerts  # noqa: PLC0415 -- it imports this module

    now = now or datetime.now(UTC)
    show = show if show in TABLE_SHOWS else "active"
    today = now.astimezone(MARKET_TZ).date()
    with session_factory() as session:
        alerts = list(session.scalars(select(UserAlert).order_by(UserAlert.created_at.desc())))
    rows, bots = [], {}
    for alert in alerts:
        if alert.active:
            row_state = "armed"
        elif alert.triggered_at is not None and not alert.dismissed:
            row_state = "fired"
        else:
            row_state = "off"
        fired_today = (
            alert.triggered_at is not None
            and alert.triggered_at.astimezone(MARKET_TZ).date() == today
        )
        wanted = {
            "active": row_state == "armed" or (row_state == "fired" and fired_today),
            "armed": row_state == "armed",
            "fired": row_state == "fired",
            "all": True,
        }[show]
        if not wanted:
            continue
        try:
            value = _value(alert, hub, open_pl)
        except Exception:  # noqa: BLE001 -- the row shows without it
            logger.warning("alert %s: live value unavailable", alert.id, exc_info=True)
            value = None
        bot = None
        if alert.bot_run_id is not None:
            if alert.bot_run_id not in bots:
                view = bot_alerts.bot_view(
                    session_factory, bot_alerts.bot_of_run(session_factory, alert.bot_run_id)
                )
                bots[alert.bot_run_id] = view.name if view else None
            bot = bots[alert.bot_run_id]
        rows.append(
            {
                "id": alert.id,
                "name": alert.label,
                "bot": bot,
                "condition": condition(alert),
                "live": _shown(alert, value) if value is not None else "—",
                "state": row_state,
                "fired_at": alert.triggered_at,
                "fired_value": (
                    _shown(alert, alert.triggered_value)
                    if alert.triggered_value is not None
                    else None
                ),
                "note": alert.note or "",
                "link": describe(alert).link,
            }
        )
    return rows


def check(
    session_factory,
    hub,
    open_pl: Callable[[str], Decimal | None],
    notify: Callable[[Fired], None],
    now: datetime | None = None,
) -> list[Fired]:
    """Check every active alert; mark and announce the ones that went off."""
    try:  # a bot's default alerts end with their trade (bots slice 9)
        from optionsjourney.services import bot_alerts  # noqa: PLC0415

        bot_alerts.sweep(session_factory, now or datetime.now(UTC))
    except Exception:  # noqa: BLE001 -- the checks go on
        logger.exception("couldn't clear the default alerts of closed trades")
    alerts = active(session_factory)
    hub.subscribe(
        {a.target for a in alerts if a.kind == "price"}
        | {leg["key"] for a in alerts if a.kind in SPREADS for leg in a.legs or []}
    )
    fired = []
    for alert in alerts:
        value = _value(alert, hub, open_pl)
        if value is None:
            continue
        crossed = value > alert.level if alert.direction == "above" else value < alert.level
        if not crossed:
            continue
        _update(
            session_factory,
            alert.id,
            active=False,
            triggered_at=now or datetime.now(UTC),
            triggered_value=value,
        )
        alert.triggered_value = value
        event = describe(alert, value)
        fired.append(event)
        _count_as_new(session_factory, alert, event, now or datetime.now(UTC))
        if alert.quiet:
            continue  # in the app only: shown, never sent
        try:
            if alert.bot_run_id is not None:
                from optionsjourney.services import bot_alerts  # noqa: PLC0415

                event = replace(event, bot_id=bot_alerts.bot_of_run(session_factory,
                                                                    alert.bot_run_id))  # fmt: skip
            notify(event)
        except Exception:  # noqa: BLE001 -- a failed send must not stop the checks
            logger.exception("couldn't send alert %s", alert.id)
    return fired


def _count_as_new(session_factory, alert, event: Fired, now: datetime) -> None:
    """A firing is new on the bell until seen (#272); it's sent by `check`, not here."""
    from optionsjourney.services import app_alerts  # noqa: PLC0415 -- it imports this module

    try:
        app_alerts.add(
            session_factory, dedupe_key=f"user_alert:{alert.id}:{now.isoformat()}",
            kind="user_alert", title=event.title, detail=event.detail, link=event.link,
            now=now, send=False,
        )  # fmt: skip
    except Exception:  # noqa: BLE001 -- the checks go on
        logger.exception("couldn't list alert %s as new", alert.id)


def start_checker(session_factory, hub, open_pl, notify) -> threading.Thread:
    """Check every 15 seconds in the background (from __main__ and the menu
    bar app, never in tests)."""

    def loop():
        while True:
            try:
                check(session_factory, hub, open_pl, notify)
            except Exception:  # noqa: BLE001 -- keep checking
                logger.exception("alert check failed")
            time.sleep(CHECK_EVERY)

    thread = threading.Thread(target=loop, name="alerts", daemon=True)
    thread.start()
    return thread


def start_alert_checker(services) -> threading.Thread:
    """Start checking with the app's services: live prices from its quote hub,
    trades' open P/L from the journal, sent wherever the user chose (#28)."""
    from optionsjourney.services import journal, notify  # noqa: PLC0415

    return start_checker(
        services.session_factory,
        services.quote_hub,
        open_pl=lambda key: journal.open_pl_of(
            services.session_factory, services.positions_cache, services.quote_hub, key
        ),
        notify=lambda event: notify.send(services.session_factory, event),
    )
