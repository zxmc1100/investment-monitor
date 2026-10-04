"""Movers math, calendar parsing and the two market fetchers over the yfinance fakes."""
from datetime import date

import numpy as np
import pandas as pd
import pytest
import time_machine

from monitor.data import market as M
from tests import fakes_yf

IDX = pd.bdate_range("2026-09-01", periods=25)


def test_movers_day_5d_and_volume_multiple():
    close = pd.DataFrame({"A": np.linspace(100, 124, 25)}, index=IDX)
    vol = pd.DataFrame({"A": [100.0] * 24 + [300.0]}, index=IDX)
    r = M.movers_from_frame(close, vol)["A"]
    assert r["day"] == pytest.approx((124 / 123 - 1) * 100)
    assert r["d5"] == pytest.approx((124 / 119 - 1) * 100)
    assert r["volx"] == pytest.approx(3.0) and r["bar"] == IDX[-1].date().isoformat()


def test_nan_latest_bar_uses_last_finite_and_zero_median_gives_none():
    c = np.linspace(100, 124, 25)
    c[-1] = np.nan
    close = pd.DataFrame({"A": c}, index=IDX)
    vol = pd.DataFrame({"A": [0.0] * 25}, index=IDX)
    r = M.movers_from_frame(close, vol)["A"]
    assert r["bar"] == IDX[-2].date().isoformat()
    assert r["day"] == pytest.approx((123 / 122 - 1) * 100) and r["volx"] is None


def test_short_or_empty_columns():
    close = pd.DataFrame({"ONE": [np.nan] * 24 + [10.0], "FOUR": [np.nan] * 21 + [1.0, 2.0, 3.0, 4.0]}, index=IDX)
    out = M.movers_from_frame(close, None)
    assert "ONE" not in out and out["FOUR"]["d5"] is None and out["FOUR"]["volx"] is None


def test_fetch_movers_maps_tickers_and_reads_volume(monkeypatch):
    fakes_yf.install(monkeypatch)
    with time_machine.travel("2026-10-02 18:00:00+00:00", tick=False):
        out = M.fetch_movers(["AAPL", "EUNL.F"])            # EUNL.F is priced as IWDA.AS
    assert set(out) == {"AAPL", "EUNL.F"}
    s = fakes_yf.series("IWDA.AS", "2026-10-02")
    assert out["EUNL.F"]["day"] == pytest.approx((s.iloc[-1] / s.iloc[-2] - 1) * 100)
    assert out["AAPL"]["bar"] == "2026-10-02" and out["AAPL"]["volx"] > 0
    assert M.fetch_movers([]) == {}


TODAY = date(2026, 10, 3)


def test_calendar_first_upcoming_earnings_and_past_ex_div_ignored():
    cal = {"Earnings Date": [date(2026, 9, 1), date(2026, 11, 5), date(2026, 10, 29)],
           "Ex-Dividend Date": date(2026, 5, 13)}
    assert M.events_from_calendar(cal, TODAY) == [{"date": "2026-10-29", "kind": "EARNINGS", "amount": None}]


def test_calendar_single_date_and_upcoming_ex_div():
    cal = {"Earnings Date": date(2026, 10, 3), "Ex-Dividend Date": pd.Timestamp("2026-10-06")}
    assert [e["kind"] for e in M.events_from_calendar(cal, TODAY)] == ["EARNINGS", "EX-DIV"]


@pytest.mark.parametrize("cal", [{}, None, [], {"Earnings Date": []}, {"Earnings Date": "garbage"}])
def test_empty_or_odd_calendar_has_no_events(cal):
    assert M.events_from_calendar(cal, TODAY) == []


def test_fetch_events_amount_and_failures(monkeypatch):
    fakes_yf.install(monkeypatch)
    monkeypatch.setitem(fakes_yf.CALENDARS, "RHM.DE", {"Earnings Date": [date(2026, 11, 5)],
                                                       "Ex-Dividend Date": date(2026, 10, 6)})
    monkeypatch.setitem(fakes_yf.DIVIDENDS, "RHM.DE", pd.Series([5.7, 8.1], index=pd.to_datetime(["2025-05-14", "2026-05-13"])))

    class Broken(fakes_yf.Ticker):
        @property
        def calendar(self):
            raise RuntimeError("yahoo down")
    real = fakes_yf.Ticker
    monkeypatch.setattr("yfinance.Ticker", lambda t: Broken(t) if t == "BAD.DE" else real(t))
    out = M.fetch_events(["RHM.DE", "BAD.DE", "AAPL"], today=TODAY)
    assert out["RHM.DE"] == [{"date": "2026-11-05", "kind": "EARNINGS", "amount": None},
                             {"date": "2026-10-06", "kind": "EX-DIV", "amount": 8.1}]
    assert "BAD.DE" not in out and out["AAPL"] == []


def test_movers_batch_uses_real_closes(monkeypatch):
    fakes_yf.install(monkeypatch)
    calls = []
    real = fakes_yf.download
    monkeypatch.setattr("yfinance.download", lambda *a, **k: (calls.append(k), real(*a, **k))[1])
    M.fetch_movers(["AAPL"])
    assert calls[0]["auto_adjust"] is False


@pytest.mark.parametrize("blank", [pd.NaT, float("nan"), None, "", "NaT"])
def test_a_blank_calendar_date_keeps_the_other_events(blank):
    cal = {"Earnings Date": [blank, date(2026, 10, 29)], "Ex-Dividend Date": blank}
    assert M.events_from_calendar(cal, TODAY) == [{"date": "2026-10-29", "kind": "EARNINGS", "amount": None}]
    assert M._as_date(blank) is None


def test_dividends_failure_keeps_the_earnings_event(monkeypatch):
    fakes_yf.install(monkeypatch)
    monkeypatch.setitem(fakes_yf.CALENDARS, "RHM.DE", {"Earnings Date": [date(2026, 11, 5)],
                                                       "Ex-Dividend Date": date(2026, 10, 6)})

    class NoDivs(fakes_yf.Ticker):
        @property
        def dividends(self):
            raise RuntimeError("yahoo down")
    monkeypatch.setattr("yfinance.Ticker", lambda t: NoDivs(t))
    assert M.fetch_events(["RHM.DE"], today=TODAY)["RHM.DE"] == [
        {"date": "2026-11-05", "kind": "EARNINGS", "amount": None},
        {"date": "2026-10-06", "kind": "EX-DIV", "amount": None}]
