"""/api/trades — TRADES' writes over HTTP: read with an etag; add, edit, delete, preview, import, start fresh.
Every write names the etag it was made on (409 when the file changed), is refused with one line when it would
leave a wrong file (400), is Origin-checked like every write, and recomputes TRADES at once and every portfolio
screen after it. Temp input dirs only; no network."""
import shutil
import time
import warnings
from pathlib import Path

import pytest

warnings.filterwarnings("ignore", message="Using `httpx` with `starlette.testclient`")
from fastapi.testclient import TestClient

from monitor.portfolio.tradebook import TradeBook
from monitor.screens import SCREENS
from monitor.screens.base import Ctx
from monitor.server.app import create_app
from monitor.server.engine import Engine
from monitor.server.store import Store
from tests.server.helpers import Recorder, make_screen

EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "portfolio.example.csv"
HEAD = "Date,Ticker,Action,Shares,Price,PricePerShare"


@pytest.fixture
def env(tmp_path, monkeypatch):
    web = tmp_path / "web"
    (web / "app").mkdir(parents=True)
    (web / "index.html").write_text('<meta name="im-mode" content="live">', encoding="utf-8")
    csv = tmp_path / "input" / "portfolio.csv"
    csv.parent.mkdir()
    shutil.copy(EXAMPLE, csv)
    port, calls, _ = make_screen(tmp_path, monkeypatch, sid="PORT")      # stands in for PORT: no network
    import dataclasses
    port = dataclasses.replace(port, uses_inputs=True, needs_portfolio=True, fkey=1)
    from monitor import config
    monkeypatch.setattr(config, "TRADES_SETTLE_S", 0.05)                # recomputes coalesce; quick in a test
    rec = Recorder()
    eng = Engine({"PORT": port, "TRADES": SCREENS["TRADES"]}, Store(tmp_path / "store"), rec,
                 ctx=Ctx(portfolio_csv=csv, buffer_dir=tmp_path / "buf", equity_log=None))
    book = TradeBook(csv, example=EXAMPLE, quote=lambda t: {"price": 1.0} if t != "ZZZ.F" else None,
                     isin=lambda code: None)
    c = TestClient(create_app(eng, web_dir=web, trade_book=book), base_url="http://127.0.0.1")
    return c, eng, csv, calls, rec


def etag(c):
    return c.get("/api/trades").json()["etag"]


def test_get_lists_rows_ids_and_the_etag(env):
    c, _, csv, _, _ = env
    body = c.get("/api/trades").json()
    assert len(body["rows"]) == 15 and body["example"] is True and body["error"] is None
    assert all(len(r["id"]) == 12 for r in body["rows"]) and len(body["etag"]) == 40


def test_add_saves_recomputes_trades_at_once_and_queues_the_portfolio_screens(env):
    c, eng, csv, calls, rec = env
    eng.compute_now("PORT")                                     # PORT was shown before
    assert c.get("/api/screen/TRADES").status_code == 200       # inline: computed in the request
    calls.clear()
    r = c.post("/api/trades", json={"etag": etag(c), "trade": {"ticker": "SAP.DE", "action": "buy", "shares": 4,
                                                                "pps": 240, "date": "2026-01-02"}})
    assert r.status_code == 200 and r.json()["text"] == "BUY 4 SAP.DE · €240.00/sh = €960.00"
    assert "2026-01-02,SAP.DE,buy,4,960.00,240.00" in csv.read_text(encoding="utf-8")
    p = c.get("/api/screen/TRADES").json()
    assert p["etag"] == r.json()["etag"] and r.json()["id"] in {row["id"] for row in p["panels"][-1]["rows"]}
    assert any(e["type"] == "screen" and e["id"] == "TRADES" for e in rec.events)
    time.sleep(0.3)
    assert eng.runner.wait_idle(5) and {t for t, _ in calls} == {"quote", "daily"}     # PORT recomputed
    assert (csv.parent / "backups").is_dir()


def test_a_stale_etag_is_409_and_a_wrong_trade_400_with_one_line(env):
    c, _, csv, _, _ = env
    old = etag(c)
    c.post("/api/trades", json={"etag": old, "trade": {"ticker": "SAP.DE", "action": "buy", "shares": 1, "total": 9}})
    r = c.post("/api/trades", json={"etag": old, "trade": {"ticker": "SAP.DE", "action": "buy", "shares": 1, "total": 9}})
    assert r.status_code == 409 and r.json()["detail"]["error"] == "YOUR TRADES CHANGED MEANWHILE — RELOADED: CHECK AND SAVE AGAIN"
    r = c.post("/api/trades", json={"etag": etag(c), "trade": {"ticker": "SAP.DE", "action": "sell", "shares": 9,
                                                                "pps": 1, "date": "2025-09-02"}})
    assert r.status_code == 400 and r.json()["detail"]["error"] == "SELL 9 SAP.DE ON 2025-09-02: ONLY 2 HELD THEN"
    r = c.post("/api/trades", json={"trade": {"ticker": "SAP.DE"}})
    assert r.status_code == 400 and r.json()["detail"]["error"] == "ETAG REQUIRED — RELOAD"
    r = c.post("/api/trades", json={"etag": etag(c), "trade": "SAP.DE"})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "MALFORMED REQUEST"


def test_a_warning_comes_back_with_the_saved_trade(env):
    c, _, _, _, _ = env
    r = c.post("/api/trades", json={"etag": etag(c), "trade": {"ticker": "ZZZ.F", "action": "buy", "shares": 1, "total": 9}})
    assert r.status_code == 200 and r.json()["warnings"] == ["NO YAHOO QUOTE FOR ZZZ.F — CHECK THE TICKER (SAVED ANYWAY)"]


def test_edit_and_delete_by_id(env):
    c, _, csv, _, _ = env
    rows = c.get("/api/trades").json()["rows"]
    last = rows[-1]
    r = c.put(f"/api/trades/{last['id']}", json={"etag": etag(c), "trade": {
        "ticker": "SIE.DE", "action": "buy", "shares": 3, "total": 690, "date": last["date"]}})
    assert r.status_code == 200 and c.get("/api/trades").json()["rows"][-1]["shares"] == 3.0
    r = c.request("DELETE", f"/api/trades/{r.json()['id']}", json={"etag": etag(c)})
    assert r.status_code == 200 and len(c.get("/api/trades").json()["rows"]) == 14
    r = c.request("DELETE", f"/api/trades/{last['id']}", json={"etag": etag(c)})
    assert r.status_code == 404 and r.json()["detail"]["error"] == "NO SUCH TRADE — IT WAS CHANGED OR DELETED; RELOADED"


def test_preview_writes_nothing_import_writes_the_good_rows(env):
    c, _, csv, _, _ = env
    before = csv.read_bytes()
    text = "Datum;Ticker;Typ;Anzahl;Betrag\n02.01.2026;SAP.DE;Kauf;4;961,00\n03.01.2026;SAP.DE;Dividende;4;10\n"
    r = c.post("/api/trades/preview", json={"text": text, "mode": "append"})
    body = r.json()
    assert r.status_code == 200 and (body["ok"], body["bad"], body["delimiter"], body["decimal"]) == (1, 1, ";", "comma")
    assert body["rows"][1]["error"] == "UNKNOWN ACTION 'DIVIDENDE' — BUY, SELL OR BONUS"
    assert csv.read_bytes() == before
    r = c.post("/api/trades/import", json={"etag": etag(c), "text": text, "mode": "append"})
    assert r.status_code == 200 and (r.json()["added"], r.json()["skipped"], r.json()["lines"]) == (1, 1, [3])
    assert r.json()["header"] is True                       # the box keeps the header above the lines to fix
    r = c.post("/api/trades/import", json={"etag": etag(c), "text": "2025-01-15,SAP.DE,buy,4,961\n", "mode": "replace"})
    assert r.status_code == 200 and len(c.get("/api/trades").json()["rows"]) == 1
    r = c.post("/api/trades/import", json={"etag": etag(c), "text": "x", "mode": "sideways"})
    assert r.status_code == 400 and r.json()["detail"]["error"] == "MODE MUST BE APPEND OR REPLACE"


def test_start_fresh_empties_the_file_and_portfolio_screens_say_press_6(env):
    c, eng, csv, calls, _ = env
    r = c.post("/api/trades/reset", json={"etag": etag(c)})
    assert r.status_code == 200 and r.json()["removed"] == 15 and csv.read_text(encoding="utf-8") == HEAD + "\n"
    r = c.get("/api/screen/PORT")
    assert r.status_code == 202 and r.json()["reason"].startswith("NO TRADES YET — press 6 (TRADES)")
    assert not [p for p in c.get("/api/screen/TRADES").json()["panels"] if p["id"] == "fresh"]   # no banner


def test_trade_writes_refuse_another_origin(env):
    c, _, csv, _, _ = env
    before = csv.read_bytes()
    bad = {"Origin": "http://evil.example"}
    for method, url, body in (("POST", "/api/trades", {"etag": "x", "trade": {}}), ("PUT", "/api/trades/a", {"etag": "x"}),
                              ("DELETE", "/api/trades/a", {"etag": "x"}), ("POST", "/api/trades/import", {"etag": "x"}),
                              ("POST", "/api/trades/reset", {"etag": "x"}), ("POST", "/api/trades/preview", {"text": ""})):
        r = c.request(method, url, json=body, headers=bad)
        assert r.status_code == 403, url
    assert csv.read_bytes() == before


def test_the_default_quote_check_reads_the_quote_buffer_first(tmp_path, monkeypatch):
    """The terminal's own TradeBook asks for one quote per new ticker through the quote buffer: a ticker PORT
    already quoted costs no network."""
    import json

    from monitor.data import yahoo as Y
    from monitor.server import app as A
    monkeypatch.setattr(Y, "fetch_quotes", lambda tickers: (_ for _ in ()).throw(AssertionError("network")))
    (tmp_path / "quotes.json").write_text(json.dumps({"SAP.DE": {"price": 250.0, "prev_close": 249.0, "date": "2026-10-02",
                                                                 "ts": "2026-10-02T17:00:00"}}), encoding="utf-8")
    assert A.quote_check("SAP.DE", tmp_path)["price"] == 250.0


def test_a_locked_file_is_423_with_one_line(env, monkeypatch):
    from monitor.portfolio import tradebook as TB
    c, _, csv, _, _ = env
    real = TB.write_bytes_durable
    monkeypatch.setattr(TB, "write_bytes_durable", lambda path, data, **k: (_ for _ in ()).throw(PermissionError(13, "in use"))
                        if Path(path).name == "portfolio.csv" else real(path, data, **k))
    r = c.post("/api/trades/reset", json={"etag": etag(c)})
    assert r.status_code == 423 and r.json()["detail"]["error"].startswith("CANNOT WRITE portfolio.csv")


def test_a_recompute_hiccup_after_a_write_is_logged_not_the_writes_failure(env, monkeypatch, caplog):
    c, eng, csv, _, _ = env
    monkeypatch.setattr(eng, "inputs_changed", lambda **k: (_ for _ in ()).throw(RuntimeError("store unreadable")))
    with caplog.at_level("WARNING"):
        r = c.post("/api/trades/reset", json={"etag": etag(c)})
    assert r.status_code == 200 and csv.read_text(encoding="utf-8") == HEAD + "\n" and "store unreadable" in caplog.text


def test_undo_puts_the_file_back(env):
    c, _, csv, _, _ = env
    before = csv.read_bytes()
    r = c.post("/api/trades", json={"etag": etag(c), "trade": {"ticker": "SAP.DE", "action": "buy", "shares": 1, "total": 9}})
    assert c.get("/api/trades").json()["undo"]["what"].startswith("ADD BUY 1 SAP.DE")
    u = c.post("/api/trades/undo", json={"etag": r.json()["etag"]})
    assert u.status_code == 200 and u.json()["text"].startswith("UNDID: ADD BUY 1 SAP.DE") and csv.read_bytes() == before
    r = c.post("/api/trades/undo", json={"etag": "stale"})
    assert r.status_code == 409


def test_recomputes_after_a_burst_of_writes_coalesce_into_one(env, monkeypatch):
    from monitor import config
    monkeypatch.setattr(config, "TRADES_SETTLE_S", 1.5)       # wider than three saves on a slow runner (fsyncs)
    c, eng, csv, calls, rec = env
    eng.compute_now("PORT")
    calls.clear()
    for i in range(3):
        c.post("/api/trades", json={"etag": etag(c), "trade": {"ticker": "SAP.DE", "action": "buy", "shares": 1,
                                                                "total": 10 + i}})
    deadline = time.monotonic() + 10
    while not calls and time.monotonic() < deadline:          # the settled recompute fires once, 1.5 s after the last
        time.sleep(0.05)
    time.sleep(0.3)
    assert eng.runner.wait_idle(5) and sorted(calls) == [("daily", False), ("quote", True)]      # once, not three times
    assert len([e for e in rec.events if e["type"] == "screen" and e["id"] == "TRADES"]) >= 3   # TRADES at once, each


def test_a_broken_trades_file_never_blocks_the_terminal(tmp_path, monkeypatch):
    """One bad cell: the registry (SEC's ticker list reads the book) still answers, TRADES shows the error on top,
    PORT says it in one line — no 500 anywhere."""
    web = tmp_path / "web"
    (web / "app").mkdir(parents=True)
    (web / "index.html").write_text('<meta name="im-mode" content="live">', encoding="utf-8")
    csv = tmp_path / "input" / "portfolio.csv"
    csv.parent.mkdir()
    csv.write_text(HEAD + "\n2025-01-15,SAP.DE,buy,4,96x,240.00\n", encoding="utf-8")
    eng = Engine(dict(SCREENS), Store(tmp_path / "store"), Recorder(),
                 ctx=Ctx(portfolio_csv=csv, buffer_dir=tmp_path / "buf", equity_log=None))
    c = TestClient(create_app(eng, web_dir=web, trade_book=TradeBook(csv)), base_url="http://127.0.0.1")
    r = c.get("/api/screens")
    assert r.status_code == 200 and r.json()["params"]["SEC"] == []
    t = c.get("/api/screen/TRADES")
    assert t.status_code == 200 and t.json()["panels"][0]["id"] == "problem"
    for sid in ("PORT", "OPT", "RISK"):
        p = c.get(f"/api/screen/{sid}")
        assert p.status_code == 202 and p.json()["reason"] == (
            "YOUR TRADES FILE HAS AN ERROR — portfolio.csv row 2, column Price: '96x' is not a number · fix it in the "
            "file, or press 6 (TRADES) and UNDO the last change"), sid
    assert eng.runner.jobs() == []
