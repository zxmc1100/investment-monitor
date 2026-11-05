"""Chart maths: web/app/ranges.js and scale.js, number for number (tests/tui/test_parity.py). x values are unix seconds. A
range is a name ("1M" … "ALL") or a period dragged on the chart: {from, to, prev, key}. Pure."""

from __future__ import annotations

import math
from datetime import datetime, timezone

DAY = 86400
INF = math.inf
HORIZON_MONTHS = {"6M": 6, "1Y": 12, "3Y": 36}


def _has(v) -> bool:
    return v is not None


def range_start(xs: list, r) -> float:
    if isinstance(r, dict):
        return r["from"]
    if not xs:
        return -INF
    last = xs[-1]
    if r == "1M":
        return last - 31 * DAY
    if r == "6M":
        return last - 183 * DAY
    if r == "1Y":
        return last - 366 * DAY
    if r == "YTD":
        return datetime(datetime.fromtimestamp(last, timezone.utc).year, 1, 1, tzinfo=timezone.utc).timestamp()
    return -INF


def range_end(r) -> float:
    return r["to"] if isinstance(r, dict) else INF


def default_range(p: dict) -> str:
    return p["range"] if p.get("range") and p["range"] in (p.get("ranges") or []) else "ALL"


def slice_from(x: list, series: list, start: float, end: float = INF, anchor: bool = False) -> dict:
    """The points from `start` to `end`; `anchor`: open on the last point BEFORE start — the close a period's return is
    measured from. Every per-point array slices with x."""
    i = 0
    while i < len(x) and x[i] < start:
        i += 1
    if anchor and i > 0:
        i -= 1
    j = i
    while j < len(x) and x[j] <= end:
        j += 1
    j = max(j, min(i + 2, len(x)))  # never fewer than two points
    i = min(i, max(0, j - 2))

    def cut(s):
        out = {**s}
        for k in ("y", "twr", "cash", "lo", "hi"):
            if s.get(k):
                out[k] = s[k][i:j]
        return out

    return {"x": x[i:j], "start": i, "series": [cut(s) for s in series]}


def can_norm(series: list) -> bool:
    lines = [s for s in series or [] if s.get("kind") != "markers"]
    return bool(lines) and all(isinstance(s.get("twr"), list) for s in lines)


def normalize(series: list, inception: bool = False) -> list:
    """Each line as its time-weighted return in % since its first point in the window."""
    out = []
    for s in series:
        twr = s.get("twr") or []
        base = 1 if inception else next((v for v in twr if _has(v)), None)
        out.append(
            {
                **s,
                "y": [
                    (twr[k] / base - 1) * 100 if base and k < len(twr) and _has(twr[k]) else None
                    for k in range(len(s["y"]))
                ],
            }
        )
    return out


def window_roi(series: list, inv: list, inception: bool = False) -> list:
    """Each line's ROI over the window: ((1 + y)·I − C₀) / (y₀·I₀ − C₀ + I) − 1."""
    if inception:
        return series
    out = []
    for s in series:
        y0, i0 = s["y"][0], inv[0]
        cash = s.get("cash")
        c0 = cash[0] if cash and cash[0] is not None else 0
        ok = _has(y0) and _has(i0)

        def one(k, y):
            den = (y0 / 100) * i0 - c0 + inv[k] if ok and _has(y) and _has(inv[k]) else 0
            return (((1 + y / 100) * inv[k] - c0) / den - 1) * 100 if den > 0 else None

        out.append({**s, "y": [one(k, y) for k, y in enumerate(s["y"])]})
    return out


def window_mwr(series: list, inv: list, xs: list, start: float, inception: bool = False) -> list:
    """Each line's money-weighted return over the window (Modified Dietz — the YTD KPI's method)."""
    p0 = xs[0] if inception else start
    out = []
    for s in series:
        cash = s.get("cash")
        c = lambda k: cash[k] if cash and cash[k] is not None else 0
        y0 = 0 if inception else s["y"][0]
        if not _has(y0) or not _has(inv[0]):
            out.append({**s, "y": [None] * len(s["y"])})
            continue
        i0 = 0 if inception else inv[0]
        c0 = 0 if inception else c(0)
        v0 = 0 if inception else (1 + y0 / 100) * i0 - c0
        p = (y0 / 100) * i0
        s1 = s2 = 0.0
        prev_i, prev_c = i0, c0
        ys = []
        for k, y in enumerate(s["y"]):
            if not inception and k == 0:
                ys.append(0)
                continue
            flow = (inv[k] - prev_i) - (c(k) - prev_c)
            prev_i, prev_c = inv[k], c(k)
            s1 += flow
            s2 += flow * xs[k]
            if not _has(y):
                ys.append(None)
                continue
            span = xs[k] - p0
            work = v0 + ((xs[k] * s1 - s2) / span if span > 0 else s1)
            ys.append(((y / 100) * inv[k] - p) / work * 100 if work > 0 else None)
        out.append({**s, "y": ys})
    return out


def chart_modes(p: dict, series: list) -> list:
    twr = can_norm(series)
    if isinstance(p.get("inv"), list):
        return ["ROI", "MWR", *(["TWR"] if twr else [])]
    return ["ROI", "TWR"] if twr else []


def last_value(ys: list):
    for v in reversed(ys):
        if _has(v):
            return v
    return None


def value_at(ys: list, i):
    if i is None:
        return last_value(ys)
    for k in range(min(i, len(ys) - 1), -1, -1):
        if _has(ys[k]):
            return ys[k]
    return None


def rebase(series: list) -> list:
    """Every series to 100 at the first point where the FIRST series has a value."""
    first = series[0].get("y") if series else None
    i0 = next((k for k, v in enumerate(first or []) if _has(v)), -1)
    if i0 < 0:
        return series
    out = []
    for s in series:
        ys, j = s["y"], i0
        while j < len(ys) and not _has(ys[j]):
            j += 1
        base = ys[j] if j < len(ys) else None
        out.append(
            {**s, "y": [None if k < i0 or not _has(v) or not base else v / base * 100 for k, v in enumerate(ys)]}
        )
    return out


def expand_at(series: list, n: int) -> list:
    """A series sent from x index `at` (a future's lines and bands) padded with nulls to the chart's n points."""

    def pad(a, at):
        return ([None] * at + list(a) + [None] * max(0, n - at - len(a)))[:n] if a else a

    out = []
    for s in series or []:
        if isinstance(s.get("at"), int) and not isinstance(s.get("at"), bool):
            t = {**s}
            if s.get("y"):
                t["y"] = pad(s["y"], s["at"])
            if s.get("lo"):
                t["lo"], t["hi"] = pad(s["lo"], s["at"]), pad(s["hi"], s["at"])
            out.append(t)
        else:
            out.append(s)
    return out


def cut_horizon(x: list, series: list, today_idx, months: int) -> dict:
    if today_idx is None:
        return {"x": x, "series": series}
    n = min(len(x), today_idx + 1 + months)

    def cut(s):
        t = {**s}
        for k in ("y", "lo", "hi"):
            if s.get(k):
                t[k] = s[k][:n]
        return t

    return {"x": x[:n], "series": [cut(s) for s in series]}


def legend_rest(n: int, today_idx, start: int = 0) -> int:
    return n - 1 if today_idx is None else max(0, min(n - 1, today_idx - start))


def legend_value(series: list, line: dict, idx, today_idx):
    """A legend line's value at the cursor: the line itself up to today; past it, its projected median."""
    if today_idx is not None and idx is not None and idx > today_idx:
        name = "NOW" if line.get("name") == "YOU" else line.get("name")
        fut = next((s for s in series if s.get("name") == f"{name} →"), None)
        if fut:
            return value_at(fut["y"], idx)
    return value_at(line["y"], idx)


# ── scale.js ─────────────────────────────────────────────────────────────────────────────────────
def extent(values) -> list | None:
    lo, hi = INF, -INF
    for v in values:
        if v is not None and math.isfinite(v):
            lo, hi = min(lo, v), max(hi, v)
    if lo == INF:
        return None
    return [lo - 1, hi + 1] if lo == hi else [lo, hi]


def nice_ticks(lo: float, hi: float, n: int = 5) -> list:
    span = hi - lo
    mag = 10 ** math.floor(math.log10(span / n))
    step = next((s for s in (m * mag for m in (1, 2, 2.5, 5, 10)) if span / s <= n), 10 * mag)
    out, v = [], math.ceil(lo / step) * step
    while v <= hi + step * 1e-9:
        out.append(round(v, 10))
        v += step
    return out


def tick_decimals(ticks: list) -> int:
    step = abs(ticks[1] - ticks[0]) if len(ticks) > 1 else 1
    d = 0
    while d < 3 and abs(round(step * 10**d) - step * 10**d) > 1e-9:
        d += 1
    return d
