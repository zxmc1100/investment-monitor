"""SEC — Security: one ticker — quote and 52-week range, your position
and its contribution to portfolio ROI, the price path with your trades, its risk inside the book,
and every trade. Private; parameter = ticker.

params (autocomplete) = every ticker you traded ∪ your watchlist; accepted = those ∪ any live
Trade Republic universe ticker. A name you never traded shows QUOTE, PRICE (real closes) and
price-only RISK, in its listing currency — no MY POSITION, no TRADES; its BETA SPX is against the
S&P 500 in that currency (spx_line)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from monitor.alerts import watchlist
from monitor.data.buffer import cached_price_history, cached_quotes
from monitor.portfolio import riskmodel, snapshot
from monitor.portfolio.ledger import ADDS
from monitor.screens.base import Ctx, Screen
from monitor.screens.common import PRIV, bar_on_or_after, epoch, kpi, num, thin_index
from monitor.screens.identity import identify
from monitor.universe import lookup

DEPS = ("monitor.screens.sec", "monitor.screens.common", "monitor.screens.base",
        "monitor.portfolio.riskmodel", "monitor.portfolio.optimizer", "monitor.portfolio.snapshot",
        "monitor.portfolio.ledger", "monitor.data.buffer", "monitor.data.yahoo",
        "monitor.data.instruments", "monitor.alerts.watchlist", "monitor.universe.lookup", "monitor.config",
        "monitor.screens.identity")


# Euro-denominated exchange suffixes of the TR universe's home tickers (Xetra, the German regional
# floors, Euronext, Milan, Madrid, Vienna, Helsinki, Lisbon, Dublin, Athens).
EUR_SUFFIXES = (".DE", ".F", ".SG", ".BE", ".DU", ".HM", ".HA", ".MU", ".AS", ".PA", ".BR", ".MI", ".MC",
                ".VI", ".HE", ".LS", ".IR", ".AT")


def spx_line(ticker: str) -> str | None:
    """The S&P 500 line a name you never traded is measured against — same currency and session:
    a US home line (no suffix) → ^GSPC; a euro line → CSPX.AS (EUR, as for your book); any other
    listing (.T .HK .L .SW …) → None: BETA shows "—", never a beta across currencies."""
    if "." not in ticker:
        return "^GSPC"
    return snapshot.SPX_PROXY if ticker[ticker.rindex("."):] in EUR_SUFFIXES else None


def _traded(ctx: Ctx) -> set[str]:
    try:
        return {t["ticker"] for t in snapshot.load_book(ctx.portfolio_csv)["transactions"]}
    except FileNotFoundError:
        return set()


def params(ctx: Ctx) -> list[str]:
    return sorted(_traded(ctx) | set(watchlist.tickers(ctx.watchlist)))


def accept(ctx: Ctx, param: str) -> bool:
    return param in params(ctx) or lookup.is_tradeable(param)


def _name(t: str, ctx: Ctx) -> str:
    """Settings / built-in name → your watchlist's → the TR universe's → Yahoo's → the ticker."""
    watched = {w["ticker"]: w["name"] for w in watchlist.load(ctx.watchlist)}
    return identify([t], buffer_dir=ctx.buffer_dir, names=watched, need=("name",))[t]["name"]


def _history(t: str, ctx: Ctx, adjusted: bool = True) -> pd.Series:
    """5y closes: real (adjusted=False) for anything shown as a price, dividend-adjusted (total
    return) for vol, beta and drawdown."""
    h = cached_price_history([t], period="5y", force=ctx.force, buffer_dir=ctx.buffer_dir, adjusted=adjusted)
    return h[t].dropna() if t in h.columns else pd.Series(dtype=float)


def compute(tier: str, ctx: Ctx, param: str) -> dict:
    traded = param in _traded(ctx)
    book = snapshot.load_book(ctx.portfolio_csv) if traded else None
    if tier == "quote":
        quotes, stale, _ = cached_quotes([param], force=ctx.force, buffer_dir=ctx.buffer_dir)
        out = {"quote": quotes.get(param), "name": _name(param, ctx), "txns": [], "positions": [],
               "stale": param in stale or param in snapshot.old_bars({param: quotes.get(param)})}
        if traded:
            q = snapshot.quote_tier(book, force=False, buffer_dir=ctx.buffer_dir)
            out.update(positions=q["positions"], acct=q["acct"], realized=book["realized"].get(param),
                       dividends=sum(d["eur"] for d in q["dividends"] if d["ticker"] == param),
                       bonus=sum(t["price"] for t in book["transactions"]
                                 if t["ticker"] == param and t["action"] == "bonus"),
                       txns=[t for t in book["transactions"] if t["ticker"] == param])
        return out
    if tier == "daily":
        model = None
        if traded:
            try:
                model = riskmodel.build_model(**snapshot.risk_inputs(book, force=False, buffer_dir=ctx.buffer_dir))
            except ValueError:
                model = None
        line = snapshot.SPX_PROXY if traded else spx_line(param)       # your book: the EUR proxy, as RISK
        spx = (cached_price_history([line], period="5y", force=False, buffer_dir=ctx.buffer_dir)
               if line else pd.DataFrame())
        return {"hist": _history(param, ctx, adjusted=False), "hist_tr": _history(param, ctx, adjusted=True),
                "model": model, "spx": spx[line] if line in spx.columns else None}
    raise ValueError(f"SEC has no {tier!r} tier")


def _ret(s: pd.Series, days: int) -> float | None:
    if len(s) < 2:
        return None
    past = s[s.index <= s.index[-1] - pd.Timedelta(days=days)]
    return float(s.iloc[-1] / past.iloc[-1] - 1) * 100 if len(past) else None


def _ytd(s: pd.Series) -> float | None:
    if len(s) < 2:
        return None
    prev = s[s.index.year < s.index[-1].year]
    return float(s.iloc[-1] / prev.iloc[-1] - 1) * 100 if len(prev) else None


def assemble(parts: dict, meta: dict, param: str) -> dict:
    q, d = parts["quote"], parts["daily"]
    pos = next((p for p in q["positions"] if p["ticker"] == param), None)
    name = q.get("name") or param
    if not q["txns"]:                                 # watched / universe name: price-only view
        panels = [{**_quote(q, d["hist"]), "span": 12}, _price(d["hist"], []), _risk(d, None, param)]
        for n, p in enumerate(panels, 1):
            p["n"] = n
        return {"screen": "SEC", "title": f"{param} · {name}",
                "context": {"text": "NOT IN YOUR BOOK · PRICES IN THE LISTING CURRENCY"},
                "meta": meta, "help": [], "panels": panels}
    return {"screen": "SEC", "title": f"{param} · {name}",
            "context": {"text": ("CLOSED POSITION · " if pos is None else "") + f"{len(q['txns'])} TRADES"},
            "meta": meta, "help": [],
            "panels": [_quote(q, d["hist"]), _mine(q, pos, param), _price(d["hist"], q["txns"]),
                       _risk(d, pos, param), _trades(q["txns"])]}


def _quote(q, hist) -> dict:
    qt = q["quote"] or {}
    last, prev = qt.get("price"), qt.get("prev_close")
    yr = hist[hist.index >= hist.index[-1] - pd.Timedelta(days=365)] if len(hist) else hist
    lo, hi = (float(yr.min()), float(yr.max())) if len(yr) else (None, None)
    if lo is not None and last:                  # the live price may sit outside lagging daily closes
        lo, hi = min(lo, last), max(hi, last)
    pos52 = (last - lo) / (hi - lo) * 100 if last and lo is not None and hi and hi > lo else None
    return {"id": "quote", "n": 1, "title": "QUOTE", "type": "kpi", "span": 6, "vis": PRIV,
            "context": {"text": "STALE QUOTE — LAST GOOD PRICE" if q["stale"] else ""},
            "items": [kpi("LAST", last, "num:2"), kpi("DAY %", (last / prev - 1) * 100 if last and prev else None, "pct+:2"),
                      kpi("BAR", qt.get("date"), "date"),
                      kpi("52W LOW", lo, "num:2"), kpi("52W HIGH", hi, "num:2"), kpi("52W POS", pos52, "pct"),
                      kpi("1M", _ret(hist, 30), "pct+"), kpi("3M", _ret(hist, 91), "pct+"),
                      kpi("YTD", _ytd(hist), "pct+"), kpi("1Y", _ret(hist, 365), "pct+")]}


def _mine(q, pos, param) -> dict:
    realized = (q["realized"] or {}).get("pnl_eur", 0.0)
    gross = q["acct"]["gross_deposits"]
    div = q["dividends"]
    # bonus shares sit in the cost basis but cost nothing: their value is gain, as in total P&L
    total_pnl = (pos["unrealized_pnl"] if pos else 0.0) + realized + div + q.get("bonus", 0.0)
    contrib = total_pnl / gross * 100 if gross else None
    if pos is None:
        items = [kpi("REALIZED", realized, "eur+"), kpi("DIVIDENDS (NET)", div, "eur+"),
                 kpi("CONTRIB pp", contrib, "pct+:2")]
    else:
        value_all = sum(p["position_value"] for p in q["positions"])
        items = [kpi("SHRS", pos["shares"], "num:4"), kpi("AVG", pos["avg_cost"], "num:2"),
                 kpi("VALUE", pos["position_value"], "eur"), kpi("P&L €", pos["unrealized_pnl"], "eur+"),
                 kpi("P&L %", pos["unrealized_pct"], "pct+"),
                 kpi("WEIGHT", pos["position_value"] / value_all * 100 if value_all else None, "pct"),
                 kpi("REALIZED", realized, "eur+"), kpi("DIVIDENDS (NET)", div, "eur+"),
                 kpi("CONTRIB pp", contrib, "pct+:2")]
    return {"id": "mine", "n": 2, "title": "MY POSITION", "type": "kpi", "span": 6, "vis": PRIV, "items": items}


def _price(hist, txns) -> dict:
    base = {"id": "price", "n": 3, "title": "PRICE", "type": "chart", "span": 8, "vis": PRIV,
            "ranges": ["1M", "6M", "YTD", "1Y", "ALL"], "yfmt": "num", "x": [], "series": []}
    if len(hist) < 2:
        return base
    bars = {t["date"]: bar_on_or_after(hist.index, t["date"]) for t in txns}
    must = pd.DatetimeIndex([b for b in bars.values() if b is not None])
    idx = thin_index(hist.index).union(must)
    y = [num(round(float(v), 4)) for v in hist.reindex(idx)]
    buy, sell = [None] * len(idx), [None] * len(idx)
    for t in txns:
        b = bars[t["date"]]
        if b is None or pd.Timestamp(t["date"]) < hist.index[0]:   # before the 5y history: no marker
            continue
        i = idx.get_loc(b)
        (buy if t["action"] in ADDS else sell)[i] = y[i]          # a bonus is a BUY marker
    return {**base, "x": epoch(idx), "series": [
        {"name": "PRICE", "role": "primary", "kind": "line", "y": y},
        {"name": "BUY", "role": "buy", "kind": "markers", "y": buy},
        {"name": "SELL", "role": "sell", "kind": "markers", "y": sell}]}


def _risk(d, pos, param) -> dict:
    hist, m = d["hist_tr"], d["model"]           # total return: a dividend is not a loss
    yr = hist[hist.index >= hist.index[-1] - pd.Timedelta(days=365)] if len(hist) else hist
    r = yr.pct_change().dropna()
    vol = float(r.std() * np.sqrt(252) * 100) if len(r) > 20 else None
    mdd = float((yr / yr.cummax() - 1).min() * 100) if len(yr) else None
    items = [kpi("VOL 1Y", vol, "pct"), kpi("BETA SPX", riskmodel.beta_of(hist, d["spx"]), "num:2"),
             kpi("MAX DD 1Y", mdd, "pct+")]
    if pos is not None and m is not None and param in m.universe:
        i = m.universe.index(param)
        others = m.corr[param].drop(param).sort_values(ascending=False).head(3)
        items += [kpi("RISK %", m.risk_contrib[i] * 100, "pct"), kpi("CAPITAL %", m.cur_w[i] * 100, "pct"),
                  kpi("TOP CORR", ", ".join(f"{t} {v:.2f}" for t, v in others.items()), "text")]
    return {"id": "risk", "n": 4, "title": "RISK", "type": "kpi", "span": 4, "vis": PRIV, "items": items}


def _trades(txns) -> dict:
    rows = [{"i": f"{t['date']}|{n}", "date": t["date"], "side": t["action"].upper(), "shrs": num(t["shares"]),
             "pps": num(t["pps"]), "amt": num(t["price"])}
            for n, t in enumerate(sorted(txns, key=lambda t: t["date"]))]
    cols = [{"k": "date", "label": "DATE", "fmt": "date"}, {"k": "side", "label": "SIDE", "fmt": "side"},
            {"k": "shrs", "label": "SHRS", "fmt": "num:4", "align": "r"},
            {"k": "pps", "label": "PRICE", "fmt": "num:2", "align": "r"},
            {"k": "amt", "label": "AMOUNT", "fmt": "eur", "align": "r"}]
    return {"id": "trades", "n": 5, "title": "TRADES", "type": "table", "span": 12, "vis": PRIV,
            "key": "i", "sort": ["date", "desc"], "cols": cols, "rows": rows}


SCREEN = Screen(id="SEC", title="Security", fkey=None, status="live", public=False,
                tiers=("quote", "daily"), deps=DEPS, compute=compute, assemble=assemble, params=params,
                accept=accept, uses_inputs=True)
