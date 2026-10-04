"""monitor.portfolio.tradebook — input/portfolio.csv as TRADES edits it: every write names the version it was made
on (409 when the file changed meanwhile), checks the whole resulting file, keeps a backup of the old one
(input/backups/, the newest 20) and replaces it atomically in the canonical form. Temp dirs only."""
import os
import shutil
from datetime import date
from pathlib import Path

import pytest

from monitor.portfolio import trades as T
from monitor.portfolio.ledger import parse_portfolio
from monitor.portfolio.tradebook import Conflict, Invalid, Missing, TradeBook

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "portfolio.example.csv"
TODAY = date(2026, 10, 4)
HEAD = "Date,Ticker,Action,Shares,Price,PricePerShare"


@pytest.fixture
def book(tmp_path):
    csv = tmp_path / "input" / "portfolio.csv"
    csv.parent.mkdir()
    shutil.copy(EXAMPLE, csv)
    quotes = {"SAP.DE": {"price": 250.0}, "NEW.DE": {"price": 10.0}, "KO": {"price": 60.0, "ccy": "USD"}}
    asked = []

    def quote(t):
        asked.append(t)
        return quotes.get(t)
    b = TradeBook(csv, example=EXAMPLE, quote=quote, today=lambda: TODAY,
                  isin=lambda code: {"DE0007030009": "RHM.DE"}.get(code))
    b.asked = asked
    return b


def test_read_lists_the_rows_with_ids_and_the_files_version(book):
    s = book.read()
    assert s["exists"] and s["example"] and s["error"] is None
    assert [r["ticker"] for r in s["rows"]][:3] == ["IWDA.AS", "SAP.DE", "ALV.DE"]
    assert [r["id"] for r in s["rows"]] == T.ids(parse_portfolio(EXAMPLE)["transactions"])
    assert s["etag"] == T.etag(EXAMPLE.read_bytes())


def test_add_slots_the_trade_in_by_date_backs_up_and_writes_canonically(book):
    s = book.read()
    res = book.add(s["etag"], {"ticker": "sap.de", "action": "buy", "shares": "4", "pps": "240", "date": "02.01.2026"})
    assert res["text"] == "BUY 4 SAP.DE · €240.00/sh = €960.00" and res["warnings"] == []
    rows = book.read()["rows"]
    at = [r["id"] for r in rows].index(res["id"])
    assert (rows[at - 1]["date"], rows[at]["date"], rows[at + 1]["date"]) == ("2025-10-01", "2026-01-02", "2026-01-05")
    assert book.csv.read_bytes() == T.to_csv([{k: r[k] for k in ("date", "ticker", "action", "shares", "price", "pps")}
                                              for r in rows]).encode("utf-8")
    assert b"\r\n" not in book.csv.read_bytes()
    backups = list((book.csv.parent / "backups").glob("portfolio-*.csv"))
    assert len(backups) == 1 and backups[0].read_bytes() == EXAMPLE.read_bytes()       # the old file, as it was
    assert res["etag"] == book.read()["etag"] and not book.read()["example"]


def test_a_write_on_an_old_version_is_refused(book):
    old = book.read()["etag"]
    book.add(old, {"ticker": "SAP.DE", "action": "buy", "shares": 1, "pps": 200})
    with pytest.raises(Conflict):
        book.add(old, {"ticker": "SAP.DE", "action": "buy", "shares": 1, "pps": 200})
    book.csv.write_text(HEAD + "\n", encoding="utf-8")                  # edited in Excel meanwhile
    with pytest.raises(Conflict):
        book.reset(book.read()["etag"][:-1] + "x")


def test_a_sale_beyond_the_shares_held_or_a_future_date_is_refused_and_nothing_written(book):
    s = book.read()
    before = book.csv.read_bytes()
    with pytest.raises(Invalid, match="^SELL 3 SAP.DE ON 2025-09-02: ONLY 2 HELD THEN$"):
        book.add(s["etag"], {"ticker": "SAP.DE", "action": "sell", "shares": 3, "pps": 230, "date": "2025-09-02"})
    with pytest.raises(Invalid, match="^DATE 2026-10-05 IS IN THE FUTURE$"):
        book.add(s["etag"], {"ticker": "SAP.DE", "action": "buy", "shares": 1, "pps": 1, "date": "2026-10-05"})
    assert book.csv.read_bytes() == before and not (book.csv.parent / "backups").exists()


def test_warnings_never_block_a_new_ticker_without_a_quote_a_foreign_currency_a_duplicate(book):
    e = book.read()["etag"]
    r = book.add(e, {"ticker": "ZZZ.F", "action": "buy", "shares": 1, "total": 10, "date": "2026-01-02"})
    assert r["warnings"] == ["NO YAHOO QUOTE FOR ZZZ.F — CHECK THE TICKER (SAVED ANYWAY)"]
    r = book.add(r["etag"], {"ticker": "KO", "action": "buy", "shares": 1, "total": 50, "date": "2026-01-02"})
    assert r["warnings"] == ["YAHOO QUOTES KO IN USD — PRICES ARE TAKEN AS EUROS: USE ITS EUR LISTING (.DE, .F …)"]
    r = book.add(r["etag"], {"ticker": "SAP.DE", "action": "buy", "shares": 4, "total": 961, "date": "2024-12-16"})
    assert r["warnings"] == ["SAME AS A TRADE ALREADY IN YOUR FILE"]
    assert book.asked == ["ZZZ.F", "KO"]                                # a ticker already in the file: no quote call


def test_edit_replaces_in_place_and_keeps_the_display_price_when_the_price_is_untouched(book):
    s = book.read()
    sap = s["rows"][1]
    res = book.update(s["etag"], sap["id"], {"ticker": "SAP.DE", "action": "buy", "shares": 4, "total": 961,
                                             "pps": 240, "keep_pps": True, "date": "2024-12-17"})
    rows = book.read()["rows"]
    assert rows[1]["date"] == "2024-12-17" and rows[1]["pps"] == 240.0 and rows[1]["id"] == res["id"] != sap["id"]
    res = book.update(res["etag"], res["id"], {"ticker": "SAP.DE", "action": "buy", "shares": 5, "pps": 240,
                                               "date": "2025-08-01"})
    rows = book.read()["rows"]
    assert [r["id"] for r in rows].index(res["id"]) == 8 and rows[8]["price"] == 1200.0         # moved by its date
    with pytest.raises(Invalid, match="^SELL 2 SAP.DE ON 2025-09-01: ONLY 0 HELD THEN$"):        # after its sale
        book.update(res["etag"], res["id"], {"ticker": "SAP.DE", "action": "buy", "shares": 5, "pps": 240,
                                             "date": "2025-12-01"})
    with pytest.raises(Missing):
        book.update(res["etag"], "nope", {"ticker": "SAP.DE", "action": "buy", "shares": 1, "pps": 1})


def test_delete_refuses_to_strand_a_later_sale(book):
    s = book.read()
    air_buy = next(r for r in s["rows"] if r["ticker"] == "AIR.PA" and r["action"] == "buy")
    with pytest.raises(Invalid, match="^SELL 6 AIR.PA ON 2026-01-05: ONLY 0 HELD THEN$"):
        book.delete(s["etag"], air_buy["id"])
    last = s["rows"][-1]
    res = book.delete(s["etag"], last["id"])
    assert len(book.read()["rows"]) == len(s["rows"]) - 1 and res["text"].startswith("BUY 2 SIE.DE")
    with pytest.raises(Missing):
        book.delete(res["etag"], last["id"])


def test_import_appends_the_good_rows_and_replace_swaps_the_file(book):
    text = ("Date,Ticker,Action,Shares,Total\n2026-02-01,NEW.DE,buy,10,100\n2026-02-02,NEW.DE,sell,20,250\n"
            "2026-02-03,NEW.DE,sell,5,60\n")
    pre = book.preview(text, "append")
    assert [r["error"] for r in pre["rows"]] == [None, "SELL 20 NEW.DE ON 2026-02-02: ONLY 10 HELD THEN", None]
    assert (pre["ok"], pre["bad"]) == (2, 1)
    s = book.read()
    res = book.import_text(s["etag"], text, "append")
    assert (res["added"], res["skipped"]) == (2, 1) and len(book.read()["rows"]) == len(s["rows"]) + 2
    res = book.import_text(res["etag"], "15.01.2025;SAP.DE;buy;4;@240,00\n", "replace")
    rows = book.read()["rows"]
    assert len(rows) == 1 and (rows[0]["price"], rows[0]["date"]) == (960.0, "2025-01-15")
    with pytest.raises(Invalid, match="^NOTHING TO ADD — EVERY ROW HAS AN ERROR$"):
        book.import_text(res["etag"], "2025-01-15,SAP.DE,hold,4,961\n", "append")
    with pytest.raises(Invalid, match="^NOTHING TO READ"):
        book.import_text(res["etag"], "\n", "append")


def test_isins_resolve_through_the_lookup(book):
    pre = book.preview("date,isin,type,shares,amount\n2025-01-15,DE0007030009,buy,1,700\n", "append")
    assert pre["rows"][0]["trade"]["ticker"] == "RHM.DE"


def test_start_fresh_leaves_the_header_and_a_backup(book):
    res = book.reset(book.read()["etag"])
    assert book.csv.read_text(encoding="utf-8") == HEAD + "\n" and res["removed"] == 15
    s = book.read()
    assert s["rows"] == [] and not s["example"] and s["error"] is None
    assert len(list((book.csv.parent / "backups").iterdir())) == 1


def test_no_file_yet_reads_empty_and_the_first_trade_creates_it(tmp_path):
    b = TradeBook(tmp_path / "input" / "portfolio.csv", example=EXAMPLE, today=lambda: TODAY)
    s = b.read()
    assert (s["exists"], s["rows"], s["etag"], s["example"]) == (False, [], "absent", False)
    b.add("absent", {"ticker": "SAP.DE", "action": "buy", "shares": 1, "pps": 240})
    assert b.csv.read_text(encoding="utf-8") == f"{HEAD}\n2026-10-04,SAP.DE,buy,1,240.00,240.00\n"
    assert not (b.csv.parent / "backups").exists()                       # nothing was there to keep


def test_a_file_that_cannot_be_read_is_shown_and_only_replace_or_start_fresh_write(book):
    book.csv.write_text(HEAD + "\n2025-01-15,SAP.DE,buy,x,961.00,240.00\n", encoding="utf-8")
    s = book.read()
    assert s["rows"] == [] and s["error"] == "portfolio.csv row 2, column Shares: 'x' is not a number"
    with pytest.raises(Invalid, match="^YOUR FILE HAS AN ERROR"):
        book.add(s["etag"], {"ticker": "SAP.DE", "action": "buy", "shares": 1, "pps": 1})
    assert book.preview("2025-01-15,SAP.DE,buy,4,961\n", "append")["error"].startswith("YOUR FILE HAS AN ERROR")
    res = book.import_text(s["etag"], "2025-01-15,SAP.DE,buy,4,961\n", "replace")
    assert len(book.read()["rows"]) == 1 and res["added"] == 1


def test_an_excel_saved_file_keeps_its_ids_and_is_rewritten_canonically(book):
    excel = "﻿Date;Ticker;Action;Shares;Price;PricePerShare\r\n16.12.2024;SAP.DE;buy;4;961,00;240,00\r\n;;;;;\r\n"
    book.csv.write_bytes(excel.encode("utf-8"))
    s = book.read()
    res = book.update(s["etag"], s["rows"][0]["id"], {"ticker": "SAP.DE", "action": "buy", "shares": 4, "total": 961,
                                                      "pps": 240, "keep_pps": True, "date": "2024-12-16"})
    assert res["id"] == s["rows"][0]["id"]                                # the same trade, the same id
    assert book.csv.read_text(encoding="utf-8") == f"{HEAD}\n2024-12-16,SAP.DE,buy,4,961.00,240.00\n"
    assert next((book.csv.parent / "backups").iterdir()).read_bytes() == excel.encode("utf-8")


def test_only_the_newest_20_backups_are_kept(book):
    e = book.read()["etag"]
    for i in range(23):
        e = book.add(e, {"ticker": "SAP.DE", "action": "buy", "shares": 1, "pps": 100 + i, "date": "2026-01-02"})["etag"]
    kept = list((book.csv.parent / "backups").iterdir())
    assert len(kept) == 20 and all(p.name.startswith("portfolio-") and p.suffix == ".csv" for p in kept)
    assert not any(p.read_bytes() == EXAMPLE.read_bytes() for p in kept)        # the oldest went first
    assert max(len(parse_portfolio(p)["transactions"]) for p in kept) == 15 + 22  # the newest is there
    assert sorted(os.listdir(book.csv.parent)) == ["backups", "portfolio.csv"]  # no temp or lock file left
