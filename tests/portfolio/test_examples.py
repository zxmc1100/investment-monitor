"""examples/: a small, clearly invented portfolio in the exact format the ledger
reads, an interest file, and a settings file documenting every key — all parsed by the real code,
no network (PORT builds from it over the yfinance fakes)."""
import json
import re
from datetime import date
from pathlib import Path

import pytest
import time_machine

from monitor import config
from monitor.portfolio import meta
from monitor.portfolio.ledger import compute_portfolio_summary, load_interest, parse_portfolio
from monitor.screens import port
from monitor.screens.base import Ctx
from tests import fakes_yf

EX = Path(__file__).resolve().parent.parent.parent / "examples"
CSV, INTEREST, SETTINGS = EX / "portfolio.example.csv", EX / "interest.example.csv", EX / "settings.example.toml"


def test_example_portfolio_parses_through_the_real_ledger():
    assert CSV.read_text().splitlines()[0] == "Date,Ticker,Action,Shares,Price,PricePerShare"
    book = parse_portfolio(CSV)
    tx = book["transactions"]
    tickers = {t["ticker"] for t in tx}
    assert 6 <= len(tickers) <= 8
    assert {"buy", "sell", "bonus"} <= {t["action"] for t in tx}
    assert tickers & set(meta.ETF_SECTOR_WEIGHTS)                         # an ETF, looked through
    dates = [t["date"] for t in tx]
    assert dates == sorted(dates)
    assert (date.fromisoformat(dates[-1]) - date.fromisoformat(dates[0])).days >= 600
    assert dates[0] >= fakes_yf.START                                      # the fakes price every trade day
    closed = set(book["realized"]) - set(book["holdings"])
    partial = set(book["realized"]) & set(book["holdings"])
    assert closed and partial                                              # a full exit and a partial sale
    for t in book["realized"]:                                             # FIFO: proceeds - oldest lots' cost
        sold = [x for x in tx if x["ticker"] == t and x["action"] == "sell"]
        assert book["realized"][t]["proceeds"] == pytest.approx(sum(x["price"] for x in sold))
    summary = compute_portfolio_summary(book, {t: h["avg_cost"] * 1.1 for t, h in book["holdings"].items()})
    assert summary["totals"]["current_value"] > 0 and all(p["shares"] > 0 for p in summary["positions"])


def test_example_interest_parses_cleanly(caplog):
    rows = load_interest(INTEREST)
    assert len(rows) >= 4 and all(r["eur"] > 0 for r in rows)
    assert "skipped" not in caplog.text


def test_example_settings_document_every_key_and_hold_the_defaults():
    text = SETTINGS.read_text()
    for key in config._SETTINGS:
        assert re.search(rf"^\[?{key}\]?\s*(=|$)", text, re.M), f"{key} undocumented"
    s, err = config.load_settings(SETTINGS)
    assert err is None
    assert s == config.load_settings(EX / "missing.toml")[0]             # copying it changes nothing


def test_port_builds_from_the_example_portfolio(tmp_path, monkeypatch):
    fakes_yf.install(monkeypatch)
    book = tmp_path / "input"
    book.mkdir()
    (book / "portfolio.csv").write_text(CSV.read_text())
    (book / "interest.csv").write_text(INTEREST.read_text())
    with time_machine.travel("2026-10-02 14:00:00+00:00", tick=False):
        ctx = Ctx(force=True, buffer_dir=tmp_path / "buffer", portfolio_csv=book / "portfolio.csv", equity_log=None)
        p = port.assemble({t: port.compute(t, ctx) for t in port.SCREEN.tiers},
                          {"computed_at": "x", "tiers": {}, "code_version": "x"})
    json.dumps(p, allow_nan=False)
    pos = next(q for q in p["panels"] if q["id"] == "positions")
    assert len([r for r in pos["rows"] if not r.get("_closed")]) == len(parse_portfolio(CSV)["holdings"])
    acct = next(q for q in p["panels"] if q["id"] == "accounting")
    assert next(x for x in acct["lines"] if x.get("label", "").startswith("Interest"))["v"] > 0
