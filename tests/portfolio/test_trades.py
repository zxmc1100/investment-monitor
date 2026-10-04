"""monitor.portfolio.trades — the trades you enter in the terminal (TRADES): price per share <-> total (fees are
never added unless you enter one), the canonical file format and stable row ids, the checks a write must pass
(no sale beyond the shares held, no future date) and the one parser behind paste and import."""
from datetime import date
from pathlib import Path

import pytest

from monitor.portfolio import trades as T
from monitor.portfolio.ledger import parse_portfolio, parse_portfolio_text

TODAY = date(2026, 10, 4)
EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "portfolio.example.csv"
HEAD = "Date,Ticker,Action,Shares,Price,PricePerShare"


def row(d, t, a, s, p, pps=None):
    return {"date": d, "ticker": t, "action": a, "shares": float(s), "price": float(p),
            "pps": float(pps if pps is not None else round(p / s, 4))}


# ── price per share <-> total ────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("action, shares, kw, out", [
    ("buy", 4, dict(pps=240), (960.0, 240.0)),                       # total = shares x pps EXACTLY
    ("buy", 4, dict(pps=240, fee=1), (961.0, 240.0)),                # a fee only when you enter one
    ("sell", 2, dict(pps=410, fee=1), (819.0, 410.0)),
    ("bonus", 0.05, dict(pps=190, fee=1), (9.5, 190.0)),             # a bonus has no fee
    ("buy", 4, dict(total=961), (961.0, 240.25)),                    # total given: pps = total / shares
    ("buy", 4, dict(total=961, fee=1), (961.0, 240.25)),             # ...and a fee is already in it
    ("sell", 2, dict(total=819), (819.0, 409.5)),
    ("bonus", 0.15, dict(total=15), (15.0, 100.0)),
    ("buy", 3, dict(total=100), (100.0, 33.3333)),                   # pps to 4 dp, total to the cent
    ("buy", 3, dict(pps=0.335), (1.01, 0.335)),                      # half a cent rounds up
    ("buy", 0.123456, dict(pps=240.13), (29.65, 240.13)),
])
def test_compute(action, shares, kw, out):
    assert T.compute(action, shares, **kw) == out


@pytest.mark.parametrize("args, kw, msg", [
    (("buy", 0), dict(pps=10), "SHARES MUST BE > 0"),
    (("buy", -2), dict(pps=10), "SHARES MUST BE > 0"),
    (("buy", 2), {}, "PRICE PER SHARE OR TOTAL REQUIRED"),
    (("buy", 2), dict(pps=0), "PRICE PER SHARE MUST BE > 0"),
    (("buy", 2), dict(total=-5), "TOTAL MUST BE > 0"),
    (("buy", 2), dict(pps=10, fee=-1), "FEE MUST BE 0 OR MORE"),
    (("sell", 1), dict(pps=1, fee=1), "THE FEE LEAVES NOTHING: TOTAL €0.00"),
    (("dividend", 1), dict(pps=1), "ACTION MUST BE BUY, SELL OR BONUS"),
])
def test_compute_refuses(args, kw, msg):
    with pytest.raises(T.TradeError, match=f"^{msg}"):
        T.compute(*args, **kw)


@pytest.mark.parametrize("text, value", [("240", 240.0), ("240.5", 240.5), ("240,50", 240.5), (" 1.234 ", 1.234),
                                         (961, 961.0), (0.15, 0.15), ("@240", None), ("1,234", None),
                                         ("1.234,56", None), ("1 234", None), ("abc", None), ("", None)])
def test_one_number_as_typed(text, value):
    """A form field or command token: a decimal point, or a decimal comma; never a thousands separator."""
    if value is None:
        with pytest.raises(T.TradeError):
            T.number(text, "SHARES")
    else:
        assert T.number(text, "SHARES") == value


def test_dates_default_to_today_and_never_lie_ahead():
    assert T.trade_date("", TODAY) == "2026-10-04" and T.trade_date(None, TODAY) == "2026-10-04"
    assert T.trade_date("02.03.2026", TODAY) == T.trade_date("02/03/2026", TODAY) == "2026-03-02"
    assert T.trade_date("2026-10-04", TODAY) == "2026-10-04"
    with pytest.raises(T.TradeError, match="^DATE 2026-10-05 IS IN THE FUTURE$"):
        T.trade_date("2026-10-05", TODAY)
    with pytest.raises(T.TradeError, match="^DATE: '31.02.2026' IS NOT A DATE"):
        T.trade_date("31.02.2026", TODAY)


def test_a_trade_from_the_form_or_a_command():
    t = T.make_trade({"ticker": " sap.de ", "action": "BUY", "shares": "4", "pps": "240"}, TODAY)
    assert t == {"date": "2026-10-04", "ticker": "SAP.DE", "action": "buy", "shares": 4.0, "price": 960.0, "pps": 240.0}
    t = T.make_trade({"ticker": "ALV.DE", "action": "sell", "shares": 2, "pps": 410, "fee": 1, "date": "2026-03-02"}, TODAY)
    assert (t["price"], t["pps"], t["date"]) == (819.0, 410.0, "2026-03-02")
    t = T.make_trade({"ticker": "SAP.DE", "action": "buy", "shares": 4, "total": "961,00", "pps": ""}, TODAY)
    assert (t["price"], t["pps"]) == (961.0, 240.25)
    for bad, msg in (({"action": "buy", "shares": 1, "pps": 1}, "TICKER MISSING"),
                     ({"ticker": "A B", "action": "buy", "shares": 1, "pps": 1}, "TICKER 'A B' HAS A SPACE"),
                     ({"ticker": "X,Y", "action": "buy", "shares": 1, "pps": 1}, "TICKER 'X,Y' HAS A ,"),
                     ({"ticker": "X.F", "action": "hold", "shares": 1, "pps": 1}, "ACTION MUST BE BUY, SELL OR BONUS"),
                     ({"ticker": "X.F", "action": "buy", "shares": "x", "pps": 1}, "SHARES: 'x' IS NOT A NUMBER")):
        with pytest.raises(T.TradeError, match=f"^{msg}"):
            T.make_trade(bad, TODAY)


def test_both_given_keeps_both_only_when_asked():
    """An edit that leaves the price alone re-sends the stored total and price per share: both are kept as they
    were (the example's 961.00 for 4 x 240.00 carries its fee); anything else derives one from the other."""
    t = T.make_trade({"ticker": "SAP.DE", "action": "buy", "shares": 4, "total": 961, "pps": 240, "keep_pps": True}, TODAY)
    assert (t["price"], t["pps"]) == (961.0, 240.0)
    t = T.make_trade({"ticker": "SAP.DE", "action": "buy", "shares": 4, "total": 961, "pps": 240}, TODAY)
    assert (t["price"], t["pps"]) == (961.0, 240.25)


def test_the_preview_line_says_what_will_be_stored():
    t = T.make_trade({"ticker": "SAP.DE", "action": "buy", "shares": 4, "pps": 240}, TODAY)
    assert T.describe(t) == "BUY 4 SAP.DE · €240.00/sh = €960.00"
    t = T.make_trade({"ticker": "SAP.DE", "action": "buy", "shares": 4, "pps": 240, "fee": 1}, TODAY)
    assert T.describe(t, fee=1) == "BUY 4 SAP.DE · €240.00/sh + €1.00 fee = €961.00"
    t = T.make_trade({"ticker": "ALV.DE", "action": "sell", "shares": 2, "pps": 410, "fee": 1}, TODAY)
    assert T.describe(t, fee=1) == "SELL 2 ALV.DE · €410.00/sh − €1.00 fee = €819.00"
    assert T.describe(row("2026-01-01", "X.F", "buy", 3, 1234.5)) == "BUY 3 X.F · €411.50/sh = €1,234.50"


# ── the file: canonical text, ids, etag ────────────────────────────────────────────────────────────
def test_numbers_are_written_plain():
    assert [T.fmt_qty(x) for x in (4.0, 0.15, 13.513513, 1e-7, 0.12345678)] == ["4", "0.15", "13.513513", "0.0000001", "0.12345678"]
    assert [T.fmt_money(x) for x in (961.0, 100.5, 33.3333, 0.01, 1234567.5)] == ["961.00", "100.50", "33.3333", "0.01", "1234567.50"]


def test_canonical_csv_round_trips_through_the_ledger():
    rows = parse_portfolio(EXAMPLE)["transactions"]
    text = T.to_csv(rows)
    assert text == EXAMPLE.read_text(encoding="utf-8").replace("\r\n", "\n")       # the example is canonical
    assert parse_portfolio_text(text)["transactions"] == rows
    assert T.to_csv([]) == HEAD + "\n"
    odd = [row("2025-01-02", "X.F", "dividend", 1, 2.0)]           # a row the ledger ignores survives a rewrite
    assert parse_portfolio_text(T.to_csv(odd))["transactions"] == odd


def test_ids_are_stable_and_tell_twins_apart():
    rows = parse_portfolio(EXAMPLE)["transactions"]
    ids = T.ids(rows)
    assert len(set(ids)) == len(rows) and ids == T.ids([dict(r) for r in rows])
    excel = "Date;Ticker;Action;Shares;Price;PricePerShare\n" + "".join(
        f"{r['date'][8:]}.{r['date'][5:7]}.{r['date'][:4]};{r['ticker']};{r['action'].upper()};"
        + ";".join(f"{v:.6f}".replace(".", ",") for v in (r["shares"], r["price"], r["pps"])) + "\n" for r in rows)
    semi = parse_portfolio_text(excel)["transactions"]
    assert T.ids(semi) == ids                                      # the same trades however the file was saved
    twins = [rows[1], rows[1]]
    assert T.ids(twins)[0] == ids[1] and T.ids(twins)[1] != ids[1]
    assert T.ids(rows[:1] + rows[2:])[1:] == ids[2:]               # deleting one row leaves the others' ids
    assert T.etag(b"abc") == T.etag(b"abc") != T.etag(b"abd") and T.etag(None) == "absent"


# ── checks on the whole resulting file ─────────────────────────────────────────────────────────────
def test_a_sale_never_exceeds_the_shares_held_then():
    book = [row("2025-01-02", "SAP.DE", "buy", 4, 961), row("2025-03-01", "SAP.DE", "sell", 2, 500)]
    assert T.oversold(book) == {}
    worse = book + [row("2025-04-01", "SAP.DE", "sell", 3, 700)]
    assert T.oversold(worse) == {2: 2.0}
    assert T.new_problems(book, worse) == ["SELL 3 SAP.DE ON 2025-04-01: ONLY 2 HELD THEN"]
    assert T.new_problems(worse, worse) == []                      # a problem the file already had is not new
    assert T.oversold([row("2025-01-02", "X.F", "buy", 1, 10), row("2025-01-03", "X.F", "sell", 1.0004, 10)]) == {}
    gone = [book[1]]                                               # deleting the buy strands the sale
    assert T.new_problems(book, gone) == ["SELL 2 SAP.DE ON 2025-03-01: ONLY 0 HELD THEN"]
    bonus = [row("2025-01-02", "IWDA.AS", "bonus", 0.15, 15), row("2025-02-02", "IWDA.AS", "sell", 0.15, 16)]
    assert T.oversold(bonus) == {}


def test_a_new_trade_goes_after_the_last_one_dated_on_or_before_it():
    book = [row("2025-01-02", "A.F", "buy", 1, 1), row("2025-03-01", "B.F", "buy", 1, 1), row("2025-03-01", "C.F", "buy", 1, 1)]
    for d, at in (("2024-12-31", 0), ("2025-01-02", 1), ("2025-03-01", 3), ("2026-01-01", 3), ("2025-02-01", 1)):
        rows, i = T.place(book, row(d, "N.F", "buy", 1, 1))
        assert i == at and rows[i]["ticker"] == "N.F" and len(rows) == 4, d


def test_duplicates_are_found_not_refused():
    book = [row("2025-01-02", "SAP.DE", "buy", 4, 961, 240)]
    assert T.duplicate(book, row("2025-01-02", "SAP.DE", "buy", 4, 961, 240.25)) is book[0]   # pps is display only
    assert T.duplicate(book, row("2025-01-03", "SAP.DE", "buy", 4, 961)) is None


def test_the_example_and_an_empty_book_are_recognised(tmp_path):
    p = tmp_path / "portfolio.csv"
    p.write_bytes(EXAMPLE.read_bytes().replace(b"\n", b"\r\n"))
    assert T.same_trades(p, EXAMPLE) and T.has_trades(p)
    p.write_text(HEAD + "\n", encoding="utf-8")
    assert not T.same_trades(p, EXAMPLE) and not T.has_trades(p)
    p.write_text(HEAD.replace(",", ";") + "\n;;;;;\n\n", encoding="utf-8")   # Excel's blank rows
    assert not T.has_trades(p)
    p.write_text("", encoding="utf-8")
    assert not T.has_trades(p) and not T.has_trades(tmp_path / "missing.csv")
    p.write_text(HEAD + "\nbroken\n", encoding="utf-8")
    assert T.has_trades(p) and not T.same_trades(p, EXAMPLE)         # unreadable: the screens say why


# ── paste / import: one parser ────────────────────────────────────────────────────────────────────
def ok(res):
    return [r["trade"] for r in res["rows"] if r["trade"]]


def errors(res):
    return {r["line"]: r["error"] for r in res["rows"] if r["error"]}


def test_the_project_format_reads_as_its_file_does():
    text = EXAMPLE.read_text(encoding="utf-8")
    res = T.parse_bulk(text, TODAY)
    assert res["header"] and res["delimiter"] == "," and res["error"] is None and errors(res) == {}
    assert ok(res) == parse_portfolio(EXAMPLE)["transactions"]       # its own format: both columns kept as written
    assert [r["line"] for r in res["rows"]] == list(range(2, 17))


def test_excel_europe_semicolons_and_decimal_commas():
    res = T.parse_bulk("Datum;Ticker;Typ;Anzahl;Kurs;Betrag;Gebühr\r\n15.01.2025;SAP.DE;Kauf;4;240,00;961,00;1,00\r\n"
                       "02.06.2025;SAP.DE;Verkauf;2;260,50;520,00;1,00\r\n;;;;;;\r\n", TODAY)
    assert (res["delimiter"], res["decimal"], res["header"]) == (";", "comma", True)
    assert [(t["date"], t["action"], t["shares"], t["price"], t["pps"]) for t in ok(res)] == [
        ("2025-01-15", "buy", 4.0, 961.0, 240.25), ("2025-06-02", "sell", 2.0, 520.0, 260.0)]
    assert all(not r["warnings"] for r in res["rows"])               # 4 x 240 + 1 = 961: consistent


def test_a_spreadsheet_paste_is_tab_separated_either_decimal_style():
    de = T.parse_bulk("15.01.2025\tSAP.DE\tbuy\t4\t961,00\t240,00\n", TODAY)
    en = T.parse_bulk("2025-01-15\tSAP.DE\tbuy\t1.234\t296.16\t240.00\n", TODAY)
    assert (de["delimiter"], de["decimal"], en["decimal"]) == ("\t", "comma", "point")
    assert (ok(de)[0]["price"], ok(en)[0]["shares"]) == (961.0, 1.234)


def test_headerless_rows_and_at_price_per_share():
    res = T.parse_bulk("2025-01-15,SAP.DE,buy,4,961.00,240.00\n2025-01-16,SAP.DE,buy,4,@240\n"
                       "2025-01-17,SAP.DE,buy,2,500\n", TODAY)
    assert not res["header"] and errors(res) == {}
    assert [(t["price"], t["pps"]) for t in ok(res)] == [(961.0, 240.0), (960.0, 240.0), (500.0, 250.0)]
    semi = T.parse_bulk("16.01.2025;ALV.DE;sell;1;@410,5\n", TODAY)
    assert (semi["delimiter"], ok(semi)[0]["price"], ok(semi)[0]["pps"]) == (";", 410.5, 410.5)


@pytest.mark.parametrize("head", ["date,symbol,side,qty,price per share", "Data,Ticker,Tipo,Quantità,Prezzo",
                                  "DATE,TICKER,ACTION,QUANTITY,PPS", "trade date,tkr,type,units,Price (EUR)"])
def test_column_synonyms_name_the_price_per_share(head):
    res = T.parse_bulk(f"{head}\n2025-01-15,SAP.DE,buy,4,240\n", TODAY)
    assert errors(res) == {} and (ok(res)[0]["price"], ok(res)[0]["pps"]) == (960.0, 240.0)


@pytest.mark.parametrize("head", ["Date,Ticker,Action,Shares,Total", "datum,ticker,art,stück,betrag",
                                  "Date,Symbol,Type,Quantity,Amount", "Data,Ticker,Operazione,Quantita,Importo",
                                  "date,ticker,action,shares,net amount", "date,ticker,action,shares,value"])
def test_column_synonyms_name_the_total(head):
    res = T.parse_bulk(f"{head}\n2025-01-15,SAP.DE,buy,4,961\n", TODAY)
    assert errors(res) == {} and (ok(res)[0]["price"], ok(res)[0]["pps"]) == (961.0, 240.25)


def test_price_and_total_both_given_the_total_wins_and_a_gap_is_flagged():
    res = T.parse_bulk("date,ticker,action,shares,price,total\n2025-01-15,SAP.DE,buy,4,240,961\n"
                       "2025-01-16,SAP.DE,buy,4,240,990\n", TODAY)
    assert [(t["price"], t["pps"]) for t in ok(res)] == [(961.0, 240.25), (990.0, 247.5)]
    assert res["rows"][0]["warnings"] == []                        # 0.1 % apart: a fee, within rounding
    assert res["rows"][1]["warnings"] == ["4 × €240.00 = €960.00 BUT TOTAL €990.00 (3.0 % APART) — A FEE OR A WRONG COLUMN?"]


def test_a_fee_column_applies_only_without_a_total():
    res = T.parse_bulk("date,ticker,action,shares,price,fees\n2025-01-15,SAP.DE,buy,4,240,1\n"
                       "2025-01-16,SAP.DE,sell,2,250,1\n2025-01-17,X.F,bonus,1,10,1\n", TODAY)
    assert [t["price"] for t in ok(res)] == [961.0, 499.0, 10.0]
    res = T.parse_bulk("date,ticker,action,shares,amount,commission\n2025-01-15,SAP.DE,buy,4,961,1\n", TODAY)
    assert ok(res)[0]["price"] == 961.0                           # the amount already holds it


@pytest.mark.parametrize("word, action", [("Buy", "buy"), ("KAUF", "buy"), ("acquisto", "buy"), ("Sparplan", "buy"),
                                          ("savings plan", "buy"), ("PAC", "buy"), ("piano", "buy"), ("plan", "buy"),
                                          ("Sell", "sell"), ("Verkauf", "sell"), ("vendita", "sell"),
                                          ("bonus", "bonus"), ("Saveback", "bonus")])
def test_action_synonyms(word, action):
    assert ok(T.parse_bulk(f"2025-01-15,X.F,{word},1,10\n", TODAY))[0]["action"] == action


def test_brokers_signs_are_directions_not_values():
    res = T.parse_bulk("date,ticker,type,shares,amount\n2025-01-15,SAP.DE,buy,4,-961.00\n2025-02-15,SAP.DE,sell,-2,500\n", TODAY)
    assert [(t["shares"], t["price"]) for t in ok(res)] == [(4.0, 961.0), (2.0, 500.0)]


def test_bad_rows_say_why_and_never_stop_the_good_ones():
    text = ("Date,Ticker,Action,Shares,Price,PricePerShare\n"
            "2025-01-15,SAP.DE,buy,4,961.00,240.00\n"
            "2025-01-16,SAP.DE,dividend,4,10.00,2.50\n"
            "2025-01-17,SAP.DE,buy,0,961.00,240.00\n"
            "2025-01-18,SAP.DE,buy,4,0,0\n"
            "2099-01-01,SAP.DE,buy,4,961.00,240.00\n"
            "2025-01-19,,buy,4,961.00,240.00\n"
            "2025-01-20,SAP.DE,buy,4,961,00,240,00\n"
            "2025-13-01,SAP.DE,buy,4,961.00,240.00\n"
            "2025-01-21,SAP.DE,buy,4,\"1,234.56\",240.00\n")
    res = T.parse_bulk(text, TODAY)
    assert len(ok(res)) == 1 and errors(res) == {
        3: "UNKNOWN ACTION 'DIVIDEND' — BUY, SELL OR BONUS",
        4: "SHARES MUST BE > 0",
        5: "TOTAL MUST BE > 0",
        6: "DATE 2099-01-01 IS IN THE FUTURE",
        7: "TICKER MISSING",
        8: "8 FIELDS, EXPECTED 6 — A DECIMAL COMMA? SEPARATE THE FIELDS WITH ; INSTEAD, OR WRITE 961.00",
        9: "DATE: '2025-13-01' IS NOT A DATE — WRITE IT AS YYYY-MM-DD (OR DD.MM.YYYY)",
        10: "PRICE: '1,234.56' HAS A THOUSANDS SEPARATOR — WRITE IT WITHOUT ONE, E.G. 1234.56"}


def test_nothing_to_read_or_no_usable_columns_is_one_line():
    assert T.parse_bulk("  \n\n", TODAY)["error"] == "NOTHING TO READ — PASTE ROWS OR PICK A CSV FILE"
    assert T.parse_bulk("Name,Ticker\nSAP,SAP.DE\n", TODAY)["error"] == (
        "NO DATE, ACTION, SHARES, PRICE COLUMN — NAME THEM DATE, TICKER, ACTION, SHARES AND PRICE (PER SHARE) OR TOTAL")
    res = T.parse_bulk("2025-01-15,SAP.DE,buy\n", TODAY)
    assert errors(res) == {1: "3 FIELDS — WRITE DATE,TICKER,ACTION,SHARES,TOTAL[,PRICEPERSHARE] OR …,SHARES,@PRICEPERSHARE"}


def test_dates_with_a_time_and_isins():
    res = T.parse_bulk("date,isin,type,shares,amount\n2025-01-15T09:31:00Z,DE0007030009,buy,1,700\n"
                       "15.01.2025 10:02,US0000000009,buy,1,10\n", TODAY,
                       isin=lambda code: {"DE0007030009": "RHM.DE"}.get(code))
    assert ok(res)[0]["ticker"] == "RHM.DE" and ok(res)[0]["date"] == "2025-01-15"
    assert errors(res) == {3: "ISIN US0000000009 IS NOT IN THE LOOKUP — WRITE ITS YAHOO TICKER INSTEAD"}


# ── review against your file, then merge ─────────────────────────────────────────────────────────────
def test_review_flags_duplicates_and_sales_beyond_the_shares_held():
    book = [row("2025-01-02", "SAP.DE", "buy", 4, 961, 240)]
    res = T.review(book, T.parse_bulk("2025-01-02,SAP.DE,buy,4,961.00,240.00\n2025-02-01,SAP.DE,sell,9,1500\n"
                                      "2025-02-02,SAP.DE,sell,1,250\n2025-02-02,SAP.DE,sell,1,250\n", TODAY), "append")
    assert [r["warnings"] for r in res["rows"]] == [["SAME AS A TRADE ALREADY IN YOUR FILE"], [], [], ["SAME AS LINE 3"]]
    assert errors(res) == {2: "SELL 9 SAP.DE ON 2025-02-01: ONLY 8 HELD THEN"}    # your 4 + the pasted 4
    rep = T.review(book, T.parse_bulk("2025-02-01,SAP.DE,sell,1,250\n", TODAY), "replace")
    assert errors(rep) == {1: "SELL 1 SAP.DE ON 2025-02-01: ONLY 0 HELD THEN"}


def test_merge_keeps_your_order_and_slots_new_trades_in_by_date():
    book = [row("2025-01-02", "A.F", "buy", 1, 1), row("2025-03-01", "B.F", "buy", 1, 1)]
    new = [row("2025-02-01", "N.F", "buy", 1, 1), row("2025-01-02", "M.F", "buy", 1, 1)]
    assert [r["ticker"] for r in T.merge(book, new, "append")] == ["A.F", "M.F", "N.F", "B.F"]
    assert [r["ticker"] for r in T.merge(book, new, "replace")] == ["M.F", "N.F"]
    newest_first = [row("2025-02-02", "S.F", "sell", 1, 1), row("2025-02-02", "S.F", "buy", 1, 1),
                    row("2025-01-01", "S.F", "buy", 1, 1)]        # a broker export, newest first
    assert [r["action"] for r in T.merge([], newest_first, "replace")] == ["buy", "buy", "sell"]


def test_a_sale_that_strands_a_later_one_in_your_file_is_the_pasted_rows_fault():
    book = [row("2025-01-02", "SAP.DE", "buy", 4, 961), row("2025-06-01", "SAP.DE", "sell", 4, 1000)]
    res = T.review(book, T.parse_bulk("2025-03-01,SAP.DE,sell,2,500\n", TODAY), "append")
    assert errors(res) == {1: "LEAVES YOUR SELL 4 SAP.DE ON 2025-06-01 SHORT: ONLY 2 HELD THEN"}
