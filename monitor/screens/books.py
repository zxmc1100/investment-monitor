"""BOOKS — every portfolio you keep, side by side: yours (input/portfolio.csv) and each other book
(input/books/<NAME>/portfolio.csv; BOOK <NAME> enters its trades, its first one creates it), on one chart against the
same benchmarks.

Each book is PORT's own computation on its file (port.compute — the same tiers, buffers and formulas: ROI, DAY, YTD,
XIRR read exactly as PORT shows them for that file alone). The benchmarks are shared: each one's total return in EUR
(adjusted closes, the USD lines at each day's EUR/USD) from the chart's first day — one line whichever book you hold
it against. PORT's are cash-flow matched instead (your euros, on your days), which would give every book its own.
Private: never exported."""
from __future__ import annotations

from dataclasses import replace
from datetime import date

import pandas as pd

from monitor.data.buffer import cached_price_history
from monitor.data.instruments import BENCHMARKS
from monitor.portfolio import books
from monitor.portfolio.analytics import daily_flows
from monitor.portfolio.snapshot import BENCH_FX
from monitor.portfolio.trades import book_error, has_trades
from monitor.screens import port
from monitor.screens.base import Ctx, Screen
from monitor.screens.common import PRIV, epoch, num, thin_index

DEPS = (*port.DEPS, "monitor.screens.books", "monitor.portfolio.books", "monitor.portfolio.trades")
BENCH_TICKERS = [t for t, _ in BENCHMARKS.values()] + [BENCH_FX]
NO_BOOKS = ("NO BOOKS YET — BOOK <NAME> opens a new one: its first trade creates it (input/books/<NAME>/); "
            "yours is input/portfolio.csv")

HELP = [
    {"h": "BOOKS", "vis": PRIV, "body": "Every portfolio you keep, one row each: YOU (input/portfolio.csv) and each "
     "other book (input/books/<NAME>/portfolio.csv — someone else's, a model, a plan to follow). Each row is what PORT "
     "would show for that book alone: VALUE, the money put in, P&L and ROI (gain over every euro spent on buys), DAY, "
     "YTD (money-weighted) and XIRR. HOLDINGS shows the cursor row's positions. Enter on a row opens BOOK <NAME>: its "
     "trades, entered exactly as in TRADES (ADD, PASTE / IMPORT, UNDO; BUY … typed there goes into that book)."},
    {"h": "NEW BOOK", "vis": PRIV, "body": "Type BOOK and a name (a letter, then letters, digits or -, up to 15: "
     "BOOK ANNA, BOOK MODEL-1): an empty book opens, and its first trade creates input/books/<NAME>/. Its dividends.csv, "
     "interest.csv and backups/ live in that folder, as yours do in input/. To remove a book, delete its folder."},
    {"h": "SHARED BENCHMARKS", "vis": PRIV, "body": "On this chart each benchmark is one line for every book: its "
     "total return in EUR (dividends reinvested; USD lines at each day's EUR/USD) — what 1 € in it on the window's "
     "first day became, with no fees. Each book's line is its own money: ROI (the default, the ROI formula over the "
     "window: gain over the value at its start plus the buys since), MWR (money-weighted) or TWR (time-weighted: "
     "performance alone, comparable with the benchmarks however each book was paid in). A book younger than the "
     "window counts from its first trade. PORT's ROI chart is different: there each benchmark is bought with your own "
     "euros on your own days."},
]

BOOK_COLS = [
    {"k": "book", "label": "BOOK", "fmt": "text", "vis": PRIV},
    {"k": "pos", "label": "POS", "fmt": "num:0", "vis": PRIV, "align": "r"},
    {"k": "value", "label": "VALUE", "fmt": "eur", "vis": PRIV, "align": "r"},
    {"k": "inv", "label": "INVESTED", "fmt": "eur", "vis": PRIV, "align": "r", "lo": True},
    {"k": "pnl", "label": "P&L €", "fmt": "eur+", "vis": PRIV, "align": "r"},
    {"k": "roi", "label": "ROI", "fmt": "pct+", "vis": PRIV, "align": "r"},
    {"k": "day", "label": "DAY%", "fmt": "pct+:2", "vis": PRIV, "align": "r"},
    {"k": "ytd", "label": "YTD", "fmt": "pct+", "vis": PRIV, "align": "r"},
    {"k": "xirr", "label": "XIRR", "fmt": "pct+", "vis": PRIV, "align": "r", "lo": True},
    {"k": "since", "label": "SINCE", "fmt": "date", "vis": PRIV, "lo": True},
]
HOLD_COLS = [
    {"k": "tkr", "label": "TKR", "fmt": "tkr", "vis": PRIV},
    {"k": "name", "label": "NAME", "fmt": "text", "vis": PRIV, "lo": True},
    {"k": "value", "label": "VALUE", "fmt": "eur", "vis": PRIV, "align": "r"},
    {"k": "wt", "label": "WT%", "fmt": "pct", "vis": PRIV, "align": "r"},
    {"k": "day", "label": "DAY%", "fmt": "pct+:2", "vis": PRIV, "align": "r"},
    {"k": "pnlp", "label": "P&L%", "fmt": "pct+", "vis": PRIV, "align": "r"},
]


# ── compute ──────────────────────────────────────────────────────────────────

def _books(ctx: Ctx) -> dict[str, Ctx]:
    """{name: PORT's context on that book's file}, yours (YOU) first — every book with a trade in it."""
    out = {}
    for name in (books.YOU, *books.names(ctx.portfolio_csv)):
        csv = books.csv_of(ctx.portfolio_csv, name)
        if has_trades(csv):
            out[name] = replace(ctx, portfolio_csv=csv, equity_log=None)   # PORT alone logs your equity
    return out


def cold(ctx: Ctx) -> str | None:
    return None if _books(ctx) else NO_BOOKS


def compute(tier: str, ctx: Ctx) -> dict:
    """{"books": {name: PORT's part for that file}, "errors": {name: why its file cannot be read}} — and on the daily
    tier `bench`, the benchmarks' adjusted closes (and EUR/USD). A network failure fails the whole tier (the last
    good one stays); a book whose file has an error is left out and named."""
    parts, errors = {}, {}
    for name, c in _books(ctx).items():
        if error := book_error(c.portfolio_csv):
            errors[name] = error
            continue
        part = port.compute(tier, c)
        if tier == "daily":
            part.pop("history", None)          # PORT's sparklines: unused here, heavy to store per book
        parts[name] = part
    out = {"books": parts, "errors": errors}
    if tier == "daily":
        out["bench"] = (cached_price_history(BENCH_TICKERS, period="max", force=ctx.force, buffer_dir=ctx.buffer_dir)
                        if parts else pd.DataFrame())
    return out


# ── assemble ─────────────────────────────────────────────────────────────────

def _today(q: dict) -> date:
    return date.fromisoformat(q["as_of"][:10]) if q.get("as_of") else date.today()


def _row(name: str, q: dict, d: dict) -> dict:
    """The book's figures, as PORT's SUMMARY shows them for that file."""
    kv = {i["k"]: i for i in port._summary(q, d)["items"]}
    first = min((t["date"] for t in q["txns"]), default=None)
    row = {"book": name, "pos": len(q["positions"]), "value": kv["VALUE"]["v"], "inv": kv["INVESTED"]["v"],
           "pnl": kv["TOTAL"]["eur"], "roi": kv["TOTAL"]["v"], "day": kv["DAY"]["v"], "ytd": kv["YTD"]["v"],
           "xirr": kv["XIRR /YR"]["v"], "since": first}
    if q["stale"] or q["missing"]:
        row["_stale"] = True
    return row


def _holdings(q: dict) -> dict:
    total = sum(p["position_value"] for p in q["positions"])
    rows = []
    for p in q["positions"]:
        qt = q["quotes"].get(p["ticker"]) or {}
        last, prev = qt.get("price"), qt.get("prev_close")
        rows.append({"tkr": p["ticker"], "name": port._name(q, p["ticker"]), "value": num(p["position_value"]),
                     "wt": num(p["position_value"] / total * 100) if total else None,
                     "day": num((last / prev - 1) * 100) if last and prev else None,
                     "pnlp": num(p["unrealized_pct"])})
    return {"rows": rows, "context": f"{len(rows)} OPEN"}


def _line(q: dict, d: dict) -> dict | None:
    """One book's ROI line, its time-weighted growth and its money (buys, and sales + dividends, to each day) —
    today's point at live quotes, as PORT's ROI chart draws YOU."""
    roi = d["roi_series"]
    if roi.empty:
        return None
    today = _today(q)
    live = port._live_points(q, d, today).get("YOU", (None, None)) if "hold" in d else (None, None)
    roi = port._at(roi, today, live[0])
    flows = daily_flows(q["txns"], q["dividends"], roi.index)
    return {"y": roi, "twr": port._at(d.get("twr"), today, live[1]), "inv": flows["buy"].cumsum(),
            "cash": (flows["sell"] + flows["dividend"]).cumsum()}


def _bench_index(bench: pd.DataFrame, quotes: dict, idx: pd.DatetimeIndex, today: date) -> dict[str, pd.Series]:
    """{short name: growth of 1 € from the first day of idx} — adjusted closes in EUR, the last close on or before
    each day; today's point at the live quote when it is at least as new as the last close."""
    out = {}
    if bench.empty or not len(idx):
        return out
    fx = bench[BENCH_FX].ffill() if BENCH_FX in bench else None
    fx_now = (quotes.get(BENCH_FX) or {}).get("price")
    for name, short in port.BENCH:
        tkr, ccy = BENCHMARKS[name]
        if tkr not in bench or (ccy == "USD" and (fx is None or not fx_now)):
            continue
        s = (bench[tkr] / fx if ccy == "USD" else bench[tkr]).dropna()
        qt = quotes.get(tkr)
        if len(s) and qt and today.weekday() < 5 and qt["date"] >= s.index[-1].date().isoformat():
            s = port._at(s, today, qt["price"] / fx_now if ccy == "USD" else qt["price"])
        s = s.reindex(s.index.union(idx)).ffill().reindex(idx).dropna()
        if len(s):
            out[short] = s / float(s.iloc[0])
    return out


def _chart(lines: dict[str, dict], bench: pd.DataFrame, quotes: dict, today: date) -> dict:
    out = {"id": "roi", "n": 3, "title": "ROI vs SHARED BENCHMARKS", "type": "chart", "span": 12, "vis": PRIV,
           "yfmt": "pct+", "legend": "rank", "ranges": ["1M", "6M", "YTD", "1Y", "ALL"], "range": "YTD",
           "x": [], "series": []}
    if not lines:
        return out
    full = pd.DatetimeIndex(sorted(set().union(*(ln["y"].index for ln in lines.values()))))
    idx = thin_index(full)

    def cut(s, nd):
        return [num(round(v, nd)) for v in s.reindex(idx)] if s is not None else None

    series = []
    for name, ln in lines.items():
        series.append({"name": name, **({"role": "primary"} if name == books.YOU else {}), "kind": "line",
                       "vis": PRIV, "y": cut(ln["y"], 4), "twr": cut(ln["twr"], 4), "inv": cut(ln["inv"], 2),
                       "cash": cut(ln["cash"], 2)})
    for short, s in _bench_index(bench, quotes, full, today).items():
        series.append({"name": short, "role": "bench", "kind": "line", "vis": PRIV,
                       "y": cut((s - 1) * 100, 4), "twr": cut(s, 4)})
    # a book's ROI over a window takes its own money (its `inv`, `cash`); a benchmark is 1 € held from the first
    # day — the chart's `inv`, so its window return is its total return over the window, in every view
    out.update(x=epoch(idx), series=series, inv=[1.0] * len(idx),
               context={"text": f"BENCHMARKS: TOTAL RETURN IN EUR · {len(lines)} BOOK{'' if len(lines) == 1 else 'S'}"})
    return out


def assemble(parts: dict, meta: dict) -> dict:
    qb, db = parts["quote"]["books"], parts["daily"]["books"]
    names = [n for n in qb if n in db]               # a book added since one of the tiers ran waits for both
    errors = {**parts["quote"]["errors"], **parts["daily"]["errors"]}
    rows = [_row(n, qb[n], db[n]) for n in names]
    hold = {n: _holdings(qb[n]) for n in names}
    lines = {n: ln for n in names if (ln := _line(qb[n], db[n])) is not None}
    quotes = next((qb[n].get("bench") or {} for n in names), {})
    today = _today(qb[names[0]]) if names else date.today()
    first = names[0] if names else None
    warn = [f"BOOK {n}: {e}" for n, e in sorted(errors.items())]
    warn += [f"{n}: {w}" for n in names for w in qb[n].get("warn") or []]
    as_of = max((qb[n]["as_of"] for n in names if qb[n].get("as_of")), default=None)
    return {"screen": "BOOKS", "title": "Books",
            "context": {"text": f"{len(names)} BOOK{'' if len(names) == 1 else 'S'} · EUR · ENTER OPENS ITS TRADES"},
            "meta": {**meta, "as_of": as_of, "warn": warn}, "help": HELP,
            "panels": [
                {"id": "books", "n": 1, "title": "BOOKS", "type": "table", "span": 7, "vis": PRIV, "key": "book",
                 "sort": ["roi", "desc"], "cols": BOOK_COLS, "rows": rows, "enter": "BOOK {key}", "cursor": first,
                 "empty": NO_BOOKS, "context": {"text": "ENTER: ITS TRADES · BOOK <NAME>: A NEW ONE"}},
                {"id": "holdings", "n": 2, "title": "HOLDINGS → {key}", "type": "table", "span": 5, "vis": PRIV,
                 "fit": True, "key": "tkr", "sort": ["wt", "desc"], "cols": HOLD_COLS, "follows": "books",
                 "rows": hold[first]["rows"] if first else [],
                 "context": {"text": hold[first]["context"] if first else ""}, "rows_by_key": hold},
                _chart(lines, parts["daily"].get("bench", pd.DataFrame()), quotes, today),
            ]}


SCREEN = Screen(id="BOOKS", title="Books", fkey=7, status="live", public=False, tiers=("quote", "daily"), deps=DEPS,
                compute=compute, assemble=assemble, uses_inputs=True, uses_books=True, cold=cold)
