"""Stored parts follow your inputs (review fix 1): a screen that reads the book or the settings
stores each part under code version + input fingerprint (portfolio.csv, interest.csv, settings), so
an edited CSV or settings.toml makes its tiers due exactly like a code change — and an unchanged
one does not. settings.toml is re-read when its mtime changes: no restart needed."""
import dataclasses

import pytest

from monitor import config
from monitor.data import instruments
from monitor.screens import SCREENS
from monitor.screens.base import Ctx, input_fingerprint
from monitor.server.engine import Engine
from monitor.server.store import Store
from tests.server.helpers import Recorder, make_screen

CSV = "Date,Ticker,Action,Shares,Price,PricePerShare\n2025-01-06,AAA.F,buy,10,1000.00,100.00\n"


def _book(tmp_path, text=CSV):
    csv = tmp_path / "input" / "portfolio.csv"
    csv.parent.mkdir(exist_ok=True)
    csv.write_text(text, encoding="utf-8")
    return csv


def test_fingerprint_moves_with_portfolio_interest_and_settings(tmp_path, settings_file):
    ctx = Ctx(portfolio_csv=_book(tmp_path))
    a = input_fingerprint(ctx)
    assert input_fingerprint(ctx) == a                                  # unchanged inputs: same
    ctx.portfolio_csv.write_text(CSV + "2025-02-03,BBB.F,buy,1,10.00,10.00\n", encoding="utf-8")
    b = input_fingerprint(ctx)
    assert b != a
    (tmp_path / "input" / "interest.csv").write_text("Date,Amount\n2025-01-31,1.00\n", encoding="utf-8")
    c = input_fingerprint(ctx)
    assert c != b
    settings_file.write_text("order_fee_eur = 2.0\n", encoding="utf-8")
    assert input_fingerprint(ctx) != c


def test_an_edited_csv_makes_the_inputs_tiers_due_and_an_unchanged_one_does_not(tmp_path, monkeypatch):
    scr, calls, _ = make_screen(tmp_path, monkeypatch)
    scr = dataclasses.replace(scr, uses_inputs=True)
    csv = _book(tmp_path)
    eng = Engine({"FAKE": scr}, Store(tmp_path / "store"), Recorder(), ctx=Ctx(buffer_dir=tmp_path, portfolio_csv=csv))
    eng.ensure_fresh("FAKE")
    assert eng.runner.wait_idle(5) and eng.payload("FAKE") is not None
    assert eng.due_tiers("FAKE") == []                                  # nothing changed: nothing due
    csv.write_text(CSV + "2025-02-03,BBB.F,buy,1,10.00,10.00\n", encoding="utf-8")
    assert eng.due_tiers("FAKE") == ["quote", "daily"]
    old = eng.payload("FAKE")
    assert eng.build_stored("FAKE") is None                             # stale parts never assemble
    assert eng.payload("FAKE") == old                                   # the screen keeps its last payload meanwhile
    eng.ensure_fresh("FAKE")
    assert eng.runner.wait_idle(5) and eng.due_tiers("FAKE") == [] and len(calls) == 4


def test_a_screen_that_ignores_inputs_is_not_disturbed(tmp_path, monkeypatch):
    scr, _, _ = make_screen(tmp_path, monkeypatch)
    csv = _book(tmp_path)
    eng = Engine({"FAKE": scr}, Store(tmp_path / "store"), Recorder(), ctx=Ctx(buffer_dir=tmp_path, portfolio_csv=csv))
    eng.ensure_fresh("FAKE")
    eng.runner.wait_idle(5)
    csv.write_text(CSV + "2025-02-03,BBB.F,buy,1,10.00,10.00\n", encoding="utf-8")
    assert eng.due_tiers("FAKE") == []


def test_port_daily_is_due_after_a_csv_edit(tmp_path):
    csv = _book(tmp_path)
    ctx = Ctx(buffer_dir=tmp_path, portfolio_csv=csv, equity_log=None)
    eng = Engine({"PORT": SCREENS["PORT"]}, Store(tmp_path / "store"), Recorder(), ctx=ctx, workers=0)
    for tier in ("quote", "daily"):
        eng.store.put_part("PORT", tier, {"x": 1}, SCREENS["PORT"].version(ctx))
    assert eng.due_tiers("PORT") == []
    csv.write_text(CSV.replace("10,1000.00", "12,1200.00"), encoding="utf-8")
    assert "daily" in eng.due_tiers("PORT")


@pytest.mark.parametrize("sid", ["PORT", "OPT", "RISK", "MKT", "SEC"])
def test_screens_that_read_the_book_or_settings_declare_it(sid):
    assert SCREENS[sid].uses_inputs
    assert not SCREENS["ALRT"].uses_inputs


def test_a_settings_edit_is_picked_up_without_a_restart(tmp_path, settings_file):
    ctx = Ctx(portfolio_csv=_book(tmp_path))
    before = input_fingerprint(ctx)
    assert config.ORDER_FEE_EUR == 1.0 and "XYZ.F" not in instruments.TICKER_MAP
    settings_file.write_text('order_fee_eur = 0.5\n[tickers]\n"XYZ.F" = "XYZ.DE"\n[names]\n"XYZ.F" = "XYZ AG"\n', encoding="utf-8")
    assert input_fingerprint(ctx) != before                            # the check re-reads the file
    assert config.ORDER_FEE_EUR == 0.5
    assert instruments.TICKER_MAP["XYZ.F"] == "XYZ.DE" and instruments.COMPANY_NAMES["XYZ.F"] == "XYZ AG"
    assert instruments.TICKER_MAP["EUNL.F"] == "IWDA.AS"                # built-ins stay
    settings_file.write_text("", encoding="utf-8")                                        # entries removed again
    input_fingerprint(ctx)
    assert config.ORDER_FEE_EUR == 1.0 and "XYZ.F" not in instruments.TICKER_MAP
    assert "XYZ.F" not in instruments.COMPANY_NAMES
