"""OPT — Optimizer: current weights vs the five optimizer portfolios, the
efficient frontier, and a minimal private ticket toward the chosen target.

daily part: RiskModel (buffered inputs). quote part: live position values and prices.
Drift, the NOW columns and the ticket use LIVE weights against the DAILY covariance.
"""
from __future__ import annotations

import logging
import pickle
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from monitor import config
from monitor.portfolio import riskmodel, snapshot, whatif
from monitor.portfolio.meta import PORTFOLIO_ETFS
from monitor.screens.base import Ctx, Screen
from monitor.screens.common import PRIV, PUB, epoch, kpi, num, thin_index, two_priced
from monitor.screens.identity import identify

DEPS = ("monitor.screens.opt", "monitor.screens.common", "monitor.screens.base",
        "monitor.portfolio.riskmodel", "monitor.portfolio.optimizer", "monitor.portfolio.snapshot",
        "monitor.portfolio.ledger", "monitor.data.buffer", "monitor.data.yahoo",
        "monitor.data.instruments", "monitor.config", "monitor.screens.identity", "monitor.universe.lookup",
        "monitor.portfolio.whatif", "monitor.portfolio.analytics", "monitor.portfolio.dividends",
        "monitor.portfolio.meta")
MIN_TRADE_EUR = 1.0
log = logging.getLogger(__name__)

HELP = [
    {"h": "EXP RET* / SHARPE*", "vis": PUB, "body": "Expected returns are Black-Litterman "
     "market-implied returns (what market-cap weights imply), not a forecast of your holdings. "
     "Read them as a coherent relative ranking; volatility and risk contributions are the solid "
     "numbers. Not financial advice."},
    {"h": "PORTFOLIOS", "vis": PUB, "body": "MINVAR lowest volatility; RP equal risk contribution; "
     "HRP clusters correlated names then splits risk (uncapped); BLSHARPE best BL return per unit of "
     "risk; BLSAME best BL return at today's volatility; EQUAL 1/N over the same lines, uncapped — the baseline "
     "every optimizer must beat. All long-only; all but HRP and EQUAL capped at "
     f"{config.MAX_W:.0%} per position."},
    {"h": "TARGET / DRIFT", "vis": PUB, "body": "TARGET <name> picks the portfolio you steer by "
     "(default HRP). DRIFT = half the summed absolute weight gap — the share of the book a full "
     "rebalance would move."},
    {"h": "TICKET", "vis": PRIV, "body": "The trades that would reach the target at live prices, "
     "about €{fee} fee per order. Informational only — not financial advice."},
    {"h": "FOLLOWED", "vis": PUB, "body": "Had your money followed a portfolio since your first trade: the same euros "
     "on the same days, weights re-estimated each month on the year before it (never after), back to target when a "
     "line drifts more than 2.5 pp (€{fee} per order), dividends reinvested, no tax. PORTFOLIOS' FOLLOWED column is "
     "the ROI it would show today (NOW: your real ROI); the FOLLOWED chart draws it for the cursor row against YOUR "
     "line and EQUAL — 1M … ALL measure each window by the ROI formula, as PORT's ROI chart (⌥R · ⌥M: ROI, MWR). The "
     "universe is the stocks you hold today, which flatters every line: judge an optimizer by EQUAL, not by YOU."},
    {"h": "EFFICIENT FRONTIER", "vis": PUB, "body": "Expected return (Black-Litterman) against volatility. The curve: "
     "the most return each level of risk can have from your holdings (long-only, capped as the optimizers); the "
     "cloud: random mixes of them; grey dots: each holding alone; white: NOW, your mix. The cursor row of PORTFOLIOS "
     "gets a line from NOW to it and, on the title, what the move changes — a dot straight above NOW is more return "
     "for the same risk, one to its left the same return for less. Expected returns are the model's, not a "
     "forecast: read the picture as a ranking."},
    {"h": "AHEAD", "vis": PUB, "body": "The frontier played out: keeping your mix (grey) and switching to the cursor "
     f"portfolio today (its colour), each its median and 90 % band of change from today on the same {whatif.MC_PATHS} "
     "simulated paths (Monte Carlo on Black-Litterman expected returns and the 1-year covariance, buy and hold). A "
     "switch starts below 0 by its costs. A lower, narrower fan is less risk for less return; fans that overlap "
     "almost entirely are not worth a switch. REBALANCE? compares them path by path. 6M · 1Y · 3Y: how far ahead. "
     "The model that picked a portfolio also scores it, so it favours it — FOLLOWED is the reality check. Not "
     "financial advice."},
    {"h": "REBALANCE?", "vis": PUB, "body": "The cursor portfolio against keeping your mix, on the same simulated "
     "paths: P(AHEAD) the share of paths it ends above; MEDIAN Δ and WORST 5% Δ in euros; BREAK-EVEN the month "
     "the median difference turns positive. COSTS: €{fee} per order plus tax on the gains its sales realize (FIFO; "
     "equity funds 30 % exempt) beyond what is left of your allowance this year (settings tax_free_allowance, "
     "allowance_eur). WORTH IT: P(AHEAD) ≥ 60 % and a positive median; NOT WORTH IT: under 50 % or a median ≤ 0; "
     "MARGINAL between. The model that picked a portfolio also scores it, so it favours it — FOLLOWED (how it "
     "did with your money, no hindsight) is the reality check. Not financial advice."},
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
        inputs = snapshot.risk_inputs(book, force=ctx.force, buffer_dir=ctx.buffer_dir)
        model = riskmodel.build_model(**inputs)
        return {"model": model, "whatif": _whatif(book, model, inputs, ctx)}
    raise ValueError(f"OPT has no {tier!r} tier")


WHATIF_LAST = "whatif_last.pkl"     # the last good what-if, in the buffer: a failed run never replaces it


def _whatif(book: dict, model, inputs: dict, ctx: Ctx) -> dict:
    """whatif.build over its inputs — your ROI line (PORT's walk), the paid dividends, which lines are funds. A
    failure (a throttled Yahoo answer, mostly) never replaces good data and never takes OPT down: the last good
    what-if comes back marked `stale` (the error's type), or — none yet — panels 5 and 6 say why. Only the error's
    type reaches the payload (the message may name a local path); the rest is logged."""
    last = Path(ctx.buffer_dir or config.BUFFER_DIR) / WHATIF_LAST
    try:
        port = snapshot.daily_tier(book, force=False, buffer_dir=ctx.buffer_dir)
        divs = snapshot.dividends(book, buffer_dir=ctx.buffer_dir)
        ident = identify(model.universe, buffer_dir=ctx.buffer_dir, need=("name",), max_asks=0)
        funds = {t for t, r in ident.items() if "countries" in r} | (PORTFOLIO_ETFS & set(model.universe))
        w = whatif.build(model, inputs["values"], inputs["history"], inputs["caps"], book, port["roi_series"],
                         divs, funds=funds, today=date.today())
    except Exception as e:                                   # noqa: BLE001 — the what-if must never take OPT down
        log.warning("what-if failed", exc_info=True)
        try:
            return {**pickle.loads(last.read_bytes()), "stale": type(e).__name__}
        except Exception:                                    # noqa: BLE001 — no last good one: say why
            return {"error": type(e).__name__}
    try:
        last.parent.mkdir(parents=True, exist_ok=True)
        tmp = last.with_suffix(".tmp")
        tmp.write_bytes(pickle.dumps(w))
        tmp.replace(last)
    except OSError:
        log.warning("what-if: could not keep the last good one", exc_info=True)
    return w


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
    w = parts["daily"].get("whatif") or {"error": "not computed yet"}
    return {"screen": "OPT", "title": "Optimizer",
            "context": {"text": f"{len(uni)} ASSETS · TARGET {target} · COV {config.LOOKBACK_DAYS}D", "vis": PUB},
            "meta": {**meta, "warn": q.get("warn") or []},
            "help": [{**h, "body": h["body"].replace("{fee}", f"{config.ORDER_FEE_EUR:g}")} for h in HELP],
            # one window: TARGET over PORTFOLIOS beside WEIGHTS · the FRONTIER (half the width, the row's height)
            # beside AHEAD and FOLLOWED · REBALANCE? beside TICKET
            "panels": [_target(target, now, tgt, drift), _weights(m, now_w, target, q.get("names") or {}),
                       _portfolios(m, now, now_w, target, w), _frontier(m, now, target), _ahead(w), _followed(w),
                       _verdict(w, target), _ticket(m, q, values, total, tgt, target)]}


def _target(target, now, tgt, drift) -> dict:
    return {"id": "target", "n": 1, "title": "TARGET", "type": "kpi", "span": 5, "vis": PUB,
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
    return {"id": "weights", "n": 2, "title": "WEIGHTS", "type": "table", "span": 7, "rows_span": 2, "vis": PUB,
            "key": "tkr", "sort": ["now", "desc"], "cols": cols, "rows": rows, "total": total,
            "context": {"text": f"TARGET {target} HIGHLIGHTED", "vis": PUB}}


def _r4(x):
    return round(float(x) * 100, 4)


def _frontier(m, now, target) -> dict:
    series = [{"name": "RANDOM", "kind": "points", "role": "cloud", "vis": PUB,
               "x": [_r4(v) for v, _ in m.cloud], "y": [_r4(r) for _, r in m.cloud]},
              {"name": "FRONTIER", "kind": "line", "role": "frontier", "vis": PUB,
               "x": [_r4(v) for v, _ in m.frontier], "y": [_r4(r) for _, r in m.frontier]},
              # each holding on its own: why the optimizers weigh it as they do
              *({"name": t, "kind": "marker", "role": "asset", "vis": PUB, "x": [_r4(v)], "y": [_r4(r)]}
                for t, v, r in zip(m.universe, np.sqrt(np.diag(m.cov.values)), m.mu_bl)),
              {"name": "NOW", "kind": "marker", "role": "now", "vis": PUB, "x": [_r4(now.vol)], "y": [_r4(now.exp_ret)]}]
    by_key = {"NOW": {"series": [], "note": "YOUR MIX", "vis": PUB}}
    for k in config.PORTFOLIOS:
        p = m.portfolios.get(k)
        if p is None:
            by_key[k] = {"series": [], "note": f"{k} INFEASIBLE", "vis": PUB}
            continue
        series.append({"name": k, "kind": "marker", "role": "target" if k == target else "port", "vis": PUB,
                       "x": [_r4(p.vol)], "y": [_r4(p.exp_ret)]})
        # the cursor row: the move from your mix to it, and what it changes
        by_key[k] = {"series": [{"name": "MOVE", "kind": "line", "role": "move", "vis": PUB,
                                 "x": [_r4(now.vol), _r4(p.vol)], "y": [_r4(now.exp_ret), _r4(p.exp_ret)]}],
                     "note": f"NOW → {k}: VOL {(p.vol - now.vol) * 100:+.1f} · EXP RET* {(p.exp_ret - now.exp_ret) * 100:+.1f} PP",
                     "vis": PUB}
    return {"id": "frontier", "n": 4, "title": "EFFICIENT FRONTIER", "type": "scatter", "span": 6, "vis": PUB,
            "xfmt": "pct", "yfmt": "pct", "xlabel": "VOLATILITY (1Y, ANNUAL)", "ylabel": "EXPECTED RETURN* (BL, ANNUAL)",
            "context": {"text": "BL · 1Y COV", "vis": PUB},
            "series": series, "follows": "portfolios", "series_by_key": by_key}


def _last(s) -> float | None:
    return num(float(s.dropna().iloc[-1])) if s is not None and s.notna().any() else None


def _portfolios(m, now, now_w, target, w: dict) -> dict:
    # FOLLOWED: the reality check next to the model's own numbers — the ROI your money would have now had it followed
    # each since your first trade (NOW: your real ROI)
    past = w.get("past") or {}
    rows = [{"id": "NOW", "name": "Current", "vol": _pct(now.vol), "dvol": 0.0, "ret": _pct(now.exp_ret),
             "sharpe": num(now.sharpe), "turn": 0.0, "past": _last(w.get("you"))}]
    for k in config.PORTFOLIOS:
        p = m.portfolios.get(k)
        rows.append({"id": k, "name": riskmodel.LABELS[k] + ("" if p else " (infeasible)"),
                     "vol": _pct(p.vol) if p else None, "dvol": _pct(p.vol - now.vol) if p else None,
                     "ret": _pct(p.exp_ret) if p else None, "sharpe": num(p.sharpe) if p else None,
                     "turn": _pct(float(np.abs(p.weights - now_w).sum() / 2)) if p else None,
                     "past": _last(past.get(k))})
    cols = [{"k": "id", "label": "ID", "fmt": "tkr", "vis": PUB},
            {"k": "name", "label": "PORTFOLIO", "fmt": "text", "vis": PUB},
            {"k": "vol", "label": "VOL", "fmt": "pct", "vis": PUB, "align": "r"},
            {"k": "dvol", "label": "Δ VOL", "fmt": "pct+", "vis": PUB, "align": "r"},
            {"k": "ret", "label": "EXP RET*", "fmt": "pct+", "vis": PUB, "align": "r"},
            {"k": "sharpe", "label": "SHARPE*", "fmt": "num:2", "vis": PUB, "align": "r"},
            {"k": "turn", "label": "TURNOVER", "fmt": "pct", "vis": PUB, "align": "r"},
            {"k": "past", "label": "FOLLOWED", "fmt": "pct+", "vis": PUB, "align": "r", "hl": True}]
    # The cursor starts on your TARGET and drives the private TICKET; Enter makes the row your TARGET.
    return {"id": "portfolios", "n": 3, "title": "PORTFOLIOS", "type": "table", "span": 5, "vis": PUB,
            "key": "id", "sort": ["vol", "asc"], "cols": cols, "rows": rows,
            "cursor": target, "enter": "TARGET {key}"}


COLORS = {"NOW": "#ffffff", "MINVAR": "#4fc3f7", "RP": "#e040fb", "HRP": "#00e676", "BLSHARPE": "#ffeb3b",
          "BLSAME": "#ff7043", "EQUAL": "#bdbdbd"}


def _r2(v):                         # hundredths: more than a chart shows, a third of the bytes
    return num(round(float(v), 2)) if v is not None and v == v else None


def _followed(w: dict) -> dict:
    """FOLLOWED → {key}: your ROI line, the cursor portfolio's and EQUAL's had your money followed them — one
    question at a time, measured like PORT's ROI chart: any window by the ROI formula (`inv`, each line's `cash`)."""
    base = {"id": "followed", "n": 6, "title": "FOLLOWED → {key}", "type": "chart", "span": 3, "vis": PUB,
            "yfmt": "pct+", "legend": "rank", "follows": "portfolios", "x": [], "series": [], "series_by_key": {},
            "ranges": ["1M", "6M", "YTD", "1Y", "ALL"], "range": "ALL"}
    if "error" in w or "past_cash" not in w:          # a what-if kept from before this panel: nothing to draw yet
        return {**base, "context": {"text": f"WHAT-IF UNAVAILABLE — {w.get('error', 'RECOMPUTING')}", "vis": PUB}}
    idx = thin_index(w["you"].index)

    def line(name, roi, cash, role="port"):
        # `cash`: what the line paid out to each day — private, as on PORT (it tells when money moved)
        return {"name": name, "role": role, "kind": "line", "vis": PUB,
                "color": COLORS["NOW"] if name == "YOU" else COLORS[name],
                "y": [_r2(v) for v in roi.reindex(idx)], "cash": [_r2(v) for v in cash.reindex(idx).fillna(0.0)]}

    shared = [line("YOU", w["you"], w["you_cash"], role="primary")]
    if "EQUAL" in w["past"]:
        shared.append(line("EQUAL", w["past"]["EQUAL"], w["past_cash"]["EQUAL"]))
    by_key = {k: {"series": [line(k, w["past"][k], w["past_cash"][k])] if k in w["past"] and k != "EQUAL" else [],
                  "vis": PUB} for k in ["NOW", *config.PORTFOLIOS]}
    stale = f"STALE — LAST GOOD {w['today']:%d %b %y}".upper() + f" ({w['stale']}) · " if w.get("stale") else ""
    return {**base, "x": epoch(idx), "series": shared, "series_by_key": by_key,
            "inv": [_r2(v) for v in w["inv"].reindex(idx)],
            "context": {"text": stale + "NO HINDSIGHT · JUDGE BY EQUAL", "vis": PUB}}


def _ahead(w: dict) -> dict:
    """AHEAD → {key}: the frontier played out — your mix kept (grey) and the cursor portfolio switched to (its
    colour), each its median and 90 % band of % change from today on the same simulated paths (a switch starts below
    0 by its costs). A lower, narrower fan: less risk, less return. Path by path: REBALANCE?."""
    base = {"id": "ahead", "n": 5, "title": "AHEAD → {key}", "type": "chart", "span": 3, "vis": PUB,
            "yfmt": "pct+", "legend": "rank", "follows": "portfolios", "x": [], "series": [], "series_by_key": {}}
    if "error" in w or not w.get("ahead"):
        return {**base, "context": {"text": f"WHAT-IF UNAVAILABLE — {w.get('error', 'RECOMPUTING')}", "vis": PUB}}
    a = w["ahead"]
    months = len(a["NOW"][0]) - 1
    x = epoch([pd.Timestamp(w["today"]) + pd.DateOffset(months=k) for k in range(months + 1)])

    def band(name, b, color):
        return {"name": f"{name} 90%", "kind": "band", "vis": PUB, "color": color,
                "lo": [_r2(v) for v in b[0]], "hi": [_r2(v) for v in b[4]]}

    def median(name, b, color, role):
        return {"name": name, "role": role, "kind": "line", "vis": PUB, "color": color, "y": [_r2(v) for v in b[2]]}

    keep = [band("NOW", a["NOW"], COLORS["NOW"]), median("NOW", a["NOW"], COLORS["NOW"], "primary")]
    by_key = {"NOW": {"series": keep, "vis": PUB, "note": "KEEP YOUR MIX"}}
    for k in config.PORTFOLIOS:
        by_key[k] = ({"series": [keep[0], band(k, a[k], COLORS[k]), keep[1], median(k, a[k], COLORS[k], "port")],
                      "vis": PUB} if k in a else
                     {"series": [], "vis": PUB, "note": f"{k} INFEASIBLE"})
    stale = f"STALE ({w['stale']}) · " if w.get("stale") else ""
    return {**base, "x": x, "series_by_key": by_key, "horizons": list(whatif.HORIZONS), "horizon": "1Y",
            "today_idx": 0, "context": {"text": f"{stale}% FROM TODAY · MEDIAN · 90%", "vis": PUB}}


def _verdict(w: dict, target: str) -> dict:
    cols = [{"k": "h", "label": "HORIZON", "fmt": "text", "vis": PUB},
            {"k": "p", "label": "P(AHEAD)", "fmt": "pct", "vis": PUB, "align": "r"},
            {"k": "med", "label": "MEDIAN Δ €", "fmt": "eur+", "vis": PRIV, "align": "r"},
            {"k": "down", "label": "WORST 5% Δ €", "fmt": "eur+", "vis": PRIV, "align": "r"},
            {"k": "be", "label": "BREAK-EVEN", "fmt": "text", "vis": PUB, "align": "r"},
            {"k": "verdict", "label": "VERDICT", "fmt": "text", "vis": PUB},
            {"k": "risk", "label": "RISK", "fmt": "text", "vis": PUB}]
    # rows in horizon order (`_i`), not the key's alphabet (1Y < 3Y < 6M); a following block's context is a plain
    # string, as TICKET's (rows_by_key never reaches the public view, so its euros stay private)
    base = {"id": "verdict", "n": 7, "title": "REBALANCE? → {key}", "type": "table", "span": 5, "vis": PUB,
            "key": "h", "sort": ["_i", "asc"], "cols": cols, "rows": [], "follows": "portfolios"}
    if "error" in w:
        return {**base, "rows_by_key": {}, "context": {"text": f"WHAT-IF UNAVAILABLE — {w['error']}", "vis": PUB}}
    by_key = {"NOW": {"rows": [], "context": "THIS IS YOUR CURRENT MIX"}}
    for k in config.PORTFOLIOS:
        v = w["verdicts"].get(k)
        if v is None:
            by_key[k] = {"rows": [], "context": f"{k} INFEASIBLE — NO VERDICT"}
            continue
        rows = [{"_i": i, "h": h, "p": num(v[h]["p_ahead"] * 100), "med": num(round(v[h]["median_diff"], 2)),
                 "down": num(round(v[h]["downside"], 2)),
                 "be": f"{v[h]['break_even']} MO" if v[h]["break_even"] else "—",
                 "verdict": v[h]["label"], "risk": v[h]["risk"]} for i, h in enumerate(whatif.HORIZONS)]
        c = w["costs"][k]
        by_key[k] = {"rows": rows, "context": f"COSTS €{c['total']:.0f} (FEES €{c['fees']:.0f} + TAX €{c['tax']:.0f})"}
    own = by_key.get(target, {"rows": [], "context": ""})
    return {**base, "rows": own["rows"], "context": {"text": own["context"], "vis": PRIV}, "rows_by_key": by_key}


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
    base = {"id": "ticket", "n": 8, "title": "TICKET → {key}", "type": "table", "span": 7, "vis": PRIV,
            "key": "tkr", "sort": ["d_eur", "desc"], "cols": cols, "rows": [],
            "follows": "portfolios", "rows_by_key": by_key}
    if tgt is None:
        return {**base, "context": {"text": "TARGET INFEASIBLE — NO TRADES", "vis": PRIV}}
    rows, ctx = by_key[target]["rows"], by_key[target]["context"]
    return {**base, "rows": rows, "context": {"text": ctx, "vis": PRIV}}


SCREEN = Screen(id="OPT", title="Optimizer", fkey=2, status="live", public=True,
                tiers=("quote", "daily"), deps=DEPS, compute=compute, assemble=assemble,
                needs_portfolio=True, uses_inputs=True, cold=two_priced)
