"""A benchmark's ROI line marked to a live price: build_roi_timeseries records each benchmark's state at
the last close before today (asset_values["__bmlast__"]), and bench_live takes it one step to a live
price — the walk's own day step, so marked at today's close it reproduces the walk's point exactly.
No network: yfinance is monkeypatched with fixed synthetic prices."""
import numpy as np
import pandas as pd
import pytest
import time_machine

import monitor.portfolio.analytics as pa

DAYS = pd.bdate_range("2026-06-01", "2026-06-30")
PRICES = {"AAA.F": 100.0, "BBB.F": 50.0, "CSPX.AS": 500.0, "BND": 70.0, "EURUSD=X": 1.10}


def _line(t: str) -> list[float]:
    """A deterministic wiggle per line, so every day moves."""
    rng = np.random.default_rng(sum(map(ord, t)))
    return list(PRICES[t] * np.exp(np.cumsum(rng.normal(0, 0.01, len(DAYS)))))


def _download(tickers, start=None, auto_adjust=True, progress=False, **kw):
    tickers = [tickers] if isinstance(tickers, str) else list(tickers)
    known = [t for t in tickers if t in PRICES]
    if not known:
        return pd.DataFrame()
    data = {("Close", t): _line(t) for t in known}
    return pd.DataFrame(data, index=DAYS, columns=pd.MultiIndex.from_product([["Close"], known]))


def _txn(date, ticker, shares, pps):
    return {"date": date, "ticker": ticker, "action": "buy", "shares": shares, "price": shares * pps, "pps": pps}


TXNS = [_txn("2026-06-02", "AAA.F", 10.0, 100.0), _txn("2026-06-15", "BBB.F", 20.0, 50.0),
        _txn("2026-06-30", "AAA.F", 2.0, 101.0)]                    # a buy today


@pytest.fixture
def walk(monkeypatch):
    monkeypatch.setattr(pa.yf, "download", _download)
    monkeypatch.setattr(pa.config, "ORDER_FEE_EUR", 1.0)
    with time_machine.travel("2026-06-30 14:00:00+00:00", tick=False):
        yield pa.build_roi_timeseries(TXNS)


def _close(t, day="2026-06-30"):
    return _line(t)[list(DAYS).index(pd.Timestamp(day))]


def test_the_state_is_the_walk_at_the_last_close_before_today(walk):
    _roi, bms, av = walk
    st = av["__bmlast__"]["S&P 500"]
    assert (st["ticker"], st["ccy"], st["day"]) == ("CSPX.AS", "EUR", "2026-06-29")
    assert st["invested"] == pytest.approx(1000.0 + 1000.0)              # today's buy is not in it
    assert st["eur"] / st["invested"] * 100 - 100 == pytest.approx(bms["S&P 500"]["2026-06-29"], abs=1e-4)
    assert st["twr"] == pytest.approx(av["__twr__"]["S&P 500"]["2026-06-29"])


@pytest.mark.parametrize("name, ticker", [("S&P 500", "CSPX.AS"), ("Fixed Income", "BND")])
def test_marked_at_todays_close_it_is_the_walks_own_point(walk, name, ticker):
    """Today's buy fills at the live price, with the walk's order fee; a USD line goes through EUR/USD."""
    _roi, bms, av = walk
    fx = _close("EURUSD=X") if ticker == "BND" else None
    roi, twr = pa.bench_live(av["__bmlast__"][name], TXNS, _close(ticker), fx, today=pd.Timestamp("2026-06-30").date())
    assert roi == pytest.approx(bms[name]["2026-06-30"], abs=1e-9)
    assert twr == pytest.approx(av["__twr__"][name]["2026-06-30"], rel=1e-12)


def test_a_live_move_moves_the_value_held(walk):
    _roi, _bms, av = walk
    st = av["__bmlast__"]["S&P 500"]
    px = _close("CSPX.AS", "2026-06-29") * 1.10
    roi, twr = pa.bench_live(st, TXNS[:2], px, None, today=pd.Timestamp("2026-06-30").date())   # no buy today
    assert roi == pytest.approx((st["eur"] * 1.10 / st["invested"] - 1) * 100, abs=1e-4)
    assert twr == pytest.approx(st["twr"] * 1.10)


def test_a_usd_line_without_a_rate_is_not_marked(walk):
    _roi, _bms, av = walk
    assert pa.bench_live(av["__bmlast__"]["Fixed Income"], TXNS, 71.0, None) is None
