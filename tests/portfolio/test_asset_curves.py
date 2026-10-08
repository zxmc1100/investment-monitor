"""Per-asset EUR value curves emitted by build_roi_timeseries.

No network: yfinance is monkeypatched with flat synthetic prices, so every
position value is exactly shares x a known constant.
"""

import pandas as pd
import pytest

import monitor.portfolio.analytics as pa

PRICES = {"AAA.F": 100.0, "BBB.F": 50.0}


def _fake_download(tickers, start=None, auto_adjust=True, progress=False, **kw):
    """Stand in for yf.download: flat prices for AAA.F/BBB.F, nothing else.

    Returns the MultiIndex ('Close', ticker) column layout real yfinance uses
    for multi-ticker downloads. Unknown tickers (the benchmarks, EURUSD=X)
    produce an empty frame, so no benchmark series are built.
    """
    tickers = [tickers] if isinstance(tickers, str) else list(tickers)
    known = [t for t in tickers if t in PRICES]
    if not known:
        return pd.DataFrame()
    idx = pd.bdate_range(start="2026-01-01", end=pd.Timestamp.today().normalize())
    cols = pd.MultiIndex.from_product([["Close"], known])
    data = {("Close", t): [PRICES[t]] * len(idx) for t in known}
    return pd.DataFrame(data, index=idx, columns=cols)


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    monkeypatch.setattr(pa.yf, "download", _fake_download)


def _txn(date, ticker, action, shares, pps):
    return {"date": date, "ticker": ticker, "action": action,
            "shares": shares, "price": shares * pps, "pps": pps}


# Two tickers in every fixture: yfinance's single-ticker download has a
# different column shape, and the production path is always multi-ticker.
BUYS = [
    _txn("2026-02-02", "AAA.F", "buy", 10.0, 100.0),   # 1000 EUR
    _txn("2026-03-02", "BBB.F", "buy", 20.0, 50.0),    # 1000 EUR
]


def test_asset_line_starts_on_its_buy_date():
    _roi, _bms, av = pa.build_roi_timeseries(BUYS)
    bbb = av["BBB.F"]
    assert bbb.loc[:"2026-02-27"].dropna().empty       # nothing before the buy
    assert bbb.loc["2026-03-02"] == pytest.approx(1000.0)
    assert av["AAA.F"].loc["2026-02-02"] == pytest.approx(1000.0)


def test_sold_asset_line_ends_at_the_sell():
    txns = BUYS + [_txn("2026-04-01", "BBB.F", "sell", 20.0, 50.0)]
    _roi, _bms, av = pa.build_roi_timeseries(txns)
    assert av["BBB.F"].loc["2026-03-02"] == pytest.approx(1000.0)
    assert av["BBB.F"].loc["2026-04-01":].dropna().empty
    # proceeds move into the cash trace, which is NaN until the first sell
    cash = av["__cash__"]
    assert cash.loc[:"2026-03-31"].dropna().empty
    assert cash.loc["2026-04-01"] == pytest.approx(1000.0)


def test_no_cash_trace_when_there_are_no_sells():
    _roi, _bms, av = pa.build_roi_timeseries(BUYS)
    assert "__cash__" not in av


def test_total_reconciles_to_assets_plus_cash():
    txns = BUYS + [_txn("2026-04-01", "BBB.F", "sell", 20.0, 50.0)]
    _roi, _bms, av = pa.build_roi_timeseries(txns)
    total = av["__total__"]
    per_asset = pd.DataFrame({k: v for k, v in av.items() if not k.startswith("__")})
    recon = per_asset.sum(axis=1, skipna=True) + av["__cash__"].fillna(0.0)
    pd.testing.assert_series_equal(recon, total, check_names=False, atol=1e-6)


def test_weekend_buy_lands_on_next_business_day():
    # 2026-02-01 is a Sunday (Tradegate trades are dated like this).
    txns = [_txn("2026-02-01", "AAA.F", "buy", 10.0, 100.0),
            _txn("2026-03-02", "BBB.F", "buy", 20.0, 50.0)]
    _roi, _bms, av = pa.build_roi_timeseries(txns)
    aaa = av["AAA.F"].dropna()
    assert aaa.index[0] == pd.Timestamp("2026-02-02")   # the Monday
    assert aaa.iloc[0] == pytest.approx(1000.0)


def test_roi_series_is_unchanged_by_the_new_return_value():
    roi, bms, av = pa.build_roi_timeseries(BUYS)
    assert isinstance(roi, pd.Series) and not roi.empty
    assert bms == {}                       # fake download yields no benchmarks
    assert set(av) >= {"AAA.F", "BBB.F", "__total__"}
    # flat prices => ROI is exactly 0% throughout
    assert roi.abs().max() == pytest.approx(0.0)


def test_per_asset_roi_uses_the_portfolio_formula_not_a_rebased_curve():
    """Topping up a position must not read as a gain. AAA is bought twice at the same
    price with a flat price series, so its EUR line doubles while its return stays 0%.
    A naive value/first-value rebase would report +100%.
    """
    txns = [_txn("2026-02-02", "AAA.F", "buy", 10.0, 100.0),
            _txn("2026-03-02", "AAA.F", "buy", 10.0, 100.0),
            _txn("2026-03-02", "BBB.F", "buy", 20.0, 50.0)]
    _roi, _bms, av = pa.build_roi_timeseries(txns)
    assert av["AAA.F"].loc["2026-02-02"] == pytest.approx(1000.0)
    assert av["AAA.F"].loc["2026-03-02"] == pytest.approx(2000.0)   # EUR doubles
    aaa_roi = av["__roi__"]["AAA.F"]
    assert aaa_roi.loc["2026-02-02"] == pytest.approx(0.0)
    assert aaa_roi.loc["2026-03-02"] == pytest.approx(0.0)          # return does not


def test_roi_series_share_the_euro_series_gaps():
    """The % view swaps y-arrays on the same traces, so the two must align exactly —
    same index, same NaN gaps, or a sold position's line would outlive its own data.
    """
    txns = BUYS + [_txn("2026-04-01", "BBB.F", "sell", 20.0, 50.0)]
    _roi, _bms, av = pa.build_roi_timeseries(txns)
    for tk in ("AAA.F", "BBB.F"):
        assert av["__roi__"][tk].index.equals(av[tk].index)
        assert av["__roi__"][tk].isna().equals(av[tk].isna())
    assert av["__roi__"]["__total__"].index.equals(av["__total__"].index)


def test_return_arity_is_pinned():
    """Callers unpack this into 3 names, some inside a broad `except Exception`,
    where an arity mismatch would silently drop a result instead of raising.
    Pin the contract here, on the cheapest possible input, so a regression
    fails loudly in this fast/no-network test.
    """
    roi, bms, av = pa.build_roi_timeseries([])   # no buys -> returns before any download
    assert isinstance(roi, pd.Series) and roi.empty
    assert bms == {}
    assert av == {}


# ── bonus shares (Trade Republic Saveback): shares received, not money spent ──────────────────

def test_bonus_raises_holdings_value_but_not_money_invested():
    """A bonus lot adds shares (so value) but no euros to the ROI denominator: with flat prices
    the bonus' value is pure gain, where an equal buy would leave ROI at exactly 0 %."""
    bonus = BUYS + [{**_txn("2026-04-01", "AAA.F", "bonus", 0.1, 100.0)}]
    bought = BUYS + [_txn("2026-04-01", "AAA.F", "buy", 0.1, 100.0)]
    roi_b, _, av_b = pa.build_roi_timeseries(bonus)
    roi_p, _, av_p = pa.build_roi_timeseries(bought)
    on = pd.Timestamp("2026-04-01")
    assert av_b["AAA.F"].loc[on] == pytest.approx(av_p["AAA.F"].loc[on]) == pytest.approx(1010.0)
    assert roi_p.loc[on] == pytest.approx(0.0)
    assert roi_b.loc[on] == pytest.approx(10.0 / 2000.0 * 100)
    assert av_b["__roi__"]["AAA.F"].loc[on] == pytest.approx(1.0)            # (1010 / 1000) − 1


def test_bonus_on_a_ticker_never_bought_has_no_roi_line_and_no_crash():
    txns = BUYS + [_txn("2026-04-01", "CCC.F", "bonus", 1.0, 10.0)]          # no history: carried at pps
    _roi, _bms, av = pa.build_roi_timeseries(txns)
    assert av["CCC.F"].loc["2026-04-01"] == pytest.approx(10.0)
    assert av["__roi__"].get("CCC.F") is None or av["__roi__"]["CCC.F"].dropna().empty


def test_a_session_yahoo_skipped_is_valued_at_its_rebuilt_close(monkeypatch):
    """Yahoo's daily answer once had no bar for one session of every European line: the day before was
    carried flat through the ROI chart. The bar rebuilt by yahoo.fill_missing_sessions values that day."""
    import time_machine
    from monitor.data import yahoo as Y
    monkeypatch.setattr(Y, "_ASKED", {})
    skipped = pd.Timestamp("2026-10-07")

    def dl(tickers, start=None, auto_adjust=True, progress=False, repair=False, **kw):
        tickers = [tickers] if isinstance(tickers, str) else list(tickers)
        known = [t for t in tickers if t in PRICES]
        if not known:
            return pd.DataFrame()
        idx = pd.bdate_range("2026-09-01", "2026-10-08")
        idx = idx if repair else idx.drop(skipped)
        data = {("Close", t): [PRICES[t] * (1.1 if d == skipped else 1.0) for d in idx] for t in known}
        return pd.DataFrame(data, index=idx, columns=pd.MultiIndex.from_product([["Close"], known]))
    monkeypatch.setattr(pa.yf, "download", dl)
    buys = [_txn("2026-09-01", "AAA.F", "buy", 10.0, 100.0), _txn("2026-09-01", "BBB.F", "buy", 20.0, 50.0)]
    with time_machine.travel("2026-10-08 10:00:00+02:00", tick=False):
        roi, _bms, av = pa.build_roi_timeseries(buys)
    assert av["AAA.F"].loc["2026-10-07"] == pytest.approx(1100.0)
    assert roi.loc["2026-10-07"] == pytest.approx(10.0) and roi.loc["2026-10-06"] == pytest.approx(0.0)
