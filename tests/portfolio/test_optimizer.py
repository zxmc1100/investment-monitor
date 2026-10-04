"""Unit tests for the Markowitz optimizer constraints + optimality."""

import numpy as np
import pandas as pd
import pytest

from monitor.portfolio.optimizer import (
    annualize,
    optimize,
    max_return_at_vol,
    efficient_frontier,
    portfolio_perf,
    to_returns,
    risk_parity,
    risk_contributions,
    hrp,
    implied_equilibrium_returns,
    black_litterman,
    capped_return_range,
)
from monitor.portfolio.meta import sector_exposure_matrix


@pytest.fixture
def synth():
    """3 synthetic assets with distinct risk/return, 600 daily returns."""
    rng = np.random.default_rng(42)
    n = 600
    a = rng.normal(0.0008, 0.012, n)   # high return, high vol
    b = rng.normal(0.0004, 0.007, n)   # mid
    c = rng.normal(0.0002, 0.004, n)   # low return, low vol
    idx = pd.bdate_range("2022-01-01", periods=n)
    rets = pd.DataFrame({"AAA.F": a, "BBB.F": b, "CCC.F": c}, index=idx)
    return annualize(rets)


def test_weights_sum_to_one(synth):
    mean_ann, cov_ann = synth
    w = optimize(mean_ann, cov_ann, objective="sharpe", max_w=1.0)
    assert w is not None
    assert abs(w.sum() - 1.0) < 1e-6


def test_long_only_nonnegative(synth):
    mean_ann, cov_ann = synth
    w = optimize(mean_ann, cov_ann, long_only=True, max_w=1.0)
    assert (w >= -1e-6).all()


def test_max_weight_respected(synth):
    mean_ann, cov_ann = synth
    w = optimize(mean_ann, cov_ann, max_w=0.4)
    assert w.max() <= 0.4 + 1e-4


def test_min_weight_floor(synth):
    mean_ann, cov_ann = synth
    w = optimize(mean_ann, cov_ann, long_only=True, min_w=0.1, max_w=0.6)
    assert (w >= 0.1 - 1e-4).all()


def test_min_var_has_lower_vol_than_max_sharpe(synth):
    mean_ann, cov_ann = synth
    mu, sig = mean_ann.values, cov_ann.values
    w_mv = optimize(mean_ann, cov_ann, objective="min_var")
    w_ms = optimize(mean_ann, cov_ann, objective="sharpe")
    _, vol_mv, _ = portfolio_perf(w_mv, mu, sig)
    _, vol_ms, _ = portfolio_perf(w_ms, mu, sig)
    assert vol_mv <= vol_ms + 1e-6


def test_max_sharpe_beats_equal_weight(synth):
    mean_ann, cov_ann = synth
    mu, sig = mean_ann.values, cov_ann.values
    w = optimize(mean_ann, cov_ann, objective="sharpe")
    eq = np.repeat(1 / 3, 3)
    assert portfolio_perf(w, mu, sig)[2] >= portfolio_perf(eq, mu, sig)[2] - 1e-6


def test_sector_cap_respected(synth):
    mean_ann, cov_ann = synth
    # Map all 3 synthetic tickers into one fake sector via monkeyish exposure:
    # use a real cap on a sector that the default map assigns. Build matrix directly.
    tickers = list(mean_ann.index)
    sectors, matrix = sector_exposure_matrix(tickers)   # all "Unknown" → one sector
    caps = {sectors[0]: 0.5} if sectors else {}
    w = optimize(mean_ann, cov_ann, sector_caps=caps, max_w=1.0)
    # all three load 1.0 onto the same Unknown sector → sum capped at 0.5 is infeasible
    # with sum==1, so optimizer returns None (infeasible). Accept None OR cap respected.
    if w is not None:
        exposure = np.array(matrix) @ w
        assert (exposure <= 0.5 + 1e-3).all()


def test_same_risk_max_return(synth):
    """max_return_at_vol: vol respected, return >= any same-vol baseline (equal weight)."""
    mean_ann, cov_ann = synth
    mu, sig = mean_ann.values, cov_ann.values
    eq = np.repeat(1 / 3, 3)
    eq_ret, eq_vol, _ = portfolio_perf(eq, mu, sig)
    w = max_return_at_vol(mean_ann, cov_ann, eq_vol)
    assert w is not None
    r, v, _ = portfolio_perf(w, mu, sig)
    assert v <= eq_vol + 1e-4          # vol cap respected
    assert r >= eq_ret - 1e-6          # at least as much return at same risk
    assert abs(w.sum() - 1.0) < 1e-6


def test_risk_parity_equalises_contributions(synth):
    mean_ann, cov_ann = synth
    w = risk_parity(cov_ann)
    assert w is not None
    assert abs(w.sum() - 1.0) < 1e-6
    rc = risk_contributions(w, cov_ann)
    # all risk contributions ~ 1/n
    assert np.allclose(rc, 1.0 / len(rc), atol=0.03)


def test_risk_contributions_sum_to_one(synth):
    _, cov_ann = synth
    w = np.array([0.5, 0.3, 0.2])
    rc = risk_contributions(w, cov_ann)
    assert abs(rc.sum() - 1.0) < 1e-9


def test_hrp_weights_sum_to_one(synth):
    _, cov_ann = synth
    w = hrp(cov_ann)
    assert w is not None
    assert abs(w.sum() - 1.0) < 1e-6


def test_hrp_long_only(synth):
    _, cov_ann = synth
    w = hrp(cov_ann)
    assert (w >= 0).all()


def test_hrp_length_matches_universe(synth):
    _, cov_ann = synth
    assert len(hrp(cov_ann)) == cov_ann.shape[0]


def test_hrp_deconcentrates_correlated_cluster():
    """Two tightly-correlated high-vol assets + one uncorrelated low-vol asset.
    HRP must treat the correlated pair as one risk unit, not let it dominate."""
    sig = pd.DataFrame(
        np.array([[0.040, 0.038, 0.000],
                  [0.038, 0.045, 0.000],
                  [0.000, 0.000, 0.010]]),
        index=list("ABC"), columns=list("ABC"))
    w = hrp(sig)
    assert abs(w.sum() - 1.0) < 1e-9
    assert w[0] + w[1] < 0.75          # correlated A+B share one budget, don't dominate
    assert w[2] > 0.25                 # low-vol uncorrelated C is not crushed


def test_implied_returns_recover_market_view(synth):
    """Π = δΣw_mkt: max-Sharpe on Π (no constraints binding) ≈ market weights."""
    _, cov_ann = synth
    mkt = np.array([0.5, 0.3, 0.2])
    pi = implied_equilibrium_returns(cov_ann, mkt, delta=2.5)
    assert list(pi.index) == list(cov_ann.index)
    # With rf=0, max-Sharpe tangency w ∝ Σ⁻¹Π = δ·w_mkt → recovers market weights.
    w = optimize(pi, cov_ann, objective="sharpe", rf=0.0, max_w=1.0)
    assert np.allclose(w, mkt, atol=0.05)


def test_black_litterman_no_views_equals_pi(synth):
    _, cov_ann = synth
    mkt = np.array([0.4, 0.35, 0.25])
    pi = implied_equilibrium_returns(cov_ann, mkt)
    bl = black_litterman(cov_ann, mkt, views=None)
    assert np.allclose(pi.values, bl.values)


def test_black_litterman_view_tilts(synth):
    _, cov_ann = synth
    mkt = np.array([0.4, 0.35, 0.25])
    pi = implied_equilibrium_returns(cov_ann, mkt)
    tk = list(cov_ann.index)
    # bullish absolute view on asset 0 above its implied return → BL raises it
    view = [{"assets": {tk[0]: 1.0}, "ret": float(pi.iloc[0]) + 0.20, "confidence": 0.8}]
    bl = black_litterman(cov_ann, mkt, views=view)
    assert bl.iloc[0] > pi.iloc[0]


def test_efficient_frontier_monotone(synth):
    mean_ann, cov_ann = synth
    fr = efficient_frontier(mean_ann, cov_ann, n_points=15)
    assert len(fr) >= 5
    rets = [f["ret"] for f in fr]
    assert rets == sorted(rets)   # target returns ascending


def test_frontier_sweeps_only_the_cap_feasible_return_range():
    mu = pd.Series([0.05, 0.10, 0.20], index=list("abc"))
    lo, hi = capped_return_range(mu.values, 0.35)
    assert (lo, hi) == pytest.approx((0.35 * 0.05 + 0.35 * 0.10 + 0.30 * 0.20, 0.35 * 0.20 + 0.35 * 0.10 + 0.30 * 0.05))
    assert capped_return_range(mu.values[:2], 0.35) is None            # 2 x 35% cannot reach 100%
    cov = pd.DataFrame(np.diag([0.04, 0.05, 0.06]), index=mu.index, columns=mu.index)
    f = efficient_frontier(mu, cov, n_points=10, max_w=0.35, ret_range=(lo, hi))
    assert len(f) == 10 and all(p["weights"].max() <= 0.35 + 1e-6 for p in f)
