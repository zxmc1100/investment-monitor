"""PORT — Portfolio Monitor.

Assembles the typed-panel payload from the portfolio snapshot tiers. The only arithmetic
here is presentation (weights, day %, thinning, sector roll-up); every financial figure
comes from monitor.portfolio.snapshot. NaN never leaves this module (_num → None).
"""
from __future__ import annotations

from datetime import date

import pandas as pd

from monitor import config
from monitor.portfolio import snapshot
from monitor.portfolio.analytics import year_returns
from monitor.portfolio.ledger import ADDS
from monitor.portfolio.meta import exposure_breakdown, region_totals
from monitor.screens.base import Ctx, Screen
from monitor.screens.common import PRIV, PUB, epoch as _epoch, kpi as _kpi, num as _num, session_day, thin_index as _thin_index
from monitor.screens.identity import identify

MAX_POINTS = 1000          # chart thinning cap
POSVAL_POINTS = 500        # per-ticker position-value chart cap (payload size)
SPARK_POINTS = 52          # ~weekly over one year
BENCH = (("S&P 500", "SPX"), ("Nasdaq 100", "NDX"), ("MSCI World", "MSCI W"),
         ("FTSE All-World", "FTSE AW"), ("Euro Stoxx 50", "STOXX50"), ("Emerging Markets", "EM"),
         ("Gold", "GOLD"), ("Bitcoin", "BTC"), ("Fixed Income", "BONDS"))
DEPS = ("monitor.config", "monitor.screens.port", "monitor.screens.common", "monitor.screens.base", "monitor.portfolio.snapshot",
        "monitor.portfolio.analytics", "monitor.portfolio.ledger", "monitor.portfolio.meta",
        "monitor.data.buffer", "monitor.data.yahoo", "monitor.data.instruments", "monitor.screens.identity",
        "monitor.universe.lookup")

# {tax} and {fee} are filled from the settings in effect when the payload assembles (help_entries)
HELP = [
    {"h": "ROI", "vis": PUB, "body": "Total P&L divided by every euro ever spent on buys. Sale "
     "proceeds and dividends received (after {tax} % dividend tax) count as "
     "cash, so selling never reads as a loss. Bonus shares (Saveback) cost nothing, so their value "
     "is gain. Interest on cash is not included. Cumulative; ignores timing."},
    {"h": "XIRR /YR", "vis": PUB, "body": "Money-weighted annual return on your dated cash flows "
     "(buys out; sells, dividends and today's value in). Rewards deploying early."},
    {"h": "YTD", "vis": PUB, "body": "This calendar year, two ways. YTD is the spreadsheet method: "
     "the gain this year (value now + sells + dividends - value on 1 Jan - buys) divided by the "
     "value on 1 Jan plus the money added (buys - sells), so money added during the year dilutes it. "
     "YTD TWR is time-weighted: each day's return with that day's deposits and withdrawals taken "
     "out, chained, so only performance moves it (how funds report). Bonus shares count as gain; "
     "interest on cash is not included. Alt+1 shows both for every year since the first trade."},
    {"h": "DAY", "vis": PUB, "body": "Last price vs previous close per position; the portfolio "
     "figure is the sum of shares x change over yesterday's value. Before the open it shows the "
     "last session."},
    {"h": "ROI vs SAME CASH ELSEWHERE", "vis": PUB, "body": "Every euro spent on a buy is "
     "virtually invested in each benchmark on the same day (USD benchmarks at that day's EUR/USD), "
     "less the same {fee} EUR order fee you paid (savings-plan buys are free). Your line counts "
     "dividends as cash received after tax; benchmarks are total return before tax (dividends "
     "reinvested). Hover the chart to read every line on that date."},
    {"h": "NORM (Alt+N)", "vis": PRIV, "body": "Redraws the ROI chart as each line's time-weighted "
     "return from the close before the period shown (or dragged): 0 at the start, then who did best in "
     "it. Each day's buys, sells and dividends are taken out of that day, so adding money never reads "
     "as a gain or a loss. Benchmarks are then their own total return in EUR less the order fees; your "
     "line is your holdings, as YTD TWR (over a calendar year it is YTD TWR at the last close)."},
    {"h": "RISK", "vis": PUB, "body": "Vol, Sharpe, Sortino, drawdowns and VaR from the daily "
     "portfolio ROI series since the first trade; beta and alpha vs the cash-flow-matched S&P 500."},
    {"h": "ALLOCATION", "vis": PUB, "body": "Sector and country weights of the whole book. The MSCI "
     "World ETF is looked through with approximate index weights (marked *, rebalanced quarterly). "
     "Alt+7 shows every sector and country with the positions behind each, and region totals."},
    {"h": "STALE / NO PRICE", "vis": PUB, "body": "A failed quote keeps the last good price and "
     "marks the row red. A ticker never priced shows NO PRICE and is carried at cost until a "
     "quote arrives."},
    {"h": "NET INVESTED vs NET COST BASIS", "vis": PRIV, "body": "Net invested is out-of-pocket "
     "cash (buys minus sells). Net cost basis is the cost still at work in open positions, bonus "
     "shares at the value Trade Republic books them. The gap is realized profit already banked "
     "plus bonus shares received."},
    {"h": "INTEREST ON CASH", "vis": PRIV, "body": "Interest Trade Republic paid on uninvested cash, "
     "from interest.csv next to the portfolio CSV (Date,Amount in EUR). Shown in ACCOUNTING for "
     "the record; it is not an investment return, so ROI, XIRR and YTD leave it out."},
]

POS_COLS = [
    {"k": "tkr", "label": "TKR", "fmt": "tkr", "vis": PUB},
    {"k": "name", "label": "NAME", "fmt": "text", "vis": PUB},
    {"k": "shrs", "label": "SHRS", "fmt": "num:4", "vis": PRIV, "align": "r"},
    {"k": "avg", "label": "AVG", "fmt": "num:2", "vis": PRIV, "align": "r"},
    {"k": "last", "label": "LAST", "fmt": "num:2", "vis": PRIV, "align": "r"},
    {"k": "day", "label": "DAY%", "fmt": "pct+:2", "vis": PUB, "align": "r"},
    {"k": "value", "label": "VALUE", "fmt": "eur", "vis": PRIV, "align": "r"},
    {"k": "wt", "label": "WT%", "fmt": "pct", "vis": PUB, "align": "r"},
    {"k": "pnl", "label": "P&L €", "fmt": "eur+", "vis": PRIV, "align": "r"},
    {"k": "pnlp", "label": "P&L%", "fmt": "pct+", "vis": PUB, "align": "r"},
    {"k": "p1y", "label": "1Y", "fmt": "spark", "vis": PUB},
]


def help_entries() -> list[dict]:
    """HELP with today's settings filled in (settings.toml can change while the server runs)."""
    vals = {"tax": f"{config.DIVIDEND_TAX * 100:g}", "fee": f"{config.ORDER_FEE_EUR:g}"}
    return [{**h, "body": h["body"].format(**vals)} for h in HELP]


# ── compute ──────────────────────────────────────────────────────────────────

def compute(tier: str, ctx: Ctx) -> dict:
    book = snapshot.load_book(ctx.portfolio_csv)
    if tier == "quote":
        q = snapshot.quote_tier(book, force=ctx.force, buffer_dir=ctx.buffer_dir)
        if ctx.equity_log is not None:
            snapshot.log_equity(ctx.equity_log, q["acct"])
        # name / sector / country of every line ever traded — a new holding needs no code edit
        q["ident"] = identify({t["ticker"] for t in book["transactions"]}, buffer_dir=ctx.buffer_dir)
        return q
    if tier == "daily":
        return snapshot.daily_tier(book, force=ctx.force, buffer_dir=ctx.buffer_dir)
    raise ValueError(f"PORT has no {tier!r} tier")


# ── helpers ──────────────────────────────────────────────────────────────────

def _spark(history: pd.DataFrame, t: str) -> list[float] | None:
    if history is None or history.empty or t not in history.columns:
        return None
    s = history[t].dropna()
    if len(s) < 2:
        return None
    s = s[s.index >= s.index[-1] - pd.Timedelta(days=365)]
    pts = s.iloc[:: max(1, len(s) // SPARK_POINTS)]
    if pts.index[-1] != s.index[-1]:
        pts = pd.concat([pts, s.iloc[-1:]])
    base = float(s.iloc[0])
    if not base:
        return None
    # rebased to 100: the public shape never carries the absolute price level (== LAST)
    return [_num(round(float(v) / base * 100, 4)) for v in pts]


# ── panels ───────────────────────────────────────────────────────────────────

def _session_day(q: dict) -> date | None:
    """Date of the latest quoted session when it is not today (weekend, holiday, pre-open) — DAY %
    then describes that session, and the screen says so. None on a trading day."""
    return session_day(q.get("quotes") or {})


def _pos_cols(q: dict) -> list[dict]:
    """POS_COLS, with DAY% naming its session when that session is not today ("DAY% · FRI")."""
    day = _session_day(q)
    if day is None:
        return POS_COLS
    return [{**c, "label": f"DAY% · {day.strftime('%a').upper()}"} if c["k"] == "day" else c for c in POS_COLS]


def _summary_context(q: dict) -> str:
    text = f"AS OF {q['as_of'][11:16]}" if q["as_of"] else ""
    day = _session_day(q)
    if day is not None:
        text += f"{' · ' if text else ''}DAY = {day.strftime('%a').upper()} {day.day:02d} {day.strftime('%b').upper()}"
    return text


def _summary(q: dict, d: dict) -> dict:
    """KPIs, plus `years` (shown when maximized): every calendar year since the first trade, the
    spreadsheet method (v) and time-weighted (v2) — percentages public, the euro gain private."""
    a = q["acct"]
    # sum of the displayed (cent-rounded) position values, so the KPI equals the table total
    value = sum(p["position_value"] for p in q["positions"])
    today = date.today()
    years = year_returns(d["hold"], q["txns"], q["dividends"], live_value=value, today=today)
    cur = next((y for y in years if y["year"] == today.year), {})
    return {"id": "summary", "n": 1, "title": "SUMMARY", "type": "kpi", "span": 6, "vis": PUB,
            "context": {"text": _summary_context(q), "vis": PUB},
            "items": [_kpi("VALUE", value, "eur"),
                      _kpi("DAY P&L", q["day_pnl"], "eur+"),
                      _kpi("DAY %", q["day_pct"], "pct+:2", PUB),
                      _kpi("TOTAL P&L", a["total_pnl"], "eur+"),
                      _kpi("ROI", a["simple_roi"], "pct+", PUB),
                      _kpi("XIRR /YR", a["mwr"] * 100 if a["mwr"] is not None else None, "pct+", PUB),
                      _kpi("YTD", cur.get("simple"), "pct+", PUB),
                      _kpi("YTD TWR", cur.get("twr"), "pct+", PUB),
                      _kpi("REALIZED", a["realized"], "eur+"),
                      _kpi("UNREALIZED", a["unrealized"], "eur+")],
            "years": [{"label": str(y["year"]), "v": _num(y["simple"]), "v2": _num(y["twr"]),
                       "gain": _num(y["gain"]), "fmt": "pct+", "vis": PUB} for y in years]}


def _risk(d: dict) -> dict:
    m = d["metrics"] or {}
    return {"id": "risk", "n": 2, "title": "RISK", "type": "kpi", "span": 6, "vis": PUB,
            "context": {"text": f"DAILY · {m.get('n_trading_days', 0)}D", "vis": PUB},
            "items": [_kpi("VOL", m.get("volatility"), "pct", PUB),
                      _kpi("SHARPE", m.get("sharpe"), "num:2", PUB),
                      _kpi("SORTINO", m.get("sortino"), "num:2", PUB),
                      _kpi("BETA SPX", m.get("beta"), "num:2", PUB),
                      _kpi("MAX DD", m.get("max_drawdown"), "pct+", PUB),
                      _kpi("CUR DD", m.get("current_drawdown"), "pct+", PUB),
                      _kpi("VaR95", m.get("var_95"), "pct+:2", PUB),
                      _kpi("ALPHA", m.get("alpha"), "pct+", PUB)]}


def _name(q: dict, t: str) -> str:
    return (q.get("ident") or {}).get(t, {}).get("name") or t


def _labels(q: dict, kind: str) -> dict[str, str]:
    return {t: r[kind] for t, r in (q.get("ident") or {}).items()}


def _positions(q: dict, d: dict) -> dict:
    quotes, positions = q["quotes"], q["positions"]
    value_sum = sum(p["position_value"] for p in positions)
    flagged = set(q["stale"]) | set(q["missing"])
    rows = []
    for p in positions:
        t = p["ticker"]
        qt = quotes.get(t) or {}
        last, prev = qt.get("price"), qt.get("prev_close")
        row = {"tkr": t, "name": _name(q, t), "shrs": _num(p["shares"]),
               "avg": _num(p["avg_cost"]), "last": _num(last),
               "day": _num((last / prev - 1) * 100) if last and prev else None,
               "value": _num(p["position_value"]),
               "wt": _num(p["position_value"] / value_sum * 100) if value_sum else None,
               "pnl": _num(p["unrealized_pnl"]), "pnlp": _num(p["unrealized_pct"]),
               "p1y": _spark(d["history"], t)}
        if t in flagged:
            row["_stale"] = True
        rows.append(row)
    held = {p["ticker"] for p in positions}
    for t, r in sorted(q["realized"].items()):
        if t in held:
            continue
        cost = r["proceeds"] - r["pnl_eur"]
        rows.append({"tkr": t, "name": _name(q, t), "shrs": 0.0, "avg": None, "last": None,
                     "day": None, "value": 0.0, "wt": 0.0, "pnl": _num(r["pnl_eur"]),
                     "pnlp": _num(r["pnl_eur"] / cost * 100) if cost > 0 else None,
                     "p1y": None, "_closed": True})
    unreal = sum(p["unrealized_pnl"] for p in positions)
    cost = sum(p["cost_basis"] for p in positions)
    return {"id": "positions", "n": 3, "title": "POSITIONS", "type": "table", "span": 7, "rows_span": 2,
            "vis": PUB, "key": "tkr", "sort": ["wt", "desc"], "drives": "posval",
            "context": {"text": f"{len(positions)} OPEN", "vis": PUB},
            "cols": _pos_cols(q), "rows": rows,
            "total": {"tkr": "TOTAL", "day": _num(q["day_pct"]), "value": _num(value_sum), "wt": 100.0,
                      "pnl": _num(unreal), "pnlp": _num(unreal / cost * 100) if cost else None}}


def _roi(d: dict) -> dict:
    roi, bms = d["roi_series"], d["bm_series"]
    out = {"id": "roi", "n": 4, "title": "ROI vs SAME CASH ELSEWHERE", "type": "chart", "span": 5,
           "vis": PUB, "context": {"text": "CASH-FLOW MATCHED", "vis": PUB}, "yfmt": "pct+",
           "legend": "rank", "ranges": ["1M", "6M", "YTD", "1Y", "ALL"], "x": [], "series": []}
    if roi.empty:
        return out
    idx = _thin_index(roi.index)
    # `twr`: each line's time-weighted growth of 1 € (money moves taken out of their days), which the
    # chart's NORM view rebases to 0 at the window's start. Not a public key (redact drops it): next
    # to the ROI line it would give away when, and how much, money was added. Absent in a part
    # computed before it existed — NORM is then simply not offered.
    twr = {"YOU": d.get("twr"), **{short: (d["asset_values"].get("__twr__") or {}).get(name) for name, short in BENCH}}

    def line(name, role, s):
        ln = {"name": name, "role": role, "kind": "line", "vis": PUB, "y": [_num(v) for v in s.reindex(idx)]}
        if twr.get(name) is not None:
            # 4 decimals: a NORM value within 0.01 pp (the legend shows 0.1), at 7 bytes a point —
            # the payload stays < 300 KB
            ln["twr"] = [_num(round(v, 4)) for v in twr[name].reindex(idx)]
        return ln

    series = [line("YOU", "primary", roi)]
    for name, short in BENCH:
        s = bms.get(name)
        if s is not None and not s.empty:
            series.append(line(short, "bench", s))
    out.update(x=_epoch(idx), series=series)
    return out


def _posval(q: dict, d: dict) -> dict:
    trades: dict[str, list[dict]] = {}
    for t in q["txns"]:
        trades.setdefault(t["ticker"], []).append(t)
    by_key = {}
    for t, s in d["asset_values"].items():
        if t.startswith("__") or not isinstance(s, pd.Series):
            continue
        valid = s.dropna()
        if len(valid) < 2:
            continue
        s = s.loc[valid.index[0]:valid.index[-1]]          # trim edges only; interior gaps stay NaN
        full = s.index
        # marker positions on the full-resolution index (weekend trades -> next business day)
        marks: dict[str, dict[int, float]] = {"buy": {}, "sell": {}}
        for tr in trades.get(t, []):
            i = int(full.searchsorted(pd.Timestamp(tr["date"])))
            if tr["action"] == "sell" and (i >= len(full) or pd.isna(s.iloc[i])):
                ok = s.iloc[:i].last_valid_index()           # full exit: last held day before it
                if ok is None:
                    continue
                i = int(full.get_loc(ok))
            i = min(i, len(full) - 1)
            if pd.isna(s.iloc[i]):
                continue
            marks["buy" if tr["action"] in ADDS else "sell"][i] = round(float(s.iloc[i]), 2)   # bonus = BUY
        keep = _thin_index(full, POSVAL_POINTS)
        pos = sorted(set(full.get_indexer(keep)) | set(marks["buy"]) | set(marks["sell"]))
        idx = full[pos]
        y = [_num(round(float(v), 2)) if pd.notna(v) else None for v in s.iloc[pos]]
        place = {j: n for n, j in enumerate(pos)}
        buy, sell = [None] * len(pos), [None] * len(pos)
        for arr, key in ((buy, "buy"), (sell, "sell")):
            for i, v in marks[key].items():
                arr[place[i]] = v
        by_key[t] = {"x": _epoch(idx), "series": [
            {"name": t, "role": "primary", "kind": "line", "vis": PRIV, "y": y},
            {"name": "BUY", "role": "buy", "kind": "markers", "vis": PRIV, "y": buy},
            {"name": "SELL", "role": "sell", "kind": "markers", "vis": PRIV, "y": sell}]}
    return {"id": "posval", "n": 5, "title": "{key} · POSITION VALUE", "type": "chart", "span": 5,
            "vis": PRIV, "follows": "positions", "yfmt": "eur",
            "context": {"text": "€ · ● BUY ● SELL", "vis": PRIV}, "series_by_key": by_key}


def _accounting(a: dict) -> dict:
    def line(label, v, fmt="eur", op="", strong=False):
        return {"label": label, "v": _num(v), "fmt": fmt, "op": op, "strong": strong, "vis": PRIV}
    sep = {"sep": True, "vis": PRIV}
    return {"id": "accounting", "n": 6, "title": "ACCOUNTING", "type": "ledger", "span": 4, "vis": PRIV,
            "lines": [line("Gross deposits", a["gross_deposits"]),
                      line("Cash returned", a["cash_returned"], op="−"),
                      line("Net invested", a["net_invested"], op="=", strong=True), sep,
                      line("Net cost basis", a["net_cost_basis"]),
                      line("Unrealized", a["unrealized"], "eur+", "+"),
                      line("Market value", a["current_value"], op="=", strong=True), sep,
                      line("Realized", a["realized"], "eur+"),
                      line("Dividends (net)", a["dividends"], "eur+", "+"),
                      line("Bonus", a["bonus"], "eur+", "+"),
                      line("Unrealized", a["unrealized"], "eur+", "+"),
                      line("Total P&L", a["total_pnl"], "eur+", "=", True), sep,
                      line("Interest on cash (not in ROI)", a["interest"], "eur+")]}


def _parts_text(parts) -> str:
    """'SAP.DE 20.7 · IWDA.AS 5.3*' — each position's share of the book in this bucket (* = via ETF)."""
    return " · ".join(f"{tk} {w * 100:.1f}{'*' if via else ''}" for tk, w, via in parts if w >= 0.0005)


def _allocation(q: dict) -> dict:
    """Compact view: top-6 sectors + Other. Maximized (Alt+7): every sector and country with the
    positions behind it, plus region totals — percentages of the book only (public)."""
    pos = q["positions"]
    total = sum(p["position_value"] for p in pos)
    weights = {p["ticker"]: p["position_value"] / total for p in pos} if total > 0 else {}
    sectors = exposure_breakdown(weights, "sector", _labels(q, "sector")) if weights else []
    countries = exposure_breakdown(weights, "country", _labels(q, "country")) if weights else []
    held = [r for r in sectors if r["w"] > 0]
    items = [{"label": r["label"], "v": _num(r["w"] * 100), "fmt": "pct", "vis": PUB} for r in held[:6]]
    if held[6:]:
        items.append({"label": f"Other ({len(held) - 6})", "v": _num(sum(r["w"] for r in held[6:]) * 100),
                      "fmt": "pct", "vis": PUB})

    def rows(breakdown):
        return [{"label": r["label"], "v": _num(r["w"] * 100), "fmt": "pct", "text": _parts_text(r["parts"]),
                 "vis": PUB} for r in breakdown]

    return {"id": "allocation", "n": 7, "title": "ALLOCATION", "type": "bars", "span": 4, "vis": PUB,
            "context": {"text": "SECTOR · ETF LOOK-THROUGH · ALT+7 DETAIL", "vis": PUB}, "items": items,
            "sectors": rows(sectors), "countries": rows(countries),
            "regions": [{"label": r["label"], "v": _num(r["w"] * 100), "fmt": "pct", "vis": PUB}
                        for r in region_totals(countries)]}


def _activity(q: dict) -> dict:
    tx = sorted(q["txns"], key=lambda t: t["date"], reverse=True)[:10]
    return {"id": "activity", "n": 8, "title": "ACTIVITY", "type": "table", "span": 4, "vis": PRIV,
            "key": "i", "sort": ["date", "desc"], "context": {"text": "LAST 10 TRADES", "vis": PRIV},
            "cols": [{"k": "date", "label": "DATE", "fmt": "date", "vis": PRIV},
                     {"k": "side", "label": "SIDE", "fmt": "side", "vis": PRIV},
                     {"k": "tkr", "label": "TKR", "fmt": "tkr", "vis": PRIV},
                     {"k": "shrs", "label": "SHRS", "fmt": "num:4", "vis": PRIV, "align": "r"},
                     {"k": "amt", "label": "AMOUNT", "fmt": "eur", "vis": PRIV, "align": "r"}],
            "rows": [{"i": f"{t['date']}|{t['ticker']}|{n}", "date": t["date"], "side": t["action"].upper(),
                      "tkr": t["ticker"], "shrs": _num(t["shares"]), "amt": _num(t["price"])}
                     for n, t in enumerate(tx)]}


# ── assemble ─────────────────────────────────────────────────────────────────

def assemble(parts: dict, meta: dict) -> dict:
    q, d = parts["quote"], parts["daily"]
    first = min((t["date"] for t in q["txns"]), default=None)
    since = date.fromisoformat(first).strftime("%d %b %y").upper() if first else "—"
    stale = {t: q["stale"].get(t) for t in sorted(set(q["stale"]) | set(q["missing"]))}
    return {"screen": "PORT", "title": "Portfolio Monitor",
            "context": {"text": f"{len(q['positions'])} POS · EUR · SINCE {since}", "vis": PUB},
            "meta": {**meta, "as_of": q["as_of"], "stale": stale, "warn": q.get("warn") or []},
            "help": help_entries(),
            "panels": [_summary(q, d), _risk(d), _positions(q, d), _roi(d), _posval(q, d),
                       _accounting(q["acct"]), _allocation(q), _activity(q)]}


SCREEN = Screen(id="PORT", title="Portfolio Monitor", fkey=1, status="live", public=True,
                tiers=("quote", "daily"), deps=DEPS, compute=compute, assemble=assemble,
                needs_portfolio=True, uses_inputs=True)
