"""BOOKS — your portfolio and every other book (input/books/<NAME>/portfolio.csv) side by side: one row each with
PORT's own figures for that file, the cursor row's holdings, and one ROI chart whose benchmarks are shared — each
one's total return from the chart's first day, the same line whichever books there are. Private, number key 7;
BOOK <NAME> (no key) is TRADES on a book's file. Fake yfinance only."""
import json
import shutil
from pathlib import Path

import pytest
import time_machine

from monitor.screens import SCREENS, books, port
from monitor.screens.base import Ctx
from tests import fakes_yf

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "portfolio_small.csv"
META = {"computed_at": "2026-06-30T14:00:00", "tiers": {}, "code_version": "x"}
HEAD = "Date,Ticker,Action,Shares,Price,PricePerShare"


@pytest.fixture
def frozen(monkeypatch):
    fakes_yf.install(monkeypatch)
    with time_machine.travel("2026-06-30 14:00:00+00:00", tick=False):
        yield


@pytest.fixture
def ctx(tmp_path):
    csv = tmp_path / "input" / "portfolio.csv"
    csv.parent.mkdir()
    shutil.copy(FIX, csv)
    return Ctx(force=True, buffer_dir=tmp_path / "buffer", portfolio_csv=csv, equity_log=None)


def add_book(ctx, name, rows):
    f = ctx.portfolio_csv.parent / "books" / name / "portfolio.csv"
    f.parent.mkdir(parents=True)
    f.write_text(HEAD + "\n" + "".join(r + "\n" for r in rows), encoding="utf-8")
    return f


def build(ctx):
    return books.assemble({t: books.compute(t, ctx) for t in books.SCREEN.tiers}, dict(META))


def panel(p, pid):
    return next(q for q in p["panels"] if q["id"] == pid)


def test_registry_books_on_key_7_and_book_by_name():
    s, b = SCREENS["BOOKS"], SCREENS["BOOK"]
    assert (s.fkey, s.public, s.uses_inputs, s.uses_books, s.needs_portfolio) == (7, False, True, True, False)
    assert (b.fkey, b.public, b.inline, b.param_name) == (None, False, True, "NAME")


def test_each_book_is_ports_own_figures_and_the_benchmarks_are_one_line_for_all(frozen, ctx):
    add_book(ctx, "ANNA", ["2026-02-02,AAA.F,buy,4,400.00,100.00", "2026-03-02,EEE.F,buy,10,500.00,50.00"])
    p = build(ctx)
    assert p["screen"] == "BOOKS" and [q["id"] for q in p["panels"]] == ["books", "holdings", "roi"]
    rows = {r["book"]: r for r in panel(p, "books")["rows"]}
    assert set(rows) == {"YOU", "ANNA"} and panel(p, "books")["enter"] == "BOOK {key}"
    # YOU is exactly what PORT shows for input/portfolio.csv
    pp = port.assemble({t: port.compute(t, ctx) for t in port.SCREEN.tiers}, dict(META))
    summ = {i["k"]: i for i in next(q for q in pp["panels"] if q["id"] == "summary")["items"]}
    assert rows["YOU"]["roi"] == summ["TOTAL"]["v"] and rows["YOU"]["value"] == summ["VALUE"]["v"]
    assert rows["ANNA"]["since"] == "2026-02-02" and rows["ANNA"]["pos"] == 2
    hold = panel(p, "holdings")
    assert hold["follows"] == "books" and {r["tkr"] for r in hold["rows_by_key"]["ANNA"]["rows"]} == {"AAA.F", "EEE.F"}
    chart = panel(p, "roi")
    names = [s["name"] for s in chart["series"]]
    assert names[:2] == ["YOU", "ANNA"] and {"SPX", "MSCI W"} <= set(names)
    assert chart["inv"] == [1.0] * len(chart["x"])             # a benchmark: 1 € held from the first day
    anna = chart["series"][1]
    assert anna["y"][0] is None and anna["inv"][0] is None      # younger than the chart: nothing before it
    assert all(len(s[k]) == len(chart["x"]) for s in chart["series"] for k in ("y", "twr"))
    spx = next(s for s in chart["series"] if s["name"] == "SPX")
    assert spx["y"][0] == 0.0 and spx["twr"][0] == 1.0 and "inv" not in spx and "cash" not in spx
    # shared: the same benchmark line with or without ANNA (the chart starts at YOU's first trade either way)
    shutil.rmtree(ctx.portfolio_csv.parent / "books")
    alone = next(s for s in panel(build(ctx), "roi")["series"] if s["name"] == "SPX")
    assert alone["y"] == spx["y"]
    json.dumps(p, allow_nan=False)
    assert len(json.dumps(p)) < 300_000


def test_a_book_whose_file_has_an_error_is_named_and_the_others_still_show(frozen, ctx):
    add_book(ctx, "BAD", ["not,a,trade"])
    p = build(ctx)
    assert [r["book"] for r in panel(p, "books")["rows"]] == ["YOU"]
    assert any(w.startswith("BOOK BAD:") for w in p["meta"]["warn"])


def test_books_without_your_own_trades_and_nothing_at_all(frozen, ctx):
    ctx.portfolio_csv.write_text(HEAD + "\n", encoding="utf-8")
    assert books.cold(ctx) == books.NO_BOOKS
    add_book(ctx, "ANNA", ["2025-03-03,AAA.F,buy,4,400.00,100.00"])
    assert books.cold(ctx) is None
    p = build(ctx)
    assert [r["book"] for r in panel(p, "books")["rows"]] == ["ANNA"]
    assert panel(p, "books")["cursor"] == "ANNA" and [s["name"] for s in panel(p, "roi")["series"]][0] == "ANNA"


def test_a_line_yahoo_never_priced_is_carried_at_cost_and_flagged(frozen, ctx, monkeypatch):
    """A holding with no Yahoo price at all (a retail government bond): at cost, flagged, never hidden — as PORT."""
    import monitor.data.yahoo as Y
    real = Y.fetch_quotes
    monkeypatch.setattr(Y, "fetch_quotes", lambda ts: {**real(ts), "BND-X": None})
    add_book(ctx, "ANNA", ["2026-02-02,AAA.F,buy,4,400.00,100.00", "2026-02-02,BND-X,buy,10,1000.00,100.00"])
    hold = panel(build(ctx), "holdings")["rows_by_key"]["ANNA"]
    bond = next(r for r in hold["rows"] if r["tkr"] == "BND-X")
    assert bond["_stale"] is True and bond["value"] == 1000.0 and "1 NO PRICE: AT COST" in hold["context"]


def test_a_new_books_history_runs_even_before_its_quotes(frozen, ctx, monkeypatch):
    """The daily tier may run before the quote tier ever asked for a new book's lines: it asks them first (buffered),
    so a line Yahoo has never priced is told from a cold start — not 'no history', failing the whole screen."""
    import yfinance
    import monitor.data.yahoo as Y
    real_q, real_dl = Y.fetch_quotes, yfinance.download
    monkeypatch.setattr(Y, "fetch_quotes", lambda ts: {**real_q(ts), "BND-X": None})

    def download(tickers, *a, **k):          # Yahoo has no history for the bond either: its columns come back empty
        df = real_dl(tickers, *a, **k)
        df.loc[:, [c for c in df.columns if c[1] == "BND-X"]] = float("nan")
        return df
    monkeypatch.setattr(yfinance, "download", download)
    port.compute("quote", ctx)                                  # yours quoted: the quote buffer exists
    add_book(ctx, "ANNA", ["2026-02-02,EEE.F,buy,4,400.00,100.00", "2026-02-02,BND-X,buy,10,1000.00,100.00"])
    daily = books.compute("daily", ctx)                        # before BOOKS' own quote tier
    assert "ANNA" in daily["books"] and not daily["errors"]
