"""Your dividends with ex date, pay date and net: what was paid (your broker's dividends.csv first, Yahoo's
estimate otherwise), what is due (ex date passed, money not yet in) and what is coming (Yahoo's calendar).
Only what was paid is cash — counted on its pay date."""
from datetime import date

import pytest

from monitor.portfolio.dividends import cash, combine

TX = [{"date": "2025-01-06", "ticker": "AAA.F", "action": "buy", "shares": 10.0, "price": 1500.0, "pps": 150.0},
      {"date": "2025-01-06", "ticker": "BBB.MI", "action": "buy", "shares": 100.0, "price": 400.0, "pps": 4.0}]
PER_SHARE = {"AAA.F": [["2025-03-18", 0.60], ["2025-06-12", 0.60], ["2025-09-16", 0.70]],
             "BBB.MI": [["2025-05-19", 0.20]]}
# what the broker paid: the March and June AAA dividends (22 and 24 days after their ex dates)
PAID = [{"pay": "2025-04-09", "ticker": "AAA.F", "shares": 10.0, "gross": 6.0, "tax": 1.58, "net": 4.42},
        {"pay": "2025-07-06", "ticker": "AAA.F", "shares": 10.0, "gross": 6.0, "tax": 1.58, "net": 4.42}]
CAL = {"BBB.MI": {"ex": "2025-11-24", "pay": "2025-11-26", "amount": 0.21}}
TODAY = date(2025, 10, 1)


def run(paid=PAID, today=TODAY, cal=CAL):
    return combine(TX, PER_SHARE, paid, cal, today=today, tax=0.26375)


def by(recs, ticker, ex):
    return next(r for r in recs if r["ticker"] == ticker and r["ex"] == ex)


def test_a_paid_dividend_takes_the_brokers_pay_date_and_net():
    r = by(run(), "AAA.F", "2025-03-18")
    assert (r["pay"], r["net"], r["status"], r["source"], r["pay_est"]) == ("2025-04-09", 4.42, "PAID", "BROKER", False)


def test_one_not_yet_paid_is_due_its_pay_date_estimated_from_this_lines_past_gaps():
    """AAA's ex date 16 Sep has passed; its past dividends took 22 and 24 days to arrive: due ~9 Oct."""
    r = by(run(), "AAA.F", "2025-09-16")
    assert (r["status"], r["pay"], r["pay_est"]) == ("DUE", "2025-10-09", True)
    assert r["net"] == pytest.approx(10 * 0.70 * (1 - 0.26375))
    assert r["source"] == "YAHOO"


def test_a_line_with_no_gap_of_its_own_takes_your_lines_usual_gap():
    """BBB.MI was never paid in the broker's file: its pay date is estimated by the gap your other lines' dividends
    took (AAA: 22 and 24 days), so 19 May + 23 → 11 Jun, paid since."""
    r = by(run(), "BBB.MI", "2025-05-19")
    assert (r["status"], r["pay"], r["pay_est"]) == ("PAID", "2025-06-11", True)


def test_with_no_gap_known_at_all_the_ex_date_stands_in_as_before():
    r = by(run(paid=[]), "BBB.MI", "2025-05-19")
    assert (r["status"], r["pay"], r["pay_est"]) == ("PAID", None, False)


def test_an_announced_payment_written_with_its_future_date_is_due_not_paid():
    """AAA's 16 Sep dividend, its payment announced for 8 Oct (copied from the broker's app into
    dividends.csv): due on that exact date, never cash before it."""
    announced = PAID + [{"pay": "2025-10-08", "ticker": "AAA.F", "shares": 10.0, "gross": 7.0, "tax": 1.85,
                         "net": 5.15}]
    r = by(run(paid=announced), "AAA.F", "2025-09-16")
    assert (r["status"], r["pay"], r["pay_est"], r["net"]) == ("DUE", "2025-10-08", False, 5.15)
    assert "2025-10-08" not in [d["date"] for d in cash(run(paid=announced))]


def test_an_announced_pay_date_without_amounts_is_due_on_it_with_yahoos_estimate():
    """dividends.csv `2025-10-08,AAA.F,,,,` — you know the day (the broker's app says it), not the amount: the
    16 Sep dividend is due on exactly that day, its net Yahoo's per share less tax; cash from that day."""
    announced = PAID + [{"pay": "2025-10-08", "ticker": "AAA.F", "shares": None, "gross": None, "tax": None,
                         "net": None}]
    r = by(run(paid=announced), "AAA.F", "2025-09-16")
    assert (r["status"], r["pay"], r["pay_est"], r["source"]) == ("DUE", "2025-10-08", False, "ANNOUNCED")
    assert r["net"] == pytest.approx(10 * 0.70 * (1 - 0.26375))
    assert by(run(paid=announced, today=date(2025, 10, 8)), "AAA.F", "2025-09-16")["status"] == "PAID"


def test_once_the_real_payment_is_imported_the_announced_row_is_not_counted_again():
    both = PAID + [{"pay": "2025-10-08", "ticker": "AAA.F", "shares": None, "gross": None, "tax": None, "net": None},
                   {"pay": "2025-10-08", "ticker": "AAA.F", "shares": 10.0, "gross": 7.0, "tax": 1.85, "net": 5.15}]
    recs = [r for r in run(paid=both, today=date(2025, 10, 9)) if r["ticker"] == "AAA.F"]
    assert [(r["ex"], r["net"], r["source"]) for r in recs if r["pay"] == "2025-10-08"] == [("2025-09-16", 5.15, "BROKER")]


def test_the_calendar_brings_the_next_one():
    r = by(run(), "BBB.MI", "2025-11-24")
    assert (r["status"], r["pay"], r["shares"], r["per_share"]) == ("UPCOMING", "2025-11-26", 100.0, 0.21)
    assert r["net"] == pytest.approx(100 * 0.21 * (1 - 0.26375))


def test_only_paid_dividends_are_cash_on_their_pay_date():
    c = cash(run())
    assert [(d["date"], d["ticker"], round(d["eur"], 2)) for d in c] == [
        ("2025-04-09", "AAA.F", 4.42), ("2025-06-11", "BBB.MI", round(100 * 0.20 * (1 - 0.26375), 2)),
        ("2025-07-06", "AAA.F", 4.42)]
    assert cash(run(today=date(2025, 10, 9)))[-1]["date"] == "2025-10-09"   # the due one, paid on its day


def test_a_broker_dividend_yahoo_never_listed_still_counts():
    extra = PAID + [{"pay": "2025-08-01", "ticker": "ZZZ.DE", "shares": 5.0, "gross": 2.0, "tax": 0.53, "net": 1.47}]
    r = next(r for r in run(paid=extra) if r["ticker"] == "ZZZ.DE")
    assert (r["ex"], r["pay"], r["status"], r["net"]) == (None, "2025-08-01", "PAID", 1.47)


def test_without_broker_dividends_everything_is_yahoos_estimate_counted_from_the_ex_date():
    recs = run(paid=[], cal={})
    assert all(r["source"] == "YAHOO" and r["status"] == "PAID" and r["pay"] is None for r in recs)
    assert [d["date"] for d in cash(recs)] == ["2025-03-18", "2025-05-19", "2025-06-12", "2025-09-16"]
