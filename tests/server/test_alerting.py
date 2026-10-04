"""Alert service, loop and API: the job end to end over the yfinance fakes, no repeats across a
restart, once per interval whatever the tab count, failures, validation, clean shutdown."""
import warnings
from datetime import date
from pathlib import Path

import pytest
import time_machine

warnings.filterwarnings("ignore", message="Using `httpx` with `starlette.testclient`")
from fastapi.testclient import TestClient

from monitor.alerts import store as astore
from monitor.screens.base import Ctx
from monitor.server.alerting import AlertLoop, AlertService
from monitor.server.app import create_app
from monitor.server.engine import Engine
from monitor.server.store import Store
from tests import fakes_yf
from tests.server.helpers import Recorder, make_screen

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "portfolio_small.csv"
NOW = "2026-06-30 14:00:00+00:00"                        # a Tuesday


class Tabs(Recorder):
    """Broker stand-in with a settable number of connected tabs."""
    subscribers = 0


def engine(tmp_path, broker=None, screens=None):
    return Engine(screens or {}, Store(tmp_path / "store"), broker or Tabs(),
                  ctx=Ctx(portfolio_csv=FIX, buffer_dir=tmp_path / "buf", equity_log=None,
                          watchlist=tmp_path / "watchlist.json", alerts=tmp_path / "alerts.json"))


def seed_port_and_opt(eng):
    """What the PORT quote part and the OPT payload hold once those screens have run. The alert job
    takes only PORT's positions (shares) from the part: its day_pct is from an older check."""
    eng.store.put_part("PORT", "quote", {
        "day_pct": -2.5, "positions": [{"ticker": "AAA.F", "shares": 15.0, "avg_cost": 103.3333, "position_value": 600.0},
                                       {"ticker": "BBB.F", "shares": 10.0, "avg_cost": 50.0, "position_value": 300.0},
                                       {"ticker": "DDD.F", "shares": 8.0, "avg_cost": 80.0, "position_value": 400.0}],
        "quotes": {"AAA.F": {"date": "2026-06-30"}, "DDD.F": {"date": "2026-06-30"}}}, "x")
    eng.store.put_payload("OPT", {"panels": [{"id": "target", "items": [{"k": "TARGET", "v": "HRP"},
                                                                       {"k": "DRIFT", "v": 12.0}]}]})


def yahoo_moves(monkeypatch, move, day="2026-06-30"):
    """Yahoo quotes every ticker at 100 × (1 + move) against a previous close of 100."""
    monkeypatch.setattr("monitor.data.yahoo.fetch_quotes", lambda ts: {
        t: {"price": round(100 * (1 + move), 4), "prev_close": 100.0, "date": day} for t in ts})


@pytest.fixture
def fakes(monkeypatch):
    fakes_yf.install(monkeypatch)
    monkeypatch.setitem(fakes_yf.CALENDARS, "AAA.F", {"Earnings Date": [date(2026, 7, 1)]})


def test_first_start_stores_the_default_rules(tmp_path):
    eng = engine(tmp_path)
    AlertService(eng, tmp_path / "alerts.json")
    assert [r["id"] for r in astore.load(tmp_path / "alerts.json")["rules"]] == ["A1", "A2", "A3", "A4", "A5", "A6"]
    assert ("ALRT", "watch") in eng.tasks


def test_job_fires_each_rule_once_and_publishes(tmp_path, fakes, monkeypatch):
    eng = engine(tmp_path)
    seed_port_and_opt(eng)
    yahoo_moves(monkeypatch, -0.025)                               # weights by shares: 45 %, 30 %, 24 %
    svc = AlertService(eng, tmp_path / "alerts.json")
    eng.runner.submit = lambda *a, **k: None                       # run the job inline below
    with time_machine.travel(NOW, tick=False):
        svc.add("AAA.F < 10000")
        new = svc.run()
        assert svc.run() == []                                     # conditions still true: no repeat
    assert sorted(e["msg"].split(" ")[0] for e in new) == ["AAA.F", "AAA.F", "AAA.F", "BBB.F", "DRIFT", "PORTFOLIO"]
    assert {e["rule"] for e in new} == {"A1", "A2", "A3", "A5", "A7"}
    ev = [e for e in eng.broker.events if e["type"] == "alerts"]
    assert ev[-2]["active"] == 6 and ev[-2]["down"] is True and len(ev[-2]["new"]) == 6
    assert ev[-1]["new"] == []


def test_no_flood_of_old_alerts_after_a_restart(tmp_path, fakes):
    eng = engine(tmp_path)
    seed_port_and_opt(eng)
    with time_machine.travel(NOW, tick=False):
        assert AlertService(eng, tmp_path / "alerts.json").run()
        again = AlertService(engine(tmp_path), tmp_path / "alerts.json")     # terminal closed, reopened
        seed_port_and_opt(again.engine)
        assert again.run() == []


def test_without_port_or_opt_payloads_the_port_rules_wait(tmp_path, fakes):
    eng = engine(tmp_path)
    svc = AlertService(eng, tmp_path / "alerts.json")
    with time_machine.travel(NOW, tick=False):
        new = svc.run()
    assert [e["rule"] for e in new] == ["A5"]                     # only the earnings tomorrow


@pytest.mark.parametrize("move,fires", [(0.005, False), (-0.03, True)])
def test_port_day_follows_this_checks_quotes_not_the_stored_day(tmp_path, fakes, monkeypatch, move, fires):
    """PORT last ran at -2.5 %; whichever screen is open now, PORT DAY acts on this minute's quotes."""
    eng = engine(tmp_path)
    seed_port_and_opt(eng)
    yahoo_moves(monkeypatch, move)
    with time_machine.travel(NOW, tick=False):
        new = AlertService(eng, tmp_path / "alerts.json").run()
    port = [e for e in new if e["rule"] == "A1"]
    assert bool(port) is fires
    if fires:
        assert port[0]["value"] == pytest.approx(-3.0) and port[0]["msg"] == "PORTFOLIO -3.00% TODAY"


def test_a_stored_part_without_positions_silences_port_day_and_weight(tmp_path, fakes, monkeypatch):
    eng = engine(tmp_path)
    eng.store.put_part("PORT", "quote", {"day_pct": -9.0, "positions": []}, "x")
    yahoo_moves(monkeypatch, -0.05)
    with time_machine.travel(NOW, tick=False):
        new = AlertService(eng, tmp_path / "alerts.json").run()
    assert {e["rule"] for e in new} == {"A4", "A5"}               # MOVE −5 % and the earnings; no A1 / A3


def test_add_validates_and_checks_at_once(tmp_path):
    eng = engine(tmp_path)
    svc = AlertService(eng, tmp_path / "alerts.json")
    eng.tasks[("ALRT", "watch")] = lambda force: None             # no network in this test
    with pytest.raises(ValueError, match="ALERT <TKR>"):
        svc.add("NVD.F <")
    with pytest.raises(ValueError, match="ZZZ.F: NOT A TRADEABLE TICKER"):
        svc.add("ZZZ.F < 5")
    assert svc.add("aaa.f > 5") == {"id": "A7", "kind": "LEVEL", "ticker": "AAA.F", "op": ">", "value": 5.0,
                                    "text": "AAA.F > 5"}
    assert svc.add("^GDAXI < 20000")["id"] == "A8" and svc.add("RHM.DE DD 20")["id"] == "A9"
    assert eng.runner.wait_idle(5)
    assert sum(1 for j in eng.runner.jobs() if j["screen"] == "ALRT" and j["tier"] == "watch") >= 1


@pytest.mark.parametrize("running_forced", [False, True])
def test_add_while_a_check_is_running_reruns_it(tmp_path, running_forced):
    """Even a FORCED check already running (the minute loop's) gets a rerun for the new rule."""
    import threading
    eng = engine(tmp_path)
    svc = AlertService(eng, tmp_path / "alerts.json")
    started, release, calls = threading.Event(), threading.Event(), []

    def slow(force):
        calls.append(force)
        started.set()
        release.wait(5)
    eng.tasks[("ALRT", "watch")] = slow
    eng.runner.submit("ALRT", "watch", force=running_forced)
    assert started.wait(5)
    with time_machine.travel(NOW, tick=False):
        # known ticker path avoids lookup: a PORT rule needs no ticker check
        svc.add("PORT DAY -3")
    release.set()
    assert eng.runner.wait_idle(5) and len(calls) == 2


def test_malformed_stored_port_part_only_silences_the_port_rules(tmp_path, fakes):
    eng = engine(tmp_path)
    eng.store.put_part("PORT", "quote", {"positions": "garbage"}, "x")
    svc = AlertService(eng, tmp_path / "alerts.json")
    with time_machine.travel(NOW, tick=False):
        new = svc.run()
    assert [e["rule"] for e in new] == ["A5"]


def test_publishes_are_ordered_with_the_stored_state(tmp_path, fakes):
    import threading
    eng = engine(tmp_path)
    seed_port_and_opt(eng)
    svc = AlertService(eng, tmp_path / "alerts.json")
    with time_machine.travel(NOW, tick=False):
        svc.run()
        threads = [threading.Thread(target=svc.ack, args=(k,)) for k in ("ALL", "A1", "A2", "A3")]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
    counts = [e["active"] for e in eng.broker.events if e["type"] == "alerts"]
    assert counts == sorted(counts, reverse=True) and counts[-1] == 0


def test_loop_runs_only_with_a_tab_and_once_per_interval(tmp_path):
    tabs, now = Tabs(), [0.0]
    eng = engine(tmp_path, tabs)
    submitted = []
    eng.runner.submit = lambda s, t, force=False: submitted.append((s, t))
    loop = AlertLoop(eng, interval=60, clock=lambda: now[0])
    assert loop.tick() is False and submitted == []               # terminal closed: nothing runs
    tabs.subscribers = 2                                          # two tabs open
    for now[0] in (1.0, 2.0, 30.0, 60.5, 61.0, 120.9, 121.0):
        loop.tick()
    assert submitted == [("ALRT", "watch")] * 3                  # t = 1, 61, 121


def test_a_failing_job_never_stops_the_loop(tmp_path):
    tabs, now = Tabs(), [0.0]
    eng = engine(tmp_path, tabs)
    tabs.subscribers = 1
    calls = []

    def boom(force):
        calls.append(force)
        raise RuntimeError("yahoo down")
    eng.tasks[("ALRT", "watch")] = boom
    loop = AlertLoop(eng, interval=60, clock=lambda: now[0])
    loop.tick()
    assert eng.runner.wait_idle(5)
    assert eng.runner.last_failure("ALRT").error == "RuntimeError: yahoo down"
    now[0] = 61.0
    assert loop.tick() is True and eng.runner.wait_idle(5) and len(calls) == 2


def test_put_and_assemble_stores_and_publishes(tmp_path, monkeypatch):
    scr, _, _ = make_screen(tmp_path, monkeypatch, sid="ALRT")
    eng = engine(tmp_path, screens={"ALRT": scr})
    eng.store.put_part("ALRT", "daily", {"d": 1}, scr.code_version())
    payload = eng.put_and_assemble("ALRT", "quote", {"rules": []})
    assert payload["parts"]["quote"] == {"rules": []}
    assert eng.broker.events[-1]["type"] == "screen"


@pytest.fixture
def web(tmp_path):
    w = tmp_path / "web"
    (w / "app").mkdir(parents=True)
    (w / "index.html").write_text('<meta name="im-mode" content="live">INDEX')
    return w


def test_alert_endpoints(tmp_path, web):
    eng = engine(tmp_path)
    app = create_app(eng, web_dir=web)
    eng.tasks[("ALRT", "watch")] = lambda force: None
    c = TestClient(app, base_url="http://127.0.0.1")
    body = c.get("/api/alerts").json()
    assert [r["text"] for r in body["rules"]][:2] == ["PORT DAY ≤ -2%", "PORT DRIFT ≥ 10pp"] and body["active"] == []
    r = c.post("/api/alerts", json={"text": "nonsense"})
    assert r.status_code == 400 and r.json()["detail"]["error"].startswith("ALERT <TKR>")
    assert c.post("/api/alerts", json={"text": "AAA.F < 50"}).json()["id"] == "A7"
    assert c.delete("/api/alerts/a7").json() == {"removed": "A7"}
    assert c.delete("/api/alerts/A7").status_code == 404
    assert c.post("/api/alerts/ack", json={"all": True}).json() == {"acked": 0}
    assert c.post("/api/alerts/ack", json={"id": "E9"}).status_code == 404
    eng.runner.wait_idle(5)


def test_alerts_disabled_without_a_rules_file(tmp_path, web):
    eng = Engine({}, Store(tmp_path / "store"), Recorder(), ctx=Ctx(buffer_dir=tmp_path))
    c = TestClient(create_app(eng, web_dir=web), base_url="http://127.0.0.1")
    assert c.get("/api/alerts").status_code == 404


def test_the_loop_starts_and_stops_with_the_app(tmp_path, web):
    app = create_app(engine(tmp_path), web_dir=web)
    with TestClient(app, base_url="http://127.0.0.1"):
        task = app.state.alert_task
        assert task is not None and not task.done()
    assert task.done()


def test_stale_waits_15_minutes_after_the_terminal_reopens(tmp_path, monkeypatch):
    """Quotes last good at 11:00, no tab until 14:00, then Yahoo fails every minute: the check that
    resumes at 14:00 fires no STALE burst — only after 15 minutes without a good quote."""
    ok = [True]
    monkeypatch.setattr("monitor.data.yahoo.fetch_quotes", lambda ts: {
        t: {"price": 100.0, "prev_close": 100.0, "date": "2026-06-30"} for t in ts} if ok[0] else {})
    eng = engine(tmp_path)
    svc = AlertService(eng, tmp_path / "alerts.json")
    with time_machine.travel("2026-06-30 11:00:00", tick=False):
        svc.run()
    ok[0] = False
    stale_at = []
    for m in range(17):
        with time_machine.travel(f"2026-06-30 14:{m:02d}:00", tick=False):
            if any("QUOTE STALE" in e["msg"] for e in svc.run()):
                stale_at.append(m)
    assert stale_at == [15]
