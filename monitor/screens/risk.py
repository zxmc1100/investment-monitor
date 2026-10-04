"""RISK — Risk & Correlation: where the risk sits, how names move together,
what historical crashes and SPX shocks would do to today's weights, and the drawdown path."""
from __future__ import annotations

import pandas as pd

from monitor.portfolio import riskmodel, snapshot
from monitor.screens.base import Ctx, Screen
from monitor.screens.common import PRIV, PUB, epoch, kpi, num, thin_index, two_priced

DEPS = ("monitor.screens.risk", "monitor.screens.common", "monitor.screens.base",
        "monitor.portfolio.riskmodel", "monitor.portfolio.optimizer", "monitor.portfolio.snapshot",
        "monitor.portfolio.analytics", "monitor.portfolio.ledger", "monitor.data.buffer",
        "monitor.data.yahoo", "monitor.data.instruments", "monitor.config")

HELP = [
    {"h": "VaR95 / CVaR95", "vis": PUB, "body": "Historical 1-day: the 5th-percentile daily return of "
     "today's weights over the covariance window, and the average of the days worse than that."},
    {"h": "RISK vs CAPITAL", "vis": PUB, "body": "Capital is how your money is split; risk is how your "
     "volatility is split (weight x co-movement). A positive gap means a name drives more of your "
     "swings than its weight suggests."},
    {"h": "VOL / BETA SPX / VaR (this screen)", "vis": PUB, "body": "What today's mix would have done "
     "over the last year: current weights x 1y daily returns. PORT's VOL / BETA / VaR measure your "
     "realized path since the first trade (contribution timing included), so the two differ."},
    {"h": "DRAWDOWN / MAX DD / CUR DD", "vis": PUB, "body": "Your realized ROI path since the first "
     "trade, contributions included — not today's weights."},
    {"h": "STRESS", "vis": PUB, "body": "Today's weights replayed over each window. A position without "
     "price history covering the window is estimated as its beta x the S&P 500's move (ESTIMATED n/N). "
     "A position whose own history jumps more than 40% in one day inside a window is treated as a data "
     "glitch and estimated with beta too, so WORST can be an estimated position. "
     "SPX shocks are beta-scaled. Worst N-day uses the last five years."},
    {"h": "HHI / EFF N / DIV RATIO", "vis": PUB, "body": "HHI = sum of squared weights; EFF N = 1/HHI "
     "(how many equal positions your book behaves like); DIV RATIO = weighted average volatility / "
     "portfolio volatility (higher = more diversification benefit)."},
]


def compute(tier: str, ctx: Ctx) -> dict:
    book = snapshot.load_book(ctx.portfolio_csv)
    if tier == "quote":
        q = snapshot.quote_tier(book, force=ctx.force, buffer_dir=ctx.buffer_dir)
        return {"value": sum(p["position_value"] for p in q["positions"]), "warn": q["warn"]}
    if tier == "daily":
        model = riskmodel.build_model(**snapshot.risk_inputs(book, force=ctx.force, buffer_dir=ctx.buffer_dir))
        long_h, spx = snapshot.long_history(model.universe, force=ctx.force, buffer_dir=ctx.buffer_dir)
        dly = snapshot.daily_tier(book, force=False, buffer_dir=ctx.buffer_dir)
        return {"model": model, "stress": riskmodel.stress(model, long_h, spx),
                "roi": dly["roi_series"], "metrics": dly["metrics"]}
    raise ValueError(f"RISK has no {tier!r} tier")


def assemble(parts: dict, meta: dict) -> dict:
    d, value = parts["daily"], parts["quote"]["value"]
    m = d["model"]
    return {"screen": "RISK", "title": "Risk & Correlation",
            "context": {"text": f"{len(m.universe)} ASSETS · 1Y DAILY", "vis": PUB},
            "meta": {**meta, "warn": parts["quote"].get("warn") or []}, "help": HELP,
            "panels": [_summary(m, d["metrics"] or {}, value), _riskcap(m), _corr(m),
                       _stress(d["stress"], value), _drawdown(d["roi"])]}


def _summary(m, metrics, value) -> dict:
    var, cvar = riskmodel.var_cvar(m)
    return {"id": "summary", "n": 1, "title": "RISK SUMMARY", "type": "kpi", "span": 12, "vis": PUB,
            "context": {"text": "TODAY'S WEIGHTS · 1Y DAILY · DD = REALIZED PATH", "vis": PUB},
            "items": [kpi("VOL", m.cur.vol * 100, "pct", PUB), kpi("BETA SPX", m.beta_port, "num:2", PUB),
                      kpi("VaR95 1D", var, "pct+:2", PUB), kpi("CVaR95 1D", cvar, "pct+:2", PUB),
                      kpi("VaR95 1D €", var / 100 * value if var is not None else None, "eur+"),
                      kpi("MAX DD", metrics.get("max_drawdown"), "pct+", PUB),
                      kpi("CUR DD", metrics.get("current_drawdown"), "pct+", PUB),
                      kpi("HHI", m.hhi, "num:3", PUB), kpi("EFF N", m.eff_n, "num:1", PUB),
                      kpi("TOP-3", m.top3 * 100, "pct", PUB), kpi("DIV RATIO", m.div_ratio, "num:2", PUB)]}


def _riskcap(m) -> dict:
    rows = [{"tkr": t, "cap": num(m.cur_w[i] * 100), "risk": num(m.risk_contrib[i] * 100),
             "gap": num((m.risk_contrib[i] - m.cur_w[i]) * 100)} for i, t in enumerate(m.universe)]
    cols = [{"k": "tkr", "label": "TKR", "fmt": "tkr", "vis": PUB},
            {"k": "cap", "label": "CAPITAL %", "fmt": "pct", "vis": PUB, "align": "r"},
            {"k": "risk", "label": "RISK %", "fmt": "pct", "vis": PUB, "align": "r"},
            {"k": "gap", "label": "GAP pp", "fmt": "pct+r", "vis": PUB, "align": "r"}]
    return {"id": "riskcap", "n": 2, "title": "RISK vs CAPITAL", "type": "table", "span": 4, "vis": PUB,
            "key": "tkr", "sort": ["risk", "desc"], "cols": cols, "rows": rows}


def _corr(m) -> dict:
    labels = list(m.universe)
    cells = [[num(round(float(m.corr.loc[a, b]), 2)) for b in labels] for a in labels]
    return {"id": "corr", "n": 3, "title": "CORRELATION", "type": "heatmap", "span": 8, "vis": PUB,
            "labels": labels, "cells": cells, "fmt": "num:2",
            "context": {"text": "1Y DAILY RETURNS", "vis": PUB}}


def _stress(rows, value) -> dict:
    out = [{"key": s.key, "scn": s.label, "port": num(s.port_ret * 100) if s.port_ret is not None else None,
            "loss": num(round(s.port_ret * value, 2)) if s.port_ret is not None else None,
            "worst": f"{s.worst_ticker} {s.worst_ret * 100:+.1f}%" if s.worst_ticker else "—",
            "est": f"{s.n_estimated}/{s.n_total}"} for s in rows]
    cols = [{"k": "scn", "label": "SCENARIO", "fmt": "text", "vis": PUB},
            {"k": "port", "label": "PORT %", "fmt": "pct+", "vis": PUB, "align": "r"},
            {"k": "loss", "label": "LOSS €", "fmt": "eur+", "vis": PRIV, "align": "r"},
            {"k": "worst", "label": "WORST", "fmt": "text", "vis": PUB},
            {"k": "est", "label": "ESTIMATED", "fmt": "text", "vis": PUB, "align": "r"}]
    # Keyed on the (unique, public) scenario label; the row's "key" field is not a column, so
    # the public build drops it.
    return {"id": "stress", "n": 4, "title": "STRESS TESTS", "type": "table", "span": 7, "vis": PUB,
            "key": "scn", "sort": ["port", "asc"], "cols": cols, "rows": out,
            "context": {"text": "TODAY'S WEIGHTS · β x SPX WHERE NO HISTORY", "vis": PUB}}


def _drawdown(roi: pd.Series) -> dict:
    base = {"id": "drawdown", "n": 5, "title": "DRAWDOWN", "type": "chart", "span": 5, "vis": PUB,
            "yfmt": "pct+", "ranges": ["6M", "YTD", "1Y", "ALL"], "x": [], "series": []}
    if roi is None or roi.empty:
        return base
    lvl = 1 + roi / 100
    dd = (lvl / lvl.cummax() - 1) * 100
    idx = thin_index(dd.index)
    return {**base, "x": epoch(idx), "series": [{"name": "DRAWDOWN", "role": "primary", "kind": "line",
                                                  "vis": PUB, "y": [num(v) for v in dd.reindex(idx)]}]}


SCREEN = Screen(id="RISK", title="Risk & Correlation", fkey=3, status="live", public=True,
                tiers=("quote", "daily"), deps=DEPS, compute=compute, assemble=assemble,
                needs_portfolio=True, uses_inputs=True, cold=two_priced)
