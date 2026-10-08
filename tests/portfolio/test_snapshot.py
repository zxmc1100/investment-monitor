"""monitor.portfolio.snapshot: the two refresh tiers behind PORT / OPT / RISK / SEC."""
import logging
from datetime import date
from pathlib import Path

import numpy as np
import pytest
import time_machine

from monitor.portfolio import riskmodel as R
from monitor.portfolio import snapshot as S
from tests import fakes_yf

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "portfolio_small.csv"


@pytest.fixture
def env(tmp_path, monkeypatch):
    fakes_yf.install(monkeypatch)
    with time_machine.travel("2026-06-30 14:00:00+00:00", tick=False):
        yield tmp_path / "buffer"


def test_accounting_reconciles():
    book = S.load_book(FIX)
    totals = {"realized_pnl": 140.0, "current_value": 3000.0}
    a = S.accounting(book["transactions"], totals, today=date(2026, 6, 30))
    assert a["gross_deposits"] == pytest.approx(3990.0)
    assert a["cash_returned"] == pytest.approx(1440.0)
    assert a["net_invested"] == pytest.approx(a["gross_deposits"] - a["cash_returned"])
    assert a["total_pnl"] == pytest.approx(a["realized"] + a["unrealized"])
    assert a["simple_roi"] == pytest.approx(a["total_pnl"] / a["gross_deposits"] * 100)


def test_quote_tier_day_pnl_matches_positions(env):
    book = S.load_book(FIX)
    q = S.quote_tier(book, force=True, buffer_dir=env)
    expect = sum(p["shares"] * (q["quotes"][p["ticker"]]["price"] - q["quotes"][p["ticker"]]["prev_close"])
                 for p in q["positions"])
    assert q["day_pnl"] == pytest.approx(expect)
    value = sum(p["position_value"] for p in q["positions"])
    assert q["day_pct"] == pytest.approx(expect / (value - expect) * 100)
    assert {p["ticker"] for p in q["positions"]} == {"AAA.F", "BBB.F", "DDD.F"}   # CCC.F exited
    assert q["missing"] == [] and q["stale"] == {}


def test_quote_tier_reports_never_quoted_ticker(env):
    book = S.load_book(FIX)
    q = S.quote_tier(book, force=True, buffer_dir=env,
                     _fetch=lambda ts: {t: None if t == "DDD.F" else
                                        {"price": 10.0, "prev_close": 9.0, "date": "2026-06-30"} for t in ts})
    assert q["missing"] == ["DDD.F"]


def test_daily_tier_total_reconciles_with_roi(env):
    book = S.load_book(FIX)
    d = S.daily_tier(book, buffer_dir=env)
    av, roi = d["asset_values"], d["roi_series"]
    parts = [s for k, s in av.items() if not k.startswith("__")]
    if "__cash__" in av:
        parts.append(av["__cash__"])
    total = sum(p.fillna(0.0) for p in parts)
    assert (total - av["__total__"].fillna(0.0)).abs().max() < 1e-6
    # Sunday 2025-03-02 buy of 5 AAA.F lands on Monday via the `<=` pointer, never dropped
    px = fakes_yf.series("AAA.F")
    assert av["AAA.F"].loc["2025-02-28"] == pytest.approx(10 * px.loc["2025-02-28"], abs=0.01)
    assert av["AAA.F"].loc["2025-03-03"] == pytest.approx(15 * px.loc["2025-03-03"], abs=0.01)
    assert d["metrics"]["volatility"] > 0
    assert set(d["history"].columns) >= {"AAA.F", "BBB.F", "DDD.F"}


@pytest.mark.parametrize("bar,stale", [("2026-10-02", False), ("2026-10-01", False), ("2026-09-30", True)])
def test_quote_older_than_two_business_days_is_stale(tmp_path, bar, stale):
    book = S.load_book(FIX)
    fetch = lambda ts: {t: {"price": 10.0, "prev_close": 9.0, "date": bar} for t in ts}
    with time_machine.travel("2026-10-05 12:00:00+00:00", tick=False):          # a Monday
        q = S.quote_tier(book, force=True, buffer_dir=tmp_path / "buf", _fetch=fetch)
    assert ("AAA.F" in q["stale"]) is stale
    if stale:
        assert q["stale"]["AAA.F"] == bar


def test_risk_inputs_are_buffered_model_kwargs(env):
    book = S.load_book(FIX)
    S.quote_tier(book, force=True, buffer_dir=env)             # warm the quote buffer
    ins = S.risk_inputs(book, buffer_dir=env)
    assert set(ins) == {"values", "history", "caps", "spx"}
    assert set(ins["values"]) == {"AAA.F", "BBB.F", "DDD.F"}            # CCC.F is closed
    assert set(ins["history"].columns) >= set(ins["values"]) and ins["spx"] is not None


def test_long_history_returns_max_period_and_spx(env):
    hist, spx = S.long_history(["AAA.F", "BBB.F"], buffer_dir=env)
    assert set(hist.columns) == {"AAA.F", "BBB.F"} and spx is not None and len(spx) > 100


def test_long_history_spx_is_converted_to_eur(env):
    _, spx = S.long_history(["AAA.F"], buffer_dir=env)
    g, fx = fakes_yf.series("^GSPC"), fakes_yf.series("EURUSD=X")
    expect = (g / fx.reindex(fx.index.union(g.index)).ffill().reindex(g.index)).dropna()
    assert len(spx) > 100
    np.testing.assert_allclose(spx.values, expect.reindex(spx.index).values, rtol=1e-3)


def test_long_history_spx_is_none_without_the_fx_rate(env, monkeypatch, caplog):
    real = S.cached_ohlc

    def flaky(ticker, *a, **k):
        if ticker == "EURUSD=X":
            raise RuntimeError("no fx")
        return real(ticker, *a, **k)

    monkeypatch.setattr(S, "cached_ohlc", flaky)
    with caplog.at_level(logging.WARNING, logger=S.__name__):
        hist, spx = S.long_history(["AAA.F"], buffer_dir=env)
    assert spx is None and "AAA.F" in hist.columns
    assert "no fx" in caplog.text                                   # logged, never silent


def test_long_history_fetch_failure_degrades_to_beta_estimates(env, monkeypatch, caplog):
    real = S.cached_price_history

    def flaky(tickers, period="5y", **k):
        if period == "max":
            raise RuntimeError("yahoo down")
        return real(tickers, period=period, **k)

    monkeypatch.setattr(S, "cached_price_history", flaky)
    book = S.load_book(FIX)
    S.quote_tier(book, force=True, buffer_dir=env)
    model = R.build_model(**S.risk_inputs(book, buffer_dir=env))
    with caplog.at_level(logging.WARNING, logger=S.__name__):
        hist, spx = S.long_history(model.universe, buffer_dir=env)
    assert hist.empty and spx is not None and "yahoo down" in caplog.text
    rows = R.stress(model, hist, spx)
    assert len(rows) == 8
    assert all(s.n_estimated == s.n_total for s in rows if s.key in ("GFC", "COVID", "RATES22"))


def test_load_book_reads_interest_next_to_the_csv(tmp_path):
    csv = tmp_path / "portfolio.csv"
    csv.write_text(FIX.read_text(encoding="utf-8"), encoding="utf-8")
    assert S.load_book(csv)["interest"] == []                                  # no file, no interest
    (tmp_path / "interest.csv").write_text("Date,Amount\n2026-02-01,4.10\n2026-01-01,3.95\n", encoding="utf-8")
    assert S.load_book(csv)["interest"] == [{"date": "2026-01-01", "eur": 3.95}, {"date": "2026-02-01", "eur": 4.10}]


def test_quote_tier_reports_interest_outside_roi(env, tmp_path):
    csv = tmp_path / "portfolio.csv"
    csv.write_text(FIX.read_text(encoding="utf-8"), encoding="utf-8")
    a0 = S.quote_tier(S.load_book(csv), force=True, buffer_dir=env)["acct"]
    (tmp_path / "interest.csv").write_text("Date,Amount\n2026-01-01,3.95\n2026-02-01,4.10\n", encoding="utf-8")
    a = S.quote_tier(S.load_book(csv), buffer_dir=env)["acct"]
    assert a0["interest"] == 0.0 and a["interest"] == pytest.approx(8.05)
    assert a["total_pnl"] == pytest.approx(a0["total_pnl"]) and a["simple_roi"] == pytest.approx(a0["simple_roi"])


def test_daily_tier_adds_holdings_value_and_keeps_ytd(env):
    book = S.load_book(FIX)
    d = S.daily_tier(book, buffer_dir=env)
    assert {"roi_series", "bm_series", "asset_values", "metrics", "history", "hold"} <= set(d)
    av = d["asset_values"]
    want = (av["__total__"] - av["__cash__"].fillna(0.0)).fillna(0.0)
    assert (d["hold"] - want).abs().max() < 1e-6 and not d["hold"].isna().any()


def _history_without(monkeypatch, *gone):
    """yfinance.download that never prices `gone` (and no sleeping on the analytics retries)."""
    import yfinance
    from monitor.portfolio import analytics
    real = yfinance.download
    monkeypatch.setattr(analytics.time, "sleep", lambda s: None)

    def dl(tickers, **kw):
        keep = [t for t in ([tickers] if isinstance(tickers, str) else tickers) if t not in gone]
        return real(keep, **kw) if keep else __import__("pandas").DataFrame()
    monkeypatch.setattr(yfinance, "download", dl)


def test_daily_tier_carries_lines_the_quote_buffer_never_priced_at_cost(env, monkeypatch):
    book = S.load_book(FIX)
    S.quote_tier(book, force=True, buffer_dir=env,           # two held lines Yahoo has never quoted
                 _fetch=lambda ts: {t: None if t in ("BBB.F", "DDD.F") else
                                    {"price": 10.0, "prev_close": 9.0, "date": "2026-06-30"} for t in ts})
    _history_without(monkeypatch, "BBB.F", "DDD.F")
    d = S.daily_tier(book, buffer_dir=env)
    assert len(d["roi_series"])


def test_daily_tier_fails_when_a_quoted_line_has_no_history(env, monkeypatch):
    from monitor.portfolio.analytics import PriceHistoryError
    book = S.load_book(FIX)
    S.quote_tier(book, force=True, buffer_dir=env)           # every held line quoted
    _history_without(monkeypatch, "BBB.F")                   # a throttle that drops one line
    with pytest.raises(PriceHistoryError, match="BBB.F"):
        S.daily_tier(book, buffer_dir=env)


def test_a_tax_free_allowance_taxes_each_estimate_at_its_homes_withholding(monkeypatch, tmp_path):
    """settings tax_free_allowance → combine gets the home withholding (yours over the built-in table); without
    it, none: every estimate keeps the flat dividend tax."""
    from monitor import config
    from monitor.portfolio import snapshot
    seen = []
    monkeypatch.setattr(snapshot, "combine", lambda *a, **k: seen.append(k) or [])
    monkeypatch.setattr(snapshot, "cached_dividends", lambda *a, **k: {})
    book = {"transactions": [{"date": "2026-01-05", "ticker": "AAA.F", "action": "buy", "shares": 1.0,
                              "price": 10.0, "pps": 10.0}], "holdings": {}}
    snapshot.dividend_records(book, buffer_dir=tmp_path)
    monkeypatch.setattr(config, "TAX_FREE_ALLOWANCE", True)
    monkeypatch.setattr(config, "WITHHOLDING", {"Taiwan": 0.2})
    snapshot.dividend_records(book, buffer_dir=tmp_path)
    assert seen[0]["withholding"] is None and seen[0]["tax"] == config.DIVIDEND_TAX
    assert seen[1]["withholding"]["Taiwan"] == 0.2 and seen[1]["withholding"]["United States"] == 0.15
