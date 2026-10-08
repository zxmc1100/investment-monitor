"""Portfolio risk model: covariance, Black-Litterman prior, the optimizer
portfolios (and the equal-weight baseline), frontier, risk contribution, correlation, concentration, beta and stress tests.

Pure: callers pass buffered prices in (see snapshot.risk_inputs / long_history); nothing here
touches the network. The portfolio recipe is pinned by a frozen parity test
(tests/fixtures/golden/riskmodel_legacy.json).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from monitor import config
from monitor.portfolio.optimizer import (annualize, black_litterman, capped_return_range,
                                         efficient_frontier, hrp, max_return_at_vol, optimize,
                                         portfolio_perf, random_portfolios, risk_contributions,
                                         risk_parity, to_returns)

LABELS = {"MINVAR": "Min-Var", "RP": "Risk Parity", "HRP": "HRP",
          "BLSHARPE": "BL Max-Sharpe", "BLSAME": "BL Same-Risk", "EQUAL": "Equal-Weight"}
CLOUD_POINTS = 600
BETA_MIN_OBS = 20

WINDOWS = (("GFC", "2008 crisis", "2007-10-09", "2009-03-09"),
           ("COVID", "COVID crash", "2020-02-19", "2020-03-23"),
           ("RATES22", "2022 rates", "2022-01-03", "2022-10-12"))
SHOCKS = (("SPX10", "SPX -10%", -0.10), ("SPX20", "SPX -20%", -0.20))
WORST = (("W1D", "Worst 1-day", 1), ("W5D", "Worst 5-day", 5), ("W21D", "Worst 21-day", 21))


@dataclass(frozen=True, eq=False)
class Portfolio:
    weights: np.ndarray
    vol: float
    exp_ret: float
    sharpe: float


@dataclass(frozen=True, eq=False)
class RiskModel:
    universe: list[str]
    cur_w: np.ndarray
    cov: pd.DataFrame
    mkt_w: np.ndarray
    mu_bl: np.ndarray
    cur: Portfolio
    portfolios: dict[str, Portfolio | None]
    risk_contrib: np.ndarray
    corr: pd.DataFrame
    frontier: list[tuple[float, float]]
    cloud: list[tuple[float, float]]
    hhi: float
    eff_n: float
    top3: float
    div_ratio: float | None
    beta: dict[str, float] = field(default_factory=dict)
    beta_port: float | None = None
    window_returns: pd.DataFrame | None = None
    full_returns: pd.DataFrame | None = None

    def perf(self, w) -> Portfolio:
        w = np.asarray(w, float)
        r, v, s = portfolio_perf(w, self.mu_bl, self.cov.values, config.RF)
        return Portfolio(w, v, r, s)


def _beta(asset_ret: pd.Series, mkt_ret: pd.Series) -> float | None:
    df = pd.concat([asset_ret, mkt_ret], axis=1, join="inner").dropna()
    if len(df) < BETA_MIN_OBS:
        return None
    c = np.cov(df.iloc[:, 0], df.iloc[:, 1])
    return float(c[0, 1] / c[1, 1]) if c[1, 1] > 0 else None


def beta_of(prices: pd.Series, mkt_prices: pd.Series | None, days: int = 365) -> float | None:
    """Beta of a price series vs a market price series over the trailing `days` calendar days."""
    if mkt_prices is None or prices is None:
        return None
    a = prices.dropna().pct_change().dropna()
    m = mkt_prices.dropna().pct_change().dropna()
    if a.empty:
        return None
    a = a[a.index >= a.index[-1] - pd.Timedelta(days=days)]
    return _beta(a, m)


def bl_mean(cov: pd.DataFrame, caps: dict[str, float]) -> tuple[np.ndarray, pd.Series]:
    """(market-cap weights, Black-Litterman expected returns RF + Π) over `cov`'s lines. ETFs / missing caps get
    the median cap (never crushed to ~0, never dominant)."""
    universe = list(cov.index)
    avail = [caps[t] for t in universe if t in caps]
    fallback = float(np.median(avail)) if avail else 1.0
    cap_vec = np.array([caps.get(t, fallback) for t in universe])
    mkt_w = cap_vec / cap_vec.sum()
    pi = black_litterman(cov, mkt_w, delta=config.BL_DELTA, tau=config.BL_TAU, views=config.BL_VIEWS)
    return mkt_w, config.RF + pi


def portfolio_weights(mean_ann: pd.Series, cov: pd.DataFrame, mean_bl: pd.Series,
                      cur_vol: float) -> dict[str, np.ndarray | None]:
    """Every config.PORTFOLIOS weight vector (None: infeasible) for one estimate — OPT's portfolios and the what-if
    backtest's month-by-month ones (portfolio.whatif) come from here, so the two never differ in method. EQUAL
    (1/N) ignores MAX_W: it is the baseline, not an optimisation."""
    kw = dict(long_only=config.LONG_ONLY, max_w=config.MAX_W)
    n = len(cov)
    raw = {"MINVAR": optimize(mean_ann, cov, objective="min_var", rf=config.RF, **kw),
           "RP": risk_parity(cov, max_w=config.MAX_W),
           "HRP": hrp(cov),
           "BLSHARPE": optimize(mean_bl, cov, objective="sharpe", rf=config.RF, **kw),
           "BLSAME": max_return_at_vol(mean_bl, cov, cur_vol, **kw),
           "EQUAL": np.full(n, 1.0 / n)}
    return {k: raw[k] for k in config.PORTFOLIOS}


def build_model(values: dict[str, float], history: pd.DataFrame, caps: dict[str, float],
                *, spx: pd.Series | None) -> RiskModel:
    tickers = list(values)
    cols = [t for t in tickers if history is not None and t in history.columns]
    rets = to_returns(history[cols]) if cols else pd.DataFrame()
    universe = [t for t in tickers if t in rets.columns]
    if len(universe) < 2 or rets.empty:
        raise ValueError("risk model needs at least two priced positions")
    window = rets.loc[rets.index >= rets.index[-1] - pd.Timedelta(days=config.LOOKBACK_DAYS)]
    mean_ann, cov = annualize(window[universe])
    tot = sum(values[t] for t in universe) or 1.0
    cur_w = np.array([values[t] / tot for t in universe])

    mkt_w, mean_bl = bl_mean(cov, caps)          # market-cap prior (see bl_mean)
    mu_bl, sig = mean_bl.values, cov.values

    def perf(w) -> Portfolio:
        r, v, s = portfolio_perf(np.asarray(w, float), mu_bl, sig, config.RF)
        return Portfolio(np.asarray(w, float), v, r, s)

    cur = perf(cur_w)
    kw = dict(long_only=config.LONG_ONLY, max_w=config.MAX_W)
    raw = portfolio_weights(mean_ann, cov, mean_bl, cur.vol)
    portfolios = {k: (perf(raw[k]) if raw[k] is not None else None) for k in config.PORTFOLIOS}

    reach = (capped_return_range(mu_bl, config.MAX_W) if config.LONG_ONLY
             else (float(mu_bl.min()), float(mu_bl.max())))
    frontier = ([] if reach is None else
                [(f["vol"], f["ret"]) for f in efficient_frontier(mean_bl, cov, n_points=40, ret_range=reach, **kw)])
    cloud_df = random_portfolios(mean_bl, cov, n=2500, rf=config.RF)
    step = math.ceil(len(cloud_df) / CLOUD_POINTS)
    cloud = list(zip(cloud_df["vol"].iloc[::step].tolist(), cloud_df["ret"].iloc[::step].tolist()))

    vols = np.sqrt(np.diag(sig))
    hhi = float((cur_w ** 2).sum())
    beta, beta_port = {}, None
    if spx is not None and len(spx.dropna()) > 1:
        m_ret = spx.dropna().pct_change().dropna()
        m_ret = m_ret[m_ret.index >= window.index[0]]
        for t in universe:
            b = _beta(window[t], m_ret)
            if b is not None:
                beta[t] = b
        beta_port = _beta(window[universe] @ cur_w, m_ret)

    return RiskModel(universe=universe, cur_w=cur_w, cov=cov, mkt_w=mkt_w, mu_bl=mu_bl, cur=cur,
                     portfolios=portfolios, risk_contrib=risk_contributions(cur_w, cov),
                     corr=window[universe].corr(), frontier=frontier, cloud=cloud,
                     hhi=hhi, eff_n=1.0 / hhi if hhi else float("nan"),
                     top3=float(np.sort(cur_w)[-3:].sum()),
                     div_ratio=float(cur_w @ vols / cur.vol) if cur.vol > 0 else None,
                     beta=beta, beta_port=beta_port,
                     window_returns=window[universe], full_returns=rets[universe])


def var_cvar(model: RiskModel, q: float = 0.05) -> tuple[float | None, float | None]:
    """Historical 1-day VaR and CVaR of today's weights over the covariance window, in %."""
    r = model.window_returns @ model.cur_w
    if len(r) < 20:
        return None, None
    var = float(r.quantile(q))
    return var * 100, float(r[r <= var].mean()) * 100


@dataclass(frozen=True)
class Scenario:
    key: str
    label: str
    port_ret: float | None
    worst_ticker: str | None
    worst_ret: float | None
    n_estimated: int
    n_total: int


# A one-day move beyond +-40% inside a stress window is treated as a data glitch (unadjusted
# split / ratio change), not a price move: such a history does not count as covering the window.
MAX_DAILY_MOVE = 0.40


def _window_ret(s: pd.Series | None, a: str, b: str) -> float | None:
    if s is None:
        return None
    s = s.dropna()
    if s.empty or s.index[0] > pd.Timestamp(a) or s.index[-1] < pd.Timestamp(b):
        return None
    start, end = s.loc[:a].iloc[-1], s.loc[:b].iloc[-1]
    seg = s.loc[s.loc[:a].index[-1]:s.loc[:b].index[-1]]
    if len(seg) > 1 and (seg.pct_change().abs() > MAX_DAILY_MOVE).any():
        return None
    return float(end / start - 1) if start > 0 else None


def _combine(key, label, rets: dict[str, float | None], model, n_est) -> Scenario:
    if any(v is None for v in rets.values()):
        return Scenario(key, label, None, None, None, n_est, len(model.universe))
    port = float(sum(w * rets[t] for t, w in zip(model.universe, model.cur_w)))
    worst = min(rets, key=rets.get)
    return Scenario(key, label, port, worst, rets[worst], n_est, len(model.universe))


def stress(model: RiskModel, long_history: pd.DataFrame, spx_long: pd.Series | None) -> list[Scenario]:
    out = []
    for key, label, a, b in WINDOWS:
        spx_ret = _window_ret(spx_long, a, b)
        rets, n_est = {}, 0
        for t in model.universe:
            own = _window_ret(long_history[t] if t in long_history.columns else None, a, b)
            if own is None:
                n_est += 1
                beta = model.beta.get(t)
                own = None if spx_ret is None or beta is None else beta * spx_ret
            rets[t] = own
        out.append(_combine(key, label, rets, model, n_est))
    for key, label, shock in SHOCKS:
        rets = {t: (model.beta[t] * shock if t in model.beta else None) for t in model.universe}
        out.append(_combine(key, label, rets, model, len(model.universe)))
    full = model.full_returns
    port = full @ model.cur_w
    for key, label, n in WORST:
        if len(port) < n:
            out.append(Scenario(key, label, None, None, None, 0, len(model.universe)))
            continue
        growth = (1 + port).rolling(n).apply(np.prod, raw=True) - 1
        end = int(np.nanargmin(growth.values))
        window = full.iloc[end - n + 1: end + 1]
        asset = ((1 + window).prod() - 1).to_dict()
        worst = min(asset, key=asset.get)
        out.append(Scenario(key, label, float(growth.iloc[end]), worst, float(asset[worst]), 0,
                            len(model.universe)))
    return out
