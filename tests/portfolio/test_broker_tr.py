"""Trade Republic's own transaction export (app → Profile → Transactions → Export) read through TRADES' import:
trades (buys incl. savings plans, sells, Saveback and free shares as BONUS), dividends with their pay date, tax
and net, interest — and every cash move that is not the portfolio skipped and counted. Invented rows only."""
from datetime import date

import pytest

from monitor.portfolio import trades as T

HEAD = ('"datetime","date","account_type","category","type","asset_class","name","symbol","shares","price","amount",'
        '"fee","tax","currency","original_amount","original_currency","fx_rate","description","transaction_id",'
        '"counterparty_name","counterparty_iban","payment_reference","mcc_code"')


def row(day, cat, typ, cls="", name="", isin="", shares="", price="", amount="", fee="", tax="", desc="", tid="x"):
    return (f'"{day}T10:00:00.000Z","{day}","DEFAULT","{cat}","{typ}","{cls}","{name}","{isin}","{shares}","{price}",'
            f'"{amount}","{fee}","{tax}","EUR","","","","{desc}","{tid}","","","",""')


ISINS = {"DE000AAA0001": "AAA.F", "IE00BBB00001": "BBB.AS", "DE000CCC0001": "CCC.DE"}
EXPORT = "\n".join([HEAD,
    row("2025-01-06", "TRADING", "BUY", "STOCK", "Alpha AG", "DE000AAA0001", "10", "100", "-1000.00", "-1.00", "-0.50",
        tid="t1"),                                                    # a transaction tax on the buy (Italy: FTT)
    row("2025-02-03", "TRADING", "BUY", "FUND", "World ETF", "IE00BBB00001", "2.5", "40", "-100.00", desc="Savings plan", tid="t2"),
    row("2025-02-04", "CASH", "BENEFITS_SAVEBACK", amount="15.00", tid="t3"),
    row("2025-02-04", "TRADING", "BUY", "FUND", "World ETF", "IE00BBB00001", "0.37", "40.54", "-15.00", tid="t4"),
    row("2025-03-03", "TRADING", "SELL", "STOCK", "Alpha AG", "DE000AAA0001", "4", "120", "480.00", "-1.00", "-12.50", tid="t5"),
    row("2025-04-01", "CASH", "INTEREST_PAYMENT", amount="3.21", tid="t6"),
    row("2025-04-10", "CASH", "DIVIDEND", "STOCK", "Alpha AG", "DE000AAA0001", "6", "2.00", "8.84", tax="-3.16", tid="t7"),
    row("2025-05-02", "DELIVERY", "FREE_RECEIPT", "STOCK", "Gamma SE", "DE000CCC0001", "1", "50", "0", tid="t8"),
    row("2025-05-03", "CASH", "CUSTOMER_INBOUND", amount="500.00", tid="t9"),
    row("2025-05-04", "CASH", "CARD_TRANSACTION", amount="-12.40", tid="t10"),
    row("2025-05-05", "TRADING", "BUY", "STOCK", "Nowhere Inc", "US000NOPE001", "1", "10", "-10.00", tid="t11")])


def resolve(code, name="", alias=""):
    return ISINS.get(code)


def read(text=EXPORT):
    return T.parse_bulk(text, date(2026, 6, 30), isin=resolve)


def test_the_export_is_recognised_by_its_header():
    out = read()
    assert out["broker"] == "TRADE REPUBLIC" and out["header"] and out["error"] is None


def test_buys_sells_saveback_and_free_shares_become_trades():
    trades = [r["trade"] for r in read()["rows"] if r["trade"]]
    assert trades == [
        {"date": "2025-01-06", "ticker": "AAA.F", "action": "buy", "shares": 10.0, "price": 1001.5, "pps": 100.0},
        {"date": "2025-02-03", "ticker": "BBB.AS", "action": "buy", "shares": 2.5, "price": 100.0, "pps": 40.0},
        {"date": "2025-02-04", "ticker": "BBB.AS", "action": "bonus", "shares": 0.37, "price": 15.0, "pps": 40.54},
        {"date": "2025-03-03", "ticker": "AAA.F", "action": "sell", "shares": 4.0, "price": 479.0, "pps": 120.0},
        {"date": "2025-05-02", "ticker": "CCC.DE", "action": "bonus", "shares": 1.0, "price": 50.0, "pps": 50.0}]


def test_a_buy_pays_its_fee_a_sell_nets_it_and_the_tax_on_a_gain_is_no_cost():
    sell = next(r for r in read()["rows"] if r["trade"] and r["trade"]["action"] == "sell")
    assert sell["trade"]["price"] == 479.0 and any("TAX" in w for w in sell["warnings"])


def test_dividends_keep_pay_date_tax_and_net():
    assert read()["dividends"] == [{"pay": "2025-04-10", "ticker": "AAA.F", "shares": 6.0, "gross": 12.0,
                                     "tax": 3.16, "net": 8.84}]


def test_interest_is_kept_and_cash_moves_are_skipped_and_counted():
    out = read()
    assert out["interest"] == [{"date": "2025-04-01", "eur": 3.21}]
    note = " ".join(out["notes"])
    assert "TRADE REPUBLIC EXPORT" in note and "1 DIVIDEND" in note and "1 INTEREST" in note
    assert "2 CASH MOVES SKIPPED" in note                                  # the deposit and the card payment


def test_one_orders_fills_booked_apart_are_one_trade():
    """Trade Republic books an order's fractional and whole parts as two rows (0.39 without a fee, 24 with
    it) — one trade, as you would write it."""
    text = "\n".join([HEAD,
        row("2025-01-06", "TRADING", "BUY", "STOCK", "Alpha AG", "DE000AAA0001", "0.39", "4.10", "-1.60", tid="a"),
        row("2025-01-06", "TRADING", "BUY", "STOCK", "Alpha AG", "DE000AAA0001", "24", "4.10", "-98.40", "-1.00",
            "-0.10", tid="b")])
    rows = read(text)["rows"]
    assert len(rows) == 1 and (rows[0]["line"], rows[0]["end"]) == (2, 3)
    t = rows[0]["trade"]
    assert (t["shares"], t["price"], t["pps"]) == (24.39, 101.1, 4.1)


def test_an_isin_without_a_ticker_is_an_error_on_its_row_only():
    bad = [r for r in read()["rows"] if r["error"]]
    assert len(bad) == 1 and "US000NOPE001" in bad[0]["error"] and "[isins]" in bad[0]["error"]
