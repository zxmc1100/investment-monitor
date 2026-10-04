"""OPT payload: structure, target preference, drift/ticket arithmetic, infeasibility, privacy."""
import dataclasses
import json
from pathlib import Path

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
    assert [q["id"] for q in p["panels"]] == ["target", "weights", "frontier", "portfolios", "ticket"]
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
    assert [q["id"] for q in pub["panels"]] == ["target", "weights", "frontier", "portfolios"]
    s = json.dumps(pub)
    for r in panel(p, "ticket")["rows"]:
        assert str(r["now_eur"]) not in s


def test_opt_needs_two_positions_fails_readably(tmp_path, monkeypatch):
    fakes_yf.install(monkeypatch)
    one = tmp_path / "one.csv"
    one.write_text("Date,Ticker,Action,Shares,Price,PricePerShare\n2025-01-06,AAA.F,buy,10,1000.00,100.00\n")
    with time_machine.travel("2026-06-30 14:00:00+00:00", tick=False):
        ctx = Ctx(force=True, buffer_dir=tmp_path / "b", portfolio_csv=one, equity_log=None)
        with pytest.raises(ValueError, match="at least two priced positions"):
            opt.compute("daily", ctx)


def test_ticket_follows_the_portfolios_cursor(parts):
    p = opt.assemble(parts, dict(META))
    port, t = panel(p, "portfolios"), panel(p, "ticket")
    assert port["cursor"] == "HRP" and port["enter"] == "TARGET {key}"
    assert t["follows"] == "portfolios" and "{key}" in t["title"]
    assert set(t["rows_by_key"]) == {"NOW", "MINVAR", "RP", "HRP", "BLSHARPE", "BLSAME"}
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
    assert "rows_by_key" not in json.dumps(pub) and "€" not in json.dumps(pub)


def test_weights_name_a_holding_from_the_tr_universe(tmp_path, monkeypatch):
    fakes_yf.install(monkeypatch)
    csv = tmp_path / "p.csv"
    csv.write_text(FIX.read_text().rstrip("\n") + "\n2026-03-02,RHM.DE,buy,2,1000.00,500.00\n")
    with time_machine.travel("2026-06-30 14:00:00+00:00", tick=False):
        ctx = Ctx(force=True, buffer_dir=tmp_path / "buffer", portfolio_csv=csv, equity_log=None)
        p = opt.assemble({t: opt.compute(t, ctx) for t in opt.SCREEN.tiers}, dict(META))
    names = {r["tkr"]: r["name"] for r in panel(p, "weights")["rows"]}
    assert names["RHM.DE"] == "Rheinmetall" and names["AAA.F"] == "AAA.F"


def test_help_fee_follows_settings(parts, monkeypatch):
    monkeypatch.setattr(config, "ORDER_FEE_EUR", 2.5)
    by = {h["h"]: h["body"] for h in opt.assemble(parts, dict(META))["help"]}
    assert "about €2.5 fee per order" in by["TICKET"]
