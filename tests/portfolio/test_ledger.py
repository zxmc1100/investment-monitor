"""ledger.parse_portfolio: FIFO lots at exact EUR amounts (Trade Republic's cost basis)."""
from datetime import date

import pytest

from monitor import config
from monitor.portfolio import dividends as D
from monitor.portfolio.ledger import compute_portfolio_summary, parse_portfolio
from monitor.portfolio.snapshot import accounting


def dividend_cash(tx, per_share, today=None):
    """Yahoo's dividends alone (no broker file, no calendar): the ex-date rule and the tax, as cash."""
    return D.cash(D.combine(tx, per_share, [], {}, today=today, tax=config.DIVIDEND_TAX))

HEAD = "Date,Ticker,Action,Shares,Price,PricePerShare\n"


def book(tmp_path, rows):
    p = tmp_path / "p.csv"
    p.write_text(HEAD + "\n".join(rows) + "\n", encoding="utf-8")
    return parse_portfolio(p)


def test_partial_sale_consumes_the_oldest_lots_first(tmp_path):
    b = book(tmp_path, ["2025-01-02,X.F,buy,10,1000.00,100.00",
                        "2025-02-03,X.F,buy,10,1200.00,120.00",
                        "2025-03-03,X.F,sell,15,1950.00,130.00"])
    assert b["realized"]["X.F"]["pnl_eur"] == pytest.approx(1950 - (1000 + 5 * 120))   # 350, avg-cost gave 300
    h = b["holdings"]["X.F"]
    assert h["shares"] == pytest.approx(5) and h["avg_cost"] == pytest.approx(120.0)


def test_cost_comes_from_the_exact_euro_amount_not_the_rounded_price(tmp_path):
    b = book(tmp_path, ["2025-01-02,X.F,buy,13.513513,63.40,4.69"])       # 63.40 / 13.513513 = 4.6916…
    assert b["holdings"]["X.F"]["avg_cost"] == pytest.approx(63.40 / 13.513513)


def test_sale_proceeds_are_the_exact_euro_amount(tmp_path):
    b = book(tmp_path, ["2025-01-02,Y.F,buy,40,80.00,2.00",
                        "2025-11-14,Y.F,sell,40,57.63,1.44"])               # 57.63 / 40 = 1.44075
    assert b["realized"]["Y.F"]["pnl_eur"] == pytest.approx(57.63 - 80.00)
    assert "Y.F" not in b["holdings"]


def test_full_exit_then_rebuy_starts_a_fresh_cost_basis(tmp_path):
    b = book(tmp_path, ["2025-01-02,X.F,buy,10,1000.00,100.00",
                        "2025-02-03,X.F,sell,10,1100.00,110.00",
                        "2025-03-03,X.F,buy,4,800.00,200.00"])
    h = b["holdings"]["X.F"]
    assert h["shares"] == pytest.approx(4) and h["avg_cost"] == pytest.approx(200.0)
    assert h["first_buy"] == "2025-03-03"
    assert b["realized"]["X.F"]["pnl_eur"] == pytest.approx(100.0)


def test_selling_more_than_held_books_no_profit_on_the_excess(tmp_path):
    b = book(tmp_path, ["2025-01-02,X.F,buy,10,1000.00,100.00",
                        "2025-02-03,X.F,sell,12,1440.00,120.00"])
    # 10 matched shares cost 1000; the 2 unmatched ones are costed at their own proceeds
    assert b["realized"]["X.F"]["pnl_eur"] == pytest.approx(1200.0 - 1000.0)
    assert "X.F" not in b["holdings"]


def test_accounting_cost_basis_reconciles_with_positions(tmp_path):
    b = book(tmp_path, ["2025-01-02,X.F,buy,10,1000.00,100.00",
                        "2025-02-03,X.F,buy,10,1200.00,120.00",
                        "2025-03-03,X.F,sell,15,1950.00,130.00",
                        "2025-03-04,Y.F,buy,3,301.00,100.00"])
    s = compute_portfolio_summary(b, {"X.F": 125.0, "Y.F": 110.0})
    acct = accounting(b["transactions"], s["totals"])
    assert acct["net_cost_basis"] == pytest.approx(sum(p["cost_basis"] for p in s["positions"]), abs=0.02)
    assert acct["realized"] == pytest.approx(350.0)


def test_dividend_entitlement_uses_shares_held_before_the_ex_date():
    tx = [{"date": "2025-01-02", "ticker": "X.F", "action": "buy", "shares": 10.0, "price": 1000.0, "pps": 100.0},
          {"date": "2025-03-03", "ticker": "X.F", "action": "buy", "shares": 5.0, "price": 500.0, "pps": 100.0},
          {"date": "2025-06-02", "ticker": "X.F", "action": "sell", "shares": 15.0, "price": 1600.0, "pps": 106.67}]
    divs = {"X.F": [["2024-12-01", 1.0], ["2025-03-03", 0.5], ["2025-06-02", 0.5], ["2025-09-01", 0.5]],
            "Y.F": [["2025-03-03", 9.0]]}
    out = dividend_cash(tx, divs)
    # before the first buy: nothing; a buy ON the ex-date misses it; a sale ON the ex-date keeps it;
    # after the full sale: nothing; a ticker never traded: nothing
    assert [(d["date"], d["ticker"], d["shares"], d["gross"]) for d in out] == [
        ("2025-03-03", "X.F", 10.0, 5.0), ("2025-06-02", "X.F", 15.0, 7.5)]
    assert [d["eur"] for d in out] == pytest.approx([5.0 * (1 - 0.26375), 7.5 * (1 - 0.26375)])


def test_future_ex_dates_are_not_cash_yet():
    tx = [{"date": "2025-01-02", "ticker": "X.F", "action": "buy", "shares": 10.0, "price": 1000.0, "pps": 100.0}]
    divs = {"X.F": [["2025-03-03", 0.5], ["2025-09-01", 0.5]]}
    assert [d["date"] for d in dividend_cash(tx, divs, today=date(2025, 6, 1))] == ["2025-03-03"]
    assert [d["date"] for d in dividend_cash(tx, divs, today=date(2025, 9, 1))] == ["2025-03-03", "2025-09-01"]


def test_dividends_are_cash_in_total_pnl_roi_and_xirr(tmp_path):
    b = book(tmp_path, ["2025-01-02,X.F,buy,10,1000.00,100.00"])
    s = compute_portfolio_summary(b, {"X.F": 100.0})
    divs = [{"date": "2025-06-02", "ticker": "X.F", "shares": 10.0, "per_share": 5.0, "eur": 50.0}]
    a0 = accounting(b["transactions"], s["totals"], today=date(2026, 1, 2))
    a = accounting(b["transactions"], s["totals"], today=date(2026, 1, 2), dividends=divs)
    assert a0["dividends"] == 0.0 and a["dividends"] == pytest.approx(50.0)
    assert a["total_pnl"] == pytest.approx(a0["total_pnl"] + 50.0)
    assert a["simple_roi"] == pytest.approx(5.0)
    assert a["mwr"] > a0["mwr"]


# ── bonus shares (Trade Republic Saveback), net dividends, interest on cash ──────────────────

NET = 1 - 0.26375                     # German Abgeltungsteuer incl. Soli


def test_bonus_adds_shares_and_a_lot_at_its_price(tmp_path):
    bonus = book(tmp_path, ["2025-01-02,X.F,buy,10,1000.00,100.00",
                            "2025-02-03,X.F,bonus,0.1,9.83,98.30"])
    (tmp_path / "b").mkdir()
    bought = book(tmp_path / "b", ["2025-01-02,X.F,buy,10,1000.00,100.00",
                                   "2025-02-03,X.F,buy,0.1,9.83,98.30"])
    h = bonus["holdings"]["X.F"]
    assert h["shares"] == pytest.approx(10.1) and h["avg_cost"] == pytest.approx(1009.83 / 10.1)
    # Trade Republic books Saveback shares at their value: P&L % is the same as an equal buy
    pos = compute_portfolio_summary(bonus, {"X.F": 120.0})["positions"][0]
    same = compute_portfolio_summary(bought, {"X.F": 120.0})["positions"][0]
    assert pos["unrealized_pct"] == same["unrealized_pct"] and pos["cost_basis"] == same["cost_basis"]
    assert [t["action"] for t in bonus["transactions"]] == ["buy", "bonus"]


def test_a_sale_consumes_bonus_lots_fifo_like_any_other(tmp_path):
    b = book(tmp_path, ["2025-01-02,X.F,bonus,1,100.00,100.00",
                        "2025-02-03,X.F,buy,1,120.00,120.00",
                        "2025-03-03,X.F,sell,1,130.00,130.00"])
    assert b["realized"]["X.F"]["pnl_eur"] == pytest.approx(30.0)          # the bonus lot went first
    assert b["holdings"]["X.F"]["avg_cost"] == pytest.approx(120.0)


def test_dividend_cash_counts_bonus_shares():
    tx = [{"date": "2025-01-02", "ticker": "X.F", "action": "buy", "shares": 10.0, "price": 1000.0, "pps": 100.0},
          {"date": "2025-02-03", "ticker": "X.F", "action": "bonus", "shares": 2.0, "price": 200.0, "pps": 100.0}]
    out = dividend_cash(tx, {"X.F": [["2025-03-03", 1.0]]})
    assert out[0]["shares"] == pytest.approx(12.0) and out[0]["gross"] == pytest.approx(12.0)


def test_dividends_are_net_of_german_tax(monkeypatch):
    from monitor import config
    tx = [{"date": "2025-01-02", "ticker": "X.F", "action": "buy", "shares": 10.0, "price": 1000.0, "pps": 100.0}]
    d = dividend_cash(tx, {"X.F": [["2025-03-03", 2.0]]})[0]
    assert d["gross"] == pytest.approx(20.0) and d["eur"] == pytest.approx(20.0 * NET)
    monkeypatch.setattr(config, "DIVIDEND_TAX", 0.0)                       # read at call time
    assert dividend_cash(tx, {"X.F": [["2025-03-03", 2.0]]})[0]["eur"] == pytest.approx(20.0)


def test_load_interest_reads_dated_euro_amounts_sorted(tmp_path):
    from monitor.portfolio.ledger import load_interest
    p = tmp_path / "interest.csv"
    p.write_text("Date,Amount\n2026-02-01,4.10\n2026-01-01,3.95\n", encoding="utf-8")
    assert load_interest(p) == [{"date": "2026-01-01", "eur": 3.95}, {"date": "2026-02-01", "eur": 4.10}]


def test_load_interest_missing_file_is_no_interest(tmp_path):
    from monitor.portfolio.ledger import load_interest
    assert load_interest(tmp_path / "interest.csv") == []


def test_load_interest_skips_a_malformed_row_with_a_warning(tmp_path, caplog):
    import logging

    from monitor.portfolio.ledger import load_interest
    p = tmp_path / "interest.csv"
    p.write_text("Date,Amount\n2026-01-01,3.95\n2026-02-01,abc\nnot-a-date,1.00\n2026-03-01,\n2026-04-01,2.50\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        out = load_interest(p)
    assert out == [{"date": "2026-01-01", "eur": 3.95}, {"date": "2026-04-01", "eur": 2.50}]
    assert len(_warnings(caplog)) == 3


def _warnings(caplog):
    import logging
    return [r for r in caplog.records if r.levelno == logging.WARNING and r.name == "monitor.portfolio.ledger"]


def test_load_interest_rejects_a_row_with_the_wrong_number_of_fields(tmp_path, caplog):
    """An unquoted German decimal comma splits the amount into two fields: never read 3,95 as 3."""
    import logging

    from monitor.portfolio.ledger import load_interest
    p = tmp_path / "interest.csv"
    p.write_text("Date,Amount\n2026-01-01,3,95\n2026-02-01\n2026-03-01,2.50\n", encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        out = load_interest(p)
    assert out == [{"date": "2026-03-01", "eur": 2.50}]
    assert len(_warnings(caplog)) == 2


def test_load_interest_accepts_a_quoted_decimal_comma(tmp_path, caplog):
    import logging

    from monitor.portfolio.ledger import load_interest
    p = tmp_path / "interest.csv"
    p.write_text('Date,Amount\n2026-01-01,"3,95"\n2026-02-01,"1.234,56"\n', encoding="utf-8")
    with caplog.at_level(logging.WARNING):
        out = load_interest(p)
    assert out == [{"date": "2026-01-01", "eur": 3.95}]            # ambiguous thousands: skipped, not guessed
    assert len(_warnings(caplog)) == 1


def test_load_interest_tolerates_an_excel_bom_and_spaced_header(tmp_path):
    from monitor.portfolio.ledger import load_interest
    p = tmp_path / "interest.csv"
    p.write_bytes("\ufeffDate , Amount\n2026-01-01,3.95\n".encode("utf-8"))
    assert load_interest(p) == [{"date": "2026-01-01", "eur": 3.95}]


def test_bonus_is_not_a_deposit_nor_a_cash_flow_but_is_cost_basis_and_gain(tmp_path):
    from monitor.portfolio.analytics import xirr
    b = book(tmp_path, ["2025-01-02,X.F,buy,10,1000.00,100.00",
                        "2025-06-02,X.F,bonus,0.1,10.00,100.00",
                        "2025-09-01,X.F,sell,2,240.00,120.00"])
    s = compute_portfolio_summary(b, {"X.F": 120.0})
    a = accounting(b["transactions"], s["totals"], today=date(2026, 1, 2))
    assert a["gross_deposits"] == pytest.approx(1000.0) and a["bonus"] == pytest.approx(10.0)
    assert a["net_invested"] == pytest.approx(1000.0 - 240.0)
    assert a["net_cost_basis"] == pytest.approx(sum(p["cost_basis"] for p in s["positions"]), abs=0.02)
    assert a["unrealized"] == pytest.approx(s["totals"]["unrealized_pnl"], abs=0.02)
    value = s["totals"]["current_value"]
    assert a["total_pnl"] == pytest.approx(value + 240.0 - 1000.0)          # value + sells + dividends − deposits
    assert a["simple_roi"] == pytest.approx((value + 240.0) / 1000.0 * 100 - 100)
    want = xirr([(date(2025, 1, 2), -1000.0), (date(2025, 9, 1), 240.0), (date(2026, 1, 2), value)])
    assert a["mwr"] == pytest.approx(want)


def test_interest_is_reported_but_never_in_pnl_roi_or_xirr(tmp_path):
    b = book(tmp_path, ["2025-01-02,X.F,buy,10,1000.00,100.00"])
    s = compute_portfolio_summary(b, {"X.F": 110.0})
    a0 = accounting(b["transactions"], s["totals"], today=date(2026, 1, 2))
    a = accounting(b["transactions"], s["totals"], today=date(2026, 1, 2),
                   interest=[{"date": "2025-06-01", "eur": 3.5}, {"date": "2025-07-01", "eur": 4.0}])
    assert a0["interest"] == 0.0 and a["interest"] == pytest.approx(7.5)
    for k in ("total_pnl", "simple_roi", "mwr", "mwr_cumulative", "net_cost_basis"):
        assert a[k] == pytest.approx(a0[k]), k


def test_a_paid_dividends_row_may_name_only_its_pay_date_and_ticker(tmp_path):
    from monitor.portfolio.ledger import load_paid_dividends
    f = tmp_path / "dividends.csv"
    f.write_text("PayDate,Ticker,Shares,Gross,Tax,Net\n2026-10-08,AAA.F,,,,\n2026-07-09,AAA.F,3,2.92,0.77,2.15\n",
                 encoding="utf-8")
    assert load_paid_dividends(f) == [
        {"pay": "2026-07-09", "ticker": "AAA.F", "shares": 3.0, "gross": 2.92, "tax": 0.77, "net": 2.15},
        {"pay": "2026-10-08", "ticker": "AAA.F", "shares": None, "gross": None, "tax": None, "net": None}]
