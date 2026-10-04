"""Before a heavy tier lands: no part is ever dropped by a concurrent assemble, heavy
jobs run on their own one-worker lane, and a running job can publish its progress."""
import threading
import time

from monitor.screens.base import Ctx
from monitor.server.engine import Engine
from monitor.server.jobs import JobRunner
from monitor.server.store import Store
from tests.server.helpers import Recorder, make_screen


def _until(cond, timeout=5.0) -> bool:
    end = time.monotonic() + timeout
    while not cond() and time.monotonic() < end:
        time.sleep(0.01)
    return bool(cond())


def test_a_part_stored_while_assemble_rechecks_is_never_dropped(tmp_path, monkeypatch):
    """assemble() re-reads an unreadable part and drops it only if still unreadable. A part stored
    by another job in between must survive: the store waits for the screen's lock."""
    scr, _, _ = make_screen(tmp_path, monkeypatch)
    eng = Engine({scr.id: scr}, Store(tmp_path / "store"), Recorder(), ctx=Ctx(buffer_dir=tmp_path))
    eng.compute_now("FAKE")
    (tmp_path / "store" / "FAKE.daily.pkl").write_bytes(b"garbage")    # unreadable; sidecar kept
    real_put, real_drop = eng.store.put_part, eng.store.invalidate_part
    stored, writer = threading.Event(), []

    def put(*a, **k):
        rec = real_put(*a, **k)
        stored.set()
        return rec

    def drop(key, tier):                       # a fresh daily part arrives just before the drop
        t = threading.Thread(target=eng.put_and_assemble, args=("FAKE", "daily", {"fresh": True}))
        writer.append(t)
        t.start()
        stored.wait(0.5)                       # an unlocked put lands here — and would be dropped
        real_drop(key, tier)

    monkeypatch.setattr(eng.store, "put_part", put)
    monkeypatch.setattr(eng.store, "invalidate_part", drop)
    eng.assemble("FAKE")
    writer[0].join(5)
    assert eng.store.get_part("FAKE", "daily")["data"] == {"fresh": True}
    assert eng.payload("FAKE")["parts"]["daily"] == {"fresh": True}


def test_heavy_jobs_run_on_their_own_lane():
    """A 10-minute heavy build must never hold a worker the quote and daily tiers need."""
    gate, done = threading.Event(), []

    def fn(s, t, force):
        if t == "heavy":
            gate.wait(5)
        done.append(t)
    r = JobRunner(fn, Recorder(), workers=1)
    r.submit("MODEL", "heavy", force=True)
    r.submit("MODEL", "quote")
    assert _until(lambda: done == ["quote"])
    gate.set()
    assert r.wait_idle(5) and done == ["quote", "heavy"]


def test_heavy_jobs_run_one_at_a_time():
    gauge, peaks, lock = [0], [], threading.Lock()

    def fn(s, t, force):
        with lock:
            gauge[0] += 1
            peaks.append(gauge[0])
        time.sleep(0.05)
        with lock:
            gauge[0] -= 1
    r = JobRunner(fn, Recorder(), workers=2)
    for sid in ("MODEL", "BATCH", "X"):
        r.submit(sid, "heavy", force=True)
    assert r.wait_idle(5) and max(peaks) == 1


def test_progress_updates_the_running_job_and_is_published():
    rec, started, gate = Recorder(), threading.Event(), threading.Event()

    def fn(s, t, force):
        started.set()
        gate.wait(5)
    r = JobRunner(fn, rec)
    r.submit("MODEL", "heavy", force=True)
    assert started.wait(5)
    r.progress("MODEL", "heavy", "2/7 GRID")
    r.progress("MODEL", "quote", "nobody runs this")          # no such running job: ignored
    assert [j["progress"] for j in r.jobs()] == ["2/7 GRID"]
    gate.set()
    assert r.wait_idle(5)
    assert [(e["state"], e["progress"]) for e in rec.events] == [
        ("queued", None), ("running", None), ("running", "2/7 GRID"), ("done", "2/7 GRID")]
