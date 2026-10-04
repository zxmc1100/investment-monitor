"""HTTP API over a fake screen (and the real PORT for the missing-ledger case)."""
import asyncio
import warnings
from pathlib import Path

import pytest

# third-party noise raised at import time of fastapi.testclient (pytestmark would be too late)
warnings.filterwarnings("ignore", message="Using `httpx` with `starlette.testclient`")
from fastapi.testclient import TestClient

from monitor.screens import SCREENS
from monitor.screens.base import Ctx, Screen
from monitor.server.app import create_app
from monitor.server.engine import Engine
from monitor.server.store import Store
from tests.server.helpers import Recorder, make_screen

@pytest.fixture
def web(tmp_path):
    w = tmp_path / "web"
    (w / "app").mkdir(parents=True)
    (w / "index.html").write_text('<meta name="im-mode" content="live">INDEX', encoding="utf-8")
    (w / "app" / "app.js").write_text("export {};", encoding="utf-8")
    return w


@pytest.fixture
def env(tmp_path, monkeypatch, web):
    fail = {}
    scr, calls, _ = make_screen(tmp_path, monkeypatch, fail=fail)
    eng = Engine({scr.id: scr, "SOON": Screen("SOON", "Later", 2, status="soon")},
                 Store(tmp_path / "store"), Recorder(), ctx=Ctx(buffer_dir=tmp_path))
    return TestClient(create_app(eng, web_dir=web), base_url="http://127.0.0.1"), eng, fail


def test_index_and_static_assets_served(env):
    c, _, _ = env
    assert "INDEX" in c.get("/").text
    assert c.get("/app/app.js").text == "export {};"


def test_foreign_host_header_is_rejected(env):
    assert env[0].get("/api/screens", headers={"Host": "attacker.example:8000"}).status_code == 400


def test_registry_lists_live_and_soon_screens(env):
    body = env[0].get("/api/screens").json()
    assert [s["id"] for s in body["screens"]] == ["FAKE", "SOON"]
    assert body["quote_interval_s"] == 60 and "legacy" not in body


def test_registry_reports_a_settings_problem_for_the_status_bar(env, monkeypatch):
    from monitor import config
    assert env[0].get("/api/screens").json()["settings_error"] is None
    monkeypatch.setattr(config, "SETTINGS_ERROR", "input/settings.toml: unknown key 'fee' ignored")
    assert env[0].get("/api/screens").json()["settings_error"] == "input/settings.toml: unknown key 'fee' ignored"


def test_cold_screen_returns_202_then_payload(env):
    c, eng, _ = env
    r = c.get("/api/screen/fake")
    assert r.status_code == 202 and r.json()["cold"] is True
    assert eng.runner.wait_idle(5)
    r = c.get("/api/screen/FAKE")
    assert r.status_code == 200 and r.json()["screen"] == "FAKE"
    assert r.json()["live"] == {"running": [], "code_changed": False, "error": None}


def test_unknown_and_soon_screens_404(env):
    c = env[0]
    assert c.get("/api/screen/nope").json()["detail"]["error"] == "UNKNOWN SCREEN NOPE"
    r = c.get("/api/screen/SOON")
    assert r.status_code == 404 and r.json()["detail"]["error"] == "SOON — SOON"


def test_refresh_validates_tier_and_queues(env):
    c, eng, _ = env
    r = c.post("/api/refresh/FAKE?tier=heavy")
    assert r.status_code == 400 and r.json()["detail"]["error"] == "FAKE HAS NO TIER 'HEAVY' — QUOTE · DAILY"
    r = c.post("/api/refresh/FAKE?tier=quote")
    assert [j["tier"] for j in r.json()["jobs"]] == ["quote"] and r.json()["jobs"][0]["force"] is True
    eng.runner.wait_idle(5)


def test_ensure_returns_jobs_and_live(env):
    c, eng, _ = env
    body = c.post("/api/ensure/FAKE").json()
    assert {j["tier"] for j in body["jobs"]} == {"quote", "daily"} and "live" in body
    eng.runner.wait_idle(5)
    assert c.post("/api/ensure/FAKE").json()["jobs"] == []


def test_jobs_endpoint_reports_failures_with_trace(env):
    c, eng, fail = env
    fail["daily"] = True
    c.post("/api/refresh/FAKE?tier=daily")
    eng.runner.wait_idle(5)
    failed = [j for j in c.get("/api/jobs").json()["jobs"] if j["state"] == "failed"]
    assert failed and "daily broke" in failed[0]["error"] and "Traceback" in failed[0]["trace"]


def test_the_retired_legacy_pages_are_gone(env):
    """Sub-project #6 deleted the old HTML pages: no /legacy route serves anything any more."""
    c = env[0]
    assert c.get("/legacy/report").status_code == 404 and c.get("/legacy/x").status_code == 404


def test_no_portfolio_yet_is_a_cold_view_that_says_how_to_start(tmp_path, web, monkeypatch, caplog):
    """No input/portfolio.csv: the server starts; PORT / OPT / RISK show one line saying how to begin — press
    6 (TRADES) — (nothing is computed, nothing fails, no stack trace); MKT, ALRT and TRADES work; the moment the
    file holds trades the screens compute — no restart."""
    import logging

    import time_machine

    from tests import fakes_yf
    NO_PORTFOLIO = "NO TRADES YET — press 6 (TRADES): add your trades, paste many or import your broker's CSV"
    fakes_yf.install(monkeypatch)
    caplog.set_level(logging.WARNING)
    csv = tmp_path / "input" / "portfolio.csv"
    with time_machine.travel("2026-06-30 14:00:00+00:00", tick=False):
        eng = Engine(dict(SCREENS), Store(tmp_path / "store"), Recorder(),
                     ctx=Ctx(portfolio_csv=csv, buffer_dir=tmp_path / "buf", equity_log=None,
                             watchlist=tmp_path / "wl.json", alerts=tmp_path / "alerts.json"))
        with TestClient(create_app(eng, web_dir=web), base_url="http://127.0.0.1") as c:
            for sid in ("PORT", "OPT", "RISK"):
                r = c.get(f"/api/screen/{sid}")
                assert r.status_code == 202 and r.json()["cold"] is True and r.json()["reason"] == NO_PORTFOLIO
                assert c.post(f"/api/ensure/{sid}").json()["jobs"] == []
                assert c.post(f"/api/refresh/{sid}").json()["jobs"] == []
            assert [j for j in eng.runner.jobs() if j["screen"] in ("PORT", "OPT", "RISK")] == []
            for sid in ("MKT", "ALRT"):
                assert c.get(f"/api/screen/{sid}").status_code == 202
            r = c.get("/api/screen/TRADES")                                     # inline: at once
            assert r.status_code == 200 and r.json()["etag"] == "absent"
            assert eng.runner.wait_idle(30)
            for sid in ("MKT", "ALRT"):
                r = c.get(f"/api/screen/{sid}")
                assert r.status_code == 200 and r.json()["screen"] == sid and r.json()["live"]["error"] is None
            assert c.get("/api/screens").status_code == 200
            csv.parent.mkdir()
            csv.write_text((Path(__file__).resolve().parent.parent / "fixtures" / "portfolio_small.csv").read_text(encoding="utf-8"), encoding="utf-8")
            r = c.get("/api/screen/PORT")
            assert r.status_code == 202 and "reason" not in r.json()            # computing now
            assert eng.runner.wait_idle(30)
            r = c.get("/api/screen/PORT")
            assert r.status_code == 200 and r.json()["live"]["error"] is None
    assert not [rec for rec in caplog.records if rec.exc_info or rec.levelno >= logging.ERROR]


def test_gzip_everything_except_the_event_stream():
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route
    from monitor.server.app import _GzipExceptStream

    inner = Starlette(routes=[Route("/big", lambda r: PlainTextResponse("x" * 5000)),
                              Route("/api/stream", lambda r: PlainTextResponse("y" * 5000))])
    c = TestClient(_GzipExceptStream(inner))
    assert c.get("/big", headers={"Accept-Encoding": "gzip"}).headers.get("content-encoding") == "gzip"
    assert "content-encoding" not in c.get("/api/stream", headers={"Accept-Encoding": "gzip"}).headers


@pytest.fixture
def penv(tmp_path, monkeypatch, web):
    scr, calls, _ = make_screen(tmp_path, monkeypatch, sid="P", params=["AAA.F", "BBB.F"])
    plain, _, _ = make_screen(tmp_path, monkeypatch, sid="FAKE")
    eng = Engine({"P": scr, "FAKE": plain}, Store(tmp_path / "store"), Recorder(),
                 ctx=Ctx(buffer_dir=tmp_path), prefs_path=tmp_path / "prefs.json")
    return TestClient(create_app(eng, web_dir=web), base_url="http://127.0.0.1"), eng


def test_param_routes(penv):
    c, eng = penv
    assert c.get("/api/screen/P/aaa.f").status_code == 202
    assert eng.runner.wait_idle(5)
    assert c.get("/api/screen/P/AAA.F").json()["parts"]["quote"]["param"] == "AAA.F"
    assert c.get("/api/screen/P/zzz.f").json()["detail"]["error"] == "ZZZ.F: NOT A TRADEABLE TICKER"
    assert c.get("/api/screen/P").json()["detail"]["error"] == "P: TICKER REQUIRED"
    assert c.get("/api/screen/FAKE/AAA.F").json()["detail"]["error"] == "FAKE TAKES NO PARAMETER"
    assert [j["tier"] for j in c.post("/api/refresh/P/AAA.F?tier=quote").json()["jobs"]] == ["quote"]
    eng.runner.wait_idle(5)


def test_screens_lists_params_and_targets(penv):
    body = penv[0].get("/api/screens").json()
    assert body["params"] == {"P": ["AAA.F", "BBB.F"]}
    assert body["targets"] == ["MINVAR", "RP", "HRP", "BLSHARPE", "BLSAME"]
    assert {s["id"]: s["param"] for s in body["screens"]} == {"P": True, "FAKE": False}


def test_prefs_endpoint(penv):
    c, _ = penv
    assert c.get("/api/prefs").json() == {"target": "HRP"}
    assert c.post("/api/prefs", json={"target": "rp"}).json() == {"target": "RP"}
    r = c.post("/api/prefs", json={"target": "bogus"})
    assert r.status_code == 400 and "RP" in r.json()["detail"]["targets"]
    assert r.json()["detail"]["error"] == "UNKNOWN TARGET BOGUS — TARGET <MINVAR|RP|HRP|BLSHARPE|BLSAME>"
    r = c.post("/api/prefs", content=b"not json", headers={"Content-Type": "application/json"})
    assert r.status_code == 422 and r.json()["detail"]["error"] == "MALFORMED REQUEST"


def test_sec_route_end_to_end(tmp_path, monkeypatch, web):
    import time_machine
    from monitor.screens import SCREENS
    from tests import fakes_yf
    fakes_yf.install(monkeypatch)
    fix = Path(__file__).resolve().parent.parent / "fixtures" / "portfolio_small.csv"
    with time_machine.travel("2026-06-30 14:00:00+00:00", tick=False):
        eng = Engine({"SEC": SCREENS["SEC"]}, Store(tmp_path / "store"), Recorder(),
                     ctx=Ctx(portfolio_csv=fix, buffer_dir=tmp_path / "buf", equity_log=None))
        c = TestClient(create_app(eng, web_dir=web), base_url="http://127.0.0.1")
        assert c.get("/api/screens").json()["params"]["SEC"] == ["AAA.F", "BBB.F", "CCC.F", "DDD.F"]
        assert c.get("/api/screen/SEC/aaa.f").status_code == 202
        assert eng.runner.wait_idle(20)
        assert c.get("/api/screen/SEC/AAA.F").json()["screen"] == "SEC"


@pytest.fixture
def wenv(tmp_path, monkeypatch, web):
    scr, _, _ = make_screen(tmp_path, monkeypatch, sid="MKT")
    eng = Engine({"MKT": scr}, Store(tmp_path / "store"), Recorder(),
                 ctx=Ctx(buffer_dir=tmp_path, watchlist=tmp_path / "watchlist.json"))
    return TestClient(create_app(eng, web_dir=web), base_url="http://127.0.0.1"), eng


def test_lookup_endpoint(wenv):
    c, _ = wenv
    assert [r["ticker"] for r in c.get("/api/lookup", params={"q": "rheinmetall"}).json()] == ["RHM.DE", "RNMBY"]
    assert c.get("/api/lookup").json() == []


def test_watchlist_endpoints_validate_and_recompute_mkt(wenv):
    c, eng = wenv
    assert c.get("/api/watchlist").json() == []
    assert c.post("/api/watchlist", json={"ticker": "rhm.de"}).json()[0]["name"] == "Rheinmetall"
    assert eng.runner.wait_idle(5)
    assert {j["tier"] for j in eng.runner.jobs() if j["screen"] == "MKT"} == {"quote", "daily"}
    assert c.post("/api/watchlist", json={"ticker": "RHM.DE"}).status_code == 200      # idempotent
    for bad in ({"ticker": "SAP.DE"}, {"ticker": "DEAD"}, {}):
        r = c.post("/api/watchlist", json=bad)
        assert r.status_code == 400 and "NOT A TRADEABLE TICKER" in r.json()["detail"]["error"]
    assert [w["ticker"] for w in c.get("/api/watchlist").json()] == ["RHM.DE"]
    assert c.delete("/api/watchlist/rhm.de").json() == []
    assert c.delete("/api/watchlist/RHM.DE").status_code == 404
    eng.runner.wait_idle(5)


def test_watching_an_already_watched_ticker_does_not_recompute_mkt(wenv, monkeypatch):
    c, eng = wenv
    calls = []
    monkeypatch.setattr(eng, "watchlist_changed", lambda: calls.append(1) or [])
    c.post("/api/watchlist", json={"ticker": "RHM.DE"})
    c.post("/api/watchlist", json={"ticker": "rhm.de"})                  # already watched: nothing changed
    assert calls == [1]
    assert c.delete("/api/watchlist/NVDA").status_code == 404 and calls == [1]
    c.delete("/api/watchlist/RHM.DE")
    assert calls == [1, 1]


def test_mkt_route_end_to_end_and_watch_recomputes_it(tmp_path, monkeypatch, web):
    import time_machine
    from tests import fakes_yf
    fakes_yf.install(monkeypatch)
    fix = Path(__file__).resolve().parent.parent / "fixtures" / "portfolio_small.csv"
    with time_machine.travel("2026-06-30 14:00:00+00:00", tick=False):
        eng = Engine({"MKT": SCREENS["MKT"]}, Store(tmp_path / "store"), Recorder(),
                     ctx=Ctx(portfolio_csv=fix, buffer_dir=tmp_path / "buf", equity_log=None,
                             watchlist=tmp_path / "watchlist.json"))
        c = TestClient(create_app(eng, web_dir=web), base_url="http://127.0.0.1")
        assert c.get("/api/screen/MKT").status_code == 202
        assert eng.runner.wait_idle(30)
        assert [p["id"] for p in c.get("/api/screen/MKT").json()["panels"]][-3:] == ["names", "sectors", "events"]
        c.post("/api/watchlist", json={"ticker": "FNTN.DE"})
        assert eng.runner.wait_idle(30)
        names = next(p for p in c.get("/api/screen/MKT").json()["panels"] if p["id"] == "names")
        assert "FNTN.DE" in {r["tkr"] for r in names["rows"]}


def test_sec_opens_for_universe_tickers_and_rejects_the_rest(tmp_path, monkeypatch, web):
    import time_machine
    from tests import fakes_yf
    fakes_yf.install(monkeypatch)
    fix = Path(__file__).resolve().parent.parent / "fixtures" / "portfolio_small.csv"
    with time_machine.travel("2026-06-30 14:00:00+00:00", tick=False):
        eng = Engine({"SEC": SCREENS["SEC"]}, Store(tmp_path / "store"), Recorder(),
                     ctx=Ctx(portfolio_csv=fix, buffer_dir=tmp_path / "buf", equity_log=None,
                             watchlist=tmp_path / "watchlist.json"))
        c = TestClient(create_app(eng, web_dir=web), base_url="http://127.0.0.1")
        c.post("/api/watchlist", json={"ticker": "FNTN.DE"})
        assert c.get("/api/screens").json()["params"]["SEC"][-1] == "FNTN.DE"
        assert c.get("/api/screen/SEC/rhm.de").status_code == 202
        assert eng.runner.wait_idle(20)
        assert c.get("/api/screen/SEC/RHM.DE").json()["title"] == "RHM.DE · Rheinmetall"
        r = c.get("/api/screen/SEC/ZZZ.F")
        assert r.status_code == 404 and r.json()["detail"]["error"] == "ZZZ.F: NOT A TRADEABLE TICKER"


def test_unknown_sec_param_starts_no_job_and_stores_nothing(tmp_path, monkeypatch, web):
    import time_machine
    from tests import fakes_yf
    fakes_yf.install(monkeypatch)
    fix = Path(__file__).resolve().parent.parent / "fixtures" / "portfolio_small.csv"
    with time_machine.travel("2026-06-30 14:00:00+00:00", tick=False):
        eng = Engine({"SEC": SCREENS["SEC"]}, Store(tmp_path / "store"), Recorder(),
                     ctx=Ctx(portfolio_csv=fix, buffer_dir=tmp_path / "buf", equity_log=None,
                             watchlist=tmp_path / "watchlist.json"))
        c = TestClient(create_app(eng, web_dir=web), base_url="http://127.0.0.1")
        assert c.get("/api/screen/SEC/ZZZ.F").status_code == 404
        assert c.post("/api/refresh/SEC/ZZZ.F").status_code == 404
        assert eng.runner.wait_idle(5)
        assert eng.runner.jobs() == []
        root = tmp_path / "store"
        assert not root.exists() or not [f for f in root.iterdir() if "ZZZ" in f.name]


def test_server_start_prunes_stale_param_keys(env):
    c, eng, _ = env
    calls = []
    eng.prune_params = lambda *a, **k: calls.append(1) or []
    with c:                                                      # runs the lifespan
        pass
    assert calls == [1]


def test_a_failing_prune_never_blocks_server_start(env, caplog):
    c, eng, _ = env

    def broken(*a, **k):
        raise OSError("store unreadable")
    eng.prune_params = broken
    with caplog.at_level("WARNING"), c:                          # the lifespan must still start
        assert c.get("/api/screens").status_code == 200
    assert "store unreadable" in caplog.text


def test_refresh_never_builds_and_build_does(tmp_path, monkeypatch, web):
    import dataclasses
    marker = tmp_path / "built"
    scr, _, _ = make_screen(tmp_path, monkeypatch)
    scr = dataclasses.replace(scr, tiers=("quote", "heavy"), build_cmd=("-c", f"open({str(marker)!r}, 'w').write('x')"),
                              build_eta="~1 MIN")
    plain, _, _ = make_screen(tmp_path, monkeypatch, sid="P")
    eng = Engine({"FAKE": scr, "P": plain}, Store(tmp_path / "store"), Recorder(), ctx=Ctx(buffer_dir=tmp_path))
    c = TestClient(create_app(eng, web_dir=web), base_url="http://127.0.0.1")
    reg = {s["id"]: s for s in c.get("/api/screens").json()["screens"]}
    assert reg["FAKE"]["build"] == "~1 MIN" and reg["P"]["build"] is None
    jobs = c.post("/api/refresh/FAKE").json()["jobs"]
    assert {(j["tier"], j["force"]) for j in jobs} == {("quote", True), ("heavy", False)}
    assert eng.runner.wait_idle(10) and not marker.exists()
    r = c.post("/api/build/fake")
    assert r.status_code == 200 and [(j["tier"], j["force"]) for j in r.json()["jobs"]] == [("heavy", True)]
    assert eng.runner.wait_idle(10) and marker.exists()
    r = c.post("/api/build/P")
    assert r.status_code == 400 and r.json()["detail"]["error"] == "P HAS NOTHING TO BUILD — BUILD FAKE"
    assert c.post("/api/build/NOPE").status_code == 404


def test_a_param_screen_names_its_param_and_its_miss(tmp_path, monkeypatch, web):
    """A parametrized screen's registry entry names its param (SEC <TICKER>; another screen's own word) and a
    miss is that screen's unknown_param message, never ticker wording."""
    from dataclasses import replace
    scr, _, _ = make_screen(tmp_path, monkeypatch, sid="DOC", params=["A1"])
    scr = replace(scr, param_name="ID", unknown_param="UNKNOWN DOC")
    eng = Engine({"DOC": scr}, Store(tmp_path / "store"), Recorder(), ctx=Ctx(buffer_dir=tmp_path / "buf"))
    c = TestClient(create_app(eng, web_dir=web), base_url="http://127.0.0.1")
    entry = c.get("/api/screens").json()["screens"][0]
    assert entry["param"] is True and entry["param_name"] == "ID" and entry["unknown_param"] == "UNKNOWN DOC"
    r = c.get("/api/screen/DOC/NOPE")
    assert r.status_code == 404 and r.json()["detail"]["error"] == "NOPE: UNKNOWN DOC"
    assert SCREENS["SEC"].entry()["param_name"] == "TICKER" and "param_name" not in SCREENS["PORT"].entry()


def test_a_cross_origin_write_is_refused(env):
    """CSRF: a page on another site — or another local port — must not make the browser BUILD, REFRESH,
    WATCH, ALERT or set prefs. A write whose Origin is present and not this terminal's
    (http://localhost:<port> / http://127.0.0.1:<port>, the port its Host names) gets 403; no Origin
    (curl, scripts) and reads pass."""
    c, _, _ = env
    bad = {"Origin": "http://evil.example"}
    for method, url, kw in (("post", "/api/refresh/FAKE?tier=quote", {}), ("post", "/api/build/FAKE", {}),
                            ("post", "/api/ensure/FAKE", {}), ("post", "/api/prefs", {"json": {"target": "HRP"}}),
                            ("post", "/api/watchlist", {"json": {"ticker": "SAP.DE"}}),
                            ("delete", "/api/watchlist/SAP.DE", {}), ("post", "/api/alerts", {"json": {"text": "x"}}),
                            ("post", "/api/alerts/ack", {"json": {"all": True}}), ("delete", "/api/alerts/A1", {})):
        r = getattr(c, method)(url, headers=bad, **kw)
        assert r.status_code == 403 and r.json()["detail"]["error"] == "CROSS-ORIGIN WRITE REFUSED", url
    for origin in ("http://127.0.0.1:9999", "null", "https://127.0.0.1", "http://localhost.evil.example"):
        assert c.post("/api/refresh/FAKE?tier=quote", headers={"Origin": origin}).status_code == 403, origin
    assert c.get("/api/screens", headers=bad).status_code == 200                       # a read is not a write
    assert c.post("/api/refresh/FAKE?tier=quote").status_code == 200                   # no Origin: curl
    for origin in ("http://127.0.0.1", "http://localhost"):                            # the client's Host: 127.0.0.1
        assert c.post("/api/refresh/FAKE?tier=quote", headers={"Origin": origin}).status_code == 200, origin
    for origin in ("http://localhost:8769", "http://127.0.0.1:8769"):                  # served on :8769
        r = c.post("/api/refresh/FAKE?tier=quote", headers={"Host": "localhost:8769", "Origin": origin})
        assert r.status_code == 200, origin
    assert c.post("/api/refresh/FAKE?tier=quote", headers={"Host": "localhost:8769",
                                                           "Origin": "http://localhost:8000"}).status_code == 403


def test_the_service_idle_watch_reads_the_real_stream_and_jobs_and_requests_touch_it(tmp_path, monkeypatch, web):
    """create_app wires IdleWatch to the live broker's tab count (a property) and the runner — its checks
    must run, see an open tab, and every request must restart its minutes."""
    from monitor.server.stream import Broker
    scr, _, _ = make_screen(tmp_path, monkeypatch)
    eng = Engine({scr.id: scr}, Store(tmp_path / "store"), Broker(), ctx=Ctx(buffer_dir=tmp_path))
    app = create_app(eng, web_dir=web, idle_minutes=15)
    w = app.state.idle
    assert w is not None and w.tabs() == 0 and w.busy() is False and w.check() is False
    async def one_tab():
        q = eng.broker.subscribe()
        seen = w.tabs()
        eng.broker.unsubscribe(q)
        return seen
    assert asyncio.run(one_tab()) == 1 and w.tabs() == 0
    w.last = -1e9
    TestClient(app, base_url="http://127.0.0.1").get("/")
    assert w.last > 0
    assert create_app(eng, web_dir=web).state.idle is None          # a start file's terminal never stops
