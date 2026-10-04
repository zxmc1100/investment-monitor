"""SEC payload: params, open vs closed positions, contribution, empty history, strict JSON."""
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import time_machine

from monitor.portfolio import snapshot
from monitor.screens import sec
from monitor.screens.base import Ctx
from monitor.screens.common import bar_on_or_after
from tests import fakes_yf

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "portfolio_small.csv"
META = {"computed_at": "2026-06-30T14:00:00", "tiers": {}, "code_version": "x", "prefs": {}}


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    fakes_yf.install(monkeypatch)
    with time_machine.travel("2026-06-30 14:00:00+00:00", tick=False):
        c = Ctx(force=True, buffer_dir=tmp_path / "buffer", portfolio_csv=FIX, equity_log=None)
        snapshot.quote_tier(snapshot.load_book(FIX), force=True, buffer_dir=c.buffer_dir)
        yield c


def build(ctx, t):
    return sec.assemble({tier: sec.compute(tier, ctx, t) for tier in sec.SCREEN.tiers}, dict(META), t)


def panel(p, pid):
    return next(q for q in p["panels"] if q["id"] == pid)


def kv(p, pid):
    return {i["k"]: i["v"] for i in panel(p, pid)["items"]}


def test_params_list_every_traded_ticker(ctx):
    assert sec.SCREEN.params(ctx) == ["AAA.F", "BBB.F", "CCC.F", "DDD.F"]


def test_open_position_panels_and_contribution(ctx):
    p = build(ctx, "AAA.F")
    assert [q["id"] for q in p["panels"]] == ["quote", "mine", "price", "risk", "trades"]
    book = snapshot.load_book(FIX)
    q = snapshot.quote_tier(book, buffer_dir=ctx.buffer_dir)
    pos = next(x for x in q["positions"] if x["ticker"] == "AAA.F")
    real = (book["realized"].get("AAA.F") or {}).get("pnl_eur", 0.0)
    div = sum(d["eur"] for d in q["dividends"] if d["ticker"] == "AAA.F")
    want = (pos["unrealized_pnl"] + real + div) / q["acct"]["gross_deposits"] * 100
    assert kv(p, "mine")["CONTRIB pp"] == pytest.approx(want)
    assert [r["side"] for r in panel(p, "trades")["rows"]] == ["BUY", "BUY"]
    json.dumps(p, allow_nan=False)


def test_closed_position_shows_realized_only(ctx):
    p = build(ctx, "CCC.F")
    assert p["context"]["text"].startswith("CLOSED POSITION")
    assert set(kv(p, "mine")) == {"REALIZED", "DIVIDENDS (NET)", "CONTRIB pp"}
    assert kv(p, "mine")["REALIZED"] == pytest.approx(80.0)


def test_dividends_count_in_position_and_contribution(tmp_path, monkeypatch):
    fakes_yf.install(monkeypatch)
    monkeypatch.setitem(fakes_yf.DIVIDENDS, "AAA.F", pd.Series([2.0], index=pd.to_datetime(["2025-06-02"])))
    with time_machine.travel("2026-06-30 14:00:00+00:00", tick=False):
        c = Ctx(force=True, buffer_dir=tmp_path / "buffer", portfolio_csv=FIX, equity_log=None)
        book = snapshot.load_book(FIX)
        snapshot.dividends(book, force=True, buffer_dir=c.buffer_dir)
        q = snapshot.quote_tier(book, force=True, buffer_dir=c.buffer_dir)
        p = build(c, "AAA.F")
    pos = next(x for x in q["positions"] if x["ticker"] == "AAA.F")
    real = (book["realized"].get("AAA.F") or {}).get("pnl_eur", 0.0)
    net = 30.0 * (1 - 0.26375)                        # 15 shares x 2.0, after German tax
    assert kv(p, "mine")["DIVIDENDS (NET)"] == pytest.approx(net)
    assert kv(p, "mine")["CONTRIB pp"] == pytest.approx(
        (pos["unrealized_pnl"] + real + net) / q["acct"]["gross_deposits"] * 100)


def test_price_chart_marks_trades(ctx):
    ch = panel(build(ctx, "AAA.F"), "price")
    buys = [y for s in ch["series"] if s["name"] == "BUY" for y in s["y"] if y is not None]
    assert len(buys) == 2


def test_sec_with_empty_history_degrades(ctx, monkeypatch):
    monkeypatch.setattr(sec, "_history", lambda t, ctx, adjusted=True: pd.Series(dtype=float))
    p = build(ctx, "AAA.F")
    assert kv(p, "quote")["52W HIGH"] is None and kv(p, "quote")["1Y"] is None
    assert kv(p, "risk")["VOL 1Y"] is None and kv(p, "risk")["MAX DD 1Y"] is None
    assert panel(p, "price")["x"] == [] and len(panel(p, "trades")["rows"]) == 2
    json.dumps(p, allow_nan=False)


def test_sec_is_private_and_unflagged_for_export():
    assert sec.SCREEN.public is False and sec.SCREEN.fkey is None


def test_52w_position_stays_in_range_when_live_price_exceeds_daily_high():
    hist = pd.Series([100.0, 110.0, 120.0], index=pd.to_datetime(["2026-06-26", "2026-06-29", "2026-06-30"]))
    q = {"quote": {"price": 130.0, "prev_close": 120.0, "date": "2026-06-30"}, "stale": False}
    items = {i["k"]: i["v"] for i in sec._quote(q, hist)["items"]}
    assert items["52W POS"] == pytest.approx(100.0) and items["52W HIGH"] == 130.0
    q["quote"]["price"] = 90.0
    items = {i["k"]: i["v"] for i in sec._quote(q, hist)["items"]}
    assert items["52W POS"] == pytest.approx(0.0) and items["52W LOW"] == 90.0


def test_trade_before_history_start_gets_no_marker():
    hist = pd.Series([10.0, 11.0, 12.0], index=pd.to_datetime(["2026-06-26", "2026-06-29", "2026-06-30"]))
    txns = [{"date": "2020-01-02", "action": "buy", "shares": 1, "pps": 5, "price": 5},
            {"date": "2026-06-29", "action": "buy", "shares": 1, "pps": 11, "price": 11}]
    buys = [y for s in sec._price(hist, txns)["series"] if s["name"] == "BUY" for y in s["y"] if y is not None]
    assert buys == [11.0]


def test_sec_chart_history_is_requested_unadjusted(ctx, monkeypatch):
    import yfinance
    seen, real = [], yfinance.download

    def spy(tickers, **kw):
        seen.append((tuple([tickers] if isinstance(tickers, str) else tickers), kw.get("auto_adjust")))
        return real(tickers, **kw)
    monkeypatch.setattr(yfinance, "download", spy)
    sec.compute("daily", ctx, "AAA.F")
    own = [adj for tks, adj in seen if tks == ("AAA.F",)]
    assert False in own and True in own          # real closes for the chart, total return for RISK


def test_markers_and_quote_use_real_closes_risk_uses_total_return(ctx, monkeypatch):
    real = fakes_yf.series("AAA.F")
    adj = real * np.linspace(0.80, 1.0, len(real))      # dividend-adjusted: older prices sit lower
    monkeypatch.setattr(sec, "_history", lambda t, c, adjusted=True: adj if adjusted else real)
    p = build(ctx, "AAA.F")
    buy = next(s for s in panel(p, "price")["series"] if s["name"] == "BUY")
    txns = [t for t in snapshot.load_book(FIX)["transactions"] if t["ticker"] == "AAA.F"]
    want = [round(float(real[bar_on_or_after(real.index, t["date"])]), 4) for t in txns]
    assert sorted(y for y in buy["y"] if y is not None) == pytest.approx(sorted(want))
    past = real[real.index <= real.index[-1] - pd.Timedelta(days=365)]
    assert kv(p, "quote")["1Y"] == pytest.approx((real.iloc[-1] / past.iloc[-1] - 1) * 100)
    yr = adj[adj.index >= adj.index[-1] - pd.Timedelta(days=365)]
    assert kv(p, "risk")["MAX DD 1Y"] == pytest.approx(float((yr / yr.cummax() - 1).min() * 100))


def test_price_chart_marks_a_bonus_as_a_buy():
    hist = pd.Series([10.0, 11.0, 12.0], index=pd.to_datetime(["2026-06-26", "2026-06-29", "2026-06-30"]))
    txns = [{"date": "2026-06-26", "action": "buy", "shares": 1, "pps": 10, "price": 10},
            {"date": "2026-06-29", "action": "bonus", "shares": 0.1, "pps": 11, "price": 1.1}]
    ser = {s["name"]: s["y"] for s in sec._price(hist, txns)["series"]}
    assert [y for y in ser["BUY"] if y is not None] == [10.0, 11.0]
    assert all(y is None for y in ser["SELL"])


def test_contribution_counts_the_bonus_value_as_gain(tmp_path, monkeypatch):
    """Portfolio total P&L counts bonus shares as gain; a position's CONTRIB must too, or the
    contributions stop adding up to ROI."""
    fakes_yf.install(monkeypatch)
    csv = tmp_path / "portfolio.csv"
    csv.write_text(FIX.read_text().rstrip("\n") + "\n2026-04-01,AAA.F,bonus,0.1,12.00,120.00\n")
    with time_machine.travel("2026-06-30 14:00:00+00:00", tick=False):
        c = Ctx(force=True, buffer_dir=tmp_path / "buffer", portfolio_csv=csv, equity_log=None)
        q = snapshot.quote_tier(snapshot.load_book(csv), force=True, buffer_dir=c.buffer_dir)
        pages = {t: build(c, t) for t in sec.SCREEN.params(c)}
    pos = next(x for x in q["positions"] if x["ticker"] == "AAA.F")
    assert kv(pages["AAA.F"], "mine")["CONTRIB pp"] == pytest.approx(
        (pos["unrealized_pnl"] + 12.0) / q["acct"]["gross_deposits"] * 100)
    assert sum(kv(p, "mine")["CONTRIB pp"] for p in pages.values()) == pytest.approx(q["acct"]["simple_roi"])


def test_params_add_the_watchlist_and_accept_any_universe_ticker(ctx, tmp_path):
    from dataclasses import replace
    from monitor.alerts import watchlist
    wl = tmp_path / "watchlist.json"
    watchlist.add(wl, "FNTN.DE", "freenet")
    c = replace(ctx, watchlist=wl)
    assert sec.SCREEN.params(c) == ["AAA.F", "BBB.F", "CCC.F", "DDD.F", "FNTN.DE"]
    assert all(sec.SCREEN.accepts(c, t) for t in ("AAA.F", "CCC.F", "FNTN.DE", "RHM.DE", "AAPL"))
    assert not any(sec.SCREEN.accepts(c, t) for t in ("ZZZ.F", "DEAD", "^GSPC", ""))


def test_a_name_you_never_traded_shows_quote_price_and_risk_only(ctx):
    p = build(ctx, "RHM.DE")
    assert p["title"] == "RHM.DE · Rheinmetall"
    assert [(q["id"], q["n"]) for q in p["panels"]] == [("quote", 1), ("price", 2), ("risk", 3)]
    assert p["context"]["text"] == "NOT IN YOUR BOOK · PRICES IN THE LISTING CURRENCY"
    assert kv(p, "quote")["LAST"] == pytest.approx(fakes_yf.series("RHM.DE", "2026-06-30").iloc[-1])
    assert set(kv(p, "risk")) == {"VOL 1Y", "BETA SPX", "MAX DD 1Y"}
    assert not any(s["name"] in ("BUY", "SELL") and any(y is not None for y in s["y"])
                   for s in panel(p, "price")["series"])
    json.dumps(p, allow_nan=False)


@pytest.mark.parametrize("tkr,bench", [("AAPL", "^GSPC"), ("RHM.DE", "CSPX.AS"), ("7203.T", None)])
def test_beta_of_a_name_not_in_the_book_never_mixes_currencies(ctx, monkeypatch, tkr, bench):
    """A US home line against ^GSPC, a euro line against CSPX.AS (EUR), anything else "—"."""
    from monitor.portfolio import riskmodel
    seen, real = [], sec.cached_price_history

    def spy(tickers, **kw):
        seen.extend(tickers)
        return real(tickers, **kw)
    monkeypatch.setattr(sec, "cached_price_history", spy)
    beta = kv(build(ctx, tkr), "risk")["BETA SPX"]
    assert set(seen) - {tkr} == ({bench} if bench else set())
    if bench is None:
        assert beta is None
    else:
        want = riskmodel.beta_of(sec._history(tkr, ctx), real([bench], period="5y", buffer_dir=ctx.buffer_dir)[bench])
        assert want is not None and beta == pytest.approx(want, abs=1e-4)


def test_beta_of_a_held_position_stays_on_the_eur_proxy(ctx, monkeypatch):
    seen, real = [], sec.cached_price_history
    monkeypatch.setattr(sec, "cached_price_history", lambda ts, **kw: (seen.extend(ts), real(ts, **kw))[1])
    build(ctx, "AAA.F")
    assert set(seen) - {"AAA.F"} == {"CSPX.AS"}


def test_a_holding_no_map_knows_is_named_from_yahoo(ctx, tmp_path, monkeypatch):
    import dataclasses

    import monitor.data.yahoo as Y
    monkeypatch.setattr(Y, "fetch_info", lambda t: {"name": "Zed AG", "sector": None, "country": None}
                        if t == "ZZZ.DE" else None)
    csv = tmp_path / "p.csv"
    csv.write_text(FIX.read_text().rstrip("\n") + "\n2026-03-02,ZZZ.DE,buy,10,1000.00,100.00\n")
    p = build(dataclasses.replace(ctx, portfolio_csv=csv), "ZZZ.DE")
    assert p["title"] == "ZZZ.DE · Zed AG"
