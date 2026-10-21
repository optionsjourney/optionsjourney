"""Paper trading (decision 0061): a Paper bot's entry runs the normal order check as a
preview, then records a pretend fill at the mid. Nothing is ever sent to a broker: the
prices come from the streaming quote hub (deps.hub), never a broker's REST quotes. Its
exits are services/bot_paper_exits.py's."""

from datetime import UTC, datetime, time
from decimal import Decimal, InvalidOperation

from sqlalchemy import select

from optionsjourney.bots import caps
from optionsjourney.bots.watched_stops import QUIET_MAX
from optionsjourney.core.market import MARKET_TZ
from optionsjourney.db.models import Bot, BotRun, PaperFill

NO_PRICE = "No price for a leg, so no paper fill."
STREAM_WAIT = 5.0  # seconds a newly picked strike gets for its first stream quote
STREAM_POLL = 0.5
ENTRY = "entry"


def _number(text) -> Decimal | None:
    try:
        return Decimal(str(text))
    except InvalidOperation, ValueError:
        return None


def _stream_mid(deps, symbol: str, now: datetime) -> Decimal | None:
    quote = deps.hub.latest(symbol)
    if quote is None or quote.bid is None or quote.ask is None:
        return None
    if quote.at is None or now - quote.at > QUIET_MAX:
        return None
    return (quote.bid + quote.ask) / 2


def _leg_mids(deps, legs: list[dict], now: datetime) -> list[Decimal] | None:
    """Each leg's mid: the one the entry priced it at, seconds ago from the chain. A leg
    without one takes the stream's, waiting up to STREAM_WAIT for a newly picked strike's
    first quote. None when a leg still has no fresh bid and ask: a pretend fill never
    uses an old price."""
    mids = [_number(leg.get("mid")) for leg in legs]
    missing = [leg["symbol"] for leg, mid in zip(legs, mids, strict=True) if mid is None]
    if not missing:
        return mids
    deps.hub.subscribe(set(missing))
    waited = 0.0
    while True:
        found = {symbol: _stream_mid(deps, symbol, now) for symbol in missing}
        if all(mid is not None for mid in found.values()):
            break
        if waited >= STREAM_WAIT:
            return None
        deps.sleep(STREAM_POLL)
        waited += STREAM_POLL
    return [mid if mid is not None else found[leg["symbol"]] for leg, mid in zip(legs, mids, strict=True)]


def enter(deps, run_id: int, legs: list[dict], contracts: int, now: datetime) -> str | None:
    """Record the run's pretend entry at the combo's mid (a credit positive). Returns None
    on a recorded fill, else why it was skipped (nothing is written then)."""
    from optionsjourney.services import bot_entry

    mids = _leg_mids(deps, legs, now)
    if mids is None:
        return NO_PRICE
    price = sum(
        (
            mid * (1 if leg["side"] == "sell" else -1) * int(leg.get("ratio") or 1)
            for leg, mid in zip(legs, mids, strict=True)
        ),
        Decimal(0),
    )
    used = [{"symbol": leg["symbol"], "side": leg["side"], "ratio": leg.get("ratio") or 1,
             "mid": f"{mid:f}"} for leg, mid in zip(legs, mids, strict=True)]  # fmt: skip
    filled_legs = [
        {"symbol": leg["symbol"], "side": leg["side"], "fill_price": f"{mid:f}",
         "contracts": contracts * int(leg.get("ratio") or 1)}
        for leg, mid in zip(legs, mids, strict=True)
    ]  # fmt: skip
    with deps.session_factory() as session:
        run = session.get(BotRun, run_id)
        bot = session.get(Bot, run.bot_id)
        name, bot_id = (bot.name if bot is not None else "A bot"), run.bot_id
        run.status, run.paper, run.live = "filled", True, False
        run.fill_credit, run.filled_at, run.contracts = price, now, contracts
        run.filled_legs, run.updated_at = filled_legs, now
        session.add(PaperFill(run_id=run_id, kind="entry", group=None, price=price,
                              contracts=contracts, reason=ENTRY, legs=used, at=now))  # fmt: skip
        session.commit()
    bot_entry.alert(
        deps, f"Paper: {name} entered at {price:f} (estimated at mid)",
        f"{contracts} contract(s). Paper trades are pretend: nothing was sent to your broker.",
        f"/automation/{bot_id}/runs", run_id=run_id, routine=True,
    )  # fmt: skip
    return None


SWITCHED = "switched to Live"


def end_paper(deps, bot_id: int, now: datetime) -> int:
    """The bot is going Live: every open paper trade closes at its mid (at the last price
    seen when the stream has no fresh one), and is watched no more. Returns how many
    closed."""
    from optionsjourney.services import bot_paper_exits

    closed = bot_paper_exits.end_bot(deps, bot_id, SWITCHED, now)
    if bot_paper_exits.open_runs(deps.session_factory, bot_id):
        raise RuntimeError("Paper trades are still open; the bot was not switched to Live.")
    return closed


def used_today(session_factory, bot_id: int, now: datetime) -> int:
    """Opening contracts in the largest leg of the bot's paper entries today (New York
    day), as orders.used_today counts a live bot's sends."""
    start = datetime.combine(now.astimezone(MARKET_TZ).date(), time(), MARKET_TZ)
    with session_factory() as session:
        rows = session.execute(
            select(PaperFill.contracts, BotRun.legs)
            .join(BotRun, BotRun.id == PaperFill.run_id)
            .where(
                BotRun.bot_id == bot_id,
                PaperFill.kind == "entry",
                PaperFill.at >= start.astimezone(UTC),
            )
        ).all()
    return sum(n * max((int(leg.get("ratio") or 1) for leg in legs), default=1) for n, legs in rows)


def closed_groups(session, runs) -> dict[int, frozenset[int]]:
    """{run id: the groups its paper exits closed} for the paper runs among `runs`, for
    caps.RunRow.closed: a paper run has no BotExit rows to say so."""
    paper = {run.id: run for run in runs if run.paper}
    if not paper:
        return {}
    fills: dict[int, list] = {}
    for row in session.scalars(select(PaperFill).where(PaperFill.run_id.in_(paper))):
        fills.setdefault(row.run_id, []).append((row.kind, row.group, row.contracts))
    return {
        run_id: caps.paper_closed(run.legs, run.contracts, fills.get(run_id, ()))
        for run_id, run in paper.items()
    }
