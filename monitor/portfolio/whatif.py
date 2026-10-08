"""What-if on OPT: the optimizer portfolios had you followed them since your first trade (weights re-estimated each
month on prices strictly before it — no hindsight), the cost of switching into one today (fees + tax on the gains
its sales realize), a paired Monte Carlo of keeping your mix vs switching, and a verdict.

Pure: callers pass buffered prices, caps, the ledger and dividends in (see screens.opt); nothing here touches the
network. Universe = today's holdings ⇒ selection bias: compare the optimizers with EQUAL, never with each other
alone."""
from __future__ import annotations

import numpy as np
import pandas as pd

from monitor import config
from monitor.portfolio import riskmodel as R
from monitor.portfolio.analytics import bench_buy_events
from monitor.portfolio.ledger import DUST, _signed_shares
from monitor.portfolio.optimizer import annualize, to_returns

MIN_OBS = 60                     # daily returns an estimate needs; fewer: the month is equal-weight


def month_starts(first: pd.Timestamp, last: pd.Timestamp) -> list[pd.Timestamp]:
    """The first business day of every month from `first`'s month to `last` — the rebalance dates."""
    return list(pd.date_range(pd.Timestamp(first.year, first.month, 1), last, freq="BMS"))


def holdings_at(transactions: list[dict], day: str) -> dict[str, float]:
    """Shares held after every trade dated strictly before `day`."""
    out: dict[str, float] = {}
    for t in transactions:
        if t["date"] < day:
            out[t["ticker"]] = out.get(t["ticker"], 0.0) + _signed_shares(t)
    return {k: v for k, v in out.items() if v > DUST}


def _equal(lines) -> pd.Series:
    return pd.Series(1.0 / len(lines), index=list(lines))


def month_weights(prices: pd.DataFrame, shares: dict[str, float], transactions: list[dict],
                  months: list[pd.Timestamp]) -> dict[str, list[tuple[pd.Timestamp, pd.Series]]]:
    """{portfolio: [(month start, weights by ticker)]} for every config.PORTFOLIOS key. At each month start m the
    weights are riskmodel.portfolio_weights estimated on `prices` (adjusted closes) strictly before m, over
    [m − LOOKBACK_DAYS, m): a line counts once its prices start by the window's first week. Market caps at m =
    `shares` (outstanding — given, so no price after m is read) × the last price before m. BLSAME targets the
    volatility of YOUR holdings at m. Fewer than two lines with a year of prices (or MIN_OBS returns): every
    portfolio equal-weight over the lines priced before m. An infeasible portfolio keeps its previous weights
    (equal-weight before it has any). A month with no line priced before it is skipped."""
    out: dict[str, list[tuple[pd.Timestamp, pd.Series]]] = {k: [] for k in config.PORTFOLIOS}
    for m in months:
        hist = prices[prices.index < m]
        priced = [t for t in prices.columns if hist[t].notna().any()]
        if not priced:
            continue
        lo = m - pd.Timedelta(days=config.LOOKBACK_DAYS)
        full = [t for t in priced if hist[t].first_valid_index() <= lo + pd.Timedelta(days=7)]
        rets = to_returns(hist.loc[hist.index >= lo, full]) if len(full) >= 2 else pd.DataFrame()
        if len(full) < 2 or len(rets) < MIN_OBS:
            for k in config.PORTFOLIOS:
                out[k].append((m, _equal(priced)))
            continue
        mean_ann, cov = annualize(rets)
        last = hist[full].ffill().iloc[-1]
        caps = {t: shares[t] * float(last[t]) for t in full if shares.get(t)}
        _, mean_bl = R.bl_mean(cov, caps)
        held = holdings_at(transactions, m.date().isoformat())
        vals = np.array([held.get(t, 0.0) * float(last[t]) for t in full])
        cur_w = vals / vals.sum() if vals.sum() > 0 else np.full(len(full), 1.0 / len(full))
        cur_vol = float(np.sqrt(cur_w @ cov.values @ cur_w))
        raw = R.portfolio_weights(mean_ann, cov, mean_bl, cur_vol)
        for k in config.PORTFOLIOS:
            w = raw.get(k)
            if w is not None:
                s = pd.Series(np.clip(np.asarray(w, float), 0.0, None), index=full)
                out[k].append((m, s / s.sum()))
            else:
                out[k].append((m, out[k][-1][1] if out[k] else _equal(full)))
    return out


REBALANCE_BAND = 0.025           # a month start rebalances when any line is this far off its new weight
MIN_TRADE_EUR = 1.0              # smaller trades are not orders


def follow(prices: pd.DataFrame, days: pd.DatetimeIndex, transactions: list[dict],
           schedule: list[tuple[pd.Timestamp, pd.Series]], *, fee: float,
           band: float = REBALANCE_BAND) -> pd.Series:
    """ROI % on `days` of your money had it followed `schedule` — the benchmarks' cash-flow matching
    (analytics.build_roi_timeseries): every buy puts the same euros in on its date (the `<=` business-day pointer,
    less the same single order fee, analytics.bench_buy_events), split by the weights in force; every sale takes
    the same euros out, pro rata, and counts as cash; bonus shares move no money. Prices are adjusted closes, so
    dividends are reinvested. At each schedule date the new weights take over: when any line is more than `band`
    off them, the whole portfolio goes back to target, `fee` per order of at least MIN_TRADE_EUR. ROI = (value +
    cash out) / money in − 1, PORT's formula; NaN before the first buy."""
    px = prices.reindex(prices.index.union(days)).ffill().reindex(days)
    buys = bench_buy_events(transactions)
    sells = sorted((t["date"], float(t["price"])) for t in transactions if t["action"] == "sell")
    sched = sorted(schedule, key=lambda s: s[0])
    units = pd.Series(0.0, index=prices.columns)
    target: pd.Series | None = None
    invested = cash_out = 0.0
    si = bi = ci = 0
    out: dict[pd.Timestamp, float] = {}

    def value(p: pd.Series) -> float:
        return float((units * p).fillna(0.0).sum())

    for d in days:
        ds, p = d.date().isoformat(), px.loc[d]
        moved = False
        while si < len(sched) and sched[si][0] <= d:
            target, moved = sched[si][1], True
            si += 1
        v = value(p)
        if moved and target is not None and v > 0:
            want = target.reindex(prices.columns).fillna(0.0)
            if ((want - (units * p).fillna(0.0) / v).abs() > band).any():
                orders = int(((want * v - (units * p).fillna(0.0)).abs() >= MIN_TRADE_EUR).sum())
                after = v - fee * orders
                units = (want * after / p).where(want > 0, 0.0).fillna(0.0)
        while bi < len(buys) and buys[bi][0] <= ds:
            _, eur, f = buys[bi]
            bi += 1
            if target is not None:
                w = target[[t for t in target.index if p.get(t, np.nan) > 0]]
                if len(w):
                    units = units.add((w / w.sum()) * (eur - f) / p[w.index], fill_value=0.0)
            invested += eur
        while ci < len(sells) and sells[ci][0] <= ds:
            eur = sells[ci][1]
            ci += 1
            v = value(p)
            if v > 0:
                units = units * max(0.0, 1.0 - eur / v)
            cash_out += eur
        if invested > 0:
            out[d] = (value(p) + cash_out) / invested * 100 - 100
    return pd.Series(out, dtype=float).reindex(days)
