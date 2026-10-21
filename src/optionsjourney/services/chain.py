"""The option chain behind the Trade page and the bots (#90): Tracker2's
analyzer data, with Tracker2's answers (core/chain.py).

- Expirations: TastyTrade's nested chain (the weekly root for SPX and RUT),
  Schwab's as the backup.
- Chains: Schwab's chain snapshot (one call, every strike, with greeks), or
  without Schwab, TastyTrade's live quotes through the quote hub.
- Futures (/ES, #53): TastyTrade's series and DXLink quotes, with its REST
  quotes filling the gaps, as Tracker2's analyzer_futures.py."""

import logging
import threading
import time as clock
from collections.abc import Callable
from datetime import UTC, date, datetime

from optionsjourney.bots import futures as bot_futures
from optionsjourney.brokers.credentials import CredentialsUnavailable
from optionsjourney.brokers.http import BrokerError
from optionsjourney.core import black_scholes as math_
from optionsjourney.core import chain as core_chain
from optionsjourney.core.market import MARKET_TZ
from optionsjourney.core.quotes import DXLINK_KEY, dxlink_symbol
from optionsjourney.services.watchlist_quotes import snapshot_quotes

logger = logging.getLogger(__name__)

CHAIN_TTL = 30
EXPIRATIONS_TTL = 3600
EARNINGS_TTL = 3600
SCHWAB_STRIKES = 400
TASTY_STRIKES = 120  # nearest the price: what a new chain waits for the streams to price
TASTY_WAIT = 4.0  # seconds for the streams to price a new chain
TASTY_COVERAGE = 0.8
_UNAVAILABLE = (BrokerError, CredentialsUnavailable, OSError, TimeoutError)


def _tasty(clients):
    return next((c for c in clients if hasattr(c, "quote_token")), None)


def _schwab(clients):
    return next((c for c in clients if hasattr(c, "option_expirations")), None)


class Cache:
    """Answers kept a while by key. Empty answers aren't kept, so a broker
    that failed is asked again next time."""

    def __init__(self, now: Callable[[], float] = clock.monotonic):
        self._clock = now
        self._lock = threading.Lock()
        self._items: dict = {}

    def get(self, key, ttl: float, produce: Callable):
        with self._lock:
            hit = self._items.get(key)
        if hit and hit[0] > self._clock():
            return hit[1]
        value = produce()
        if value:
            with self._lock:
                self._items[key] = (self._clock() + ttl, value)
        return value


def _await_quotes(hub, keys: set[str]) -> None:
    """Give the streams up to TASTY_WAIT seconds to price most of `keys`."""
    deadline = clock.monotonic() + TASTY_WAIT
    while clock.monotonic() < deadline:
        priced = sum(1 for key in keys if (q := hub.latest(key)) and q.bid is not None)
        if priced >= TASTY_COVERAGE * len(keys):
            return
        clock.sleep(0.25)


def _side_quote(hub, key: str, **extra) -> dict | None:
    quote = hub.latest(key)
    if quote is None:
        return None
    return {
        "bid": None if quote.bid is None else float(quote.bid),
        "ask": None if quote.ask is None else float(quote.ask),
        "at": quote.at,  # when it arrived: a bot reads it only while the stream is healthy
        **_greek(hub, key),
        **extra,
    }


def _greek(hub, key: str) -> dict:
    """The stream's Greeks delta for the option and when it arrived (bots slice 10),
    or nothing: a hub without Greeks, or none yet."""
    found = hub.delta(key) if hasattr(hub, "delta") else None
    if not found:
        return {}
    return {"stream_delta": found[0], "stream_delta_at": found[1]}


def _today() -> date:
    return datetime.now(MARKET_TZ).date()


def _root_item(items: list[dict], ticker: str) -> dict | None:
    wanted = core_chain.WEEKLY_ROOTS.get(ticker, ticker)
    return next((i for i in items if i.get("root-symbol") == wanted), items[0] if items else None)


class ChainData:
    def __init__(self, cache: Cache | None = None):
        self.cache = cache or Cache()

    # --- expirations and chains ---

    def expirations(self, clients: list, ticker: str) -> dict:
        """{"expirations": [...]}, and for futures each date's "series"."""
        if ticker.startswith("/"):
            series = self.futures_series(clients, ticker)
            now = datetime.now(UTC)
            live = [d for d, e in series.items() if datetime.fromisoformat(e["expires-at"]) > now]
            return {
                "expirations": live,
                "series": {d: core_chain.series_info(e) for d, e in series.items()},
            }
        return {"expirations": self._expirations(clients, ticker)}

    def _expirations(self, clients: list, ticker: str) -> list[str]:
        def produce():
            tasty = _tasty(clients)
            if tasty is not None:
                try:
                    item = _root_item(tasty.option_chain(ticker), ticker)
                    days = [
                        date.fromisoformat(e["expiration-date"])
                        for e in (item or {}).get("expirations", [])
                    ]
                    if days:
                        return core_chain.filter_expirations(days, _today())
                except _UNAVAILABLE as e:
                    logger.info("TastyTrade expirations for %s unavailable: %s", ticker, e)
            schwab = _schwab(clients)
            if schwab is not None:
                try:
                    days = schwab.option_expirations(ticker)
                    return core_chain.filter_expirations(days, _today())
                except _UNAVAILABLE as e:
                    logger.info("Schwab expirations for %s unavailable: %s", ticker, e)
            return None

        return self.cache.get(("exp", ticker), EXPIRATIONS_TTL, produce) or []

    def _schwab_rows(self, client, ticker: str, expiration: str) -> dict | None:
        """strike -> {"call": contract, "put": contract}: Schwab's contracts on
        the root the chain prices (SPXW, not the SPX monthly)."""
        body = client.option_chain(ticker, date.fromisoformat(expiration), SCHWAB_STRIKES)
        root = core_chain.WEEKLY_ROOTS.get(ticker, ticker)
        rows: dict[float, dict] = {}
        for side, key in (("call", "callExpDateMap"), ("put", "putExpDateMap")):
            for day_key, strikes in (body.get(key) or {}).items():
                if not day_key.startswith(expiration):
                    continue
                for strike, contracts in strikes.items():
                    picked = next(
                        (c for c in contracts if c.get("symbol", "").startswith(root + " ")),
                        contracts[0] if contracts else None,
                    )
                    if picked is not None:
                        rows.setdefault(float(strike), {})[side] = picked
        return rows or None

    def _tasty_rows(self, client, hub, ticker: str, expiration: str) -> dict | None:
        """strike -> {"call": quote dict, "put": quote dict} from the quote hub.
        Every listed strike is subscribed; the wait is only for the strikes
        nearest the price, and the rest show once the streams price them."""
        item = _root_item(client.option_chain(ticker), ticker)
        symbols: dict[float, dict[str, str]] = {}
        for exp in (item or {}).get("expirations", []):
            if exp["expiration-date"] == expiration:
                for s in exp["strikes"]:
                    symbols[float(s["strike-price"])] = {"call": s["call"], "put": s["put"]}
        if not symbols:
            return None
        hub.subscribe({ticker})
        spot = self.underlying_last([client], hub, ticker)
        near = sorted(symbols)
        if spot:
            near = sorted(symbols, key=lambda k: abs(k - spot))[:TASTY_STRIKES]
        hub.subscribe({sym for k in symbols for sym in symbols[k].values()})
        _await_quotes(hub, {sym for k in near for sym in symbols[k].values()})
        rows = {}
        for k in sorted(symbols):
            row = {}
            for side, key in symbols[k].items():
                found = _side_quote(hub, key, symbol=key)
                if found is not None:
                    row[side] = found
            rows[k] = row
        return rows

    def _rows(
        self, clients, hub, ticker, expiration, prefer: str | None = None
    ) -> tuple[dict | None, str | None]:
        """The chain from the preferred broker ("schwab" or "tastytrade": the
        ticket's account), else Schwab first; the other broker if that fails."""
        order = ["schwab", "tastytrade"]
        if prefer in order:
            order.remove(prefer)
            order.insert(0, prefer)
        for name in order:
            client = _schwab(clients) if name == "schwab" else _tasty(clients)
            if client is None:
                continue
            try:
                if name == "schwab":
                    rows = self._schwab_rows(client, ticker, expiration)
                else:
                    rows = self._tasty_rows(client, hub, ticker, expiration)
                if rows:
                    return rows, name
            except _UNAVAILABLE as e:
                logger.info("%s chain for %s %s unavailable: %s", name, ticker, expiration, e)
        return None, None

    def option_rows(self, clients: list, hub, ticker: str, expiration: str) -> dict | None:
        """strike -> {"call": {"bid", "ask", "symbol"}, "put": ...} for an
        expiration, for the bots: the same chain the Trade page reads."""
        rows, _ = self._rows(clients, hub, ticker, expiration)
        return rows

    def chain_table(
        self, clients: list, hub, ticker: str, expiration: str, prefer: str | None = None
    ) -> dict | None:
        """The option chain panel's table: bid, ask, delta, open interest and
        volume per strike, with each side's DXLink symbol for live quotes, and
        which broker it came from (`prefer` first: the ticket account's)."""

        if ticker.startswith("/"):
            return self._futures_table(clients, hub, ticker, expiration)

        def produce():
            rows, source = self._rows(clients, hub, ticker, expiration, prefer)
            if not rows:
                return None
            spot = self.underlying_last(clients, hub, ticker)
            years = (
                max(
                    (
                        math_.expiry_close_utc(date.fromisoformat(expiration)) - datetime.now(UTC)
                    ).total_seconds(),
                    60,
                )
                / math_.YEAR
            )
            table = _table_rows(rows, spot, years, math_.RATE)
            return {"spot": spot, "expiration": expiration, "source": source, "rows": table}

        return self.cache.get(("table", ticker, expiration, prefer), CHAIN_TTL, produce)

    # --- futures (Tracker2's analyzer_futures.py) ---

    def _futures_nested(self, clients: list, ticker: str) -> dict | None:
        tasty = next((c for c in clients if hasattr(c, "futures_option_nested")), None)
        if tasty is None:
            return None

        def produce():
            try:
                return tasty.futures_option_nested(ticker.lstrip("/"))
            except _UNAVAILABLE as e:
                logger.info("TastyTrade futures chain for %s unavailable: %s", ticker, e)
                return None

        return self.cache.get(("fut-nested", ticker), EXPIRATIONS_TTL, produce)

    def futures_nested(self, clients: list, ticker: str) -> dict | None:
        """TastyTrade's nested futures chain for a root ("/ES"), cached as the page's
        own (bots slice 17: a bot resolves its legs in it, bots/futures.py)."""
        return self._futures_nested(clients, ticker)

    def futures_series(self, clients: list, ticker: str) -> dict[str, dict]:
        nested = self._futures_nested(clients, ticker) or {}
        expirations = [e for oc in nested.get("option-chains", []) for e in oc["expirations"]]
        return core_chain.pick_series(expirations, _today())

    def _contract_symbol(self, clients: list, ticker: str, contract: str) -> str | None:
        """A futures contract's DXLink symbol ("/ESZ6" -> "/ESZ26:XCME")."""
        return bot_futures.contract_stream(self._futures_nested(clients, ticker), contract)

    def futures_last(self, clients, hub, contract: str) -> float | None:
        """One futures contract's price ("/ESH7"), not its root's front month."""
        contract = contract.strip().upper().removeprefix(".")
        return self._contract_last(clients, hub, core_chain.chain_root(contract), contract)

    def _contract_last(self, clients, hub, ticker: str, contract: str) -> float | None:
        stream = self._contract_symbol(clients, ticker, contract)
        if stream:
            key = DXLINK_KEY + stream
            hub.subscribe({key})
            deadline = clock.monotonic() + 2
            while clock.monotonic() < deadline:
                quote = hub.latest(key)
                if quote is not None and (quote.last or quote.mark):
                    return float(quote.last if quote.last is not None else quote.mark)
                clock.sleep(0.2)
        tasty = next((c for c in clients if hasattr(c, "market_data")), None)
        if tasty is None:
            return None
        try:
            items = tasty.market_data({"future": [contract]})
        except _UNAVAILABLE as e:
            logger.info("TastyTrade quote for %s unavailable: %s", contract, e)
            return None
        item = items[0] if items else {}
        price = item.get("last") or item.get("mark")
        return float(price) if price else None

    def _futures_raw(self, clients, hub, ticker: str, expiration: str) -> dict | None:
        """The series' quotes from the streams, for the strikes nearest the
        contract's price, with TastyTrade's REST quotes filling what the
        streams haven't priced yet."""
        series = self.futures_series(clients, ticker).get(expiration)
        if series is None:
            return None

        def produce():
            contract = series["underlying-symbol"]
            stream = self._contract_symbol(clients, ticker, contract)
            spot = self._contract_last(clients, hub, ticker, contract)
            listed = {
                float(k["strike-price"]): {
                    "call": (k["call-streamer-symbol"], k["call"]),
                    "put": (k["put-streamer-symbol"], k["put"]),
                }
                for k in series.get("strikes", [])
            }
            if not listed or not stream or not spot:
                return None
            chosen = sorted(sorted(listed, key=lambda k: abs(k - spot))[:TASTY_STRIKES])
            keys = {DXLINK_KEY + listed[k][side][0] for k in chosen for side in ("call", "put")}
            hub.subscribe(keys)
            _await_quotes(hub, keys)
            rows = {
                k: {
                    side: _side_quote(
                        hub, DXLINK_KEY + listed[k][side][0], symbol=listed[k][side][1]
                    )
                    for side in ("call", "put")
                }
                for k in chosen
            }
            self._fill_from_rest(clients, rows, listed)
            return {"spot": spot, "rows": rows, "listed": listed, "stream": stream}

        return self.cache.get(("fut-raw", ticker, expiration), CHAIN_TTL, produce)

    def _fill_from_rest(self, clients, rows: dict, listed: dict) -> None:
        tasty = next((c for c in clients if hasattr(c, "market_data")), None)
        missing = {
            listed[k][side][1]: (k, side)
            for k, row in rows.items()
            for side in ("call", "put")
            if not row.get(side) or row[side].get("bid") is None
        }
        if tasty is None or not missing:
            return
        symbols = list(missing)
        for start in range(0, len(symbols), 50):  # a longer list overflows the address
            try:
                items = tasty.market_data({"future-option": symbols[start : start + 50]})
            except _UNAVAILABLE as e:
                logger.info("TastyTrade futures option quotes unavailable: %s", e)
                continue
            for item in items:
                if item.get("symbol") in missing:
                    k, side = missing[item["symbol"]]
                    rows[k][side] = {
                        "bid": None if item.get("bid") is None else float(item["bid"]),
                        "ask": None if item.get("ask") is None else float(item["ask"]),
                        "symbol": item["symbol"],
                    }

    def _futures_table(self, clients, hub, ticker: str, expiration: str) -> dict | None:
        raw = self._futures_raw(clients, hub, ticker, expiration)
        series = self.futures_series(clients, ticker).get(expiration)
        if raw is None or series is None:
            return None
        rows = {
            k: {
                side: (raw["rows"][k].get(side) or {})
                | {"stream_symbol": raw["listed"][k][side][0]}
                for side in ("call", "put")
            }
            for k in raw["rows"]
        }
        years = (
            max(
                (datetime.fromisoformat(series["expires-at"]) - datetime.now(UTC)).total_seconds(),
                60,
            )
            / math_.YEAR
        )
        table = _table_rows(rows, raw["spot"], years, 0.0)
        if not table:
            return None
        return {
            "spot": raw["spot"],
            "expiration": expiration,
            "source": "tastytrade",
            "rows": table,
            "multiplier": core_chain.series_info(series)["multiplier"],
        }

    # --- the underlying ---

    def underlying_last(self, clients: list, hub, ticker: str) -> float | None:
        if ticker.startswith("/"):
            series = self.futures_series(clients, ticker)
            if not series:
                return None
            front = series[min(series)]["underlying-symbol"]
            return self._contract_last(clients, hub, ticker, front)
        hub.subscribe({ticker})
        quote = hub.latest(ticker)
        if quote is not None:
            price = quote.last if quote.last is not None else quote.mark
            if price is not None:
                return float(price)

        def produce():
            found = snapshot_quotes(clients, [ticker]).get(ticker) or {}
            return {"last": float(found["last"])} if found.get("last") is not None else None

        hit = self.cache.get(("underlying", ticker), CHAIN_TTL, produce)
        return hit["last"] if hit else None

    def earnings(self, clients: list, ticker: str) -> dict | None:
        """The next earnings report, from TastyTrade, or None."""
        if ticker.startswith("/"):
            return None
        tasty = next((c for c in clients if hasattr(c, "market_metrics")), None)
        if tasty is None:
            return None

        def produce():
            try:
                items = tasty.market_metrics([ticker])
            except _UNAVAILABLE as e:
                logger.info("TastyTrade earnings for %s unavailable: %s", ticker, e)
                return None
            report = (items[0].get("earnings") if items else None) or {}
            when = report.get("expected-report-date")
            if not when or report.get("visible") is False or date.fromisoformat(when) < _today():
                return {"earnings": None}
            return {
                "earnings": {
                    "date": when,
                    "time_of_day": report.get("time-of-day") or None,
                    "estimated": bool(report.get("estimated")),
                }
            }

        hit = self.cache.get(("earn", ticker), EARNINGS_TTL, produce)
        return hit["earnings"] if hit else None


def _table_rows(rows: dict, spot, years: float, rate: float) -> list[dict]:
    """[{strike, call, put}] ascending; strikes with neither side are left out."""
    table = []
    for strike in sorted(rows):
        entry = {"strike": strike}
        for side in ("call", "put"):
            entry[side] = _table_side(rows[strike].get(side), side, strike, spot, years, rate)
        if entry["call"] or entry["put"]:
            table.append(entry)
    return table


def _table_side(contract, side: str, strike: float, spot, years: float, rate: float):
    """One side of a chain table row; None without a market (ask of zero)."""
    if not contract or not contract.get("ask") or contract["ask"] <= 0:
        return None
    bid, ask = float(contract.get("bid") or 0), float(contract["ask"])
    delta = contract.get("delta")
    if (delta is None or delta == -999) and spot:
        iv = math_.implied_vol((bid + ask) / 2, spot, strike, years, rate, side.upper())
        if iv:
            delta = math_.greeks(spot, strike, years, rate, iv, side.upper())["delta"]
    return {
        "bid": bid,
        "ask": ask,
        "delta": None if delta in (None, -999) else round(float(delta), 4),
        "oi": contract.get("openInterest"),
        "volume": contract.get("totalVolume"),
        "stream_symbol": contract.get("stream_symbol") or dxlink_symbol(contract["symbol"]),
    }
