"""The book keeps what the switching cost needs: the open FIFO lots and the gain of every sale."""
import pytest

from monitor.portfolio.ledger import parse_portfolio_text

CSV = """Date,Ticker,Action,Shares,Price,PricePerShare
2025-01-02,AAA.F,buy,10,101,10.1
2025-02-03,AAA.F,buy,10,121,12.1
2025-03-03,BBB.F,buy,4,40,10
2026-03-02,AAA.F,sell,15,240,16
2026-04-01,BBB.F,sell,4,48,12
"""


def test_the_book_keeps_its_open_fifo_lots():
    book = parse_portfolio_text(CSV)
    assert book["lots"] == {"AAA.F": [[5.0, pytest.approx(12.1)]]}      # 10 of the first and 5 of the second sold


def test_every_sale_records_its_fifo_gain_and_date():
    book = parse_portfolio_text(CSV)
    assert [(s["date"], s["ticker"]) for s in book["sales"]] == [("2026-03-02", "AAA.F"), ("2026-04-01", "BBB.F")]
    assert book["sales"][0]["pnl"] == pytest.approx(240 - (101 + 5 * 12.1))
    assert book["sales"][1]["pnl"] == pytest.approx(8.0)
