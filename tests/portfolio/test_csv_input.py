"""input/portfolio.csv and input/interest.csv as Excel writes them: UTF-8 with or without a BOM, `,` or `;`
between fields (sniffed from the header line), a decimal comma in a `;` file, dates as YYYY-MM-DD, DD.MM.YYYY
or DD/MM/YYYY. What cannot be read safely (a thousands separator, a split decimal comma, a bad date) is
one line naming the file, the row and the column — never a guess."""
import logging
from pathlib import Path

import pytest

from monitor.portfolio.ledger import CSVError, load_interest, parse_portfolio

EX = Path(__file__).resolve().parent.parent.parent / "examples"
CSV, INTEREST = EX / "portfolio.example.csv", EX / "interest.example.csv"
HEAD = "Date,Ticker,Action,Shares,Price,PricePerShare"


def _excel(text: str, *, sep=";", dates="dmy.", bom=True, crlf=True) -> bytes:
    """`text` (an example file) re-written the way a European Excel saves it."""
    out = []
    for i, line in enumerate(text.strip().splitlines()):
        cells = line.split(",")
        if i:
            y, m, d = cells[0].split("-")
            cells[0] = {"dmy.": f"{d}.{m}.{y}", "dmy/": f"{d}/{m}/{y}", "iso": cells[0]}[dates]
            if sep == ";":
                cells[1:] = [c.replace(".", ",") if c.replace(".", "").isdigit() else c for c in cells[1:]]
        out.append(sep.join(cells))
    out.append(sep * (len(cells) - 1))                                  # Excel's empty trailing row
    body = ("\r\n" if crlf else "\n").join(out) + ("\r\n" if crlf else "\n")
    return (b"\xef\xbb\xbf" if bom else b"") + body.encode("utf-8")


def _write(tmp_path, data, name="portfolio.csv") -> Path:
    p = tmp_path / name
    p.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return p


@pytest.mark.parametrize("form", [
    dict(sep=";", dates="dmy."),                  # German Excel: 15.01.2025;SAP.DE;buy;4;961,00;240,00
    dict(sep=";", dates="dmy/"),                  # 15/01/2025
    dict(sep=";", dates="iso"),
    dict(sep=",", dates="iso", bom=True, crlf=True),
    dict(sep=",", dates="dmy.", bom=False, crlf=False),
])
def test_the_example_portfolio_reads_the_same_in_every_excel_form(tmp_path, form):
    assert parse_portfolio(_write(tmp_path, _excel(CSV.read_text(encoding="utf-8"), **form))) == parse_portfolio(CSV)


def test_the_example_interest_reads_the_same_in_excel_form(tmp_path, caplog):
    p = _write(tmp_path, _excel(INTEREST.read_text(encoding="utf-8")), "interest.csv")
    with caplog.at_level(logging.WARNING):
        assert load_interest(p) == load_interest(INTEREST)
    assert "skipped" not in caplog.text


def test_the_brief_example_row(tmp_path):
    p = _write(tmp_path, "Date;Ticker;Action;Shares;Price;PricePerShare\n2025-01-15;SAP.DE;buy;4;961,00;240,00\n")
    t = parse_portfolio(p)["transactions"][0]
    assert (t["date"], t["ticker"], t["shares"], t["price"], t["pps"]) == ("2025-01-15", "SAP.DE", 4.0, 961.0, 240.0)


def test_a_decimal_point_still_works_in_a_semicolon_file(tmp_path):
    p = _write(tmp_path, "Date;Ticker;Action;Shares;Price;PricePerShare\n"
                         "2025-01-15;SAP.DE;buy;0.125;30.01;240.08\n2025-01-16;SAP.DE;buy;13.513513;63.40;4.69\n")
    tx = parse_portfolio(p)["transactions"]
    assert [(t["shares"], t["price"]) for t in tx] == [(0.125, 30.01), (13.513513, 63.40)]


def _error(tmp_path, text, name="portfolio.csv") -> str:
    with pytest.raises(CSVError) as e:
        parse_portfolio(_write(tmp_path, text, name))
    msg = str(e.value)
    assert "\n" not in msg and msg.startswith(name), msg
    return msg


@pytest.mark.parametrize("text, column", [
    ("Date;Ticker;Action;Shares;Price;PricePerShare\n2025-01-02;X.F;buy;1;10,00;10,00\n"
     "2025-01-15;SAP.DE;buy;4;1.234,56;308,64\n", "Price"),
    ("Date;Ticker;Action;Shares;Price;PricePerShare\n2025-01-02;X.F;buy;1;10,00;10,00\n"
     "2025-01-15;SAP.DE;buy;4;1.234;308,50\n", "Price"),                 # 1.234 in a decimal-comma file
    (HEAD + "\n2025-01-02,X.F,buy,1,10.00,10.00\n2025-01-15,SAP.DE,buy,4,\"1,234.56\",308.64\n", "Price"),
    (HEAD + "\n2025-01-02,X.F,buy,1,10.00,10.00\n2025-01-15,SAP.DE,buy,4,\"1,234\",308.50\n", "Price"),
    ("Date;Ticker;Action;Shares;Price;PricePerShare\n2025-01-02;X.F;buy;1;10,00;10,00\n"
     "2025-01-15;SAP.DE;buy;1 000;961,00;0,96\n", "Shares"),
    ("Date;Ticker;Action;Shares;Price;PricePerShare\n2025-01-02;X.F;buy;1;10,00;10,00\n"
     "2025-01-15;SAP.DE;buy;4;1'234.56;308,64\n", "Price"),
])
def test_a_thousands_separator_is_rejected_naming_file_row_and_column(tmp_path, text, column):
    msg = _error(tmp_path, text)
    assert "row 3" in msg and f"column {column}" in msg and "thousands" in msg


def test_an_unquoted_decimal_comma_in_a_comma_file_is_rejected_not_misread(tmp_path):
    msg = _error(tmp_path, HEAD + "\n2025-01-15,SAP.DE,buy,4,961,00,240,00\n")
    assert "row 2" in msg and "8 fields" in msg and "6" in msg and ";" in msg


@pytest.mark.parametrize("value, hint", [("2025/01/15", "YYYY-MM-DD"), ("01/15/2025", "day/month/year"),
                                         ("31.02.2025", "YYYY-MM-DD"), ("15.01.25", "YYYY-MM-DD"), ("", "empty")])
def test_a_bad_date_names_row_and_column(tmp_path, value, hint):
    msg = _error(tmp_path, f"{HEAD}\n2025-01-02,X.F,buy,1,10.00,10.00\n{value},SAP.DE,buy,4,961.00,240.00\n")
    assert "row 3" in msg and "column Date" in msg and hint in msg


def test_slash_dates_are_day_month_year(tmp_path):
    p = _write(tmp_path, f"{HEAD}\n01/02/2025,SAP.DE,buy,4,961.00,240.00\n")
    assert parse_portfolio(p)["transactions"][0]["date"] == "2025-02-01"


@pytest.mark.parametrize("value", ["", "abc", "nan", "inf", "1e3", "1_000"])
def test_a_number_that_is_not_one_names_row_and_column(tmp_path, value):
    msg = _error(tmp_path, f"{HEAD}\n2025-01-15,SAP.DE,buy,{value},961.00,240.00\n")
    assert "row 2" in msg and "column Shares" in msg


def test_a_missing_column_names_it(tmp_path):
    msg = _error(tmp_path, "Date,Ticker,Action,Shares,Total,PricePerShare\n2025-01-15,SAP.DE,buy,4,961.00,240.00\n")
    assert "Price" in msg and "missing" in msg and HEAD in msg


def test_a_file_that_is_not_utf8_says_how_to_save_it(tmp_path):
    msg = _error(tmp_path, (HEAD + "\n2025-01-15,SAP.DE,buy,4,961.00,240.00\n# Gebühr\n").encode("cp1252"))
    assert "UTF-8" in msg


def test_spaces_around_values_and_header_names_are_fine(tmp_path):
    p = _write(tmp_path, " Date ; Ticker ;Action;Shares;Price;PricePerShare\n 15.01.2025 ; SAP.DE ; Buy ; 4 ; 961,00 ; 240,00 \n")
    t = parse_portfolio(p)["transactions"][0]
    assert (t["date"], t["ticker"], t["action"], t["price"]) == ("2025-01-15", "SAP.DE", "buy", 961.0)


def test_interest_skips_a_bad_row_with_a_warning_naming_file_row_and_column(tmp_path, caplog):
    p = _write(tmp_path, "Date;Amount\n31.01.2025;1,12\n28.02.2025;1.234,56\n31.03.2025;1,48\n", "interest.csv")
    with caplog.at_level(logging.WARNING):
        assert load_interest(p) == [{"date": "2025-01-31", "eur": 1.12}, {"date": "2025-03-31", "eur": 1.48}]
    msg = next(r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)
    assert "interest.csv" in msg and "row 3" in msg and "column Amount" in msg and "thousands" in msg


def test_interest_that_cannot_be_read_at_all_is_one_warning_and_no_interest(tmp_path, caplog):
    p = _write(tmp_path, "Datum;Betrag\n31.01.2025;1,12\n", "interest.csv")
    with caplog.at_level(logging.WARNING):
        assert load_interest(p) == []
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1
    assert "Date" in caplog.text and "missing" in caplog.text
