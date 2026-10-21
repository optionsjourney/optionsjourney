"""Company headlines for the charts' News panel (#238, #239; Tracker2's news_bp
and finnhub_client.get_company_news): which symbols have company news, and
Finnhub's rows or Yahoo Finance's RSS feed turned into the panel's items. Pure."""

import re
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

# Stocks and ETFs only. Futures (/ES) and indices ($SPX, ^VIX) have no company news.
EQUITY = re.compile(r"^[A-Z][A-Z0-9.]{0,9}$")
NO_COMPANY_NEWS = "No company news for futures or indices"
DAYS = 7
LIMIT = 40
SUMMARY_CHARS = 400
YAHOO_SOURCE = "Yahoo Finance"


def has_company_news(symbol: str) -> bool:
    return bool(EQUITY.match(symbol or ""))


def item(row: dict) -> dict:
    """One headline as the panel reads it; `datetime` is epoch seconds. Only web
    addresses are kept as links, since the panel puts them in an href."""
    url = (row.get("url") or "").strip()
    return {
        "headline": row.get("headline") or "",
        "source": row.get("source") or "",
        "url": url if url.startswith(("https://", "http://")) else "",
        "summary": (row.get("summary") or "")[:SUMMARY_CHARS],
        "datetime": int(row.get("datetime") or 0),
    }


def headlines(rows, limit: int = LIMIT) -> list[dict]:
    """Newest first, without blank or repeated headlines (same words, any case), at most `limit`."""
    rows = rows if isinstance(rows, list) else []
    seen, out = set(), []
    for row in sorted(rows, key=lambda r: -(r.get("datetime") or 0)):
        news = item(row)
        key = news["headline"].strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(news)
        if len(out) >= limit:
            break
    return out


def _epoch(pub_date: str) -> int:
    try:
        return int(parsedate_to_datetime(pub_date).timestamp())
    except TypeError, ValueError, IndexError:
        return 0


def yahoo_headlines(xml_text: str, now_s: float, days: int = DAYS) -> list[dict]:
    """Yahoo Finance's per-ticker RSS feed as the panel's items: the last `days` days,
    newest first. Items with no readable date are left out. Bad XML raises ValueError."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        raise ValueError(f"Yahoo Finance sent something that isn't a feed ({e})") from e
    oldest = now_s - days * 86400
    rows = []
    for node in root.iter("item"):
        when = _epoch(node.findtext("pubDate") or "")
        if when < oldest:
            continue
        rows.append({
            "headline": (node.findtext("title") or "").strip(),
            "source": YAHOO_SOURCE,
            "url": (node.findtext("link") or "").strip(),
            "summary": (node.findtext("description") or "").strip(),
            "datetime": when,
        })  # fmt: skip
    return headlines(rows)
