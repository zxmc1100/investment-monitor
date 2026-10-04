"""Heavy builds as a child process. A screen whose heavy tier declares `build_cmd` runs
`python <build…>` on the heavy lane outside the network lock, relays its `STAGE i/n NAME` lines as
job progress, then loads the result in-process. The fake builds here are tiny `python -c` scripts."""
import dataclasses
import os
import time
from datetime import datetime

import pytest

from monitor.screens.base import Ctx
from monitor.server.engine import _NET_LOCK, Engine
from monitor.server.store import Store
from tests.server.helpers import Recorder, make_screen

OK = ("print('STAGE 1/2 FETCH', flush=True); print('chatter', flush=True); "
      "print('STAGE 2/2 DISTILL', flush=True)")


def _engine(tmp_path, monkeypatch, script: str):
    scr, calls, dep = make_screen(tmp_path, monkeypatch)
    art = tmp_path / "artifact.pkl"
    scr = dataclasses.replace(scr, tiers=("quote", "heavy"), build_cmd=("-c", script),
                              stamp=lambda ctx: art.stat().st_mtime if art.exists() else None)
    rec = Recorder()
    eng = Engine({scr.id: scr}, Store(tmp_path / "store"), rec, ctx=Ctx(buffer_dir=tmp_path))
    return eng, rec, calls, art, dep


def _until(cond, timeout=10.0) -> bool:
    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        time.sleep(0.02)
    return bool(cond())


def _progress(rec) -> list:
    return [e["progress"] for e in rec.events
            if e["type"] == "job" and e["tier"] == "heavy" and e["state"] == "running"]


def test_a_forced_heavy_runs_the_build_child_then_loads_in_process(tmp_path, monkeypatch):
    eng, rec, calls, _, _ = _engine(tmp_path, monkeypatch, OK)
    eng.build("FAKE")
    assert eng.runner.wait_idle(10)
    assert _progress(rec) == [None, "1/2 FETCH", "2/2 DISTILL"]
    assert ("heavy", True) in calls and eng.store.get_part("FAKE", "heavy") is not None


def test_a_failed_build_keeps_the_last_good_part_and_reports_its_last_stderr_line(tmp_path, monkeypatch):
    script = ("import sys; print('STAGE 1/2 FETCH', flush=True); "
              "sys.stderr.write('Traceback (most recent call last):\\nValueError: universe empty\\n'); sys.exit(3)")
    eng, _, calls, _, _ = _engine(tmp_path, monkeypatch, script)
    eng.compute_now("FAKE")                      # last good parts — computed in-process, no child
    before, n = eng.store.get_part("FAKE", "heavy"), len(calls)
    eng.build("FAKE")
    assert eng.runner.wait_idle(10)
    err = eng.live("FAKE")["error"]
    assert err["tier"] == "heavy" and err["error"] == "BuildFailed: ValueError: universe empty"
    assert eng.store.get_part("FAKE", "heavy") == before and len(calls) == n
    assert eng.payload("FAKE") is not None


def test_the_build_child_runs_outside_the_network_lock(tmp_path, monkeypatch):
    eng, rec, _, _, _ = _engine(tmp_path, monkeypatch,
                                "import time; print('STAGE 1/1 WAIT', flush=True); time.sleep(1.5)")
    eng.build("FAKE")
    assert _until(lambda: "1/1 WAIT" in _progress(rec))
    assert _NET_LOCK.acquire(timeout=0.5)        # quotes keep flowing while the build runs
    _NET_LOCK.release()
    assert eng.runner.wait_idle(10)


def test_shutdown_terminates_the_build_child(tmp_path, monkeypatch):
    pid = tmp_path / "pid"
    script = (f"import os, time; open({str(pid)!r}, 'w').write(str(os.getpid())); "
              "print('STAGE 1/1 WAIT', flush=True); time.sleep(60)")
    eng, rec, _, _, _ = _engine(tmp_path, monkeypatch, script)
    eng.build("FAKE")
    assert _until(lambda: "1/1 WAIT" in _progress(rec))
    eng.shutdown()
    assert _dead(int(pid.read_text()))


def test_a_build_screen_loads_when_cold_newer_or_recoded_but_never_builds_unasked(tmp_path, monkeypatch):
    marker = tmp_path / "built"
    eng, _, calls, art, dep = _engine(tmp_path, monkeypatch, f"open({str(marker)!r}, 'w').write('x')")
    assert eng.due_tiers("FAKE") == ["quote", "heavy"]          # cold: load whatever is there
    eng.ensure_fresh("FAKE")
    assert eng.runner.wait_idle(10)
    assert ("heavy", False) in calls and not marker.exists()    # a load, never a build
    assert eng.due_tiers("FAKE") == [] and eng.payload("FAKE") is not None
    at = datetime.fromisoformat(eng.store.part_info("FAKE", "heavy")["at"]).timestamp()
    art.write_bytes(b"a CLI build landed")
    os.utime(art, (at + 1, at + 1))
    assert eng.due_tiers("FAKE") == ["quote", "heavy"]          # newer artifact: reload every tier
    os.utime(art, (at - 60, at - 60))
    dep.write_text("X = 2  # edited\n")
    assert eng.due_tiers("FAKE") == ["quote", "heavy"]          # new code: reload (cheap), no build


def test_a_second_refresh_while_building_attaches_to_the_running_build(tmp_path, monkeypatch):
    runs = tmp_path / "runs"
    script = (f"import time; open({str(runs)!r}, 'a').write('x'); print('STAGE 1/1 WAIT', flush=True); "
              "time.sleep(1)")
    eng, rec, _, _, _ = _engine(tmp_path, monkeypatch, script)
    eng.build("FAKE")
    assert _until(lambda: "1/1 WAIT" in _progress(rec))
    eng.build("FAKE")               # REFRESH again (or the schedule) while it builds
    eng.ensure_fresh("FAKE")                     # a poke: the cold heavy part is due — a load, attached too
    assert eng.runner.wait_idle(10)
    assert runs.read_text() == "x"               # one child


def test_a_scheduled_build_tells_the_child_and_a_manual_one_does_not(tmp_path, monkeypatch):
    out = tmp_path / "argv"
    eng, _, _, _, _ = _engine(tmp_path, monkeypatch, f"import sys; open({str(out)!r}, 'a').write(repr(sys.argv[1:]) + '\\n')")
    eng.build("FAKE", scheduled=True)
    assert eng.runner.wait_idle(10)
    eng.build("FAKE")
    assert eng.runner.wait_idle(10)
    assert out.read_text().splitlines() == ["['--scheduled']", "[]"]
    with pytest.raises(ValueError):
        Engine({"P": make_screen(tmp_path, monkeypatch, sid="P")[0]}, Store(tmp_path / "s2"), Recorder(),
               ctx=Ctx(buffer_dir=tmp_path), workers=0).build("P")


def _dead(pid: int) -> bool:
    if os.name == "nt":                          # os.kill(pid, 0) would TERMINATE it on Windows: ask instead
        import ctypes
        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(0x1000, False, pid)            # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return True
        try:
            code = ctypes.c_ulong()
            return bool(k32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value != 259   # STILL_ACTIVE
        finally:
            k32.CloseHandle(handle)
    try:
        os.kill(pid, 0)                          # a zombie (killed, never waited for) still answers
    except ProcessLookupError:
        return True
    return False


def test_a_build_child_past_its_wall_clock_limit_is_killed_and_reaped(tmp_path, monkeypatch):
    from monitor import config
    monkeypatch.setattr(config, "BUILD_TIMEOUT_MIN", 0.02)              # 1.2 s
    pid = tmp_path / "pid"
    script = (f"import os, time; open({str(pid)!r}, 'w').write(str(os.getpid())); "
              "print('STAGE 1/1 HANG', flush=True); time.sleep(60)")
    eng, _, _, _, _ = _engine(tmp_path, monkeypatch, script)
    eng.build("FAKE")
    assert eng.runner.wait_idle(15)
    err = eng.live("FAKE")["error"]
    assert err["tier"] == "heavy" and err["error"].startswith("BuildFailed: build timed out after")
    assert _dead(int(pid.read_text())) and not eng._children


def test_a_failure_while_reading_the_child_kills_and_reaps_it(tmp_path, monkeypatch):
    pid = tmp_path / "pid"
    script = (f"import os, time; open({str(pid)!r}, 'w').write(str(os.getpid())); "
              "print('STAGE 1/1 WAIT', flush=True); time.sleep(60)")
    eng, _, _, _, _ = _engine(tmp_path, monkeypatch, script)

    def boom(*a):
        raise RuntimeError("relay broke")
    monkeypatch.setattr(eng.runner, "progress", boom)
    eng.build("FAKE")
    assert eng.runner.wait_idle(15)
    assert "relay broke" in eng.live("FAKE")["error"]["error"]
    assert _dead(int(pid.read_text())) and not eng._children


def test_plain_refresh_reloads_a_build_screen_but_never_builds(tmp_path, monkeypatch):
    """REFRESH on a build screen recomputes quote and daily and reloads the artifact — a multi-GB build
    runs only on BUILD (or the schedule)."""
    marker = tmp_path / "built"
    eng, _, calls, _, _ = _engine(tmp_path, monkeypatch, f"open({str(marker)!r}, 'w').write('x')")
    assert {(j.tier, j.force) for j in eng.refresh("FAKE")} == {("quote", True), ("heavy", False)}
    assert eng.runner.wait_idle(10) and not marker.exists() and ("heavy", False) in calls
    assert [(j.tier, j.force) for j in eng.refresh("FAKE", ["heavy"])] == [("heavy", False)]
    assert eng.runner.wait_idle(10) and not marker.exists()
    eng.build("FAKE")
    assert eng.runner.wait_idle(10) and marker.exists()
