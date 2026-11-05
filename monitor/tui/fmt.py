"""Number and date formatting: web/app/fmt.js, cell for cell (tests/tui/test_parity.py). Pure."""

from __future__ import annotations

import math
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

MON = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
NUMERIC = {"eur", "eur+", "pct", "pct+", "pct+r", "num", "num+", "int", "bp+", "mult", "qty", "px"}
MARKS = {"H": "●", "W": "★", "HW": "●★"}
DAY = 86400


def is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def js_str(v) -> str:
    """String(v) as JavaScript writes it — a key or a cell reads the same here as in the browser."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e21:
        return str(int(v))
    if isinstance(v, float) and 1e-6 <= abs(v) < 1e-4:  # Python writes these with an exponent, JavaScript does not
        return format(Decimal(repr(v)), "f")
    return "" if v is None else str(v)


def _fixed(v: float, nd: int) -> Decimal:
    """toFixed: the exact binary value, ties away from zero (a plain format rounds ties to even)."""
    return Decimal(v).quantize(Decimal(1).scaleb(-nd), rounding=ROUND_HALF_UP)


def _group(d: Decimal, nd: int) -> str:
    return f"{abs(d):,.{nd}f}"


def _plain(v, nd, pre, post) -> tuple:
    r = _fixed(v, nd)
    return ("-" if r < 0 else "") + pre + _group(r, nd) + post, ""


def _signed(v, nd, pre, post) -> tuple:
    r = _fixed(v, nd)
    sign = "+" if r > 0 else "-" if r < 0 else ""
    return sign + pre + _group(r, nd) + post, "up" if r > 0 else "dn" if r < 0 else ""


def _span(v, lo, hi) -> tuple:
    """toLocaleString with lo to hi decimals, on the number as written."""
    d = Decimal(repr(float(v))).quantize(Decimal(1).scaleb(-hi), rounding=ROUND_HALF_UP)
    text = f"{d:,.{hi}f}"
    if hi > lo:
        whole, _, frac = text.partition(".")
        frac = frac.rstrip("0").ljust(lo, "0")
        text = f"{whole}.{frac}" if frac else whole
    return text, ""


def fmt_date(v) -> str:
    s = str(v)
    if len(s) >= 10 and s[4] == "-" and s[7] == "-" and s[:4].isdigit() and s[5:7].isdigit() and s[8:10].isdigit():
        return f"{s[8:10]} {MON[int(s[5:7]) - 1]} {s[2:4]}"
    return s


def fmt_clock(d: datetime) -> str:
    return f"{d.day:02d} {MON[d.month - 1]} {d.year % 100:02d} {d:%H:%M:%S}"


def fmt_stamp(iso, now: datetime | None = None) -> str:
    """A status-bar time: today's as HH:MM:SS, an older one as DD MON YY HH:MM, in local time."""
    if not iso:
        return "—"
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    except ValueError:
        return "—"
    d = d.astimezone() if d.tzinfo else d
    now = now or datetime.now()
    return f"{d:%H:%M:%S}" if d.date() == now.date() else f"{d.day:02d} {MON[d.month - 1]} {d.year % 100:02d} {d:%H:%M}"


def time_ticks(ticks: list, incr: float) -> list[str]:
    """Time-axis labels: a year for year ticks, 04 OCT for days, OCT for months — and the year on the first month tick
    of a new one. Never "OCT 26": next to day ticks it reads as 26 October."""
    out = []
    for i, t in enumerate(ticks):
        d = datetime.fromtimestamp(t)
        if incr >= 360 * DAY:
            out.append(str(d.year))
        elif incr >= 28 * DAY:
            turned = i > 0 and datetime.fromtimestamp(ticks[i - 1]).year != d.year
            out.append(str(d.year) if turned else MON[d.month - 1])
        else:
            out.append(f"{d.day:02d} {MON[d.month - 1]}")
    return out


def fmt(v, spec="text") -> tuple[str, str]:
    """(text, class) of a value: `spec` is "<kind>[:<decimals>]"; the classes are terminal.css's: up, dn, dim, am, na."""
    if v is None or (isinstance(v, float) and not math.isfinite(v)):
        return "—", "na"
    kind, _, arg = str(spec or "text").partition(":")
    if kind in NUMERIC and not is_num(v):
        return js_str(v), ""
    nd = int(arg) if arg else None
    if kind == "eur":
        return _plain(v, 2 if nd is None else nd, "€", "")
    if kind == "eur+":
        return _signed(v, 2 if nd is None else nd, "€", "")
    if kind == "pct":
        return _plain(v, 1 if nd is None else nd, "", "%")
    if kind == "pct+":
        return _signed(v, 1 if nd is None else nd, "", "%")
    if kind == "pct+r":
        text, cls = _signed(v, 1 if nd is None else nd, "", "%")
        return text, "dn" if cls == "up" else "up" if cls == "dn" else ""
    if kind == "num":
        return _plain(v, 2 if nd is None else nd, "", "")
    if kind == "num+":
        return _signed(v, 2 if nd is None else nd, "", "")
    if kind == "int":
        return _plain(v, 0, "", "")
    if kind == "bp+":
        return _signed(v, 1 if nd is None else nd, "", "bp")
    if kind == "mult":
        return _plain(v, 1 if nd is None else nd, "", "×")
    if kind == "qty":
        return _span(v, 0, 6)  # shares as entered: 4, 0.15, 13.513513
    if kind == "px":
        return _span(v, 2, 4)  # a price per share: 240.00, 33.3333
    if kind == "mark":
        return MARKS.get(v, js_str(v)), "am"
    if kind == "date":
        return fmt_date(v), "dim"
    if kind == "side":
        return js_str(v), "up" if v == "BUY" else "dn" if v == "SELL" else ""
    return js_str(v), ""
