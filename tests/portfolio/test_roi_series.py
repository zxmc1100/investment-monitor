"""build_roi_timeseries: real (unadjusted) closes for your holdings, dividends as cash,
benchmarks total-return and charged the same order fee."""
import pandas as pd
import pytest
import time_machine
import yfinance

from monitor import config
from monitor.portfolio.analytics import build_roi_timeseries
from tests import fakes_yf

TX = [{"date": "2025-01-06", "ticker": "AAA.F", "action": "buy", "shares": 10.0, "price": 1000.0, "pps": 100.0}]


@pytest.fixture
def calls(monkeypatch):
    fakes_yf.install(monkeypatch)
    seen = []
    real = yfinance.download

    def spy(tickers, **kw):
        seen.append((tuple([tickers] if isinstance(tickers, str) else tickers), kw.get("auto_adjust")))
        return real(tickers, **kw)

    monkeypatch.setattr(yfinance, "download", spy)
    with time_machine.travel("2026-06-30 14:00:00+00:00", tick=False):
        yield seen


def test_holdings_use_unadjusted_closes_and_benchmarks_adjusted(calls):
    build_roi_timeseries(TX)
    assert [adj for tks, adj in calls if "AAA.F" in tks] == [False]
    assert [adj for tks, adj in calls if "CSPX.AS" in tks] == [True]


def test_dividends_count_as_cash_from_the_ex_date(calls):
    div = [{"date": "2025-03-03", "ticker": "AAA.F", "shares": 10.0, "per_share": 2.0, "eur": 20.0}]
    base, _, _ = build_roi_timeseries(TX)
    roi, _, av = build_roi_timeseries(TX, dividends=div)
    before, on = pd.Timestamp("2025-02-28"), pd.Timestamp("2025-03-03")
    assert roi[before] == pytest.approx(base[before])
    assert roi[on] - base[on] == pytest.approx(20.0 / 1000 * 100, abs=1e-3)
    assert av["__cash__"][on] == pytest.approx(20.0)
    assert av["__total__"][on] - av["AAA.F"][on] == pytest.approx(20.0)      # total = holdings + cash


def test_benchmarks_pay_the_order_fee_except_savings_plans(calls, monkeypatch):
    monkeypatch.setattr(config, "SAVINGS_PLAN_TICKERS", ("PLAN.F",))
    _, bm, _ = build_roi_timeseries(TX)
    _, bm_plan, _ = build_roi_timeseries([{**TX[0], "ticker": "PLAN.F"}])
    first = bm["S&P 500"].index[0]
    assert bm["S&P 500"][first] == pytest.approx((999.0 / 1000 - 1) * 100, abs=1e-3)   # bought 999, invested 1000
    assert bm_plan["S&P 500"][first] == pytest.approx(0.0, abs=1e-3)


def test_benchmarks_ignore_bonus_shares(calls):
    bonus = TX + [{"date": "2025-03-03", "ticker": "AAA.F", "action": "bonus", "shares": 0.1, "price": 10.0, "pps": 100.0}]
    _, bm0, _ = build_roi_timeseries(TX)
    _, bm, _ = build_roi_timeseries(bonus)
    for name in bm0:
        pd.testing.assert_series_equal(bm[name], bm0[name])


# ── Yahoo throttling: a degraded answer must fail the build, never price positions at cost ──
from monitor.portfolio import analytics                                     # noqa: E402

TWO = TX + [{"date": "2025-02-03", "ticker": "NOPE.F", "action": "buy", "shares": 5.0, "price": 500.0, "pps": 100.0}]


@pytest.fixture
def yahoo(monkeypatch):
    """fakes_yf behind a switch: drop(tickers, adjusted) decides what Yahoo leaves out of a call."""
    fakes_yf.install(monkeypatch)
    monkeypatch.setattr(analytics.time, "sleep", lambda s: None)
    real, state = yfinance.download, {"calls": 0, "drop": lambda tks, adj, n: set()}

    def flaky(tickers, **kw):
        tks = [tickers] if isinstance(tickers, str) else list(tickers)
        state["calls"] += 1
        gone = state["drop"](tks, kw.get("auto_adjust"), state["calls"])
        keep = [t for t in tks if t not in gone]
        return real(keep, **kw) if keep else pd.DataFrame()

    monkeypatch.setattr(yfinance, "download", flaky)
    with time_machine.travel("2026-06-30 14:00:00+00:00", tick=False):
        yield state


def test_a_throttled_yahoo_fails_instead_of_pricing_at_cost(yahoo):
    yahoo["drop"] = lambda tks, adj, n: set(tks)                    # every call comes back empty
    with pytest.raises(analytics.PriceHistoryError, match="AAA.F"):
        build_roi_timeseries(TX)


def test_missing_benchmarks_only_drop_the_comparison_lines(yahoo):
    yahoo["drop"] = lambda tks, adj, n: set(tks) if adj else set()  # holdings fine, benchmarks empty
    roi, bm, _ = build_roi_timeseries(TX)
    assert bm == {} and roi.iloc[-1] != pytest.approx(0.0)          # your ROI is still right


def test_a_transient_empty_answer_is_retried(yahoo):
    yahoo["drop"] = lambda tks, adj, n: set(tks) if n <= 2 else set()   # the first two calls throttled
    roi, bm, _ = build_roi_timeseries(TX)
    assert yahoo["calls"] > 2 and roi.iloc[-1] != pytest.approx(0.0)


def test_one_held_line_yahoo_never_prices_is_carried_at_cost_not_fatal(yahoo):
    yahoo["drop"] = lambda tks, adj, n: {"NOPE.F"}                  # a listing Yahoo does not have
    roi, bm, av = build_roi_timeseries(TWO)
    assert "S&P 500" in bm and len(roi)


# ── never_priced: only a line the quote buffer never priced may be missing (PORT's daily tier) ──
THREE = TWO + [{"date": "2025-02-03", "ticker": "NIX.F", "action": "buy", "shares": 2.0, "price": 300.0, "pps": 150.0}]


def test_lines_yahoo_never_priced_may_be_missing_however_many(yahoo):
    yahoo["drop"] = lambda tks, adj, n: {"NOPE.F", "NIX.F"}            # two listings Yahoo does not have
    roi, bm, av = build_roi_timeseries(THREE, never_priced={"NOPE.F", "NIX.F"})
    assert len(roi) and "S&P 500" in bm
    assert yahoo["calls"] == 2                                       # no retries for lines never expected


def test_a_sole_held_line_yahoo_never_priced_is_carried_at_cost(yahoo):
    yahoo["drop"] = lambda tks, adj, n: {"NOPE.F"} if not adj else set()
    roi, _, _ = build_roi_timeseries([TWO[1]], never_priced={"NOPE.F"})
    assert roi.iloc[-1] == pytest.approx(0.0)                       # carried at cost, not an error


def test_with_never_priced_given_one_dropped_quoted_line_fails(yahoo):
    yahoo["drop"] = lambda tks, adj, n: {"NOPE.F"} if not adj else set()   # a throttle that drops one line
    with pytest.raises(analytics.PriceHistoryError, match="NOPE.F"):
        build_roi_timeseries(TWO, never_priced=set())


def test_retried_attempts_are_merged(yahoo):
    # call 1 loses AAA.F, call 2 loses NOPE.F: together they price both lines
    yahoo["drop"] = lambda tks, adj, n: set() if adj else ({"AAA.F"} if n % 2 else {"NOPE.F"})
    _, _, av = build_roi_timeseries(TWO, never_priced=set())
    assert av["AAA.F"].dropna().iloc[-1] != pytest.approx(10 * 100.0)  # a real price, not cost
    assert av["NOPE.F"].dropna().iloc[-1] != pytest.approx(5 * 100.0)


def test_a_dust_remainder_is_not_a_held_line(yahoo):
    dust = TWO + [{"date": "2025-03-03", "ticker": "NOPE.F", "action": "sell", "shares": 4.9995, "price": 600.0, "pps": 120.0}]
    yahoo["drop"] = lambda tks, adj, n: {"NOPE.F"} if not adj else set()
    build_roi_timeseries(dust, never_priced=set())                   # 0.0005 shares: below ledger.DUST


def test_without_eurusd_the_usd_benchmarks_are_dropped_not_valued_as_euros(yahoo):
    yahoo["drop"] = lambda tks, adj, n: {"EURUSD=X"} if adj else set()
    _, bm, _ = build_roi_timeseries(TX)
    usd = {name for name, (_, cur) in analytics.BENCHMARKS.items() if cur == "USD"}
    assert usd and not usd & set(bm) and "S&P 500" in bm


def test_every_held_line_unquoted_is_an_outage_not_a_listing_gap(yahoo):
    """Yahoo down at start: the quote buffer has no quote for ANY held line. That is an outage —
    with history missing too the build fails (last good kept), never prices everything at cost."""
    yahoo["drop"] = lambda tks, adj, n: set(tks) if not adj else set()   # holdings history throttled
    with pytest.raises(analytics.PriceHistoryError):
        build_roi_timeseries(TWO, never_priced={"AAA.F", "NOPE.F"})
