"""OPT payload: structure, target preference, drift/ticket arithmetic, infeasibility, privacy."""
import dataclasses
import json
from pathlib import Path

import numpy as np
import pytest
import time_machine

from monitor import config
from monitor.screens import opt
from monitor.screens.base import Ctx
from monitor.server.redact import public_view
from tests import fakes_yf

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "portfolio_small.csv"
META = {"computed_at": "2026-06-30T14:00:00", "tiers": {}, "code_version": "x", "prefs": {"target": "HRP"}}


@pytest.fixture
def parts(tmp_path, monkeypatch):
    fakes_yf.install(monkeypatch)
    with time_machine.travel("2026-06-30 14:00:00+00:00", tick=False):
        ctx = Ctx(force=True, buffer_dir=tmp_path / "buffer", portfolio_csv=FIX, equity_log=None)
        yield {t: opt.compute(t, ctx) for t in opt.SCREEN.tiers}


def panel(p, pid):
    return next(q for q in p["panels"] if q["id"] == pid)


def kv(p, pid):
    return {i["k"]: i["v"] for i in panel(p, pid)["items"]}


def test_panels_and_default_target(parts):
    p = opt.assemble(parts, dict(META))
    assert [q["id"] for q in p["panels"]] == ["target", "weights", "portfolios", "frontier", "ahead", "followed",
                                              "verdict", "ticket"]
    assert kv(p, "target")["TARGET"] == "HRP"
    hl = [c["k"] for c in panel(p, "weights")["cols"] if c.get("hl")]
    assert hl == ["hrp"]


def test_strict_json(parts):
    json.dumps(opt.assemble(parts, dict(META)), allow_nan=False)


def test_target_preference_moves_highlight_and_drift(parts):
    p = opt.assemble(parts, {**META, "prefs": {"target": "RP"}})
    w = panel(p, "weights")
    assert [c["k"] for c in w["cols"] if c.get("hl")] == ["rp"]
    drift = sum(abs(r["rp"] - r["now"]) for r in w["rows"]) / 2
    assert kv(p, "target")["DRIFT"] == pytest.approx(drift, abs=0.01)


def test_ticket_trades_net_to_zero_and_count_fees(parts):
    p = opt.assemble(parts, dict(META))
    t, n_names = panel(p, "ticket"), len(panel(p, "weights")["rows"])
    skipped = n_names - len(t["rows"])                       # sub-€1 trades are left out
    assert abs(sum(r["d_eur"] for r in t["rows"])) <= 0.05 + opt.MIN_TRADE_EUR * skipped
    assert f"{len(t['rows'])} ORDERS · FEES ~€{len(t['rows']) * config.ORDER_FEE_EUR:.0f}" in t["context"]["text"]
    assert all(r["side"] == ("BUY" if r["d_eur"] > 0 else "SELL") for r in t["rows"])


def test_ticket_fees_use_the_configured_order_fee(parts, monkeypatch):
    monkeypatch.setattr(config, "ORDER_FEE_EUR", 2.5)              # settings: order_fee_eur
    t = panel(opt.assemble(parts, dict(META)), "ticket")
    assert t["rows"] and f"FEES ~€{len(t['rows']) * 2.5:.0f} ·" in t["context"]["text"]


def test_infeasible_target_shows_dash_and_empty_ticket(parts):
    m = parts["daily"]["model"]
    broken = {**parts, "daily": {"model": dataclasses.replace(m, portfolios={**m.portfolios, "HRP": None})}}
    p = opt.assemble(broken, dict(META))
    assert kv(p, "target")["VOL TGT"] is None
    t = panel(p, "ticket")
    assert t["rows"] == [] and "TARGET INFEASIBLE" in t["context"]["text"]


def test_public_view_has_no_ticket_or_euros(parts):
    p = opt.assemble(parts, dict(META))
    pub = public_view(p)
    assert [q["id"] for q in pub["panels"]] == ["target", "weights", "portfolios", "frontier", "ahead", "followed",
                                                "verdict"]
    s = json.dumps(pub)
    for r in panel(p, "ticket")["rows"]:
        assert str(r["now_eur"]) not in s


def test_opt_needs_two_positions_fails_readably(tmp_path, monkeypatch):
    fakes_yf.install(monkeypatch)
    one = tmp_path / "one.csv"
    one.write_text("Date,Ticker,Action,Shares,Price,PricePerShare\n2025-01-06,AAA.F,buy,10,1000.00,100.00\n", encoding="utf-8")
    with time_machine.travel("2026-06-30 14:00:00+00:00", tick=False):
        ctx = Ctx(force=True, buffer_dir=tmp_path / "b", portfolio_csv=one, equity_log=None)
        with pytest.raises(ValueError, match="at least two priced positions"):
            opt.compute("daily", ctx)


def test_ticket_follows_the_portfolios_cursor(parts):
    p = opt.assemble(parts, dict(META))
    port, t = panel(p, "portfolios"), panel(p, "ticket")
    assert port["cursor"] == "HRP" and port["enter"] == "TARGET {key}"
    assert t["follows"] == "portfolios" and "{key}" in t["title"]
    assert set(t["rows_by_key"]) == {"NOW", *opt.config.PORTFOLIOS}             # EQUAL has a ticket too
    assert t["rows_by_key"]["NOW"]["rows"] == [] and "CURRENT MIX" in t["rows_by_key"]["NOW"]["context"]
    assert t["rows_by_key"]["HRP"]["rows"] == t["rows"]                 # the TARGET's ticket is the default
    rp = t["rows_by_key"]["RP"]
    n_names = len(panel(p, "weights")["rows"])
    assert abs(sum(r["d_eur"] for r in rp["rows"])) <= 0.05 + opt.MIN_TRADE_EUR * (n_names - len(rp["rows"]))
    assert rp["context"].startswith(f"{len(rp['rows'])} ORDERS")


def test_infeasible_portfolio_ticket_says_so(parts):
    m = parts["daily"]["model"]
    broken = {**parts, "daily": {"model": dataclasses.replace(m, portfolios={**m.portfolios, "RP": None})}}
    t = panel(opt.assemble(broken, dict(META)), "ticket")
    assert t["rows_by_key"]["RP"] == {"rows": [], "context": "RP INFEASIBLE — NO TRADES"}


def test_ticket_choices_never_reach_the_public_view(parts):
    pub = public_view(opt.assemble(parts, dict(META)))
    assert "ticket" not in [q["id"] for q in pub["panels"]]
    # only REBALANCE? keeps its per-portfolio rows in public (public columns, no header); never a ticket, never €
    assert [q["id"] for q in pub["panels"] if "rows_by_key" in q] == ["verdict"] and "€" not in json.dumps(pub)


def test_weights_name_a_holding_from_the_tr_universe(tmp_path, monkeypatch):
    fakes_yf.install(monkeypatch)
    csv = tmp_path / "p.csv"
    csv.write_text(FIX.read_text(encoding="utf-8").rstrip("\n") + "\n2026-03-02,RHM.DE,buy,2,1000.00,500.00\n", encoding="utf-8")
    with time_machine.travel("2026-06-30 14:00:00+00:00", tick=False):
        ctx = Ctx(force=True, buffer_dir=tmp_path / "buffer", portfolio_csv=csv, equity_log=None)
        p = opt.assemble({t: opt.compute(t, ctx) for t in opt.SCREEN.tiers}, dict(META))
    names = {r["tkr"]: r["name"] for r in panel(p, "weights")["rows"]}
    assert names["RHM.DE"] == "Rheinmetall" and names["AAA.F"] == "AAA.F"


def test_help_fee_follows_settings(parts, monkeypatch):
    monkeypatch.setattr(config, "ORDER_FEE_EUR", 2.5)
    by = {h["h"]: h["body"] for h in opt.assemble(parts, dict(META))["help"]}
    assert "about €2.5 fee per order" in by["TICKET"]


def test_panels_number_top_to_bottom_with_the_what_if(parts):
    p = opt.assemble(parts, dict(META))
    assert [(q["id"], q["n"]) for q in p["panels"]] == [("target", 1), ("weights", 2), ("portfolios", 3),
        ("frontier", 4), ("ahead", 5), ("followed", 6), ("verdict", 7), ("ticket", 8)]
    json.dumps(p, allow_nan=False)
    assert len(json.dumps(p)) < 300_000


def test_the_screen_fits_four_rows_in_reading_order(parts):
    """One window: TARGET over PORTFOLIOS beside WEIGHTS · the FRONTIER (half the width) beside AHEAD and FOLLOWED ·
    REBALANCE? beside TICKET — placed as the browser's grid (and the terminal client's) auto-places them, numbered in
    that order."""
    from monitor.tui.panels import place
    p = opt.assemble(parts, dict(META))
    at = {q["id"]: (r, c) for q, r, c, _, _ in place(p["panels"])}
    assert at == {"target": (0, 0), "weights": (0, 5), "portfolios": (1, 0), "frontier": (2, 0), "ahead": (2, 6),
                  "followed": (2, 9), "verdict": (3, 0), "ticket": (3, 5)}


def test_followed_draws_you_equal_and_the_cursor_row_measured_like_port(parts):
    """FOLLOWED: YOUR line and EQUAL always, the cursor portfolio on top; `inv` and each line's `cash` let the chart
    measure a window by the ROI formula — private, as on PORT. PORTFOLIOS' FOLLOWED column is each line's last point."""
    p = opt.assemble(parts, dict(META))
    f = panel(p, "followed")
    assert f["follows"] == "portfolios" and f["range"] == "ALL" and "1M" in f["ranges"]
    assert [s["name"] for s in f["series"]] == ["YOU", "EQUAL"] and f["series"][0]["role"] == "primary"
    assert [s["name"] for s in f["series_by_key"]["HRP"]["series"]] == ["HRP"]
    assert f["series_by_key"]["NOW"]["series"] == [] == f["series_by_key"]["EQUAL"]["series"]
    n = len(f["x"])
    assert len(f["inv"]) == n and all(len(s["y"]) == len(s["cash"]) == n for s in f["series"])
    lines = {s["name"]: s for s in f["series"] + [b for k in config.PORTFOLIOS for b in f["series_by_key"][k]["series"]]}
    rows = {r["id"]: r for r in panel(p, "portfolios")["rows"]}
    last = lambda s: next(v for v in reversed(s["y"]) if v is not None)
    assert rows["NOW"]["past"] == pytest.approx(last(lines["YOU"]), abs=0.01)
    for k in config.PORTFOLIOS:
        assert rows[k]["past"] == pytest.approx(last(lines[k]), abs=0.01), k
    assert lines["YOU"]["cash"][-1] > 0                     # the fixture sells: its money out is in YOUR line
    pub = panel(public_view(p), "followed")
    assert "inv" not in pub and not any("cash" in s for s in pub["series"])
    assert [s["name"] for s in pub["series_by_key"]["HRP"]["series"]] == ["HRP"]


def test_ahead_draws_keeping_and_switching_as_their_own_fans(parts):
    """AHEAD: your mix kept and the cursor portfolio switched to, each its median and 90 % band of % change from
    today on the same paths — keeping starts at 0, a switch below it by its costs; a portfolio with less volatility
    than yours has the narrower band (the frontier's trade-off over time)."""
    p = opt.assemble(parts, dict(META))
    a, w, m = panel(p, "ahead"), parts["daily"]["whatif"], parts["daily"]["model"]
    assert a["follows"] == "portfolios" and a["horizons"] == ["6M", "1Y", "3Y"] and a["horizon"] == "1Y"
    assert a["today_idx"] == 0 and len(a["x"]) == 37
    total = sum(parts["quote"]["values"].values())
    vals = np.array([parts["quote"]["values"].get(t, 0.0) for t in m.universe])
    now_vol = m.perf(vals / vals.sum()).vol
    for k in config.PORTFOLIOS:
        blk = a["series_by_key"][k]
        assert [(s["name"], s["kind"]) for s in blk["series"]] == [("NOW 90%", "band"), (f"{k} 90%", "band"),
                                                                   ("NOW", "line"), (k, "line")]
        keep, band, med = blk["series"][2], blk["series"][1], blk["series"][3]
        assert keep["y"][0] == 0.0 and "note" not in blk          # the title names it: AHEAD → {key}
        assert med["y"][0] == pytest.approx(-w["costs"][k]["total"] / total * 100, abs=0.01)
        assert all(lo <= y <= hi for lo, y, hi in zip(band["lo"], med["y"], band["hi"]))
        dvol = m.portfolios[k].vol - now_vol
        if abs(dvol) > 0.01:
            wide = lambda s: s["hi"][12] - s["lo"][12]
            assert (wide(band) - wide(blk["series"][0])) * dvol > 0, k
    now = a["series_by_key"]["NOW"]["series"]
    assert [s["name"] for s in now] == ["NOW 90%", "NOW"] and a["series_by_key"]["NOW"]["note"] == "KEEP YOUR MIX"
    pub = panel(public_view(p), "ahead")
    assert "lo" in pub["series_by_key"]["HRP"]["series"][0] and pub["today_idx"] == 0


def test_the_frontier_shows_every_holding_and_the_move_to_the_cursor_row(parts):
    """The FRONTIER: each holding alone (its volatility, its BL return), and — following PORTFOLIOS — a line from
    NOW to the cursor row with what the move changes on the title."""
    p = opt.assemble(parts, dict(META))
    f, m = panel(p, "frontier"), parts["daily"]["model"]
    assets = [s for s in f["series"] if s.get("role") == "asset"]
    assert [s["name"] for s in assets] == list(m.universe)
    i = list(m.universe).index(assets[0]["name"])
    assert assets[0]["x"][0] == pytest.approx(float(np.sqrt(m.cov.values[i, i])) * 100, abs=1e-2)   # in %, as all
    assert assets[0]["y"][0] == pytest.approx(float(m.mu_bl[i]) * 100, abs=1e-2)
    assert f["follows"] == "portfolios" and f["series_by_key"]["NOW"] == {"series": [], "note": "YOUR MIX", "vis": "public"}
    now = next(s for s in f["series"] if s["name"] == "NOW")
    hrp, move = m.portfolios["HRP"], f["series_by_key"]["HRP"]["series"][0]
    assert move["role"] == "move" and move["x"] == [now["x"][0], pytest.approx(hrp.vol * 100, abs=1e-2)]
    assert move["y"] == [now["y"][0], pytest.approx(hrp.exp_ret * 100, abs=1e-2)]
    assert f["series_by_key"]["HRP"]["note"].startswith("NOW → HRP: VOL ")
    pub = panel(public_view(p), "frontier")                       # named axes, in the public view too
    assert (pub["xlabel"], pub["ylabel"]) == ("VOLATILITY (1Y, ANNUAL)", "EXPECTED RETURN* (BL, ANNUAL)")


def test_the_verdict_follows_the_cursor_and_keeps_euros_private(parts):
    p = opt.assemble(parts, dict(META))
    v = panel(p, "verdict")
    assert v["follows"] == "portfolios" and [r["h"] for r in v["rows_by_key"]["HRP"]["rows"]] == ["6M", "1Y", "3Y"]
    assert v["rows_by_key"]["NOW"]["rows"] == [] and v["rows_by_key"]["NOW"]["context"] == "THIS IS YOUR CURRENT MIX"
    assert v["rows_by_key"]["HRP"]["context"].startswith("COSTS €")
    # the horizons in their own order, not the alphabet's (1Y < 3Y < 6M)
    assert v["sort"] == ["_i", "asc"] and [r["_i"] for r in v["rows_by_key"]["HRP"]["rows"]] == [0, 1, 2]
    assert {r["verdict"] for r in v["rows_by_key"]["HRP"]["rows"]} <= {"WORTH IT", "MARGINAL", "NOT WORTH IT"}
    pub = panel(public_view(p), "verdict")
    assert {c["k"] for c in pub["cols"]} == {"h", "p", "be", "verdict", "risk"} and "€" not in json.dumps(pub)


def test_an_infeasible_portfolio_has_no_fan_and_says_so(parts, monkeypatch):
    m = parts["daily"]["model"]
    w = parts["daily"]["whatif"]
    w["verdicts"].pop("BLSAME", None)
    w["ahead"].pop("BLSAME", None)
    monkeypatch.setitem(m.portfolios, "BLSAME", None)
    p = opt.assemble(parts, dict(META))
    assert panel(p, "verdict")["rows_by_key"]["BLSAME"]["context"].startswith("BLSAME INFEASIBLE")   # a string, as TICKET
    assert panel(p, "ahead")["series_by_key"]["BLSAME"]["series"] == []
    assert panel(p, "ahead")["series_by_key"]["BLSAME"]["note"] == "BLSAME INFEASIBLE"
    assert panel(p, "frontier")["series_by_key"]["BLSAME"] == {"series": [], "note": "BLSAME INFEASIBLE", "vis": "public"}


def test_a_what_if_failure_leaves_the_rest_of_opt(tmp_path, monkeypatch):
    fakes_yf.install(monkeypatch)
    monkeypatch.setattr(opt.whatif, "build", lambda *a, **k: (_ for _ in ()).throw(ValueError("no history")))
    with time_machine.travel("2026-06-30 14:00:00+00:00", tick=False):
        ctx = Ctx(force=True, buffer_dir=tmp_path / "buffer", portfolio_csv=FIX, equity_log=None)
        parts = {t: opt.compute(t, ctx) for t in opt.SCREEN.tiers}
    p = opt.assemble(parts, dict(META))
    assert panel(p, "followed")["context"]["text"] == "WHAT-IF UNAVAILABLE — ValueError"
    assert panel(p, "ahead")["context"]["text"] == "WHAT-IF UNAVAILABLE — ValueError"
    assert "no history" not in json.dumps(p)                       # the message (a path, maybe) is logged, never shown
    assert panel(p, "verdict")["rows"] == [] and panel(p, "ticket")["n"] == 8 and panel(p, "weights")["rows"]


def test_a_failed_what_if_keeps_the_last_good_one_marked_stale(tmp_path, monkeypatch):
    """A throttled Yahoo answer never replaces good data: the last good what-if is kept, said to be stale."""
    fakes_yf.install(monkeypatch)
    with time_machine.travel("2026-06-30 14:00:00+00:00", tick=False):
        ctx = Ctx(force=True, buffer_dir=tmp_path / "buffer", portfolio_csv=FIX, equity_log=None)
        good = opt.compute("daily", ctx)["whatif"]
        assert "error" not in good and "stale" not in good
        monkeypatch.setattr(opt.whatif, "build", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("throttled")))
        parts = {t: opt.compute(t, ctx) for t in opt.SCREEN.tiers}
    w = parts["daily"]["whatif"]
    assert w["stale"] == "ConnectionError" and set(w["ahead"]) == set(good["ahead"])
    p = opt.assemble(parts, dict(META))
    assert panel(p, "followed")["context"]["text"].startswith("STALE — LAST GOOD")
    assert panel(p, "ahead")["context"]["text"].startswith("STALE")
    assert panel(p, "verdict")["rows_by_key"]["HRP"]["rows"]



def test_the_public_verdict_follows_the_public_cursor_and_carries_no_euros(parts):
    """The public view keeps each portfolio's verdict rows (public columns only, no € header) and where the cursor
    starts — else it showed the TARGET's verdict under whichever row the cursor started on."""
    p = opt.assemble(parts, dict(META))
    pub = public_view(p)
    assert panel(pub, "portfolios")["cursor"] == "HRP"
    v = panel(pub, "verdict")
    assert set(v["rows_by_key"]) == {"NOW", *config.PORTFOLIOS}
    rows = v["rows_by_key"]["MINVAR"]["rows"]
    assert [r["h"] for r in rows] and all(set(r) <= {"h", "p", "be", "verdict", "risk"} for r in rows)
    assert all(b["context"] == "" for b in v["rows_by_key"].values()) and "€" not in json.dumps(v)



def test_the_help_says_the_backtest_pays_no_tax(parts):
    body = next(h["body"] for h in opt.assemble(parts, dict(META))["help"] if h["h"] == "FOLLOWED")
    assert "no tax" in body


def test_rebalance_and_ticket_share_a_row(parts):
    p = opt.assemble(parts, dict(META))
    assert panel(p, "verdict")["span"] + panel(p, "ticket")["span"] == 12


def test_the_what_if_charts_send_hundredths_of_a_percent(parts):
    """2 decimals of a % are more than the chart can show, and keep the payload far under its cap as history grows."""
    p = opt.assemble(parts, dict(META))
    f, a = panel(p, "followed"), panel(p, "ahead")
    blocks = [s for c in (f, a) for b in c["series_by_key"].values() for s in b["series"]] + f["series"]
    nums = [v for s in blocks for k in ("y", "lo", "hi", "cash") for v in s.get(k) or [] if v is not None] + f["inv"]
    assert nums and all(round(v, 2) == v for v in nums if v is not None)


def test_an_older_what_if_without_the_followed_cash_says_so(parts):
    """A last-good what-if kept from before FOLLOWED existed has no cash per line: it says so instead of failing."""
    w = parts["daily"]["whatif"]
    for k in ("past_cash", "inv", "you_cash", "ahead"):
        w.pop(k)
    p = opt.assemble(parts, dict(META))
    assert panel(p, "followed")["context"]["text"] == "WHAT-IF UNAVAILABLE — RECOMPUTING"
    assert panel(p, "ahead")["series_by_key"] == {} and panel(p, "verdict")["rows_by_key"]["HRP"]["rows"]
