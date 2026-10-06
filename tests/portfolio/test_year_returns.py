"""Calendar-year returns two ways: the spreadsheet's simple method (gain ÷ value on 1 Jan + net
money added — new money dilutes it) and time-weighted (daily returns with each day's money moves
taken out). Pure functions on a holdings-value series; no network."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from monitor.portfolio.analytics import daily_flows, twr_index, year_returns


def tx(d, action, price, ticker="X.F", shares=1.0):
    return {"date": d, "ticker": ticker, "action": action, "shares": shares, "price": price, "pps": price / shares}


def div(d, eur, ticker="X.F"):
    return {"date": d, "ticker": ticker, "shares": 1.0, "per_share": eur, "gross": eur, "eur": eur}


def by_year(rows):
    return {r["year"]: r for r in rows}


def test_daily_flows_apply_on_the_first_index_date_on_or_after_each_event():
    idx = pd.bdate_range("2026-01-05", "2026-01-16")
    txns = [tx("2026-01-04", "buy", 100.0),           # Sunday, before the index: first index date
            tx("2026-01-07", "sell", 40.0),
            tx("2026-01-08", "bonus", 10.0),          # shares received, no money moved
            tx("2026-01-20", "buy", 7.0)]             # after the index: not in it
    f = daily_flows(txns, [div("2026-01-10", 5.0)], idx)   # Saturday dividend: Monday
    assert f.index.equals(idx) and list(f.columns) == ["buy", "sell", "dividend"]
    moved = f[(f != 0).any(axis=1)]
    assert moved.to_dict("index") == {
        pd.Timestamp("2026-01-05"): {"buy": 100.0, "sell": 0.0, "dividend": 0.0},
        pd.Timestamp("2026-01-07"): {"buy": 0.0, "sell": 40.0, "dividend": 0.0},
        pd.Timestamp("2026-01-12"): {"buy": 0.0, "sell": 0.0, "dividend": 5.0}}


# a book: bought 1000 on 2 Jun 2025, worth 1200 at the end of 2025; in 2026 buys 500 more, sells
# 300, gets a 20 dividend and is worth 1800 at the end of June
IDX = pd.bdate_range("2025-06-02", "2026-06-30")
HOLD = pd.Series(1000.0, index=IDX)
HOLD.loc["2025-12-31"] = 1200.0
HOLD.loc["2026-01-01":] = 1200.0
HOLD.loc["2026-03-02":] = 1700.0
HOLD.loc["2026-05-04":] = 1400.0
HOLD.loc["2026-06-30"] = 1800.0
TXNS = [tx("2025-06-02", "buy", 1000.0), tx("2026-03-02", "buy", 500.0), tx("2026-05-04", "sell", 300.0)]
DIVS = [div("2026-04-01", 20.0)]


def test_simple_is_the_spreadsheet_formula():
    rows = year_returns(HOLD, TXNS, DIVS, today=date(2026, 6, 30))
    assert [r["year"] for r in rows] == [2026, 2025]                       # newest first
    y = by_year(rows)[2026]
    assert (y["start"], y["end"], y["buys"], y["sells"], y["dividends"]) == pytest.approx((1200, 1800, 500, 300, 20))
    assert y["gain"] == pytest.approx(1800 + 300 + 20 - 1200 - 500)
    assert y["simple"] == pytest.approx(420 / (1200 + 500 - 300) * 100)    # 30 %
    # the money moves sit exactly on the days the value jumps, so only Dec→Jun growth and the
    # dividend remain: 1200 → 1700 − 500 → 1700 → (1400 + 300) → 1800, plus the 20 dividend
    assert y["twr"] == pytest.approx((1720 / 1700) * (1800 / 1400) * 100 - 100)


def test_growth_is_the_gain_over_the_money_at_work_payments_excluded():
    """Money-weighted (Modified Dietz): the year's gain ÷ (value on its first day + each euro paid in, less
    each taken out, counted for the share of the year it was invested). 2026 to 30 Jun (180 days): 500 in on
    2 Mar (120 days left), 20 dividend out on 1 Apr (90), 300 sold on 4 May (57)."""
    y = by_year(year_returns(HOLD, TXNS, DIVS, today=date(2026, 6, 30)))
    at_work = 1200 + 500 * 120 / 180 - 20 * 90 / 180 - 300 * 57 / 180
    assert y[2026]["growth"] == pytest.approx(420 / at_work * 100)
    assert y[2026]["first"] == 1200.0
    # the first year runs from the first trade (2 Jun 2025): the 1000 bought then was at work all of it
    assert (y[2025]["first"], y[2025]["growth"]) == (1000.0, pytest.approx(20.0))


def test_growth_never_counts_money_paid_in_as_growth():
    """A flat market and a big deposit: the value doubles, growth stays 0."""
    idx = pd.bdate_range("2026-01-02", "2026-06-30")
    hold = pd.Series(1000.0, index=idx)
    hold.loc["2026-04-01":] = 2000.0
    rows = year_returns(hold, [tx("2025-12-01", "buy", 1000.0), tx("2026-04-01", "buy", 1000.0)], [],
                        today=date(2026, 6, 30))
    assert by_year(rows)[2026]["growth"] == pytest.approx(0.0)


def test_no_money_at_work_gives_no_growth():
    idx = pd.bdate_range("2026-01-05", "2026-01-09")
    rows = year_returns(pd.Series(0.0, index=idx), [tx("2026-01-05", "buy", 0.0)], [], today=date(2026, 1, 9))
    assert rows[0]["growth"] is None


def test_first_year_starts_at_zero():
    y = by_year(year_returns(HOLD, TXNS, DIVS, today=date(2026, 6, 30)))[2025]
    assert y["start"] == 0.0 and y["end"] == pytest.approx(1200.0) and y["buys"] == pytest.approx(1000.0)
    assert y["simple"] == pytest.approx(20.0) and y["twr"] == pytest.approx(20.0)


def test_mid_year_deposit_leaves_twr_unchanged_while_simple_dilutes():
    idx = pd.bdate_range("2025-12-01", "2026-06-30")
    px = pd.Series(np.nan, index=idx)
    px.loc[:"2025-12-31"] = 100.0
    px.loc["2026-03-31":] = 150.0
    px = px.interpolate()                                   # +50 % by end of March, then flat
    alone = 10 * px
    topped = alone.copy()
    topped.loc["2026-04-01":] = 20 * px.loc["2026-04-01":]  # 10 more shares bought at 150
    base = [tx("2025-12-01", "buy", 1000.0)]
    a = by_year(year_returns(alone, base, [], today=date(2026, 6, 30)))[2026]
    b = by_year(year_returns(topped, base + [tx("2026-04-01", "buy", 1500.0)], [], today=date(2026, 6, 30)))[2026]
    assert a["twr"] == pytest.approx(50.0) and b["twr"] == pytest.approx(50.0)
    assert a["simple"] == pytest.approx(50.0)
    assert b["simple"] == pytest.approx(500 / 2500 * 100)  # same euro gain over 1000 + 1500


def test_live_value_sets_the_current_year_end_after_the_last_close():
    idx_end = HOLD.copy()
    idx_end.loc[pd.Timestamp("2026-07-01")] = 1800.0
    idx_end.loc[pd.Timestamp("2026-07-02")] = 1800.0
    idx_end.loc[pd.Timestamp("2026-07-03")] = 1800.0        # Friday
    base = by_year(year_returns(idx_end, TXNS, DIVS, today=date(2026, 7, 4)))
    live = by_year(year_returns(idx_end, TXNS, DIVS, live_value=1890.0, today=date(2026, 7, 4)))   # Saturday
    assert live[2026]["end"] == 1890.0 and base[2026]["end"] == pytest.approx(1800.0)
    assert live[2026]["simple"] == pytest.approx((1890 + 300 + 20 - 1200 - 500) / 1400 * 100)
    assert live[2026]["twr"] == pytest.approx(((1 + base[2026]["twr"] / 100) * 1890 / 1800 - 1) * 100)
    assert live[2025] == base[2025]                         # only the current year moves


def test_live_value_replaces_todays_close_on_a_trading_day():
    rows = by_year(year_returns(HOLD, TXNS, DIVS, live_value=1890.0, today=date(2026, 6, 30)))
    # chained through Monday 29 Jun (1400), then live: 1400 → 1890
    assert rows[2026]["end"] == 1890.0
    assert rows[2026]["twr"] == pytest.approx((1720 / 1700) * (1890 / 1400) * 100 - 100)


def test_a_weekend_trade_after_the_last_close_counts_in_the_live_year():
    rows = year_returns(HOLD.loc[:"2026-06-26"], TXNS + [tx("2026-06-28", "buy", 100.0)], DIVS,
                        live_value=1500.0, today=date(2026, 6, 28))                 # Sunday
    y = by_year(rows)[2026]
    assert y["buys"] == pytest.approx(600.0) and y["end"] == 1500.0
    assert y["simple"] == pytest.approx((1500 + 300 + 20 - 1200 - 600) / (1200 + 600 - 300) * 100)
    # the weekend buy counts from the start of its day: 1500 / (1400 + 100), so it is not a gain
    assert y["twr"] == pytest.approx((1720 / 1700) * (1500 / (1400 + 100)) * 100 - 100)


def test_new_money_counts_from_the_start_of_its_day():
    """A large buy on a small base (18 Nov 2024 in the real book: 520 of buys on 117 held) whose
    new shares close below what was paid must not charge that same-day move to the old money:
    the day is (V_t + out) / (V_{t-1} + in), not (V_t - in) / V_{t-1} (which read -6.8 %)."""
    idx = pd.bdate_range("2026-01-05", "2026-01-09")
    hold = pd.Series([117.0, 117.0, 117.0 + 512.0, 629.0, 629.0], index=idx)   # old money flat
    txns = [tx("2026-01-05", "buy", 117.0), tx("2026-01-07", "buy", 520.0)]   # closes at 512
    y = year_returns(hold, txns, [], today=date(2026, 1, 9))[0]
    assert y["twr"] == pytest.approx((629 / 637 - 1) * 100)                  # −1.3 %, the buy's own loss
    assert y["twr"] > -2.0


def test_sales_and_dividends_leave_at_the_end_of_their_day():
    idx = pd.bdate_range("2026-01-05", "2026-01-07")
    hold = pd.Series([1000.0, 1100.0, 440.0], index=idx)          # +10 %, then sold 600 of 1100, -20 %
    txns = [tx("2026-01-05", "buy", 1000.0), tx("2026-01-07", "sell", 440.0)]
    y = year_returns(hold, txns, [div("2026-01-07", 0.0)], today=date(2026, 1, 7))[0]
    assert y["twr"] == pytest.approx((1100 / 1000) * ((440 + 440) / 1100) * 100 - 100)


def test_no_denominator_gives_no_simple_return():
    idx = pd.bdate_range("2026-01-05", "2026-01-09")
    hold = pd.Series([1000.0, 1000.0, 0.0, 0.0, 0.0], index=idx)
    rows = year_returns(hold, [tx("2026-01-05", "buy", 1000.0), tx("2026-01-07", "sell", 1000.0)], [],
                        today=date(2026, 1, 9))
    assert rows[0]["simple"] is None and rows[0]["twr"] == pytest.approx(0.0)


def test_bonus_value_is_gain_in_both_methods():
    idx = pd.bdate_range("2026-01-05", "2026-01-09")
    hold = pd.Series([1000.0, 1000.0, 1010.0, 1010.0, 1010.0], index=idx)
    y = year_returns(hold, [tx("2026-01-05", "buy", 1000.0), tx("2026-01-07", "bonus", 10.0)], [],
                     today=date(2026, 1, 9))[0]
    assert y["buys"] == pytest.approx(1000.0)
    assert y["simple"] == pytest.approx(1.0) and y["twr"] == pytest.approx(1.0)


def test_a_stale_daily_part_across_new_year_still_counts_the_late_money():
    """The daily part ends 30 Dec; a 500 buy dated 31 Dec is only in the live value on 4 Jan. It is
    money added in the live year, not a +50 % gain (review repro)."""
    idx = pd.bdate_range("2026-12-01", "2026-12-30")
    hold = pd.Series(1000.0, index=idx)                                   # flat book
    txns = [tx("2026-12-01", "buy", 1000.0), tx("2026-12-31", "buy", 500.0)]
    rows = by_year(year_returns(hold, txns, [], live_value=1500.0, today=date(2027, 1, 4)))
    assert rows[2027]["buys"] == pytest.approx(500.0) and rows[2027]["gain"] == pytest.approx(0.0)
    assert rows[2027]["simple"] == pytest.approx(0.0) and rows[2027]["twr"] == pytest.approx(0.0)
    assert rows[2026]["buys"] == pytest.approx(1000.0) and rows[2026]["gain"] == pytest.approx(0.0)


def test_money_dated_after_today_is_not_counted():
    rows = year_returns(HOLD, TXNS + [tx("2026-07-15", "buy", 100.0)], DIVS, live_value=1800.0,
                        today=date(2026, 7, 4))
    assert by_year(rows)[2026]["buys"] == pytest.approx(500.0)


# ── twr_index: the time-weighted growth curve a normalized (NORM) chart is drawn from ─────────────

def test_twr_index_is_flat_through_a_deposit_in_a_flat_market():
    idx = pd.bdate_range("2026-01-05", "2026-01-16")
    hold = pd.Series(1000.0, index=idx)
    hold.loc["2026-01-12":] = 1500.0                       # 500 more money, prices unchanged
    t = twr_index(hold, [tx("2026-01-05", "buy", 1000.0), tx("2026-01-12", "buy", 500.0)], [])
    assert t.index.equals(idx) and list(t) == pytest.approx([1.0] * len(idx))


def test_twr_index_reconciles_to_the_year_table():
    t = twr_index(HOLD, TXNS, DIVS)
    y = by_year(year_returns(HOLD, TXNS, DIVS, today=date(2026, 6, 30)))
    assert (t["2026-06-30"] / t["2025-12-31"] - 1) * 100 == pytest.approx(y[2026]["twr"])
    assert (t["2025-12-31"] - 1) * 100 == pytest.approx(y[2025]["twr"])


def test_twr_index_is_one_until_money_is_at_work():
    idx = pd.bdate_range("2026-01-05", "2026-01-09")
    hold = pd.Series([0.0, 0.0, 100.0, 110.0, 121.0], index=idx)
    assert list(twr_index(hold, [tx("2026-01-07", "buy", 100.0)], [])) == pytest.approx([1.0, 1.0, 1.0, 1.1, 1.21])
