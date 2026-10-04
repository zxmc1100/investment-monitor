"""
Mean-variance and risk-based portfolio construction.

Pure functions (numpy / pandas / scipy): efficient frontier, max-Sharpe and
min-variance portfolios under long-only / max-weight / min-weight / sector-cap
constraints (sector caps via monitor.portfolio.meta.sector_exposure_matrix),
risk parity, hierarchical risk parity and Black-Litterman — the five
portfolios OPT and RISK show (monitor.portfolio.riskmodel).
"""

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.cluster.hierarchy import linkage
from scipy.spatial.distance import squareform

from monitor.portfolio.meta import sector_exposure_matrix

TRADING_DAYS = 252


# ── Data ──────────────────────────────────────────────────────────────────────



def to_returns(prices: pd.DataFrame) -> pd.DataFrame:
    """Daily simple returns, rows with any missing asset dropped (aligned panel)."""
    return prices.pct_change().dropna(how="any")


def annualize(returns: pd.DataFrame) -> tuple[pd.Series, pd.DataFrame]:
    """Annualised mean-return vector and covariance matrix."""
    return returns.mean() * TRADING_DAYS, returns.cov() * TRADING_DAYS


# ── Portfolio math ────────────────────────────────────────────────────────────

def portfolio_perf(w: np.ndarray, mean_ann: np.ndarray, cov_ann: np.ndarray,
                   rf: float = 0.045) -> tuple[float, float, float]:
    """Return (annual_return, annual_vol, sharpe) for weight vector w."""
    ret = float(w @ mean_ann)
    vol = float(np.sqrt(w @ cov_ann @ w))
    sharpe = (ret - rf) / vol if vol > 0 else 0.0
    return ret, vol, sharpe


def _bounds(n: int, long_only: bool, max_w: float, min_w: float):
    lo = min_w if long_only else -max_w
    return tuple((lo, max_w) for _ in range(n))


def _sector_constraints(tickers: list[str], sector_caps: dict[str, float]) -> list[dict]:
    """Linear inequality constraints: sector exposure S @ w <= cap."""
    if not sector_caps:
        return []
    sectors, matrix = sector_exposure_matrix(tickers)
    cons = []
    for sec, row in zip(sectors, matrix):
        cap = sector_caps.get(sec)
        if cap is None or cap >= 0.999:
            continue
        r = np.array(row)
        cons.append({"type": "ineq", "fun": (lambda w, r=r, cap=cap: cap - float(r @ w))})
    return cons


def _solve(objective, n: int, bounds, constraints, x0=None) -> np.ndarray | None:
    if x0 is None:
        x0 = np.repeat(1.0 / n, n)
        x0 = np.clip(x0, [b[0] for b in bounds], [b[1] for b in bounds])
    res = minimize(objective, x0, method="SLSQP", bounds=bounds,
                   constraints=constraints, options={"maxiter": 500, "ftol": 1e-9})
    if not res.success:
        return None
    w = res.x
    w[np.abs(w) < 1e-6] = 0.0
    s = w.sum()
    return w / s if s != 0 else None


def optimize(mean_ann: pd.Series, cov_ann: pd.DataFrame, *, objective: str = "sharpe",
             rf: float = 0.045, long_only: bool = True, max_w: float = 1.0,
             min_w: float = 0.0, sector_caps: dict[str, float] | None = None) -> np.ndarray | None:
    """Solve for optimal weights. objective: 'sharpe' (max) or 'min_var'."""
    tickers = list(mean_ann.index)
    n = len(tickers)
    mu, sig = mean_ann.values, cov_ann.values
    bounds = _bounds(n, long_only, max_w, min_w)
    cons = [{"type": "eq", "fun": lambda w: w.sum() - 1.0}]
    cons += _sector_constraints(tickers, sector_caps or {})

    if objective == "min_var":
        fn = lambda w: float(w @ sig @ w)
    else:
        fn = lambda w: -portfolio_perf(w, mu, sig, rf)[2]   # negative Sharpe
    return _solve(fn, n, bounds, cons)


def risk_contributions(w, cov_ann) -> np.ndarray:
    """Fraction of total portfolio variance contributed by each asset (sums to 1)."""
    w = np.asarray(w, float)
    sig = cov_ann.values if hasattr(cov_ann, "values") else np.asarray(cov_ann)
    pv = float(w @ sig @ w)
    if pv <= 0:
        return np.zeros_like(w)
    return (w * (sig @ w)) / pv


def risk_parity(cov_ann: pd.DataFrame, *, max_w: float = 1.0) -> np.ndarray | None:
    """
    Equal Risk Contribution portfolio: weights so every asset contributes the same
    share of total risk. Needs only the covariance matrix — no return forecast.
    Long-only, weights sum to 1.
    """
    sig = cov_ann.values if hasattr(cov_ann, "values") else np.asarray(cov_ann)
    n = sig.shape[0]
    target = 1.0 / n

    def obj(w):
        pv = float(w @ sig @ w)
        if pv <= 0:
            return 1e6
        rc = (w * (sig @ w)) / pv
        return float(np.sum((rc - target) ** 2))

    bounds = tuple((1e-4, max_w) for _ in range(n))   # tiny floor keeps risk-contrib defined
    cons = [{"type": "eq", "fun": lambda w: w.sum() - 1.0}]
    return _solve(obj, n, bounds, cons, x0=np.repeat(1.0 / n, n))


def hrp(cov_ann: pd.DataFrame) -> np.ndarray | None:
    """Hierarchical Risk Parity (López de Prado): allocate risk top-down across
    a correlation-clustered tree. Covariance-only, no return forecast, long-only,
    weights sum to 1. Unlike Risk-Parity it has no per-asset cap — clustering
    controls concentration naturally (correlated mega-caps are treated as one
    risk unit), which is the whole point.

    Returns weights as an ndarray in cov_ann.index order (matches risk_parity).
    """
    sig = cov_ann.values if hasattr(cov_ann, "values") else np.asarray(cov_ann, float)
    n = sig.shape[0]
    if n == 0:
        return None
    if n == 1:
        return np.array([1.0])

    # correlation → distance d_ij = sqrt(0.5*(1 - corr_ij))
    std = np.sqrt(np.diag(sig))
    corr = sig / np.outer(std, std)
    corr = np.clip(corr, -1.0, 1.0)
    dist = np.sqrt(np.maximum(0.5 * (1.0 - corr), 0.0))

    # 1. tree clustering on the condensed distance matrix
    link = linkage(squareform(dist, checks=False), method="single")

    # 2. quasi-diagonalization: recover the leaf order from the dendrogram
    order = _hrp_quasi_diag(link, n)

    # 3. recursive bisection: split risk budget down the tree (inverse-variance)
    ivp_var = np.diag(sig)
    w = pd.Series(1.0, index=order)
    clusters = [order]
    while clusters:
        clusters = [c[half:] if j else c[:half]            # bisect each cluster
                    for c in clusters if len(c) > 1
                    for half in (len(c) // 2,) for j in (0, 1)]
        for i in range(0, len(clusters), 2):
            left, right = clusters[i], clusters[i + 1]
            v_left = _hrp_cluster_var(sig, ivp_var, left)
            v_right = _hrp_cluster_var(sig, ivp_var, right)
            alpha = 1.0 - v_left / (v_left + v_right)
            w[left] *= alpha
            w[right] *= 1.0 - alpha

    return w.reindex(range(n)).values    # back to cov_ann.index order


def _hrp_quasi_diag(link: np.ndarray, n: int) -> list[int]:
    """Leaf order of the dendrogram so correlated assets sit adjacent."""
    link = link.astype(int)
    order = [link[-1, 0], link[-1, 1]]
    while max(order) >= n:                                  # expand merged nodes
        new = []
        for node in order:
            if node < n:
                new.append(node)
            else:
                m = node - n
                new.extend([link[m, 0], link[m, 1]])
        order = new
    return order


def _hrp_cluster_var(sig: np.ndarray, ivp_var: np.ndarray, idx: list[int]) -> float:
    """Variance of a cluster under inverse-variance weights (the bisection metric)."""
    sub = sig[np.ix_(idx, idx)]
    iv = 1.0 / ivp_var[idx]
    w = iv / iv.sum()
    return float(w @ sub @ w)


def implied_equilibrium_returns(cov_ann: pd.DataFrame, market_weights, delta: float = 2.5) -> pd.Series:
    """
    Black-Litterman reverse optimization: the excess returns the market must expect
    to hold its current cap-weighted mix. Π = δ · Σ · w_market.
    """
    sig = cov_ann.values if hasattr(cov_ann, "values") else np.asarray(cov_ann)
    w = np.asarray(market_weights, float)
    return pd.Series(delta * (sig @ w), index=cov_ann.index)


def black_litterman(cov_ann: pd.DataFrame, market_weights, *, delta: float = 2.5,
                    tau: float = 0.05, views: list[dict] | None = None) -> pd.Series:
    """
    Black-Litterman posterior expected EXCESS returns.

    Starts from market-implied equilibrium (Π = δΣw_mkt) — the market's collective
    forecast, not trailing momentum — and blends in optional subjective views.
    No views → returns Π unchanged.

    views: [{"assets": {ticker: weight}, "ret": annual_excess, "confidence": 0..1}]
           e.g. absolute view "SAP returns 15%/yr": {"assets": {"SAP.DE": 1}, "ret": 0.15, "confidence": 0.6}
    """
    idx = list(cov_ann.index)
    sig = cov_ann.values if hasattr(cov_ann, "values") else np.asarray(cov_ann)
    pi = delta * (sig @ np.asarray(market_weights, float))
    if not views:
        return pd.Series(pi, index=idx)

    P, Q, conf = [], [], []
    for v in views:
        row = np.zeros(len(idx))
        for tk, wt in v["assets"].items():
            if tk in idx:
                row[idx.index(tk)] = wt
        P.append(row); Q.append(v["ret"]); conf.append(v.get("confidence", 0.5))
    P, Q = np.array(P), np.array(Q)
    tau_sig = tau * sig
    # Ω: view uncertainty scaled inversely by confidence
    omega = np.diag(np.diag(P @ tau_sig @ P.T) / np.clip(conf, 1e-3, 1.0))
    A = np.linalg.inv(tau_sig)
    Oi = np.linalg.inv(omega)
    mu = np.linalg.inv(A + P.T @ Oi @ P) @ (A @ pi + P.T @ Oi @ Q)
    return pd.Series(mu, index=idx)




def max_return_at_vol(mean_ann: pd.Series, cov_ann: pd.DataFrame, vol_cap: float, *,
                      long_only: bool = True, max_w: float = 1.0, min_w: float = 0.0,
                      sector_caps: dict[str, float] | None = None) -> np.ndarray | None:
    """
    Maximize expected return subject to portfolio volatility <= vol_cap.
    Answers: "same risk as now, how much more expected return can the mix deliver?"
    """
    tickers = list(mean_ann.index)
    n = len(tickers)
    mu, sig = mean_ann.values, cov_ann.values
    bounds = _bounds(n, long_only, max_w, min_w)
    cons = [{"type": "eq", "fun": lambda w: w.sum() - 1.0},
            {"type": "ineq", "fun": lambda w: vol_cap ** 2 - float(w @ sig @ w)}]
    cons += _sector_constraints(tickers, sector_caps or {})
    return _solve(lambda w: -float(w @ mu), n, bounds, cons)


def capped_return_range(mu, max_w: float) -> tuple[float, float] | None:
    """Lowest and highest expected return a long-only, fully invested portfolio with
    per-asset cap `max_w` can reach (greedy fill), or None when the cap makes 100% infeasible."""
    mu = np.asarray(mu, float)
    if len(mu) * max_w < 1 - 1e-12:
        return None

    def fill(order) -> float:
        w, left = np.zeros(len(mu)), 1.0
        for i in order:
            w[i] = min(max_w, left)
            left -= w[i]
        return float(w @ mu)

    return fill(np.argsort(mu)), fill(np.argsort(-mu))


def efficient_frontier(mean_ann: pd.Series, cov_ann: pd.DataFrame, *, n_points: int = 40,
                       long_only: bool = True, max_w: float = 1.0, min_w: float = 0.0,
                       sector_caps: dict[str, float] | None = None,
                       ret_range: tuple[float, float] | None = None) -> list[dict]:
    """For a sweep of target returns, minimize variance. Returns [{ret,vol,weights}].
    ret_range: sweep bounds (default: min..max asset mean — reaches infeasible targets under caps)."""
    tickers = list(mean_ann.index)
    n = len(tickers)
    mu, sig = mean_ann.values, cov_ann.values
    bounds = _bounds(n, long_only, max_w, min_w)
    base = _sector_constraints(tickers, sector_caps or {})

    lo, hi = ret_range if ret_range is not None else (float(mu.min()), float(mu.max()))
    out = []
    for target in np.linspace(lo, hi, n_points):
        cons = [{"type": "eq", "fun": lambda w: w.sum() - 1.0},
                {"type": "eq", "fun": (lambda w, t=target: float(w @ mu) - t)}] + base
        w = _solve(lambda w: float(w @ sig @ w), n, bounds, cons)
        if w is None:
            continue
        ret, vol, _ = portfolio_perf(w, mu, sig)
        out.append({"ret": ret, "vol": vol, "weights": w})
    return out


def random_portfolios(mean_ann: pd.Series, cov_ann: pd.DataFrame, n: int = 3000,
                      rf: float = 0.045, seed: int = 0) -> pd.DataFrame:
    """Dirichlet-sampled long-only portfolios for the frontier scatter cloud."""
    rng = np.random.default_rng(seed)
    mu, sig = mean_ann.values, cov_ann.values
    k = len(mu)
    W = rng.dirichlet(np.ones(k), size=n)
    rets = W @ mu
    vols = np.sqrt(np.einsum("ij,jk,ik->i", W, sig, W))
    sharpe = np.where(vols > 0, (rets - rf) / vols, 0.0)
    return pd.DataFrame({"ret": rets, "vol": vols, "sharpe": sharpe})
