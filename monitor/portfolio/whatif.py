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
