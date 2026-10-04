"""TRADES — enter and manage your trades in the terminal: the ADD form, PASTE / IMPORT and TRANSACTIONS (newest
first), plus a START FRESH banner while the file is still the example. Private (never exported), number key 6,
no portfolio needed, inline (local, no network: it computes in the request)."""
import shutil
from datetime import datetime
from pathlib import Path

import pytest

from monitor.data import yahoo as Y
from monitor.screens import SCREENS
from monitor.screens import trades as TR
from monitor.screens.base import Ctx

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "portfolio.example.csv"
HEAD = "Date,Ticker,Action,Shares,Price,PricePerShare"


def build(ctx: Ctx) -> dict:
    meta = {"computed_at": datetime.now().isoformat(timespec="seconds"), "tiers": {}, "code_version": "x"}
    return TR.assemble({"quote": TR.compute("quote", ctx)}, meta)


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    def no_network(*a, **k):
        raise AssertionError("TRADES never asks Yahoo")
    monkeypatch.setattr(Y, "fetch_info", no_network)
    monkeypatch.setattr(Y, "fetch_quotes", no_network)
    csv = tmp_path / "input" / "portfolio.csv"
    csv.parent.mkdir()
    shutil.copy(EXAMPLE, csv)
    return Ctx(buffer_dir=tmp_path / "buffer", portfolio_csv=csv, equity_log=None)


def panel(p, pid):
    return next(q for q in p["panels"] if q["id"] == pid)


def test_the_screen_is_private_key_6_inline_and_needs_no_portfolio():
    s = SCREENS["TRADES"]
    assert (s.fkey, s.public, s.inline, s.needs_portfolio, s.uses_inputs, s.tiers) == (6, False, True, False, True, ("quote",))
    assert {k: v.fkey for k, v in SCREENS.items()} == {"PORT": 1, "OPT": 2, "RISK": 3, "SEC": None, "MKT": 4,
                                                      "ALRT": 5, "TRADES": 6}


def test_the_example_shows_the_start_fresh_banner_the_form_the_paste_box_and_every_trade(ctx):
    p = build(ctx)
    assert p["screen"] == "TRADES" and p["etag"] and p["error"] is None
    assert [q["id"] for q in p["panels"]] == ["fresh", "add", "paste", "txns"]
    fresh = panel(p, "fresh")
    assert fresh["type"] == "banner" and fresh["text"] == "EXAMPLE PORTFOLIO — START FRESH clears it"
    assert fresh["run"] == "START FRESH" and fresh["button"] == "START FRESH"
    add = panel(p, "add")
    assert add["type"] == "form" and add["form"] == "trade"
    assert [f["k"] for f in add["fields"]] == ["ticker", "action", "shares", "pps", "total", "date", "fee"]
    assert next(f for f in add["fields"] if f["k"] == "action")["options"] == ["buy", "sell", "bonus"]
    assert {"ticker": "SAP.DE", "name": "SAP.DE"} in add["book"]
    assert panel(p, "paste")["type"] == "paste"
    t = panel(p, "txns")
    assert t["type"] == "table" and t["key"] == "id" and t["sort"] == ["when", "desc"]
    assert t["edit"] == "add" and t["remove"] is True and len(t["rows"]) == 15
    assert [c["k"] for c in t["cols"]] == ["date", "tkr", "name", "action", "shares", "pps", "total"]
    sap = next(r for r in t["rows"] if r["tkr"] == "SAP.DE" and r["action"] == "BUY")
    assert {k: sap[k] for k in ("date", "shares", "pps", "total")} == {"date": "2024-12-16", "shares": 4.0,
                                                                     "pps": 240.0, "total": 961.0}
    newest = max(t["rows"], key=lambda r: r["when"])
    assert (newest["date"], newest["tkr"]) == ("2026-09-01", "SIE.DE")


def test_your_own_trades_have_no_banner_and_an_empty_file_says_how_to_start(ctx):
    Path(ctx.portfolio_csv).write_text(HEAD + "\n2025-01-15,RHM.DE,buy,1,700.00,700.00\n", encoding="utf-8")
    p = build(ctx)
    assert [q["id"] for q in p["panels"]] == ["add", "paste", "txns"]
    assert panel(p, "txns")["rows"][0]["name"] == "Rheinmetall"           # the universe names it, offline
    Path(ctx.portfolio_csv).write_text(HEAD + "\n", encoding="utf-8")
    p = build(ctx)
    assert panel(p, "txns")["rows"] == [] and "NO TRADES YET" in panel(p, "txns")["empty"]
    Path(ctx.portfolio_csv).unlink()
    p = build(ctx)
    assert panel(p, "txns")["rows"] == [] and p["etag"] == "absent"


def test_a_file_that_cannot_be_read_says_so_on_top_and_offers_undo_never_start_fresh(ctx):
    from monitor.portfolio.tradebook import TradeBook
    Path(ctx.portfolio_csv).write_text(HEAD + "\n2025-01-15,SAP.DE,buy,x,961.00,240.00\n", encoding="utf-8")
    p = build(ctx)
    err = panel(p, "problem")
    assert p["panels"][0] is err and err["type"] == "banner" and err["tone"] == "dn"
    assert err["text"] == "portfolio.csv row 2, column Shares: 'x' is not a number" and p["error"] == err["text"]
    assert "run" not in err and err["context"]["text"] == "FIX IT IN THE FILE" and p["lines"] == 1
    book = TradeBook(ctx.portfolio_csv)                            # a TRADES write broke it? then UNDO is the way back
    Path(ctx.portfolio_csv).write_text(HEAD + "\n2025-01-15,SAP.DE,buy,4,961.00,240.00\n", encoding="utf-8")
    book.add(book.read()["etag"], {"ticker": "SAP.DE", "action": "buy", "shares": 1, "pps": 1, "date": "2025-01-16"})
    Path(ctx.portfolio_csv).write_text(Path(ctx.portfolio_csv).read_text(encoding="utf-8") + "oops\n", encoding="utf-8")
    err = panel(build(ctx), "problem")
    assert "run" not in err                                          # the file changed since: UNDO would lose that
    book.read()


def test_undo_is_offered_in_the_strip_while_the_file_is_as_trades_left_it(ctx):
    from monitor.portfolio.tradebook import TradeBook
    assert build(ctx)["actions"] == []
    book = TradeBook(ctx.portfolio_csv)
    book.add(book.read()["etag"], {"ticker": "SAP.DE", "action": "buy", "shares": 1, "pps": 2, "date": "2025-01-16"})
    p = build(ctx)
    assert p["actions"] == [{"label": "UNDO", "run": "UNDO", "title": "UNDO: ADD BUY 1 SAP.DE · €2.00/sh = €2.00"}]
    assert p["undo"]["what"] == "ADD BUY 1 SAP.DE · €2.00/sh = €2.00" and p["undo"]["ready"] is True


def test_help_explains_the_two_ways_the_fee_and_the_file():
    text = " ".join(h["h"] + " " + h["body"] for h in TR.HELP)
    for word in ("EITHER", "No fee is ever added", "input/backups/", "BUY SAP.DE 4 @ 240", "BUY SAP.DE 4 = 961",
                 "START FRESH", "@PricePerShare", "UNDO", "portfolio-original.csv", "only while TRANSACTIONS is focused"):
        assert word in text, word


def test_the_msci_world_etf_has_its_name_built_in():
    """IWDA.AS is looked through as MSCI World (never asked of Yahoo): its name is built in, like EUNL.F's."""
    from monitor.data.instruments import COMPANY_NAMES
    assert COMPANY_NAMES["IWDA.AS"] == "iShares Core MSCI World ETF"
