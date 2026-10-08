"""what-if: the optimizer portfolios had you followed them (no hindsight), switching costs, Monte Carlo, verdict.
Pure functions over synthetic prices — no network."""
import numpy as np
import pandas as pd
import pytest

from monitor import config
from monitor.portfolio import whatif as W

DAYS = pd.bdate_range("2023-01-02", "2026-06-30")


def prices(cols=("AAA.F", "BBB.F", "CCC.F"), start=None, seed=1):
    rng = np.random.default_rng(seed)
    data = {c: 50 * np.exp(np.cumsum(rng.normal(0.0004, 0.012, len(DAYS)))) for c in cols}
    px = pd.DataFrame(data, index=DAYS)
    if start:
        px.loc[px.index < start] = np.nan
    return px


def tx(date, ticker, action, eur, shares=1.0):
    return {"date": date, "ticker": ticker, "action": action, "shares": shares, "price": eur, "pps": eur / shares}


TXNS = [tx("2025-01-06", "AAA.F", "buy", 1000.0, 20.0), tx("2025-03-03", "BBB.F", "buy", 500.0, 10.0)]
SHARES = {"AAA.F": 1e9, "BBB.F": 2e9, "CCC.F": 5e8}


def test_month_starts_are_each_months_first_business_day():
    got = W.month_starts(pd.Timestamp("2025-01-06"), pd.Timestamp("2025-04-15"))
    assert [d.date().isoformat() for d in got] == ["2025-01-01", "2025-02-03", "2025-03-03", "2025-04-01"]


def test_weights_never_read_a_price_after_their_month():
    px = prices()
    months = W.month_starts(pd.Timestamp("2025-01-06"), pd.Timestamp("2026-06-30"))
    full = W.month_weights(px, SHARES, TXNS, months)
    cut = pd.Timestamp("2025-10-01")
    early = W.month_weights(px[px.index < cut], SHARES, TXNS, [m for m in months if m <= cut])
    for k in config.PORTFOLIOS:
        assert len(early[k]) == len([m for m in months if m <= cut])
        for (m1, w1), (m2, w2) in zip(early[k], full[k]):
            assert m1 == m2
            pd.testing.assert_series_equal(w1, w2)


def test_every_months_weights_sum_to_one_over_lines_with_a_year_of_prices():
    px = prices(start="2025-03-01")                                  # CCC… all start late: none has a year yet
    months = W.month_starts(pd.Timestamp("2025-06-02"), pd.Timestamp("2026-06-30"))
    got = W.month_weights(px, SHARES, TXNS, months)
    first = dict(got["MINVAR"])[months[0]]
    assert first.to_dict() == pytest.approx({t: 1 / 3 for t in px.columns})   # fewer than two with a year: 1/N
    for k in config.PORTFOLIOS:
        for _, w in got[k]:
            assert w.sum() == pytest.approx(1.0) and (w >= -1e-9).all()


def test_an_infeasible_month_keeps_the_previous_weights(monkeypatch):
    px = prices()
    months = W.month_starts(pd.Timestamp("2025-01-06"), pd.Timestamp("2025-03-31"))
    real, calls = W.R.portfolio_weights, []

    def flaky(*a, **k):
        calls.append(1)
        out = real(*a, **k)
        return out if len(calls) == 1 else {**out, "MINVAR": None}
    monkeypatch.setattr(W.R, "portfolio_weights", flaky)
    got = W.month_weights(px, SHARES, TXNS, months)
    w = [s for _, s in got["MINVAR"]]
    for later in w[1:]:
        pd.testing.assert_series_equal(later, w[0])
