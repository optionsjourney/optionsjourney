"""What a run's orders did, step by step (bots slice 20; Tracker2's run_view.py), and
the watch-exit banner (Tracker2's automation_watch_banner.js). Pure: reads only a run's
and its exits' own columns and logs, never the broker.

The entry's steps are `run.chase_log`. Steps written by the chase carry `at` and their
payload (a price and the order path's answer, a cancel and its status, a tranche's
fill); the event is inferred from those keys (run_view._infer_event), so every log the
app ever wrote reads as "Placed / Replaced / Cancelled / Filled", and a step it doesn't
know still shows its keys and values. Each exit row's steps are its own `log`, a closing
chase nested under `chase` (run_view.exit_chase_steps)."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation

from optionsjourney.bots import stops
from optionsjourney.core.market import MARKET_TZ
from optionsjourney.core.symbols import parse_occ

SENT, REPLACED, UNCLEAR = "Sent.", "Replaced.", "Unclear:"
PREVIEW_WORDS = "preview mode"  # trading/check.py: "… is in preview mode: checked, not sent."

# The run page's Kind column (slice 7's _KINDS): a close a rule started names the rule.
KINDS = {"timed": "timed close", "action": "time action", "close": "your close",
         "watch": "watch rule", "profit": "profit action",
         "settle": "far legs (near legs expired)"}  # fmt: skip
STAGES = {"chase": "Limit", "timed": "Limit", "close": "Limit", "natural": "Natural",
          "leg_out": "Leg out", "market": "Market", "sized": "Sized"}  # fmt: skip
EVENTS = {
    "asked to stop": "Asked to stop",
    "window closed": "Entry window closed",
    "expiry_warned": "Expiry warning",
    "futures_expiry_warned": "Futures expiry warning",
    "placed": "Placed (resting)",
    "cancel_requested": "Cancel asked",
    "cancel_failed": "Cancel failed",
    "partial_seen": "Partly filled (seen)",
    "remainder": "Rest placed (tranche)",
}
# A watch rule's or profit action's row records what fired: "filled" means it acted.
RULE_ROWS = {kind: {"filled": "Acted", "failed": "Couldn't act"} for kind in ("watch", "profit")}
LIVE = ("placing", "pending", "armed", "working", "firing", "chasing", "watching")
OPEN_ACTION = {"sell": "STO", "buy": "BTO"}
CLOSE_ACTION = {"sell": "BTC", "buy": "STC"}
# Keys a step's own row already shows; the rest go in its note when nothing else does.
_SHOWN = {"at", "price", "result", "event", "chase", "stage", "status"}


@dataclass(frozen=True)
class Step:
    """One row of the timeline. `when` is "MM-DD HH:MM:SS" New York, or None for a step
    logged without a time. `live`: the order is still working."""

    when: str | None
    source: str
    event: str
    price: str | None = None
    legs: str = ""
    note: str | None = None
    live: bool = False


def _at(text) -> datetime | None:
    """A step's `at`: an ISO time; one without a zone was written in UTC."""
    if not isinstance(text, str) or not text:
        return None
    try:
        at = datetime.fromisoformat(text)
    except ValueError:
        return None
    return at.replace(tzinfo=UTC) if at.tzinfo is None else at


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _shown(at: datetime | None) -> str | None:
    return None if at is None else f"{at.astimezone(MARKET_TZ):%m-%d %H:%M:%S}"


def signed(price) -> str | None:
    """A signed net price as read: -0.40 → "0.40 debit", 1.25 → "1.25 credit"
    (run_view._close_price; trading/orders.py: positive is a credit)."""
    if price is None or price == "":
        return None
    try:
        d = Decimal(str(price))
    except InvalidOperation:
        return str(price)
    return f"{-d:f} debit" if d < 0 else f"{d:f} credit"


def _plain(value) -> str:
    if isinstance(value, list | tuple):
        return ", ".join(_plain(v) for v in value)
    if isinstance(value, dict):
        return "; ".join(f"{k}: {_plain(v)}" for k, v in value.items())
    return str(value)


def _rest(step: dict) -> str | None:
    """A step's other keys and values: nothing is ever dropped from the page."""
    parts = [f"{k}: {_plain(v)}" for k, v in step.items() if k not in _SHOWN]
    return "; ".join(parts) or None


def _leg_text(symbol: str, side: str, quantity, actions: dict) -> str:
    action = actions.get(str(side).lower(), str(side))
    found = parse_occ(symbol)
    if found is None:
        return f"{action} {quantity} {' '.join(str(symbol).split())}"
    option = found[1]
    return f"{action} {quantity} {option.strike.normalize():f}{option.put_call[0]}"


def entry_legs(run) -> str:
    """The run's legs as orders: "STO 1 5800P · BTO 1 5725P" (the ratio × contracts)."""
    units = getattr(run, "contracts", 0) or 1
    return " · ".join(
        _leg_text(
            leg.get("symbol", ""),
            leg.get("side", ""),
            int(leg.get("ratio", 1)) * units,
            OPEN_ACTION,
        )  # fmt: skip
        for leg in getattr(run, "legs", None) or ()
        if isinstance(leg, dict)
    )


def exit_legs(row) -> str:
    """The legs an exit row closes, as closing orders: "BTC 1 5800P · STC 1 5725P"."""
    legs = [leg for leg in (getattr(row, "legs", None) or ()) if isinstance(leg, dict)]
    units = getattr(row, "contracts", 0) or 1
    if legs:
        return " · ".join(
            _leg_text(
                leg.get("symbol", ""),
                leg.get("side", ""),
                int(leg.get("ratio", 1)) * units,
                CLOSE_ACTION,
            )  # fmt: skip
            for leg in legs
        )
    return " · ".join(
        _leg_text(symbol, side, units, CLOSE_ACTION)
        for symbol, side in ((row.short_symbol, "sell"), (row.long_symbol, "buy"))
        if symbol
    )


def _order_step(step: dict, previous: str | None, preview_run: bool) -> tuple[str, str | None]:
    """(event, note) of a step with a price and the order path's answer
    (run_view._infer_event: a price right after another re-priced the same order)."""
    result = str(step.get("result") or "")
    if "tranche" in step:
        tranche = step.get("tranche")
        if result == SENT:
            return f"Placed for the rest (tranche {tranche})", None
        return f"Not placed for the rest (tranche {tranche})", result or None
    if result == SENT:
        return "Placed", None
    if result == REPLACED:
        return "Replaced", None
    if result.startswith(UNCLEAR):
        return "Unclear", result
    if PREVIEW_WORDS in result or (preview_run and previous is None):
        return "Preview", "checked by the broker, nothing sent"
    if previous in ("Placed", "Replaced") or str(previous or "").startswith("Placed for"):
        return "Replace refused", result or None
    return "Refused", result or None


def _entry_event(step: dict, previous: str | None, preview_run: bool, units_left: bool):
    """(event, price, note) for one entry step (the spec's table)."""
    if "price" in step and ("result" in step or "tranche" in step):
        event, note = _order_step(step, previous, preview_run)
        return event, signed(step.get("price")), note
    if "tranche" in step and "filled" in step:
        n = step.get("filled")
        words = f"{n} contract{'' if str(n) == '1' else 's'} (tranche {step.get('tranche')})"
        return ("Partly filled" if units_left else "Filled"), None, words
    if "cancel" in step:
        return "Cancelled", None, f"order {step.get('cancel')}: {step.get('status', '?')}"
    if "error" in step and "order" in step:
        return "Couldn't read the order", None, f"order {step.get('order')}: {step['error']}"
    if step.get("sizing") == "contracts":  # decision 0048 (older runs): priced as a credit
        return "Sized by contracts", None, step.get("why")
    if step.get("sizing") == "buying_power":  # decision 0050
        note = (f"${step.get('budget')} at worst {step.get('worst')} "
                f"({step.get('broker')}: ${step.get('unit_cost')} a contract): "
                f"{step.get('contracts')}")  # fmt: skip
        return "Sized by buying power", None, note
    if "sizing" in step:  # older runs: by the worst debit
        note = (f"${step.get('allocation')} at worst {step.get('worst')} "
                f"({step.get('unit_cost')} a contract): {step.get('contracts')}")  # fmt: skip
        return "Sized by dollars", None, note
    if "capped" in step:
        return "Capped", None, f"{step.get('capped')} cut to {step.get('to')} by Max per trade"
    if "strikes" in step:
        return "Strikes moved", None, _plain(step["strikes"])
    if "expirations" in step:
        return "Expirations moved", None, _plain(step["expirations"])
    if "event" in step:
        event = str(step["event"])
        named = EVENTS.get(event, event[:1].upper() + event[1:])
        return named, signed(step.get("price")), _rest(step)
    return "Step", None, _rest(step)


def entry_steps(run, source: str = "Entry") -> list[tuple[datetime | None, Step]]:
    """The entry's steps, each with the time it sorts by (run_view._entry_rows)."""
    legs = entry_legs(run)
    log = [s for s in (getattr(run, "chase_log", None) or ()) if isinstance(s, dict)]
    preview_run = getattr(run, "status", None) == "preview"
    fills = [s for s in log if "tranche" in s and "filled" in s]
    # The entry ended short when the rest went out after the last fill and never filled,
    # or the run itself says partial (a fill recovered after a restart).
    after_last = log[log.index(fills[-1]) + 1 :] if fills else []
    short = getattr(run, "status", None) == "partial" or any(
        "tranche" in s and "contracts" in s for s in after_last
    )
    out: list[tuple[datetime | None, Step]] = []
    previous: str | None = None
    filled = False
    for step in log:
        last_fill = bool(fills) and step is fills[-1]
        units_left = "filled" in step and (not last_fill or short)
        event, price, note = _entry_event(step, previous, preview_run, units_left)
        if event in ("Filled", "Partly filled"):
            filled = True
            if last_fill and getattr(run, "fill_credit", None) is not None:
                price = signed(run.fill_credit)
        if "price" in step or event in ("Filled", "Partly filled"):
            previous = event
        at = _at(step.get("at"))
        out.append((at, Step(_shown(at), source, event, price, legs, note)))
    if not filled and getattr(run, "filled_at", None) is not None:
        # No chase recorded the fill (a hand-over, a resting entry): the run's own fill
        # stands in for it (run_view._entry_rows).
        at = _utc(run.filled_at)
        out.append((at, Step(_shown(at), source, "Filled", signed(run.fill_credit), legs)))
    return out


def kind_label(row) -> str:
    """The run page's Kind column; a close a rule started names it (slice 7)."""
    first = next(iter(row.log or ()), None)
    by = first.get("by") if isinstance(first, dict) else None
    if row.kind == "close" and by:
        return f"close ({by})"
    if row.kind == "paired":  # bots slice 21: the close another group's stop started
        return f"paired close (after {by})" if by else "paired close"
    return KINDS.get(row.kind, row.kind)


WING_MODES = {"close_now": "sell it now", "close_limit": "rest a limit",
              "close_chase": "chase it", "leave": "leave it open"}  # fmt: skip


def _result(step: dict) -> tuple[str, str | None, str | None]:
    """An exit entry's order-path answer, with its price if it has one; a market order
    says so."""
    result = str(step["result"])
    market = "market" in step
    where = f" at market ({step['market']})" if market and step["market"] not in (1, True) else (
        " at market" if market else "")  # fmt: skip
    price = signed(step.get("price"))
    if result == SENT:
        return f"Placed{where}", price, None
    if result == REPLACED:
        return "Replaced", price, None
    if result.startswith(UNCLEAR):
        return "Unclear", None, result
    return "Note", None, result


def _exit_entry(step: dict) -> tuple[str, str | None, str | None]:
    """(event, price, note) for one exit log entry that isn't a nested chase."""
    if "stop_calc" in step or ("stop" in step and len(step) <= 3):
        parts = stops.describe(step.get("stop_calc"))
        return "Worked out", None, " · ".join(parts) or f"stop {step.get('stop')}"
    if "target" in step and "unit" in step:
        if step.get("problem"):
            return "Not armed", None, str(step["problem"])
        close_at = step.get("close_at")
        return "Worked out", None, f"close at {close_at}" if close_at else _rest(step)
    if "stage" in step:
        stage = STAGES.get(str(step["stage"]), str(step["stage"]).replace("_", " ").capitalize())
        if "result" in step:
            event, price, note = _result(step)
            return f"{stage}: {event}", price, note
        if "status" in step:
            return f"{stage} ended: {step['status']}", None, None
        if "bought_back" in step:
            return f"{stage}: short bought back", None, _plain(step["bought_back"])
        if "selling" in step:
            return f"{stage}: selling the rest", None, _plain(step["selling"])
        if "handed" in step:
            return f"{stage}: wing handed to its own close", None, _plain(step["handed"])
        if step["stage"] == "left":  # Ignore bid-less longs: legs left out to expire
            legs = ", ".join(" ".join(str(s).split()) for s in step.get("left") or ())
            return f"Left {legs} to expire: {step.get('why') or 'no bid'}", None, None
        if step["stage"] == "sized":
            return "Sized", None, f"{step.get('contracts')} contracts"
        start = step.get("start")
        return f"{stage} started", signed(start) if start else None, _rest(step)
    if "result" in step:
        return _result(step)
    if "mode" in step:  # a wing after its stop (slice 2's on_stop_remainder)
        mode = WING_MODES.get(str(step["mode"]), str(step["mode"]))
        return f"Wing: {mode}", None, None
    if "event" in step:
        event = str(step["event"])
        note = step.get("why") or step.get("reading") or None
        return event[:1].upper() + event[1:], None, None if note is None else str(note)
    if "rule" in step:
        notes = [str(step.get("detail") or ""), *map(str, step.get("notes") or ())]
        return str(step["rule"]), None, " · ".join(n for n in notes if n) or None
    return "Step", None, _rest(step)


def _chase_steps(entry: dict, source: str, legs: str, day_at: datetime | None):
    """A nested closing chase (run_view.exit_chase_steps): its orders, then how it ended."""
    stage = STAGES.get(str(entry.get("stage") or "chase"), "Limit")
    out, previous = [], None
    for step in entry.get("chase") or ():
        if not isinstance(step, dict):
            continue
        event, price, note = _entry_event(step, previous, False, False)
        if "price" in step:
            previous = event
        at = _at(step.get("at"))
        out.append((at, Step(_shown(at), source, f"{stage}: {event}", price, legs, note)))
    if "status" in entry:
        at = _at(entry.get("at"))
        out.append((at, Step(_shown(at), source, f"{stage} chase ended: {entry['status']}",
                             legs=legs)))  # fmt: skip
    return out


def _fill_time(row) -> datetime | None:
    """When a filled exit filled: its log's "closed…" entry (or a chase that ended filled),
    not the row's last change, which a later bookkeeping write can move."""
    for entry in reversed([e for e in row.log or () if isinstance(e, dict)]):
        if str(entry.get("event", "")).startswith("closed") or entry.get("status") == "filled":
            return _at(entry.get("at"))
    return None


def _exit_price(row, entry: dict, event: str) -> str | None:
    """The price a step without one of its own stands at: a stop placed at its trigger, a
    one-tick limit's tick, a fill's price."""
    if "one_tick" in entry:
        return f"limit {entry['one_tick']}"
    if "price" in entry:
        return f"fill {entry['price']}" if event.lower().startswith("filled") else str(entry["price"])
    if row.kind == "stop" and event.startswith("Placed") and getattr(row, "trigger", None) is not None:
        return f"stop {row.trigger}"
    return None


def exit_steps(row, group_names: dict | None = None,
               fills: dict | None = None) -> list[tuple[datetime | None, Step]]:  # fmt: skip
    """One exit row's steps and its end (run_view._exit_rows). `fills`: broker order id ->
    its net fill price a contract (+ credit), from the synced fills, for a close whose row
    kept no fill price (one that ended at market, or a wing's own close)."""
    group = (group_names or {}).get(row.group_position) or f"Group {row.group_position + 1}"
    source, legs = f"{kind_label(row)} · {group}", exit_legs(row)
    out: list[tuple[datetime | None, Step]] = []
    for entry in row.log or ():
        if not isinstance(entry, dict):
            continue
        if isinstance(entry.get("chase"), list):
            out.extend(_chase_steps(entry, source, legs, _at(entry.get("at"))))
            continue
        event, price, note = _exit_entry(entry)
        if price is None:
            price = _exit_price(row, entry, event)
        at = _at(entry.get("at"))
        out.append((at, Step(_shown(at), source, event, price, legs, note)))
    changed = _utc(getattr(row, "updated_at", None))
    if row.kind in RULE_ROWS:  # a rule's record, not an order: say what it did
        word = RULE_ROWS[row.kind].get(row.state, row.state.capitalize())
        out.append((changed, Step(_shown(changed), source, word, legs=legs)))
    elif row.state == "filled":
        fill = getattr(row, "fill_price", None)
        broker_fill = (fills or {}).get(str(getattr(row, "broker_order_id", None) or ""))
        price = f"fill {fill}" if fill is not None else signed(broker_fill)
        filled_at = _fill_time(row) or changed
        if row.kind == "paired":  # bookkeeping: its own close (the next row) did the selling
            out.append((filled_at, Step(_shown(filled_at), source, "Handed to its close", None, legs)))
        elif not any(step.event.lower().startswith("filled") for _, step in out):  # said already
            out.append((filled_at, Step(_shown(filled_at), source, "Filled", price, legs)))
    elif row.state in LIVE:
        if out:
            at, last = out[-1]
            out[-1] = (at, Step(last.when, last.source, last.event, last.price, last.legs,
                                last.note, live=True))  # fmt: skip
        else:
            created = _utc(getattr(row, "created_at", None))
            out.append((created, Step(_shown(created), source, row.state.capitalize(),
                                      legs=legs, live=True)))  # fmt: skip
    else:
        out.append((changed, Step(_shown(changed), source, row.state.capitalize(), legs=legs)))
    return out


def order_timeline(run, exits, group_names: dict | None = None,
                   entry_source: str = "Entry", fills: dict | None = None) -> list[Step]:  # fmt: skip
    """Every step oldest first: the entry, then each exit row's (run_view.order_timeline).
    A step with no time sorts right after the step before it; ties keep their order."""
    rows = entry_steps(run, entry_source)
    for row in sorted(exits, key=lambda r: r.id):
        rows.extend(exit_steps(row, group_names, fills))
    keyed, last = [], datetime.min.replace(tzinfo=UTC)
    for at, step in rows:
        last = at or last
        keyed.append((last, step))
    keyed.sort(key=lambda pair: pair[0])  # stable
    return [step for _, step in keyed]


# --- The watch-exit banner (automation_watch_banner.js) ------------------------------

FAILED = ("failed", "cancelled")


@dataclass(frozen=True)
class Banner:
    """`tone`: "danger" (the close didn't happen), "warning" (closing), "quiet" (done)."""

    tone: str
    text: str


STARTING = "starting the close"  # bot_watch._fire_side: the row is written before its claim
NOT_CLAIMED = "the side couldn't be claimed"  # another close already has that side


def _notes(row) -> list[str]:
    first = (row.log or [{}])[0]
    return [str(n) for n in first.get("notes") or ()] if isinstance(first, dict) else []


STARTING_FOR = timedelta(seconds=30)  # longer "starting" than this: the close never began


def _starting(row, now) -> bool:
    """Still starting its close: written moments ago. Older, the app died before the
    claim (or nothing finished the row), so it reads as failed."""
    if STARTING not in _notes(row):
        return False
    written = _utc(getattr(row, "updated_at", None))
    return now is None or written is None or now - written <= STARTING_FOR


def watch_banner(watch_rows, close_rows, now: datetime | None = None) -> Banner | None:
    """The run page's banner for the latest watch rule that fired: its "watch" rows (one
    per side, slice 7) and the closes they started (kind "close", started `by` the rule).
    Every branch says its whole state, so a refresh never keeps an old one. A watch row
    is written "failed" before its close is claimed (so a rule never fires twice) and
    finished after: "failed" with "starting the close" is still starting, and one whose
    side another close already had is that close's to finish; neither is red."""
    rows = sorted(watch_rows, key=lambda r: r.id)
    if not rows:
        return None
    first = (rows[-1].log or [{}])[0]
    rule = str(first.get("rule") or "Watch rule")
    detail = str(first.get("detail") or "")
    fired = [r for r in rows if (r.log or [{}])[0].get("rule") == rule]
    closes = [c for c in close_rows if (c.log or [{}])[0].get("by") == rule]
    why = f" ({detail})" if detail else ""
    broke = [
        r for r in fired
        if r.state == "failed" and NOT_CLAIMED not in _notes(r) and not _starting(r, now)
    ]  # fmt: skip
    if broke or any(c.state in FAILED for c in closes):
        return Banner("danger", f"{rule} fired{why}, close failed: close it yourself")
    if any(c.state == "reduced" for c in closes):
        return Banner("warning", f"{rule} fired{why}: partly closed; the rest is protected")
    busy = [r for r in fired if r.state == "failed" and NOT_CLAIMED in _notes(r)]
    if busy and not closes:
        return Banner("warning", f"{rule} fired{why}: another close already has that side")
    if closes and all(c.state == "expired" for c in closes):
        return Banner("quiet", f"{rule} fired{why}: nothing left open")
    if closes and all(c.state in ("filled", "expired") for c in closes):
        return Banner("quiet", f"{rule} fired{why}: closed")
    return Banner("warning", f"{rule} fired: {detail or 'see the exits'} (closing)")


def side_closed(exits, position: int, fills: dict | None = None) -> list[str]:
    """How a closed group ended, for the Manage section: its stop and target as they stood,
    then what closed it and at what price ("Stop 0.32", "Stopped out at 0.34 (15:08)").
    `fills` as order_timeline's. Empty when nothing on record says."""
    rows = sorted((r for r in exits if r.group_position == position), key=lambda r: r.id)
    lines = []
    for kind, word in (("stop", "Stop"), ("target", "Target")):
        levels = [str(r.trigger if r.trigger is not None else r.limit) for r in rows
                  if r.kind == kind and (r.trigger is not None or r.limit is not None)]  # fmt: skip
        if levels:
            lines.append(f"{word} {levels[-1]}")
    for row in rows:
        if row.kind == "wing" and row.state in LIVE:  # #179: the leg is still held
            limit = next((e.get("one_tick") or e.get("price") for e in reversed(row.log or ())
                          if isinstance(e, dict) and (e.get("one_tick") or e.get("price"))), None)  # fmt: skip
            at = _utc(getattr(row, "created_at", None))
            when = f" since {at.astimezone(MARKET_TZ):%H:%M}" if at else ""
            lines.append(f"Selling the wing{f' at {limit}' if limit else ''}: working{when}")
            continue
        if row.state != "filled" or row.kind == "paired" or row.kind in RULE_ROWS:
            continue
        handed = any(isinstance(e, dict) and "handed" in e for e in row.log or ())
        if handed and getattr(row, "fill_price", None) is None:
            lines.append("Handed the wing to its own sell order")
            continue
        fill = getattr(row, "fill_price", None)
        price = (f"{fill}" if fill is not None
                 else signed((fills or {}).get(str(getattr(row, "broker_order_id", None) or ""))))  # fmt: skip
        at = _fill_time(row) or _utc(getattr(row, "updated_at", None))
        when = f" ({at.astimezone(MARKET_TZ):%H:%M})" if at else ""
        first = next(iter(row.log or ()), None)
        by = first.get("by") if isinstance(first, dict) else None
        what = {"stop": "Stopped out", "target": "Target filled", "wing": "Wing sold"}.get(
            row.kind, f"Closed after the {by}" if by else f"Closed ({kind_label(row)})")
        lines.append(f"{what}{f' at {price}' if price else ''}{when}")
    return lines
