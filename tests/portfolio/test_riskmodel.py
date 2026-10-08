"""riskmodel.build_model: known answers on synthetic data + parity with the retired HTML report."""
import pickle

import numpy as np
import pandas as pd
import pytest

from monitor.portfolio import riskmodel as R
from tests.conftest import FIXTURES


def _hist(vols, n=800, seed=1, start="2022-01-03", corr_with=None):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start, periods=n)
    out = {}
    for i, v in enumerate(vols):
        r = rng.normal(0, v, n)
        out[f"A{i}"] = 100 * np.exp(np.cumsum(r))
    return pd.DataFrame(out, index=idx)


def test_minvar_matches_the_analytic_minimum_variance_portfolio():
    h = _hist([0.010, 0.011, 0.012, 0.013], n=3000)               # uncorrelated → weights ∝ 1/σ²
    m = R.build_model({c: 100.0 for c in h.columns}, h, caps={}, spx=None)
    inv = np.linalg.solve(m.cov.values, np.ones(4))
    analytic = inv / inv.sum()                                    # unconstrained min-var: Σ⁻¹1 / 1ᵀΣ⁻¹1
    assert (analytic > 0).all() and analytic.max() < 0.35         # so the caps do not bind
    np.testing.assert_allclose(m.portfolios["MINVAR"].weights, analytic, atol=1e-4)
    assert pickle.loads(pickle.dumps(m)).universe == m.universe   # the store pickles parts
    assert m.risk_contrib.sum() == pytest.approx(1.0)
    assert m.hhi == pytest.approx(0.25) and m.eff_n == pytest.approx(4.0)
    assert np.allclose(np.diag(m.corr.values), 1.0)
    assert len(m.frontier) == 40 and 100 <= len(m.cloud) <= R.CLOUD_POINTS


def test_two_assets_make_capped_portfolios_infeasible():
    h = _hist([0.01, 0.02])
    m = R.build_model({"A0": 50.0, "A1": 50.0}, h, caps={}, spx=None)
    for key in ("MINVAR", "RP", "BLSHARPE", "BLSAME"):
        assert m.portfolios[key] is None, key                 # MAX_W=0.35 x 2 < 100%
    assert m.portfolios["HRP"] is not None                     # uncapped by design
    assert m.frontier == []                                    # no reachable capped portfolio


def test_needs_two_priced_positions():
    h = _hist([0.01])
    with pytest.raises(ValueError, match="at least two priced positions"):
        R.build_model({"A0": 100.0, "ZZZ": 50.0}, h, caps={}, spx=None)


def test_perf_reevaluates_any_weights():
    h = _hist([0.01, 0.01, 0.01, 0.01])
    m = R.build_model({c: 100.0 for c in h.columns}, h, caps={}, spx=None)
    p = m.perf([1.0, 0.0, 0.0, 0.0])
    assert p.vol == pytest.approx(float(np.sqrt(m.cov.values[0, 0])))


def test_beta_against_the_market():
    rng = np.random.default_rng(3)
    idx = pd.bdate_range("2024-01-01", periods=400)
    mkt_r = rng.normal(0, 0.01, 400)
    mkt = pd.Series(100 * np.exp(np.cumsum(mkt_r)), index=idx)
    h = pd.DataFrame({"X": 50 * np.exp(np.cumsum(2 * mkt_r)),
                      "Y": 80 * np.exp(np.cumsum(rng.normal(0, 0.01, 400))),
                      "Z": 70 * np.exp(np.cumsum(rng.normal(0, 0.01, 400)))}, index=idx)
    m = R.build_model({"X": 1.0, "Y": 1.0, "Z": 1.0}, h, caps={}, spx=mkt)
    assert m.beta["X"] == pytest.approx(2.0, abs=0.01)
    assert R.beta_of(h["X"], mkt) == pytest.approx(2.0, abs=0.01)
    assert R.beta_of(h["X"], None) is None


def test_portfolios_match_the_frozen_legacy_report(port_env):
    """The five portfolios RISK / OPT build (riskmodel.build_model over snapshot.risk_inputs) on the
    fixture portfolio equal what the retired HTML portfolio report computed — its numbers, frozen in
    tests/fixtures/golden/riskmodel_legacy.json before the report was deleted."""
    import json
    from monitor.portfolio import snapshot
    frozen = json.loads((FIXTURES / "golden" / "riskmodel_legacy.json").read_text(encoding="utf-8"))
    m = R.build_model(**snapshot.risk_inputs(snapshot.load_book(port_env)))
    assert m.universe == frozen["universe"]
    np.testing.assert_allclose(m.cur_w, frozen["cur_w"], atol=1e-12)
    np.testing.assert_allclose(m.mkt_w, frozen["mkt_w"], atol=1e-12)
    np.testing.assert_allclose(m.risk_contrib, frozen["cur_rc"], atol=1e-12)
    for key in ("MINVAR", "RP", "HRP", "BLSHARPE", "BLSAME"):
        if frozen[key] is None:
            assert m.portfolios[key] is None
        else:
            np.testing.assert_allclose(m.portfolios[key].weights, frozen[key], atol=1e-9)


def test_var_cvar_known_answer():
    """101 daily portfolio returns: six losses, 95 gains. With pandas' linear interpolation the
    5th percentile sits at position 0.05 x 100 = 5, exactly the 6th-worst day (-1%); CVaR is
    the mean of the six days at or below it: (-6-5-4-3-2-1)/6 = -3.5%."""
    from types import SimpleNamespace
    port = np.array([-0.06, -0.05, -0.04, -0.03, -0.02, -0.01] + [0.01] * 95)
    np.random.default_rng(3).shuffle(port)
    b = np.random.default_rng(4).normal(0, 0.01, len(port))
    win = pd.DataFrame({"A": 2 * port - b, "B": b}, index=pd.bdate_range("2025-01-01", periods=len(port)))
    var, cvar = R.var_cvar(SimpleNamespace(window_returns=win, cur_w=np.array([0.5, 0.5])))   # 0.5A+0.5B = port
    assert var == pytest.approx(-1.0) and cvar == pytest.approx(-3.5)
    assert R.var_cvar(SimpleNamespace(window_returns=win.iloc[:19], cur_w=np.array([0.5, 0.5]))) == (None, None)


def test_equal_weight_is_the_sixth_portfolio_and_ignores_the_cap(port_env):
    """EQUAL (1/N) is the baseline every optimizer must beat — never capped, never infeasible."""
    from monitor import config
    from monitor.portfolio import snapshot
    m = R.build_model(**snapshot.risk_inputs(snapshot.load_book(port_env)))
    assert config.PORTFOLIOS[-1] == "EQUAL" and R.LABELS["EQUAL"] == "Equal-Weight"
    n = len(m.universe)
    np.testing.assert_allclose(m.portfolios["EQUAL"].weights, np.full(n, 1 / n))


def test_one_weight_method_builds_opt_and_the_backtest(port_env):
    """portfolio_weights over the model's own estimate gives exactly the model's portfolios."""
    from monitor import config
    from monitor.portfolio import snapshot
    from monitor.portfolio.optimizer import annualize, to_returns
    inputs = snapshot.risk_inputs(snapshot.load_book(port_env))
    m = R.build_model(**inputs)
    rets = to_returns(inputs["history"][m.universe])
    window = rets.loc[rets.index >= rets.index[-1] - pd.Timedelta(days=config.LOOKBACK_DAYS)]
    mean_ann, cov = annualize(window)
    mkt_w, mean_bl = R.bl_mean(cov, inputs["caps"])
    np.testing.assert_allclose(mkt_w, m.mkt_w)
    w = R.portfolio_weights(mean_ann, cov, mean_bl, m.cur.vol)
    for k in config.PORTFOLIOS:
        assert (w[k] is None) == (m.portfolios[k] is None)
        if w[k] is not None:
            np.testing.assert_allclose(w[k], m.portfolios[k].weights, atol=1e-12)
