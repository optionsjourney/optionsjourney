"""What the chart grid saves (#25): layouts, tabs, drawings and watchlist
groups, with the checks and answers Tracker2's chart pages expect (its
chart_bp.py and watchlist_bp.py), so those pages run unchanged."""

import math
import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import sessionmaker

from optionsjourney.core.chart_bars import HISTORY
from optionsjourney.db.models import ChartDrawing, ChartLayout, ChartTab, WatchlistGroup

LAYOUT_ID = re.compile(r"^[A-Za-z0-9-]{1,36}$")
PANE_ID = re.compile(r"^[A-Za-z0-9-]{1,40}$")
TAB_SYMBOL = re.compile(r"^/?[A-Za-z0-9.]{1,15}$")
DRAWING_SYMBOL = re.compile(r"^(/[A-Z0-9]{1,8}|[A-Z][A-Z0-9.]{0,9})$")
DRAWING_POINTS = {"hline": 1, "trend": 2, "rect": 2, "fib": 2}
MAX_DRAWINGS = 200
WATCH_SYMBOL = re.compile(r"^(?:[A-Z]{1,6}|/[A-Z0-9]{1,4})$")  # NVDA, SPX, or /ES
MAX_WATCH_SYMBOLS = 128
FUTURES_CONTRACT = re.compile(r"^\.?/([A-Z0-9]{1,4}?)[FGHJKMNQUVXZ]\d{1,2}$")  # ./ESZ6


class GridError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(when: datetime | None) -> str | None:
    return when.isoformat() if when else None


# --- layouts ---


def layout_dict(layout: ChartLayout) -> dict:
    return {
        "id": layout.id,
        "name": layout.name,
        "payload": layout.payload,
        "isActive": bool(layout.is_active),
        "createdAt": _iso(layout.created_at),
        "updatedAt": _iso(layout.updated_at),
    }


def list_layouts(session_factory: sessionmaker) -> list[dict]:
    with session_factory() as session:
        rows = session.scalars(select(ChartLayout).order_by(ChartLayout.updated_at.desc()))
        return [layout_dict(r) for r in rows]


def get_layout(session_factory: sessionmaker, layout_id: str) -> dict:
    with session_factory() as session:
        layout = session.get(ChartLayout, layout_id)
        if layout is None:
            raise GridError("Not found", 404)
        return layout_dict(layout)


def create_layout(session_factory: sessionmaker, data: dict) -> dict:
    layout_id, payload = data.get("id"), data.get("payload")
    name = (data.get("name") or "").strip() if isinstance(data.get("name"), str) else ""
    if not (isinstance(layout_id, str) and LAYOUT_ID.match(layout_id)) or not name:
        raise GridError("id, name, payload required")
    if payload is None:
        raise GridError("id, name, payload required")
    with session_factory() as session:
        if session.get(ChartLayout, layout_id):
            raise GridError("id already exists", 409)
        now = _now()
        layout = ChartLayout(
            id=layout_id,
            name=name[:200],
            payload=payload,
            is_active=False,
            created_at=now,
            updated_at=now,
        )
        session.add(layout)
        session.commit()
        return layout_dict(layout)


def update_layout(session_factory: sessionmaker, layout_id: str, data: dict) -> dict:
    """Either field may be left out and keeps its value, so a save from one
    window never undoes a rename made in another."""
    if "name" not in data and "payload" not in data:
        raise GridError("name or payload required")
    name = data.get("name")
    if "name" in data and not (isinstance(name, str) and name.strip()):
        raise GridError("name must not be blank")
    if "payload" in data and data["payload"] is None:
        raise GridError("payload must not be null")
    with session_factory() as session:
        layout = session.get(ChartLayout, layout_id)
        if layout is None:
            raise GridError("Not found", 404)
        if "name" in data:
            layout.name = name.strip()[:200]
        if "payload" in data:
            layout.payload = data["payload"]
        layout.updated_at = _now()
        session.commit()
        return layout_dict(layout)


def activate_layout(session_factory: sessionmaker, layout_id: str) -> dict:
    with session_factory() as session:
        layout = session.get(ChartLayout, layout_id)
        if layout is None:
            raise GridError("Not found", 404)
        session.execute(
            update(ChartLayout).where(ChartLayout.id != layout_id).values(is_active=False)
        )
        layout.is_active = True
        session.commit()
        return layout_dict(layout)


def delete_layout(session_factory: sessionmaker, layout_id: str) -> None:
    """If it was the active layout, the most recently changed one left takes over."""
    with session_factory() as session:
        layout = session.get(ChartLayout, layout_id)
        if layout is None:
            raise GridError("Not found", 404)
        was_active = layout.is_active
        session.execute(delete(ChartTab).where(ChartTab.layout_id == layout_id))
        session.delete(layout)
        session.flush()
        if was_active:
            following = session.scalars(
                select(ChartLayout).order_by(ChartLayout.updated_at.desc())
            ).first()
            if following is not None:
                following.is_active = True
        session.commit()


# --- tabs ---


def tab_dict(tab: ChartTab) -> dict:
    return {
        "id": tab.id,
        "layoutId": tab.layout_id,
        "position": tab.position,
        "symbols": tab.symbols,
    }


def _tab_symbols(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    out = {}
    for pane_id, symbol in raw.items():
        if not (
            isinstance(pane_id, str)
            and PANE_ID.match(pane_id)
            and isinstance(symbol, str)
            and TAB_SYMBOL.match(symbol)
        ):
            return None
        out[pane_id] = symbol.upper()
    return out


def list_tabs(session_factory: sessionmaker) -> list[dict]:
    with session_factory() as session:
        rows = session.scalars(select(ChartTab).order_by(ChartTab.position, ChartTab.created_at))
        return [tab_dict(r) for r in rows]


def create_tab(session_factory: sessionmaker, data: dict) -> dict:
    tab_id = data.get("id")
    symbols = _tab_symbols(data.get("symbols") or {})
    if not (isinstance(tab_id, str) and LAYOUT_ID.match(tab_id)) or symbols is None:
        raise GridError("id, layoutId required; symbols {pane: ticker}")
    with session_factory() as session:
        if session.get(ChartLayout, str(data.get("layoutId") or "")) is None:
            raise GridError("no such layout", 404)
        if session.get(ChartTab, tab_id):
            raise GridError("id already exists", 409)
        position = data.get("position")
        if not isinstance(position, int) or isinstance(position, bool):
            last = session.scalar(select(func.max(ChartTab.position)))
            position = 0 if last is None else last + 1
        now = _now()
        tab = ChartTab(
            id=tab_id,
            layout_id=data["layoutId"],
            position=position,
            symbols=symbols,
            created_at=now,
            updated_at=now,
        )
        session.add(tab)
        session.commit()
        return tab_dict(tab)


def update_tab(session_factory: sessionmaker, tab_id: str, data: dict) -> dict:
    with session_factory() as session:
        tab = session.get(ChartTab, tab_id)
        if tab is None:
            raise GridError("Not found", 404)
        if "symbols" in data:
            symbols = _tab_symbols(data["symbols"])
            if symbols is None:
                raise GridError("symbols must be {pane: ticker}")
            tab.symbols = symbols
        if "layoutId" in data:
            if session.get(ChartLayout, str(data["layoutId"] or "")) is None:
                raise GridError("no such layout", 404)
            tab.layout_id = data["layoutId"]
        if "position" in data:
            if not isinstance(data["position"], int) or isinstance(data["position"], bool):
                raise GridError("position must be an integer")
            tab.position = data["position"]
        tab.updated_at = _now()
        session.commit()
        return tab_dict(tab)


def delete_tab(session_factory: sessionmaker, tab_id: str) -> None:
    with session_factory() as session:
        tab = session.get(ChartTab, tab_id)
        if tab is None:
            raise GridError("Not found", 404)
        session.delete(tab)
        session.commit()


# --- drawings ---


def drawing_key(symbol, interval) -> tuple[str, str] | None:
    """(SYMBOL, interval), or None. A futures root, never a contract month."""
    symbol = (symbol or "").strip().upper() if isinstance(symbol, str) else ""
    if not DRAWING_SYMBOL.match(symbol) or interval not in HISTORY:
        return None
    return symbol, interval


def drawings_room(symbol: str, interval: str) -> str:
    return f"drawings:{symbol}:{interval}"


def _finite(value) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def clean_drawings(raw) -> list[dict] | None:
    """The drawings with only their known fields, or None if any is malformed."""
    if not isinstance(raw, list) or len(raw) > MAX_DRAWINGS:
        return None
    out = []
    for drawing in raw:
        if not isinstance(drawing, dict):
            return None
        did, kind, points = drawing.get("id"), drawing.get("type"), drawing.get("p")
        if (
            not isinstance(did, str)
            or not 0 < len(did) <= 32
            or kind not in DRAWING_POINTS
            or not isinstance(points, list)
            or len(points) != DRAWING_POINTS[kind]
        ):
            return None
        clean = []
        for point in points:
            if not isinstance(point, dict) or not (
                _finite(point.get("time")) and _finite(point.get("price"))
            ):
                return None
            clean.append({"time": point["time"], "price": point["price"]})
        out.append({"id": did, "type": kind, "p": clean})
    return out


def get_drawings(session_factory: sessionmaker, symbol, interval) -> dict:
    key = drawing_key(symbol, interval)
    if key is None:
        raise GridError("bad symbol or interval")
    with session_factory() as session:
        row = session.get(ChartDrawing, key)
        return {"symbol": key[0], "interval": key[1], "drawings": row.drawings if row else []}


def put_drawings(session_factory: sessionmaker, data: dict) -> dict:
    """Replace one symbol and timeframe's drawings; an empty list removes them."""
    key = drawing_key(data.get("symbol"), data.get("interval"))
    drawings = clean_drawings(data.get("drawings"))
    if key is None or drawings is None:
        raise GridError("bad symbol, interval or drawings")
    with session_factory() as session:
        row = session.get(ChartDrawing, key)
        if drawings:
            if row is None:
                session.add(
                    ChartDrawing(
                        symbol=key[0], interval=key[1], drawings=drawings, updated_at=_now()
                    )
                )
            else:
                row.drawings, row.updated_at = drawings, _now()
        elif row is not None:
            session.delete(row)
        session.commit()
    return {"symbol": key[0], "interval": key[1], "drawings": drawings}


# --- watchlist ---


def group_dict(group: WatchlistGroup) -> dict:
    return {
        "id": group.id,
        "name": group.name,
        "sortOrder": group.sort_order,
        "collapsed": bool(group.collapsed),
        "symbols": list(group.symbols or []),
    }


def _ordered(session) -> list[WatchlistGroup]:
    return list(
        session.scalars(
            select(WatchlistGroup).order_by(WatchlistGroup.sort_order, WatchlistGroup.created_at)
        )
    )


def watch_symbol(underlying: str) -> str:
    """A trade's underlying as a watchlist symbol: futures by root (/ESZ6 -> /ES)."""
    symbol = (underlying or "").strip().upper()
    found = FUTURES_CONTRACT.match(symbol)
    return f"/{found.group(1)}" if found else symbol


def clean_watch_symbols(raw) -> list[str] | None:
    """Upper-cased, de-duplicated, in order; None when any is invalid."""
    if not isinstance(raw, list):
        return None
    out: list[str] = []
    for symbol in raw:
        symbol = symbol.strip().upper() if isinstance(symbol, str) else ""
        if not WATCH_SYMBOL.match(symbol):
            return None
        if symbol not in out:
            out.append(symbol)
    return out


def list_groups(session_factory: sessionmaker) -> list[dict]:
    with session_factory() as session:
        return [group_dict(g) for g in _ordered(session)]


def create_group(session_factory: sessionmaker, data: dict) -> dict:
    name = data.get("name")
    if not (isinstance(name, str) and name.strip()):
        raise GridError("name required")
    group_id = data.get("id")
    if group_id is not None and not (isinstance(group_id, str) and LAYOUT_ID.match(group_id)):
        raise GridError("bad id")
    with session_factory() as session:
        if group_id and session.get(WatchlistGroup, group_id):
            raise GridError("id already exists", 409)
        last = session.scalar(select(func.max(WatchlistGroup.sort_order)))
        now = _now()
        group = WatchlistGroup(
            id=group_id or str(uuid.uuid4()),
            name=name.strip()[:100],
            symbols=[],
            sort_order=0 if last is None else last + 1,
            collapsed=False,
            created_at=now,
            updated_at=now,
        )
        session.add(group)
        session.commit()
        return group_dict(group)


def update_group(session_factory: sessionmaker, group_id: str, data: dict) -> dict:
    with session_factory() as session:
        group = session.get(WatchlistGroup, group_id)
        if group is None:
            raise GridError("Not found", 404)
        if "name" in data:
            name = data["name"]
            if not (isinstance(name, str) and name.strip()):
                raise GridError("name must not be blank")
            group.name = name.strip()[:100]
        if "collapsed" in data:
            group.collapsed = bool(data["collapsed"])
        if "symbols" in data:
            symbols = clean_watch_symbols(data["symbols"])
            if symbols is None:
                raise GridError(
                    "symbols must be stock, ETF or index tickers like NVDA or SPX, "
                    "or futures like /ES"
                )
            others = {s for g in _ordered(session) if g.id != group.id for s in g.symbols or []}
            if len(others | set(symbols)) > MAX_WATCH_SYMBOLS:
                raise GridError(f"watchlist is limited to {MAX_WATCH_SYMBOLS} symbols")
            group.symbols = symbols
        group.updated_at = _now()
        session.commit()
        return group_dict(group)


def delete_group(session_factory: sessionmaker, group_id: str) -> None:
    with session_factory() as session:
        group = session.get(WatchlistGroup, group_id)
        if group is None:
            raise GridError("Not found", 404)
        session.delete(group)
        session.commit()


def reorder_groups(session_factory: sessionmaker, ids) -> list[dict]:
    if not isinstance(ids, list):
        raise GridError("ids must be a list")
    rank = {gid: i for i, gid in enumerate(ids)}
    with session_factory() as session:
        for group in _ordered(session):
            group.sort_order = rank.get(group.id, len(ids) + group.sort_order)
        session.commit()
        return [group_dict(g) for g in _ordered(session)]
