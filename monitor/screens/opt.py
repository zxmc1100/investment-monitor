"""OPT — Optimizer: current weights vs the five optimizer portfolios, the
efficient frontier, and a minimal private ticket toward the chosen target.

daily part: RiskModel (buffered inputs). quote part: live position values and prices.
Drift, the NOW columns and the ticket use LIVE weights against the DAILY covariance.
"""
from __future__ import annotations

import numpy as np

from monitor import config
from monitor.portfolio import riskmodel, snapshot
from monitor.screens.base import Ctx, Screen
from monitor.screens.common import PRIV, PUB, kpi, num, two_priced
from monitor.screens.identity import identify

DEPS = ("monitor.screens.opt", "monitor.screens.common", "monitor.screens.base",
        "monitor.portfolio.riskmodel", "monitor.portfolio.optimizer", "monitor.portfolio.snapshot",
        "monitor.portfolio.ledger", "monitor.data.buffer", "monitor.data.yahoo",
        "monitor.data.instruments", "monitor.config", "monitor.screens.identity", "monitor.universe.lookup")
MIN_TRADE_EUR = 1.0

HELP = [
    {"h": "EXP RET* / SHARPE*", "vis": PUB, "body": "Expected returns are Black-Litterman "
     "market-implied returns (what market-cap weights imply), not a forecast of your holdings. "
     "Read them as a coherent relative ranking; volatility and risk contributions are the solid "
     "numbers. Not financial advice."},
    {"h": "PORTFOLIOS", "vis": PUB, "body": "MINVAR lowest volatility; RP equal risk contribution; "
     "HRP clusters correlated names then splits risk (uncapped); BLSHARPE best BL return per unit of "
     "risk; BLSAME best BL return at today's volatility. All long-only; all but HRP capped at "
     f"{config.MAX_W:.0%} per position."},
    {"h": "TARGET / DRIFT", "vis": PUB, "body": "TARGET <name> picks the portfolio you steer by "
     "(default HRP). DRIFT = half the summed absolute weight gap — the share of the book a full "
     "rebalance would move."},
    {"h": "TICKET", "vis": PRIV, "body": "The trades that would reach the target at live prices, "
     "about €{fee} fee per order. Informational only — not financial advice."},
]


def compute(tier: str, ctx: Ctx) -> dict:
    book = snapshot.load_book(ctx.portfolio_csv)
    if tier == "quote":
        q = snapshot.quote_tier(book, force=ctx.force, buffer_dir=ctx.buffer_dir)
        return {"values": {p["ticker"]: p["position_value"] for p in q["positions"]}, "warn": q["warn"],
                "prices": {t: p for t, p in q["prices"].items() if p},
                "names": {t: r["name"] for t, r in identify(book["holdings"], buffer_dir=ctx.buffer_dir,
                                                            need=("name",)).items()}}
    if tier == "daily":
        return {"model": riskmodel.build_model(**snapshot.risk_inputs(
            book, force=ctx.force, buffer_dir=ctx.buffer_dir))}
    raise ValueError(f"OPT has no {tier!r} tier")


def _pct(x) -> float | None:
    return num(x * 100) if x is not None else None


def assemble(parts: dict, meta: dict) -> dict:
    q, m = parts["quote"], parts["daily"]["model"]
    target = (meta.get("prefs") or {}).get("target", config.DEFAULT_TARGET)
    uni = m.universe
    values = np.array([q["values"].get(t, 0.0) for t in uni])
    total = float(values.sum())
    now_w = values / total if total else m.cur_w
    now = m.perf(now_w)
    tgt = m.portfolios.get(target)
    drift = float(np.abs(tgt.weights - now_w).sum() / 2) if tgt is not None else None
    return {"screen": "OPT", "title": "Optimizer",
            "context": {"text": f"{len(uni)} ASSETS · TARGET {target} · COV {config.LOOKBACK_DAYS}D", "vis": PUB},
            "meta": {**meta, "warn": q.get("warn") or []},
            "help": [{**h, "body": h["body"].replace("{fee}", f"{config.ORDER_FEE_EUR:g}")} for h in HELP],
            "panels": [_target(target, now, tgt, drift), _weights(m, now_w, target, q.get("names") or {}),
                       _frontier(m, now, target), _portfolios(m, now, now_w, target),
                       _ticket(m, q, values, total, tgt, target)]}


def _target(target, now, tgt, drift) -> dict:
    return {"id": "target", "n": 1, "title": "TARGET", "type": "kpi", "span": 12, "vis": PUB,
            "items": [kpi("TARGET", target, "text", PUB), kpi("DRIFT", _pct(drift), "pct", PUB),
                      kpi("VOL NOW", _pct(now.vol), "pct", PUB),
                      kpi("VOL TGT", _pct(tgt.vol) if tgt else None, "pct", PUB),
                      kpi("EXP RET* NOW", _pct(now.exp_ret), "pct+", PUB),
                      kpi("EXP RET* TGT", _pct(tgt.exp_ret) if tgt else None, "pct+", PUB),
                      kpi("SHARPE* NOW", now.sharpe, "num:2", PUB),
                      kpi("SHARPE* TGT", tgt.sharpe if tgt else None, "num:2", PUB)]}


def _weights(m, now_w, target, names: dict) -> dict:
    keys = [k.lower() for k in config.PORTFOLIOS]
    cols = ([{"k": "tkr", "label": "TKR", "fmt": "tkr", "vis": PUB},
             {"k": "name", "label": "NAME", "fmt": "text", "vis": PUB},
             {"k": "now", "label": "NOW", "fmt": "pct", "vis": PUB, "align": "r"}]
            + [{"k": k, "label": K, "fmt": "pct", "vis": PUB, "align": "r", **({"hl": True} if K == target else {})}
               for k, K in zip(keys, config.PORTFOLIOS)]
            + [{"k": "mkt", "label": "MKTCAP", "fmt": "pct", "vis": PUB, "align": "r"},
               {"k": "dtgt", "label": "Δ TGT pp", "fmt": "pct+", "vis": PUB, "align": "r"}])
    tgt = m.portfolios.get(target)
    rows = []
    for i, t in enumerate(m.universe):
        row = {"tkr": t, "name": names.get(t, t), "now": num(now_w[i] * 100), "mkt": num(m.mkt_w[i] * 100),
               "dtgt": num((tgt.weights[i] - now_w[i]) * 100) if tgt else None}
        for k, K in zip(keys, config.PORTFOLIOS):
            p = m.portfolios.get(K)
            row[k] = num(p.weights[i] * 100) if p else None
        rows.append(row)
    total = {"tkr": "TOTAL", "now": 100.0, "mkt": 100.0,
             **{k: (100.0 if m.portfolios.get(K) else None) for k, K in zip(keys, config.PORTFOLIOS)}}
    return {"id": "weights", "n": 2, "title": "WEIGHTS", "type": "table", "span": 12, "vis": PUB,
            "key": "tkr", "sort": ["now", "desc"], "cols": cols, "rows": rows, "total": total,
            "context": {"text": f"TARGET {target} HIGHLIGHTED", "vis": PUB}}


def _r4(x):
    return round(float(x) * 100, 4)


def _frontier(m, now, target) -> dict:
    series = [{"name": "RANDOM", "kind": "points", "role": "cloud", "vis": PUB,
               "x": [_r4(v) for v, _ in m.cloud], "y": [_r4(r) for _, r in m.cloud]},
              {"name": "FRONTIER", "kind": "line", "role": "frontier", "vis": PUB,
               "x": [_r4(v) for v, _ in m.frontier], "y": [_r4(r) for _, r in m.frontier]},
              {"name": "NOW", "kind": "marker", "role": "now", "vis": PUB, "x": [_r4(now.vol)], "y": [_r4(now.exp_ret)]}]
    for k in config.PORTFOLIOS:
        p = m.portfolios.get(k)
        if p is not None:
            series.append({"name": k, "kind": "marker", "role": "target" if k == target else "port", "vis": PUB,
                           "x": [_r4(p.vol)], "y": [_r4(p.exp_ret)]})
    return {"id": "frontier", "n": 3, "title": "EFFICIENT FRONTIER", "type": "scatter", "span": 7, "vis": PUB,
            "xfmt": "pct", "yfmt": "pct", "context": {"text": "VOL vs EXP RET* (BL)", "vis": PUB},
            "series": series}


def _portfolios(m, now, now_w, target) -> dict:
    rows = [{"id": "NOW", "name": "Current", "vol": _pct(now.vol), "dvol": 0.0, "ret": _pct(now.exp_ret),
             "sharpe": num(now.sharpe), "turn": 0.0}]
    for k in config.PORTFOLIOS:
        p = m.portfolios.get(k)
        rows.append({"id": k, "name": riskmodel.LABELS[k] + ("" if p else " (infeasible)"),
                     "vol": _pct(p.vol) if p else None, "dvol": _pct(p.vol - now.vol) if p else None,
                     "ret": _pct(p.exp_ret) if p else None, "sharpe": num(p.sharpe) if p else None,
                     "turn": _pct(float(np.abs(p.weights - now_w).sum() / 2)) if p else None})
    cols = [{"k": "id", "label": "ID", "fmt": "tkr", "vis": PUB},
            {"k": "name", "label": "PORTFOLIO", "fmt": "text", "vis": PUB},
            {"k": "vol", "label": "VOL", "fmt": "pct", "vis": PUB, "align": "r"},
            {"k": "dvol", "label": "Δ VOL", "fmt": "pct+", "vis": PUB, "align": "r"},
            {"k": "ret", "label": "EXP RET*", "fmt": "pct+", "vis": PUB, "align": "r"},
            {"k": "sharpe", "label": "SHARPE*", "fmt": "num:2", "vis": PUB, "align": "r"},
            {"k": "turn", "label": "TURNOVER", "fmt": "pct", "vis": PUB, "align": "r"}]
    # The cursor starts on your TARGET and drives the private TICKET; Enter makes the row your TARGET.
    return {"id": "portfolios", "n": 4, "title": "PORTFOLIOS", "type": "table", "span": 5, "vis": PUB,
            "key": "id", "sort": ["vol", "asc"], "cols": cols, "rows": rows,
            "cursor": target, "enter": "TARGET {key}"}


def _trades(m, q, values, total, port) -> tuple[list[dict], str]:
    """Orders that turn today's live values into `port`'s weights, and their summary line."""
    rows = []
    for i, t in enumerate(m.universe):
        want = float(port.weights[i] * total)
        d = want - float(values[i])
        if abs(d) < MIN_TRADE_EUR:
            continue
        price = q["prices"].get(t)
        rows.append({"tkr": t, "now_eur": num(round(float(values[i]), 2)), "tgt_eur": num(round(want, 2)),
                     "d_eur": num(round(d, 2)), "shares": num(round(d / price, 4)) if price else None,
                     "side": "BUY" if d > 0 else "SELL"})
    turn = sum(abs(r["d_eur"]) for r in rows) / 2 / total * 100 if total else 0.0
    return rows, f"{len(rows)} ORDERS · FEES ~€{len(rows) * config.ORDER_FEE_EUR:.0f} · TURNOVER {turn:.1f}%"


def _ticket(m, q, values, total, tgt, target) -> dict:
    """Trades toward the TARGET (`rows`/`context`), plus one ticket per portfolio in
    `rows_by_key` so the panel follows the PORTFOLIOS cursor (title "TICKET → {key}")."""
    cols = [{"k": "tkr", "label": "TKR", "fmt": "tkr", "vis": PRIV},
            {"k": "now_eur", "label": "NOW €", "fmt": "eur", "vis": PRIV, "align": "r"},
            {"k": "tgt_eur", "label": "TARGET €", "fmt": "eur", "vis": PRIV, "align": "r"},
            {"k": "d_eur", "label": "Δ €", "fmt": "eur+", "vis": PRIV, "align": "r"},
            {"k": "shares", "label": "≈SHRS", "fmt": "num:4", "vis": PRIV, "align": "r"},
            {"k": "side", "label": "SIDE", "fmt": "side", "vis": PRIV}]
    by_key = {"NOW": {"rows": [], "context": "NO TRADES — THIS IS YOUR CURRENT MIX"}}
    for k in config.PORTFOLIOS:
        p = m.portfolios.get(k)
        if p is None:
            by_key[k] = {"rows": [], "context": f"{k} INFEASIBLE — NO TRADES"}
        else:
            rows, ctx = _trades(m, q, values, total, p)
            by_key[k] = {"rows": rows, "context": ctx}
    base = {"id": "ticket", "n": 5, "title": "TICKET → {key}", "type": "table", "span": 12, "vis": PRIV,
            "key": "tkr", "sort": ["d_eur", "desc"], "cols": cols, "rows": [],
            "follows": "portfolios", "rows_by_key": by_key}
    if tgt is None:
        return {**base, "context": {"text": "TARGET INFEASIBLE — NO TRADES", "vis": PRIV}}
    rows, ctx = by_key[target]["rows"], by_key[target]["context"]
    return {**base, "rows": rows, "context": {"text": ctx, "vis": PRIV}}


SCREEN = Screen(id="OPT", title="Optimizer", fkey=2, status="live", public=True,
                tiers=("quote", "daily"), deps=DEPS, compute=compute, assemble=assemble,
                needs_portfolio=True, uses_inputs=True, cold=two_priced)
