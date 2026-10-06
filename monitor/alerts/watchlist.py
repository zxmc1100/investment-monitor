"""Watchlist: [{ticker, name, added}] in local/buffer/watchlist.json.

A missing, corrupt or hand-mangled file reads as [] (bad entries are skipped); a file there but unreadable
right now (OSError: out of file handles, permissions) raises, so an add or remove never saves over it.
path None means "no watchlist" (tests, the public export): it loads [] and saves nothing.
Lives in the alerts layer, not server/, because MKT and SEC (screens) read it.
"""
import json
import logging
import threading
from datetime import date
from pathlib import Path

from monitor.alerts.jsonfile import write_atomic

log = logging.getLogger("monitor.watchlist")
_LOCK = threading.Lock()               # API add/remove run on threadpool threads


def load(path: Path | None) -> list[dict]:
    if path is None or not Path(path).exists():
        return []
    text = Path(path).read_text(encoding="utf-8")       # unreadable now (OSError): raised, never saved over
    try:
        raw = json.loads(text)
    except Exception as e:
        log.warning("unreadable watchlist %s (%s) — treating as empty", path, e)
        return []
    out, seen = [], set()
    for it in raw if isinstance(raw, list) else []:
        t = it.get("ticker") if isinstance(it, dict) else None
        if not isinstance(t, str) or not t.strip() or t.strip().upper() in seen:
            continue
        t = t.strip().upper()
        seen.add(t)
        out.append({"ticker": t, "name": str(it.get("name") or t), "added": str(it.get("added") or "")})
    return out


def tickers(path: Path | None) -> list[str]:
    return [w["ticker"] for w in load(path)]


def add(path: Path | None, ticker: str, name: str, today: date | None = None) -> list[dict]:
    """Idempotent: adding a watched ticker again changes nothing."""
    t = str(ticker).strip().upper()
    with _LOCK:
        items = load(path)
        if any(w["ticker"] == t for w in items):
            return items
        items.append({"ticker": t, "name": name or t, "added": (today or date.today()).isoformat()})
        if path is not None:
            write_atomic(path, items)
        return items


def remove(path: Path | None, ticker: str) -> list[dict]:
    t = str(ticker).strip().upper()
    with _LOCK:
        items = load(path)
        kept = [w for w in items if w["ticker"] != t]
        if path is not None and len(kept) != len(items):
            write_atomic(path, kept)
        return kept
