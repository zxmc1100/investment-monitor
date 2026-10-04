"""riskmodel.stress: own-history windows, beta-estimated windows, instant shocks, worst N-day."""
import numpy as np
import pandas as pd
import pytest

from monitor.portfolio import riskmodel as R

IDX = pd.bdate_range("2005-01-03", "2026-06-30")
RNG = np.random.default_rng(7)
NOISE = RNG.normal(0, 0.01, len(IDX))


def _step(before, after, cut="2007-10-09"):
    """Constant 'before' up to the cut date, constant 'after' later, with small noise on top."""
    pos = np.searchsorted(IDX, pd.Timestamp(cut), side="right")
    k = np.clip((np.arange(len(IDX)) - pos + 1) / 60.0, 0.0, 1.0)
    base = before * (after / before) ** k          # ramp over 60 bars: no one-day move looks like a glitch
    return pd.Series(base * np.exp(np.cumsum(NOISE * 0.1)), index=IDX)


def _model(long_h, spx):
    five = long_h[long_h.index >= long_h.index[-1] - pd.Timedelta(days=5 * 365)]
    return R.build_model({c: 100.0 for c in long_h.columns}, five, caps={}, spx=spx[five.index[0]:])


def _scn(rows, key):
    return next(s for s in rows if s.key == key)


def test_window_uses_own_history_when_it_covers_the_window():
    spx = _step(200.0, 100.0)
    h = pd.DataFrame({"A": _step(100.0, 50.0), "B": _step(10.0, 5.0),
                      "C": pd.Series(np.exp(np.cumsum(RNG.normal(0, 0.01, len(IDX)))), index=IDX)})
    m = _model(h, spx)
    gfc = _scn(R.stress(m, h, spx), "GFC")
    assert gfc.n_estimated == 0 and gfc.n_total == 3
    own = {c: float(h[c].loc[:"2009-03-09"].iloc[-1] / h[c].loc[:"2007-10-09"].iloc[-1] - 1) for c in h}
    assert gfc.port_ret == pytest.approx(sum(w * own[c] for c, w in zip(m.universe, m.cur_w)))


def test_window_estimates_with_beta_when_history_is_short():
    spx = _step(200.0, 100.0)
    late = pd.Series(np.nan, index=IDX)
    late[IDX >= "2015-01-02"] = 50 * np.exp(np.cumsum(2 * NOISE[IDX >= "2015-01-02"]))
    h = pd.DataFrame({"A": _step(100.0, 50.0), "L": late,
                      "C": pd.Series(np.exp(np.cumsum(RNG.normal(0, 0.01, len(IDX)))), index=IDX)})
    m = _model(h, spx)
    gfc = _scn(R.stress(m, h, spx), "GFC")
    assert gfc.n_estimated == 1
    spx_ret = float(spx.loc[:"2009-03-09"].iloc[-1] / spx.loc[:"2007-10-09"].iloc[-1] - 1)
    i = m.universe.index("L")
    contrib_l = m.cur_w[i] * m.beta["L"] * spx_ret
    others = sum(m.cur_w[j] * float(h[c].loc[:"2009-03-09"].iloc[-1] / h[c].loc[:"2007-10-09"].iloc[-1] - 1)
                 for j, c in enumerate(m.universe) if c != "L")
    assert gfc.port_ret == pytest.approx(others + contrib_l)


def test_glitched_history_in_window_is_estimated_with_beta():
    spx = _step(200.0, 100.0)
    g = _step(100.0, 50.0).copy()
    g[g.index >= "2008-07-16"] *= 7.0                      # +600% unit/ratio glitch inside the GFC window
    h = pd.DataFrame({"A": _step(100.0, 50.0), "G": g,
                      "C": pd.Series(np.exp(np.cumsum(RNG.normal(0, 0.01, len(IDX)))), index=IDX)})
    m = _model(h, spx)
    gfc = _scn(R.stress(m, h, spx), "GFC")
    assert gfc.n_estimated == 1
    spx_ret = float(spx.loc[:"2009-03-09"].iloc[-1] / spx.loc[:"2007-10-09"].iloc[-1] - 1)
    i = m.universe.index("G")
    others = sum(m.cur_w[j] * float(h[c].loc[:"2009-03-09"].iloc[-1] / h[c].loc[:"2007-10-09"].iloc[-1] - 1)
                 for j, c in enumerate(m.universe) if c != "G")
    assert gfc.port_ret == pytest.approx(others + m.cur_w[i] * m.beta["G"] * spx_ret)


def test_scenario_without_spx_or_coverage_is_none():
    late = pd.Series(np.nan, index=IDX)
    late[IDX >= "2021-01-04"] = 50 * np.exp(np.cumsum(NOISE[IDX >= "2021-01-04"]))
    h = pd.DataFrame({"L": late, "M": late * 1.5 + 1})
    m = R.build_model({"L": 1.0, "M": 1.0}, h[h.index >= "2021-07-01"], caps={}, spx=None)
    rows = R.stress(m, h, None)
    assert _scn(rows, "GFC").port_ret is None
    assert _scn(rows, "W1D").port_ret is not None              # history-only scenarios still work


def test_instant_shocks_are_beta_weighted():
    spx = _step(200.0, 100.0)
    h = pd.DataFrame({"A": _step(100.0, 50.0), "B": _step(10.0, 5.0),
                      "C": pd.Series(np.exp(np.cumsum(RNG.normal(0, 0.01, len(IDX)))), index=IDX)})
    m = _model(h, spx)
    s10 = _scn(R.stress(m, h, spx), "SPX10")
    assert s10.port_ret == pytest.approx(sum(w * m.beta[c] * -0.10 for c, w in zip(m.universe, m.cur_w)))
    assert s10.n_estimated == s10.n_total


def test_worst_one_day_finds_the_crash():
    idx = pd.bdate_range("2022-01-03", periods=600)
    r = np.random.default_rng(9).normal(0, 0.005, (600, 3))
    r[300, :] = -0.10                                          # one crash day for everyone
    h = pd.DataFrame(100 * np.exp(np.cumsum(r, axis=0)), index=idx, columns=["A", "B", "C"])
    m = R.build_model({c: 1.0 for c in h}, h, caps={}, spx=None)
    w1 = _scn(R.stress(m, h, None), "W1D")
    assert w1.port_ret == pytest.approx(float(np.exp(-0.10) - 1), abs=0.002)   # not -0.10: a log step


def test_no_market_data_means_no_assumed_beta():
    late = pd.Series(np.nan, index=IDX)
    late[IDX >= "2015-01-02"] = 50 * np.exp(np.cumsum(2 * NOISE[IDX >= "2015-01-02"]))
    h = pd.DataFrame({"A": _step(100.0, 50.0), "L": late,
                      "C": pd.Series(np.exp(np.cumsum(RNG.normal(0, 0.01, len(IDX)))), index=IDX)})
    five = h[h.index >= h.index[-1] - pd.Timedelta(days=5 * 365)]
    m = R.build_model({c: 100.0 for c in h.columns}, five, caps={}, spx=None)
    assert m.beta == {}
    rows = R.stress(m, h, None)
    assert _scn(rows, "SPX10").port_ret is None and _scn(rows, "SPX20").port_ret is None
    assert _scn(rows, "GFC").port_ret is None
    assert _scn(rows, "W1D").port_ret is not None
