"""Month-start rebuild: with a tab open, a `monthly` screen whose build predates the
month's rebuild day (its second business day) is rebuilt — once a day at most, never before its
first manual build."""
import dataclasses
import os
from datetime import date, datetime

from monitor.screens.base import Ctx
from monitor.server.engine import Engine
from monitor.server.jobs import Job
from monitor.server.schedule import BuildSchedule, rebuild_day
from monitor.server.store import Store
from tests.server.helpers import Recorder, make_screen


def test_rebuild_day_is_the_second_business_day():
    assert rebuild_day(date(2026, 11, 15)) == date(2026, 11, 3)     # Sun 1, Mon 2 (first session), Tue 3
    assert rebuild_day(date(2026, 10, 3)) == date(2026, 10, 2)      # Thu 1, Fri 2


def _setup(tmp_path, monkeypatch, built: datetime | None, tabs: int = 1):
    scr, _, _ = make_screen(tmp_path, monkeypatch, sid="MODEL")
    art = tmp_path / "model.pkl"
    if built is not None:
        art.write_bytes(b"a build")
        os.utime(art, (built.timestamp(), built.timestamp()))
    scr = dataclasses.replace(scr, tiers=("quote", "heavy"), build_cmd=("-c", "pass"), monthly=True,
                              stamp=lambda ctx: art.stat().st_mtime if art.exists() else None)
    rec = Recorder()
    rec.subscribers = tabs
    eng = Engine({"MODEL": scr}, Store(tmp_path / "store"), rec, ctx=Ctx(buffer_dir=tmp_path), workers=0)
    sent = []
    monkeypatch.setattr(eng.runner, "submit", lambda s, t, force=False, rerun=False: sent.append((s, t, force)))
    now = [datetime(2026, 11, 1, 10, 0)]
    return eng, sent, now, art


class FakeRunner:
    """The JobRunner surface the schedule reads: submit / running / jobs / last_run, states set by hand."""

    def __init__(self):
        self.all: list[Job] = []

    def submit(self, s, t, force=False, rerun=False):
        live = next((j for j in self.all if j.state in ("queued", "running")), None)
        if live is not None:
            return live
        self.all.append(Job(s, t, force, id=len(self.all) + 1, state="running", started=NOW[0].isoformat()))
        return self.all[-1]

    def running(self, sid):
        return ["heavy"] if any(j.state in ("queued", "running") for j in self.all) else []

    def jobs(self):
        return [j.as_dict() for j in reversed(self.all)]

    def last_run(self, sid, tier):
        return next((j for j in reversed(self.all) if j.state in ("done", "failed")), None)


NOW = [datetime(2026, 11, 1, 10, 0)]


def test_rebuilds_on_the_rebuild_day_once_per_day(tmp_path, monkeypatch):
    eng, _, now, _ = _setup(tmp_path, monkeypatch, built=datetime(2026, 10, 1, 9, 0))
    eng.runner = run = FakeRunner()
    sch = BuildSchedule(eng, tmp_path / "schedule.json", clock=lambda: now[0])
    assert sch.tick() == []                                   # Sunday 1 Nov: not a business day
    now[0] = datetime(2026, 11, 2, 18, 0)
    assert sch.tick() == []                                   # the month's first session: its close is not in yet
    now[0] = NOW[0] = datetime(2026, 11, 3, 9, 0)
    assert sch.tick() == ["MODEL"] and [(j.screen, j.tier, j.force) for j in run.all] == [("MODEL", "heavy", True)]
    assert eng._scheduled == {"MODEL"}                          # the child is told: --scheduled
    assert sch.tick() == []                                   # running: nothing new
    run.all[-1].state, run.all[-1].error = "failed", "BuildFailed: ValueError: universe empty"
    assert sch.tick() == []                                   # a real failure: the day's attempt is spent
    assert BuildSchedule(eng, tmp_path / "schedule.json", clock=lambda: now[0]).tick() == []   # a reload too
    now[0] = NOW[0] = datetime(2026, 11, 4, 9, 0)             # try again tomorrow
    assert sch.tick() == ["MODEL"] and len(run.all) == 2


def test_a_build_lost_to_a_server_reload_is_retried_on_the_next_tick(tmp_path, monkeypatch):
    """The attempt is marked only when the build ends — done, or failed for real. A reload kills the child
    (BuildKilled) or simply forgets it: the new server's schedule submits it again."""
    eng, _, now, _ = _setup(tmp_path, monkeypatch, built=datetime(2026, 10, 1, 9, 0))
    eng.runner = FakeRunner()
    now[0] = NOW[0] = datetime(2026, 11, 3, 9, 0)
    path = tmp_path / "schedule.json"
    assert BuildSchedule(eng, path, clock=lambda: now[0]).tick() == ["MODEL"]
    assert not path.exists()                                  # in flight: nothing marked yet
    eng.runner = FakeRunner()                                 # the server reloaded mid-build
    sch = BuildSchedule(eng, path, clock=lambda: now[0])
    assert sch.tick() == ["MODEL"]
    eng.runner.all[-1].state, eng.runner.all[-1].error = "failed", "BuildKilled: the server stopped this build"
    assert sch.tick() == ["MODEL"] and not path.exists()        # killed, not failed: once more
    eng.runner.all[-1].state = "done"
    assert sch.tick() == [] and path.exists()                 # done: marked for the day


def test_shutdown_reports_the_killed_build_as_killed(tmp_path, monkeypatch):
    import time
    scr, _, _ = make_screen(tmp_path, monkeypatch, sid="MODEL")
    scr = dataclasses.replace(scr, tiers=("quote", "heavy"), build_cmd=("-c", "import time; print('STAGE 1/1 W', flush=True); time.sleep(30)"),
                              stamp=lambda ctx: None)
    eng = Engine({"MODEL": scr}, Store(tmp_path / "store"), Recorder(), ctx=Ctx(buffer_dir=tmp_path))
    eng.build("MODEL")
    end = time.monotonic() + 10
    while "heavy" not in eng.runner.running("MODEL") or not eng._children:
        assert time.monotonic() < end
        time.sleep(0.02)
    eng.shutdown()
    while not any(j["state"] == "failed" for j in eng.runner.jobs()):
        assert time.monotonic() < end
        time.sleep(0.02)
    assert eng.runner.jobs()[0]["error"].startswith("BuildKilled")


def test_no_tab_no_first_build_or_a_current_build_means_no_rebuild(tmp_path, monkeypatch):
    eng, sent, now, art = _setup(tmp_path, monkeypatch, built=datetime(2026, 10, 1, 9, 0), tabs=0)
    now[0] = datetime(2026, 11, 3, 9, 0)
    assert BuildSchedule(eng, None, clock=lambda: now[0]).tick() == []        # nobody is watching
    eng.broker.subscribers = 1
    os.utime(art, (datetime(2026, 11, 3, 8, 0).timestamp(),) * 2)
    assert BuildSchedule(eng, None, clock=lambda: now[0]).tick() == []        # built this month
    art.unlink()
    assert BuildSchedule(eng, None, clock=lambda: now[0]).tick() == []        # never built: BUILD MODEL
    assert sent == []


def test_a_build_running_or_already_run_today_counts(tmp_path, monkeypatch):
    eng, sent, now, _ = _setup(tmp_path, monkeypatch, built=datetime(2026, 10, 1, 9, 0))
    now[0] = datetime(2026, 11, 3, 9, 0)
    monkeypatch.setattr(eng.runner, "running", lambda sid: ["heavy"])
    assert BuildSchedule(eng, None, clock=lambda: now[0]).tick() == []
    monkeypatch.setattr(eng.runner, "running", lambda sid: [])
    monkeypatch.setattr(eng.runner, "jobs", lambda: [{"screen": "MODEL", "tier": "heavy", "force": True,
                                                      "state": "failed", "started": "2026-11-03T08:30:00"}])
    assert BuildSchedule(eng, None, clock=lambda: now[0]).tick() == [] and sent == []


def test_the_server_runs_the_schedule_only_for_a_monthly_screen(tmp_path, monkeypatch):
    import warnings
    warnings.filterwarnings("ignore", message="Using `httpx` with `starlette.testclient`")
    from fastapi.testclient import TestClient

    from monitor.server.app import create_app
    eng, _, _, _ = _setup(tmp_path, monkeypatch, built=None)
    web = tmp_path / "web"
    (web / "app").mkdir(parents=True)
    (web / "index.html").write_text("x", encoding="utf-8")
    app = create_app(eng, web_dir=web, schedule_file=tmp_path / "schedule.json")
    with TestClient(app, base_url="http://127.0.0.1"):
        task = app.state.build_task
        assert task is not None and not task.done()
    assert task.cancelled()
    plain, _, _ = make_screen(tmp_path, monkeypatch)
    app = create_app(Engine({"FAKE": plain}, Store(tmp_path / "s2"), Recorder(), ctx=Ctx(buffer_dir=tmp_path)),
                     web_dir=web, schedule_file=tmp_path / "schedule.json")
    with TestClient(app, base_url="http://127.0.0.1"):
        assert app.state.build_task is None


def test_the_rebuild_day_waits_for_a_session_after_the_rebalance(tmp_path, monkeypatch):
    """1 Jan 2026 is a Thursday: the rebuild day (2nd business day) is Fri 2 Jan, yet XETRA's first session
    of the year is that same day — a morning build still ends on 30 Dec and holds last year's book. A
    build is current only once its prices hold a session after the rebalance (Screen.current); until then
    the schedule tries again, once a day."""
    assert rebuild_day(date(2026, 1, 15)) == date(2026, 1, 2)
    eng, _, now, _ = _setup(tmp_path, monkeypatch, built=datetime(2026, 1, 2, 8, 0))
    state = {"current": False}
    eng.screens["MODEL"] = dataclasses.replace(eng.screens["MODEL"], current=lambda ctx: state["current"])
    eng.runner = run = FakeRunner()
    sch = BuildSchedule(eng, tmp_path / "schedule.json", clock=lambda: now[0])
    now[0] = NOW[0] = datetime(2026, 1, 2, 18, 0)
    assert sch.tick() == ["MODEL"]                              # built today, but not current: build again
    run.all[-1].state = "done"
    assert sch.tick() == []                                   # that was today's attempt
    now[0] = NOW[0] = datetime(2026, 1, 5, 9, 0)
    assert sch.tick() == ["MODEL"]
    run.all[-1].state, state["current"] = "done", True
    now[0] = NOW[0] = datetime(2026, 1, 6, 9, 0)
    assert sch.tick() == []                                   # current: done for the month


def test_the_schedule_follows_a_forced_build_queued_behind_an_unforced_reload(tmp_path, monkeypatch):
    """BUILD while an unforced REFRESH reload of MODEL runs attaches to that job, which then queues a forced
    follow-up (the build). The schedule follows the newest heavy job of the screen, not the reload."""
    eng, _, now, _ = _setup(tmp_path, monkeypatch, built=datetime(2026, 10, 1, 9, 0))
    eng.runner = run = FakeRunner()
    now[0] = NOW[0] = datetime(2026, 11, 3, 9, 0)
    run.all.append(Job("MODEL", "heavy", False, id=1, state="running", started=NOW[0].isoformat()))   # a reload
    path = tmp_path / "schedule.json"
    sch = BuildSchedule(eng, path, clock=lambda: now[0])
    monkeypatch.setattr(sch, "due", lambda sid, n: sid == "MODEL" and not sch._pending and not path.exists())
    assert sch.tick() == ["MODEL"] and sch._pending["MODEL"][0] == 1   # attached to the reload
    run.all[0].state = "done"
    run.all.append(Job("MODEL", "heavy", True, id=2, state="queued"))  # the forced follow-up: the build
    assert sch.tick() == [] and not path.exists()             # the build has not run yet
    run.all[1].state, run.all[1].error = "failed", "BuildFailed: ValueError: universe empty"
    assert sch.tick() == [] and path.exists()                 # settled on the build's own outcome
