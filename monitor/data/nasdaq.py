"""Nasdaq's public dividend history — ex date and pay date of every dividend of a Nasdaq-listed US company (NYSE
lines answer N/A). No key. Used for real dividend pay dates (monitor.portfolio.dividends)."""
from __future__ import annotations

import json
import urllib.request
from datetime import datetime

URL = "https://api.nasdaq.com/api/quote/{symbol}/dividends?assetclass=stocks"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/126.0 Safari/537.36",
           "Accept": "application/json, text/plain, */*", "Origin": "https://www.nasdaq.com",
           "Referer": "https://www.nasdaq.com/"}


def _iso(us_date: str) -> str | None:
    try:
        return datetime.strptime(str(us_date).strip(), "%m/%d/%Y").date().isoformat()
    except ValueError:
        return None


def parse_dividends(body: dict) -> list[list[str]]:
    """[[ex, pay], ...] oldest first from the API's answer; rows without both dates are left out."""
    rows = (((body or {}).get("data") or {}).get("dividends") or {}).get("rows") or []
    pairs = {(ex, pay) for r in rows if (ex := _iso(r.get("exOrEffDate"))) and (pay := _iso(r.get("paymentDate")))}
    return [list(p) for p in sorted(pairs)]


def dividend_history(symbol: str, timeout: float = 15) -> list[list[str]]:
    """[[ex, pay], ...] of a US symbol; [] when Nasdaq has none (NYSE, not a dividend payer) or does not answer."""
    req = urllib.request.Request(URL.format(symbol=symbol), headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return parse_dividends(json.loads(r.read().decode("utf-8")))
    except Exception:
        return []
