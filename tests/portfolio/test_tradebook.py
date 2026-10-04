"""monitor.portfolio.tradebook — input/portfolio.csv as TRADES edits it: every write names the version it was made
on (409 when the file changed meanwhile), checks the whole resulting file, keeps a backup of the old one
(input/backups/: a ring of the newest 20, the file before the first write and before each START FRESH or REPLACE
pinned) and replaces it atomically in the canonical form — your own extra columns carried along. UNDO puts back
the file as it was before the last write. Temp dirs only."""
import os
import shutil
from datetime import date
from pathlib import Path

import pytest

from monitor.portfolio import trades as T
from monitor.portfolio.ledger import parse_portfolio
from monitor.portfolio.tradebook import Blocked, Conflict, Invalid, Missing, TradeBook

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
    ring = list((book.csv.parent / "backups").glob("portfolio-[0-9]*.csv"))
    assert [p.name for p in ring] == ["portfolio-0000001.csv"] and ring[0].read_bytes() == EXAMPLE.read_bytes()
    assert (book.csv.parent / "backups" / "portfolio-original.csv").read_bytes() == EXAMPLE.read_bytes()   # pinned
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
    assert sorted(p.name for p in (book.csv.parent / "backups").glob("*.csv")) == [
        "before-start-fresh-0001.csv", "portfolio-original.csv"]           # pinned, never the 20-copy ring


def test_no_file_yet_reads_empty_and_the_first_trade_creates_it(tmp_path):
    b = TradeBook(tmp_path / "input" / "portfolio.csv", example=EXAMPLE, today=lambda: TODAY)
    s = b.read()
    assert (s["exists"], s["rows"], s["etag"], s["example"]) == (False, [], "absent", False)
    b.add("absent", {"ticker": "SAP.DE", "action": "buy", "shares": 1, "pps": 240})
    assert b.csv.read_text(encoding="utf-8") == f"{HEAD}\n2026-10-04,SAP.DE,buy,1,240.00,240.00\n"
    assert [p.name for p in (b.csv.parent / "backups").iterdir()] == ["undo.json"]   # nothing was there to keep


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
    excel = "\ufeffDate;Ticker;Action;Shares;Price;PricePerShare\r\n16.12.2024;SAP.DE;buy;4;961,00;240,00\r\n;;;;;\r\n"
    book.csv.write_bytes(excel.encode("utf-8"))
    s = book.read()
    res = book.update(s["etag"], s["rows"][0]["id"], {"ticker": "SAP.DE", "action": "buy", "shares": 4, "total": 961,
                                                      "pps": 240, "keep_pps": True, "date": "2024-12-16"})
    assert res["id"] == s["rows"][0]["id"]                                # the same trade, the same id
    assert book.csv.read_text(encoding="utf-8") == f"{HEAD}\n2024-12-16,SAP.DE,buy,4,961.00,240.00\n"
    assert (book.csv.parent / "backups" / "portfolio-original.csv").read_bytes() == excel.encode("utf-8")


def test_only_the_newest_20_backups_are_kept(book):
    e = book.read()["etag"]
    for i in range(23):
        e = book.add(e, {"ticker": "SAP.DE", "action": "buy", "shares": 1, "pps": 100 + i, "date": "2026-01-02"})["etag"]
    kept = sorted((book.csv.parent / "backups").glob("portfolio-[0-9]*.csv"))
    assert [p.name for p in kept] == [f"portfolio-{n:07d}.csv" for n in range(4, 24)]
    assert not any(p.read_bytes() == EXAMPLE.read_bytes() for p in kept)        # the oldest went first …
    assert (book.csv.parent / "backups" / "portfolio-original.csv").read_bytes() == EXAMPLE.read_bytes()   # … not this
    assert max(len(parse_portfolio(p)["transactions"]) for p in kept) == 15 + 22  # the newest is there
    assert sorted(os.listdir(book.csv.parent)) == ["backups", "portfolio.csv"]  # no temp or lock file left


def test_a_file_another_program_holds_is_one_line_and_left_as_it_was(book, monkeypatch):
    """Excel on Windows locks a CSV it has open: the write fails as one line saying so, nothing changes."""
    from monitor.portfolio import tradebook as TB
    from monitor.portfolio.tradebook import Blocked
    before = book.csv.read_bytes()

    def locked(*a, **k):
        raise PermissionError(13, "The process cannot access the file because it is being used by another process")
    monkeypatch.setattr(TB, "write_bytes_durable", locked)
    with pytest.raises(Blocked, match="^CANNOT WRITE portfolio.csv — OPEN IN EXCEL OR ANOTHER PROGRAM\\? CLOSE IT AND SAVE AGAIN$"):
        book.add(book.read()["etag"], {"ticker": "SAP.DE", "action": "buy", "shares": 1, "pps": 1})
    assert book.csv.read_bytes() == before


def test_a_big_import_is_quick(book):
    """A broker's CSV of a few thousand rows previews and imports in well under a second or two."""
    import time
    rows = "".join(f"2025-{1 + i % 12:02d}-{1 + i % 28:02d},T{i % 300}.DE,buy,1,{10 + i % 7}.00\n" for i in range(4000))
    t = time.perf_counter()
    pre = book.preview(rows, "append")
    assert pre["ok"] == 4000 and time.perf_counter() - t < 3
    t = time.perf_counter()
    res = book.import_text(book.read()["etag"], rows, "append")
    assert res["added"] == 4000 and time.perf_counter() - t < 3


# ── fix round 1 ───────────────────────────────────────────────────────────────────────────────────
def _book(tmp_path, text, name="portfolio.csv"):
    csv = tmp_path / "in" / name
    csv.parent.mkdir(exist_ok=True)
    csv.write_bytes(text.encode("utf-8") if isinstance(text, str) else text)
    return TradeBook(csv, example=EXAMPLE, today=lambda: TODAY)


def test_your_extra_columns_survive_every_rewrite(tmp_path):
    b = _book(tmp_path, "\ufeffDate;Ticker;Action;Shares;Price;PricePerShare;Notes;Broker\r\n"
                        "15.01.2025;SAP.DE;Buy;4;961,00;240,00;first buy;TR\r\n;;;;;;;\r\n"
                        "17.01.2025;SAP.DE;sell;1;250,5;250,5;;TR\r\n")
    r = b.add(b.read()["etag"], {"ticker": "ALV.DE", "action": "buy", "shares": 1, "pps": 300, "date": "2025-02-01"})
    assert b.csv.read_text(encoding="utf-8").splitlines() == [
        "Date,Ticker,Action,Shares,Price,PricePerShare,Notes,Broker",
        "2025-01-15,SAP.DE,buy,4,961.00,240.00,first buy,TR", "2025-01-17,SAP.DE,sell,1,250.50,250.50,,TR",
        "2025-02-01,ALV.DE,buy,1,300.00,300.00,,"]
    sell = next(x for x in b.read()["rows"] if x["action"] == "sell")
    b.update(r["etag"], sell["id"], {"ticker": "SAP.DE", "action": "sell", "shares": 1, "total": 251, "date": "2025-01-18"})
    assert "2025-01-18,SAP.DE,sell,1,251.00,251.00,,TR" in b.csv.read_text(encoding="utf-8")    # the row's own notes


def test_a_ticker_with_a_comma_in_your_file_is_quoted_not_a_500(tmp_path):
    b = _book(tmp_path, "Date;Ticker;Action;Shares;Price;PricePerShare\n2025-01-15;BRK,B;buy;1;400;400\n")
    b.add(b.read()["etag"], {"ticker": "SAP.DE", "action": "buy", "shares": 1, "pps": 1, "date": "2025-02-01"})
    assert b.csv.read_text(encoding="utf-8").splitlines()[1] == '2025-01-15,"BRK,B",buy,1,400.00,400.00'
    assert [x["ticker"] for x in b.read()["rows"]] == ["BRK,B", "SAP.DE"]


def test_a_file_that_would_not_read_back_as_meant_is_never_written(tmp_path, monkeypatch):
    b = _book(tmp_path, EXAMPLE.read_text(encoding="utf-8"))
    before = b.csv.read_bytes()
    real = T.to_csv
    monkeypatch.setattr(T, "to_csv", lambda rows, extra=(): real(rows[:-1], extra))       # a writer bug
    with pytest.raises(Invalid, match="^THE FILE WOULD NOT READ BACK AS WRITTEN — NOTHING CHANGED$"):
        b.add(b.read()["etag"], {"ticker": "SAP.DE", "action": "buy", "shares": 1, "pps": 1})
    assert b.csv.read_bytes() == before


def test_old_lower_case_tickers_keep_working_and_are_written_in_capitals(tmp_path):
    b = _book(tmp_path, f"{HEAD}\n2025-01-15,sap.de,buy,4,961.00,240.00\n2025-03-15,sap.de,sell,2,500.00,250.00\n")
    s = b.read()
    b.update(s["etag"], s["rows"][0]["id"], {"ticker": "sap.de", "action": "buy", "shares": 4, "total": 961, "pps": 240,
                                             "keep_pps": True, "date": "2025-01-14"})
    assert b.csv.read_text(encoding="utf-8") == (f"{HEAD}\n2025-01-14,SAP.DE,buy,4,961.00,240.00\n"
                                                  "2025-03-15,SAP.DE,sell,2,500.00,250.00\n")


def test_a_stored_total_is_never_rounded_by_an_edit_of_something_else(tmp_path):
    b = _book(tmp_path, f"{HEAD}\n2025-01-15,IWDA.AS,bonus,0.1234,12.3456,100.0486\n")
    s = b.read()
    b.update(s["etag"], s["rows"][0]["id"], {"ticker": "IWDA.AS", "action": "bonus", "shares": "0.1234",
                                             "total": "12.3456", "pps": 100.0486, "keep_pps": True, "date": "2025-01-16"})
    assert b.csv.read_text(encoding="utf-8").splitlines()[1] == "2025-01-16,IWDA.AS,bonus,0.1234,12.3456,100.0486"


def test_an_unreadable_file_counts_its_lines_and_start_fresh_says_how_many(tmp_path):
    b = _book(tmp_path, f"{HEAD}\n2025-01-15,SAP.DE,buy,4,961.00,240.00\n2025-01-16,SAP.DE,buy,4,96x,240.00\n\n"
                        "2025-01-17,ALV.DE,buy,4,961.00,240.00\n")
    s = b.read()
    assert (s["rows"], s["lines"]) == ([], 3) and "96x" in s["error"]
    assert b.reset(s["etag"])["removed"] == 3
    assert "96x" in (b.csv.parent / "backups" / "before-start-fresh-0001.csv").read_text(encoding="utf-8")


def test_backups_are_numbered_not_dated_so_a_wrong_clock_never_deletes_the_new_one(tmp_path):
    b = _book(tmp_path, EXAMPLE.read_text(encoding="utf-8"))
    bk = b.csv.parent / "backups"
    bk.mkdir()
    for i in range(20):
        (bk / f"portfolio-20991231-1200{i:02d}.csv").write_text("x", encoding="utf-8")   # an older naming, or a clock
    b.add(b.read()["etag"], {"ticker": "SAP.DE", "action": "buy", "shares": 1, "pps": 1})
    assert (bk / "portfolio-0000001.csv").read_bytes() == EXAMPLE.read_bytes()
    assert len(list(bk.glob("portfolio-2099*.csv"))) == 20                # not ours to rotate


def test_a_failed_write_rotates_nothing_and_its_retry_adds_no_second_copy(tmp_path, monkeypatch):
    from monitor.portfolio import tradebook as TB
    b = _book(tmp_path, EXAMPLE.read_text(encoding="utf-8"))
    e = b.read()["etag"]
    for i in range(20):
        e = b.add(e, {"ticker": "SAP.DE", "action": "buy", "shares": 1, "pps": 100 + i})["etag"]
    ring = lambda: sorted(p.name for p in (b.csv.parent / "backups").glob("portfolio-[0-9]*.csv"))   # noqa: E731
    before = ring()
    real = TB.write_bytes_durable

    def held(path, data, **kw):
        if Path(path).name == "portfolio.csv":
            raise PermissionError(13, "in use")
        return real(path, data, **kw)
    monkeypatch.setattr(TB, "write_bytes_durable", held)
    with pytest.raises(Blocked):
        b.add(e, {"ticker": "SAP.DE", "action": "buy", "shares": 1, "pps": 1})
    assert ring() == before + ["portfolio-0000021.csv"]                    # kept, nothing rotated out
    monkeypatch.setattr(TB, "write_bytes_durable", real)
    b.add(e, {"ticker": "SAP.DE", "action": "buy", "shares": 1, "pps": 1})
    assert ring() == before[1:] + ["portfolio-0000021.csv"]                # the same bytes: no second copy


def test_start_fresh_and_replace_backups_are_pinned_newest_5_each(tmp_path):
    b = _book(tmp_path, EXAMPLE.read_text(encoding="utf-8"))
    for i in range(6):
        e = b.import_text(b.read()["etag"], f"2025-01-{i + 10},SAP.DE,buy,1,{100 + i}\n", "replace")["etag"]
        b.reset(e)
    bk = b.csv.parent / "backups"
    assert sorted(p.name for p in bk.glob("before-*.csv")) == [f"before-replace-{n:04d}.csv" for n in range(2, 7)] + \
        [f"before-start-fresh-{n:04d}.csv" for n in range(2, 7)]
    assert (bk / "portfolio-original.csv").read_bytes() == EXAMPLE.read_bytes() and not list(bk.glob("portfolio-[0-9]*"))


def test_undo_puts_back_the_file_as_it_was_and_is_undone_itself(tmp_path):
    excel = "Date;Ticker;Action;Shares;Price;PricePerShare\r\n16.12.2024;SAP.DE;buy;4;961,00;240,00\r\n"
    b = _book(tmp_path, excel)
    assert b.read()["undo"] is None
    with pytest.raises(Invalid, match="^NOTHING TO UNDO$"):
        b.undo(b.read()["etag"])
    r = b.add(b.read()["etag"], {"ticker": "ALV.DE", "action": "buy", "shares": 1, "pps": 300, "date": "2025-01-02"})
    after = b.csv.read_bytes()
    assert b.read()["undo"]["what"] == "ADD BUY 1 ALV.DE · €300.00/sh = €300.00"
    u = b.undo(r["etag"])
    assert b.csv.read_bytes() == excel.encode("utf-8") and u["text"] == "UNDID: ADD BUY 1 ALV.DE · €300.00/sh = €300.00"
    assert b.read()["undo"]["what"] == "UNDO: ADD BUY 1 ALV.DE · €300.00/sh = €300.00"
    b.undo(u["etag"])
    assert b.csv.read_bytes() == after                                    # the undo, undone
    b.csv.write_text(HEAD + "\n", encoding="utf-8")                       # edited in Excel since
    with pytest.raises(Invalid, match="^THE FILE CHANGED SINCE THE LAST CHANGE HERE — UNDO WOULD LOSE THAT"):
        b.undo(b.read()["etag"])


def test_undo_of_the_very_first_write_removes_the_file_again(tmp_path):
    b = TradeBook(tmp_path / "in" / "portfolio.csv", example=EXAMPLE, today=lambda: TODAY)
    r = b.add("absent", {"ticker": "SAP.DE", "action": "buy", "shares": 1, "pps": 240})
    b.undo(r["etag"])
    assert not b.csv.exists() and b.read()["etag"] == "absent"
    b.undo("absent")
    assert len(b.read()["rows"]) == 1
