"""Presentation helpers shared by every screen module: JSON-safe numbers, KPI nodes, chart
thinning and epoch axes, and the readiness check of the optimizer screens. No financial math
lives here."""
from __future__ import annotations

import math
from datetime import date

import pandas as pd

from monitor.data.buffer import never_quoted
from monitor.portfolio import snapshot

PUB, PRIV = "public", "private"
MAX_POINTS = 1000
NEEDS_TWO = "NEEDS AT LEAST TWO PRICED POSITIONS"


def two_priced(ctx) -> str | None:
    """OPT / RISK cold reason: NEEDS_TWO while fewer than two open positions are priced — a
    position counts until the quote buffer knows Yahoo never priced it (a cold start knows nothing,
    so the compute runs). An unreadable book is no reason: the compute then reports the error."""
    try:
        held = list(snapshot.load_book(ctx.portfolio_csv)["holdings"])
    except Exception:
        return None
    priced = set(held) - never_quoted(held, buffer_dir=ctx.buffer_dir)
    return NEEDS_TWO if len(priced) < 2 else None


def num(v) -> float | None:
    """JSON-safe number: None/NaN/inf → None, numpy → float."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def kpi(k: str, v, fmt: str, vis: str = PRIV) -> dict:
    return {"k": k, "v": v if isinstance(v, str) else num(v), "fmt": fmt, "vis": vis}


def thin_index(idx: pd.DatetimeIndex, n: int = MAX_POINTS) -> pd.DatetimeIndex:
    if len(idx) <= n:
        return idx
    if n < 2:
        return idx[-1:]
    sel = idx[:: math.ceil((len(idx) - 1) / (n - 1))]
    return sel if sel[-1] == idx[-1] else sel.append(idx[-1:])


def epoch(idx) -> list[int]:
    return [int(pd.Timestamp(t).timestamp()) for t in idx]


def bar_on_or_after(idx: pd.DatetimeIndex, d) -> pd.Timestamp | None:
    i = idx.searchsorted(pd.Timestamp(d))
    return idx[i] if i < len(idx) else None


def session_day(quotes: dict, today: date | None = None) -> date | None:
    """Date of the latest quoted session when it is not today (weekend, holiday, pre-open) — a
    DAY % then describes that session and the screen says so. None on a trading day."""
    dates = [v["date"] for v in quotes.values() if v and v.get("date")]
    if not dates or max(dates) >= (today or date.today()).isoformat():
        return None
    return date.fromisoformat(max(dates))


def rebased(s: pd.Series, points: int = 52) -> list[float] | None:
    """The last year of a close series, ~`points` samples (last close kept), rebased to 100 — the
    public shape of a price path never carries its level."""
    s = s.dropna()
    if len(s) < 2:
        return None
    s = s[s.index >= s.index[-1] - pd.Timedelta(days=365)]
    pts = s.iloc[:: max(1, len(s) // points)]
    if pts.index[-1] != s.index[-1]:
        pts = pd.concat([pts, s.iloc[-1:]])
    base = float(s.iloc[0])
    return [num(round(float(v) / base * 100, 4)) for v in pts] if base else None
