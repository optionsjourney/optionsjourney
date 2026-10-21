"""Company headlines for the charts' News panel (#238, #239; Tracker2's
finnhub_client.company_news): the last 7 days for one ticker, each ticker
fetched at most every 5 minutes (decision 0090).

- **Without a key:** Yahoo Finance's per-ticker RSS feed. It needs no sign-up,
  but Yahoo doesn't promise it, so it can change or stop.
- **With the optional Finnhub API key setting:** Finnhub's company news, as in
  Tracker2. The key goes in a header, so it never shows in a logged address."""

import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta

import httpx

from optionsjourney.core import news as core
from optionsjourney.core.market import MARKET_TZ
from optionsjourney.services import settings

logger = logging.getLogger(__name__)

FINNHUB_URL = "https://finnhub.io/api/v1/company-news"
YAHOO_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline"
USER_AGENT = "Mozilla/5.0 (OptionsJourney)"  # Yahoo turns away requests without a browser's
CACHE_S = 300
TIMEOUT_S = 8
MAX_FEED_BYTES = 2_000_000


class NewsError(Exception):
    pass


def fetch_finnhub(symbol: str, start: str, end: str, key: str):
    response = httpx.get(FINNHUB_URL, params={"symbol": symbol, "from": start, "to": end},
                         headers={"X-Finnhub-Token": key}, timeout=TIMEOUT_S)  # fmt: skip
    if response.status_code in (401, 403):
        raise NewsError(
            f"Finnhub refused the key (HTTP {response.status_code}); check it in Settings"
        )
    if response.status_code != 200:
        raise NewsError(f"Finnhub HTTP {response.status_code}")
    return response.json()


def fetch_yahoo(symbol: str) -> str:
    response = httpx.get(YAHOO_URL, params={"s": symbol, "region": "US", "lang": "en-US"},
                         headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_S,
                         follow_redirects=True)  # fmt: skip
    if response.status_code != 200:
        raise NewsError(f"Yahoo Finance HTTP {response.status_code}")
    if len(response.content) > MAX_FEED_BYTES:
        raise NewsError("Yahoo Finance sent too much")
    return response.text


_cache: dict[tuple[str, str], tuple[float, list[dict]]] = {}
_lock = threading.Lock()


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def _key(session_factory) -> str:
    try:
        return settings.get(session_factory, "finnhub_api_key").strip()
    except Exception:  # noqa: BLE001 -- no key readable: Yahoo instead
        logger.exception("couldn't read the Finnhub key")
        return ""


def company_news(session_factory, symbol: str, fetch: Callable | None = None,
                 fetch_feed: Callable | None = None,
                 clock: Callable[[], float] = time.time) -> dict:  # fmt: skip
    """{"symbol", "news": [...], "note"?}. `note` explains an empty list that isn't an error.
    Raises NewsError when the source can't answer; a failed fetch isn't cached."""
    fetch, fetch_feed = fetch or fetch_finnhub, fetch_feed or fetch_yahoo
    sym = (symbol or "").strip().upper()
    if not core.has_company_news(sym):
        return {"symbol": sym, "news": [], "note": core.NO_COMPANY_NEWS}
    key = _key(session_factory)
    source = "finnhub" if key else "yahoo"
    now = clock()
    with _lock:
        hit = _cache.get((source, sym))
    if hit and now - hit[0] < CACHE_S:
        return {"symbol": sym, "news": hit[1]}
    try:
        if key:
            today = datetime.fromtimestamp(now, MARKET_TZ).date()
            start = (today - timedelta(days=core.DAYS)).isoformat()
            items = core.headlines(fetch(sym, start, today.isoformat(), key))
        else:
            items = core.yahoo_headlines(fetch_feed(sym), now)
    except NewsError:
        raise
    except Exception as e:  # noqa: BLE001 -- network, JSON or feed trouble: the panel says so
        logger.warning("news fetch failed for %s: %s", sym, e)
        raise NewsError(str(e) or type(e).__name__) from e
    with _lock:
        _cache[(source, sym)] = (now, items)
    return {"symbol": sym, "news": items}
