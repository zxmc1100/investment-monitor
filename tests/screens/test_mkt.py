"""MKT payload over the yfinance fakes and the fixture universe: layout, board formats, movers,
my names / sectors / events, privacy of the public view, degraded inputs."""
import json
from dataclasses import replace
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
import time_machine

from monitor.alerts import watchlist
from monitor.portfolio import meta as pmeta
from monitor.screens import mkt
from monitor.screens.base import Ctx
from monitor.server.redact import public_view
from tests import fakes_yf

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "portfolio_small.csv"
META = {"computed_at": "2026-06-30T14:00:00", "tiers": {}, "code_version": "x", "prefs": {}}


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    fakes_yf.install(monkeypatch)
    monkeypatch.setitem(fakes_yf.CALENDARS, "AAA.F", {"Earnings Date": [date(2026, 7, 1)]})
    monkeypatch.setitem(fakes_yf.CALENDARS, "RHM.DE", {"Earnings Date": [date(2026, 8, 6)],
                                                       "Ex-Dividend Date": date(2026, 7, 10)})
    monkeypatch.setitem(fakes_yf.DIVIDENDS, "RHM.DE", pd.Series([8.1], index=pd.to_datetime(["2026-05-13"])))
    monkeypatch.setitem(pmeta.PORTFOLIO_SECTOR_MAP, "AAA.F", "Information Technology")
    wl = tmp_path / "watchlist.json"
    watchlist.add(wl, "RHM.DE", "Rheinmetall")
    watchlist.add(wl, "FNTN.DE", "freenet")                  # a watched small cap: never a public mover
    with time_machine.travel("2026-06-30 14:00:00+00:00", tick=False):
        yield Ctx(force=True, buffer_dir=tmp_path / "buffer", portfolio_csv=FIX, equity_log=None, watchlist=wl)


def build(ctx):
    return mkt.assemble({t: mkt.compute(t, ctx) for t in mkt.SCREEN.tiers}, dict(META))


def panel(p, pid):
    return next(q for q in p["panels"] if q["id"] == pid)


def test_nine_panels_in_three_rows(ctx):
    p = build(ctx)
    assert [q["id"] for q in p["panels"]] == ["indices", "fx", "cmdty", "gainers", "losers", "spikes",
                                             "names", "sectors", "events"]
    assert [q["n"] for q in p["panels"]] == list(range(1, 10))
    assert [q["span"] for q in p["panels"]] == [6, 3, 3, 4, 4, 4, 6, 3, 3]
    json.dumps(p, allow_nan=False)


def test_board_rows_formats_and_changes(ctx):
    p = build(ctx)
    idx = panel(p, "indices")
    assert [r["name"] for r in idx["rows"]][:3] == ["S&P 500", "Nasdaq 100", "DAX"] and idx["sort"] == ["n", "asc"]
    q = fakes_yf.series("^GSPC", "2026-06-30")
    spx = idx["rows"][0]
    assert spx["lvl"] == pytest.approx(q.iloc[-1]) and spx["day"] == pytest.approx((q.iloc[-1] / q.iloc[-2] - 1) * 100)
    assert len(spx["spark"]) > 40 and spx["spark"][0] == 100.0
    fx = {r["name"]: r for r in panel(p, "fx")["rows"]}
    assert fx["EUR/USD"]["_fmt"] == {"lvl": "num:4"}
    tnx = fakes_yf.series("^TNX", "2026-06-30")
    assert fx["US 10Y"]["_fmt"]["day"] == "bp+"
    assert fx["US 10Y"]["day"] == pytest.approx((tnx.iloc[-1] - tnx.iloc[-2]) * 100)
    assert [r["name"] for r in panel(p, "cmdty")["rows"]] == ["Gold", "Silver", "WTI", "Brent", "Bitcoin", "Ether"]


def test_movers_rank_the_liquid_universe_and_open_sec(ctx):
    p = build(ctx)
    g, l, s = panel(p, "gainers"), panel(p, "losers"), panel(p, "spikes")
    liquid = {"NVDA", "AAPL", "RHM.DE", "ISP.MI", "BAS.DE", "GLE.PA"}
    assert {r["tkr"] for r in g["rows"]} == liquid and "FNTN.DE" not in {r["tkr"] for r in g["rows"] + s["rows"]}
    days = [r["day"] for r in g["rows"]]
    assert days == sorted(days, reverse=True) and [r["day"] for r in l["rows"]] == sorted(days)
    vols = [r["volx"] for r in s["rows"]]
    assert vols == sorted(vols, reverse=True) and len(vols) == 6
    assert g["enter"] == "SEC {key}" and next(r for r in g["rows"] if r["tkr"] == "RHM.DE")["sector"] == "Industrials"
    assert g["context"]["text"].startswith("LIQUID 6 · BAR 30 JUN 26")


def test_my_names_held_and_watched(ctx):
    rows = {r["tkr"]: r for r in panel(build(ctx), "names")["rows"]}
    assert {t: r["mark"] for t, r in rows.items()} == {"AAA.F": "H", "BBB.F": "H", "DDD.F": "H",
                                                        "RHM.DE": "W", "FNTN.DE": "W"}
    assert rows["AAA.F"]["next"] == "EARN 01 JUL 26" and rows["RHM.DE"]["next"] == "EX-DIV 10 JUL 26"
    assert 0 <= rows["RHM.DE"]["pos52"] <= 100 and rows["RHM.DE"]["name"] == "Rheinmetall"
    assert rows["AAA.F"]["volx"] is not None and rows["AAA.F"]["last"] > 0


def test_my_sectors_weights_and_proxy(ctx):
    rows = {r["sector"]: r for r in panel(build(ctx), "sectors")["rows"]}
    assert sum(r["wt"] for r in rows.values()) == pytest.approx(100.0)
    tech = rows["Info Tech"]
    xlk = fakes_yf.series("XLK", "2026-06-30")
    assert tech["etf"] == "XLK" and tech["eday"] == pytest.approx((xlk.iloc[-1] / xlk.iloc[-2] - 1) * 100)
    assert tech["myday"] is not None and rows["Unknown"]["etf"] == "—" and rows["Unknown"]["eday"] is None


def test_events_next_30_days_with_tomorrow_highlighted(ctx):
    rows = panel(build(ctx), "events")["rows"]
    assert [(r["tkr"], r["event"], r["when"]) for r in rows] == [("AAA.F", "EARNINGS", "TOMORROW"),
                                                                 ("RHM.DE", "EX-DIV 8.10", "IN 10D")]
    assert rows[0]["_hot"] is True and "_hot" not in rows[1]


def test_public_view_has_no_private_panels_or_watched_small_caps(ctx):
    pub = public_view(build(ctx))
    text = json.dumps(pub, allow_nan=False)
    assert [q["id"] for q in pub["panels"]] == ["indices", "fx", "cmdty", "gainers", "losers", "spikes", "sectors"]
    assert "FNTN.DE" not in text and "freenet" not in text and "€" not in text and '"last"' not in text
    fx = next(q for q in pub["panels"] if q["id"] == "fx")
    assert next(r for r in fx["rows"] if r["name"] == "US 10Y")["_fmt"]["day"] == "bp+"
    assert len(json.dumps(build(ctx))) < 300_000


def test_movers_unavailable_shows_empty_panels_not_zeros(ctx, monkeypatch):
    monkeypatch.setattr(mkt, "cached_movers", lambda ts, **k: ({}, None, True))
    p = build(ctx)
    assert panel(p, "gainers")["rows"] == [] and panel(p, "spikes")["rows"] == []
    assert panel(p, "gainers")["context"]["text"] == "MOVERS UNAVAILABLE — RETRY WITHIN 15 MIN"
    assert all(r["volx"] is None and r["d5"] is None for r in panel(p, "names")["rows"])


def test_dead_watched_ticker_degrades_to_a_flagged_row(ctx, monkeypatch):
    real = mkt.cached_quotes

    def quotes(ts, **k):
        q, stale, at = real(ts, **k)
        return {t: (None if t == "FNTN.DE" else v) for t, v in q.items()}, stale, at
    monkeypatch.setattr(mkt, "cached_quotes", quotes)
    p = build(ctx)
    row = next(r for r in panel(p, "names")["rows"] if r["tkr"] == "FNTN.DE")
    assert row["_stale"] is True and row["last"] is None and row["day"] is None and row["pos52"] is None
    assert p["meta"]["stale"]["FNTN.DE"] is None


def test_without_a_ledger_the_market_still_shows(ctx, tmp_path):
    from dataclasses import replace
    p = build(replace(ctx, portfolio_csv=tmp_path / "missing.csv"))
    assert [r["tkr"] for r in panel(p, "names")["rows"]] == ["FNTN.DE", "RHM.DE"]
    assert panel(p, "sectors")["rows"] == [] and panel(p, "gainers")["rows"]


def _history_without(monkeypatch, drop):
    real = mkt.cached_price_history

    def hist(ts, **k):
        h = real(ts, **k)
        return h.drop(columns=[c for c in h.columns if drop is None or c in drop])
    monkeypatch.setattr(mkt, "cached_price_history", hist)


@pytest.mark.parametrize("drop", [{"^TNX"}, None], ids=["one-ticker-missing", "no-history"])
def test_board_survives_tickers_missing_from_daily_history(ctx, monkeypatch, drop):
    _history_without(monkeypatch, drop)
    p = build(ctx)
    assert [q["n"] for q in p["panels"]] == list(range(1, 10))
    json.dumps(p, allow_nan=False)
    tnx = next(r for r in panel(p, "fx")["rows"] if r["name"] == "US 10Y")
    assert tnx["w1"] is None and tnx["ytd"] is None and tnx["spark"] is None and tnx["_stale"] is True
    if drop is None:
        assert all(r["spark"] is None for r in panel(p, "indices")["rows"])


def test_public_view_drops_stale_meta_and_hot_flags(ctx, monkeypatch):
    real = mkt.cached_quotes
    monkeypatch.setattr(mkt, "cached_quotes", lambda ts, **k: (lambda q, s, a: (
        {t: (None if t == "FNTN.DE" else v) for t, v in q.items()}, s, a))(*real(ts, **k)))
    p = build(ctx)
    assert "FNTN.DE" in p["meta"]["stale"] and any(r.get("_hot") for r in panel(p, "events")["rows"])
    pub = public_view(p)
    assert "stale" not in pub.get("meta", {}) and "_hot" not in json.dumps(pub)


def test_my_names_flag_a_quote_the_alert_loop_stopped_refreshing(ctx):
    """MY NAMES reads the shared buffer unforced (the alert check refreshes every name each minute):
    a buffered quote older than three checks keeps its last-good price and is flagged STALE."""
    ctx = replace(ctx, force=False)
    build(replace(ctx, force=True))                              # 14:00 — every name quoted
    with time_machine.travel("2026-06-30 14:03:00+00:00", tick=False):
        p = build(ctx)
    assert not any(r.get("_stale") for r in panel(p, "names")["rows"]) and "RHM.DE" not in p["meta"]["stale"]
    with time_machine.travel("2026-06-30 14:03:01+00:00", tick=False):
        p = build(ctx)
    rows = panel(p, "names")["rows"]
    assert all(r.get("_stale") and r["last"] for r in rows)
    assert {"AAA.F", "RHM.DE"} <= set(p["meta"]["stale"]) and isinstance(p["meta"]["stale"]["RHM.DE"], str)


def _parts(ctx):
    return {t: mkt.compute(t, ctx) for t in mkt.SCREEN.tiers}


def test_a_zero_price_is_a_price_not_a_missing_quote(ctx):
    parts = _parts(ctx)
    parts["quote"]["board"]["^VIX"] = {**parts["quote"]["board"]["^VIX"], "price": 0.0}
    row = next(r for r in panel(mkt.assemble(parts, dict(META)), "fx")["rows"] if r["name"] == "VIX")
    assert row["lvl"] == 0.0                                      # not the last daily close


def test_movers_header_omits_an_unknown_last_good_time(ctx):
    parts = _parts(ctx)
    parts["quote"]["movers_stale"] = True
    for bad in (None, "garbage"):
        parts["quote"]["movers_at"] = bad
        text = panel(mkt.assemble(parts, dict(META)), "gainers")["context"]["text"]
        assert text.endswith("· STALE") and "LAST GOOD" not in text
    parts["quote"]["movers_at"] = "2026-06-30T13:45:10"
    assert panel(mkt.assemble(parts, dict(META)), "gainers")["context"]["text"].endswith("STALE, LAST GOOD 13:45")


def test_my_sectors_survive_a_position_with_no_day_change(ctx):
    """A held name without a quote (and so no DAY %) drops out of its sector's MY DAY; a sector whose
    names are all unquoted shows MY DAY None, never 0 — and the payload stays strict JSON."""
    parts = _parts(ctx)
    parts["quote"]["mine"].pop("AAA.F")                           # the only Info Tech position
    p = mkt.assemble(parts, dict(META))
    json.dumps(p, allow_nan=False)
    rows = {r["sector"]: r for r in panel(p, "sectors")["rows"]}
    assert rows["Info Tech"]["myday"] is None and rows["Info Tech"]["eday"] is not None
    assert any(r["myday"] is not None for s, r in rows.items() if s != "Info Tech")
    assert sum(r["wt"] for r in rows.values()) == pytest.approx(100.0)


def test_weekend_board_label_ignores_the_round_the_clock_crypto_quotes(ctx):
    with time_machine.travel("2026-06-27 12:00:00+00:00", tick=False):        # Saturday
        parts = _parts(ctx)
        b = parts["quote"]["board"]
        for t, d in (("GC=F", "2026-06-26"), ("CL=F", "2026-06-26"), ("BTC-EUR", "2026-06-27"),
                     ("ETH-EUR", "2026-06-27")):
            b[t] = {**b[t], "date": d}
        row = {r["name"]: r for r in panel(mkt.assemble(parts, dict(META)), "cmdty")["rows"]}
        assert panel(mkt.assemble(parts, dict(META)), "cmdty")["context"]["text"] == "DAY = FRI 26 JUN"
        assert row["Bitcoin"]["day"] is not None


def test_mover_sectors_use_the_short_labels():
    assert [mkt._short_sector(s) for s in ("Communication Services", "Consumer Cyclical", "Financial Services",
                                           "Information Technology", "Industrials", "—")] == [
        "Comm Svcs", "Cons Cyclical", "Financials", "Info Tech", "Industrials", "—"]


def test_a_name_the_forced_refetch_prices_is_not_left_stale(ctx, monkeypatch):
    """A new WATCH makes the unforced names read fetch; if that read loses a name the forced board
    fetch then prices, the name is live — not STALE from the first read."""
    import monitor.data.yahoo as Y
    build(ctx)                                                   # 14:00 — buffer filled
    with time_machine.travel("2026-06-30 18:00:00+00:00", tick=False):
        watchlist.add(ctx.watchlist, "BAS.DE", "BASF")           # not buffered: the names read fetches
        real, n = Y.fetch_quotes, [0]

        def flaky(ts):
            n[0] += 1
            return {t: (None if n[0] == 1 and t == "RHM.DE" else q) for t, q in real(ts).items()}
        monkeypatch.setattr(Y, "fetch_quotes", flaky)
        p = build(ctx)
    assert n[0] >= 2 and "RHM.DE" not in p["meta"]["stale"]
    assert not next(r for r in panel(p, "names")["rows"] if r["tkr"] == "RHM.DE").get("_stale")


def test_a_forced_first_paint_refetches_names_the_alert_loop_left_stale(ctx):
    """Terminal reopened hours later: the forced MKT compute refreshes the stale names with the board,
    so MY NAMES is live on the first paint; an unforced compute still reads the buffer (STALE)."""
    build(ctx)                                                   # 14:00 — buffer filled
    with time_machine.travel("2026-06-30 18:00:00+00:00", tick=False):
        p = build(replace(ctx, force=False))
        assert all(r.get("_stale") for r in panel(p, "names")["rows"])
        p = build(ctx)                                           # forced
        assert not any(r.get("_stale") for r in panel(p, "names")["rows"])
        assert not {"AAA.F", "RHM.DE", "FNTN.DE"} & set(p["meta"]["stale"])
        assert all(r["lvl"] for r in panel(p, "indices")["rows"])


def test_my_names_and_sectors_place_a_holding_no_map_knows(ctx, tmp_path, monkeypatch):
    import monitor.data.yahoo as Y
    monkeypatch.setattr(Y, "fetch_info", lambda t: {"name": "BASF SE", "sector": "Basic Materials",
                                                    "country": "Germany"} if t == "BAS.DE" else None)
    csv = tmp_path / "p.csv"
    csv.write_text("Date,Ticker,Action,Shares,Price,PricePerShare\n2026-01-05,BAS.DE,buy,10,450.00,45.00\n", encoding="utf-8")
    p = build(replace(ctx, portfolio_csv=csv))
    assert {r["tkr"]: r["name"] for r in panel(p, "names")["rows"]}["BAS.DE"] == "BASF"   # universe name
    assert [r["sector"] for r in panel(p, "sectors")["rows"]] == ["Materials"]           # Yahoo sector, GICS name


def test_a_watched_only_name_honours_your_names_before_the_watchlist(ctx, monkeypatch):
    from monitor.data.instruments import COMPANY_NAMES
    monkeypatch.setitem(COMPANY_NAMES, "RHM.DE", "Rheinmetall AG (mine)")     # settings [names]
    rows = {r["tkr"]: r for r in panel(build(ctx), "names")["rows"]}
    assert rows["RHM.DE"]["mark"] == "W" and rows["RHM.DE"]["name"].startswith("Rheinmetall AG (min")   # clipped to the column
    assert rows["FNTN.DE"]["name"] == "freenet"                               # no override: the watch name


def test_without_any_universe_the_movers_panels_say_so(ctx, tmp_path, monkeypatch):
    from monitor import config
    monkeypatch.setattr(config, "UNIVERSE_DIR", tmp_path / "no-universe")
    p = build(ctx)
    for pid in ("gainers", "losers", "spikes"):
        q = panel(p, pid)
        assert q["rows"] == [] and q["empty"] == mkt.NO_UNIVERSE and q["context"]["text"] == mkt.NO_UNIVERSE
    assert "\n" not in mkt.NO_UNIVERSE and "data/universe" in mkt.NO_UNIVERSE
