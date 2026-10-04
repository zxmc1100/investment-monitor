import threading

from monitor.server.jobs import JobRunner
from tests.server.helpers import Recorder


def test_dedupe_attaches_to_running_job():
    gate, calls = threading.Event(), []

    def fn(s, t, force):
        calls.append((s, t))
        gate.wait(5)
    r = JobRunner(fn, Recorder())
    a = r.submit("X", "quote")
    assert r.submit("X", "quote") is a
    gate.set()
    assert r.wait_idle(5)
    assert calls == [("X", "quote")]
    assert r.submit("X", "quote") is not a
    assert r.wait_idle(5)


def test_failure_recorded_with_trace_until_next_success():
    mode = {"fail": True}

    def fn(s, t, force):
        if mode["fail"]:
            raise FileNotFoundError("input/portfolio.csv")
    r = JobRunner(fn, Recorder())
    r.submit("X", "daily")
    assert r.wait_idle(5)
    fail = r.last_failure("X")
    assert fail.state == "failed" and "FileNotFoundError" in fail.error and "Traceback" in fail.trace
    mode["fail"] = False
    r.submit("X", "daily")
    assert r.wait_idle(5)
    assert r.last_failure("X") is None


def test_lifecycle_events_published_without_trace():
    rec = Recorder()
    r = JobRunner(lambda s, t, f: None, rec)
    r.submit("X", "quote", force=True)
    assert r.wait_idle(5)
    assert [e["state"] for e in rec.events] == ["queued", "running", "done"]
    assert all(e["type"] == "job" and "trace" not in e and e["force"] is True for e in rec.events)


def test_forced_request_on_running_unforced_job_reruns_forced():
    gate, started, forces = threading.Event(), threading.Event(), []

    def fn(s, t, force):
        forces.append(force)
        started.set()
        gate.wait(5)
    r = JobRunner(fn, Recorder())
    a = r.submit("X", "quote")
    assert started.wait(5)
    assert r.submit("X", "quote", force=True) is a
    gate.set()
    assert r.wait_idle(5)
    assert forces == [False, True]


def test_changed_inputs_rerun_even_a_running_forced_job():
    """rerun=True: the job's inputs changed (a WATCH) — a running job, forced or not, runs once
    more; a plain forced request on a running forced job is still absorbed (it is fresh anyway)."""
    gate, started, forces = threading.Event(), threading.Event(), []

    def fn(s, t, force):
        forces.append(force)
        started.set()
        gate.wait(5)
    r = JobRunner(fn, Recorder())
    a = r.submit("X", "quote", force=True)
    assert started.wait(5)
    assert r.submit("X", "quote", force=True) is a
    assert r.submit("X", "quote", force=True, rerun=True) is a
    gate.set()
    assert r.wait_idle(5)
    assert forces == [True, True]


def test_worker_survives_system_exit_in_a_job():
    calls = []

    def fn(s, t, force):
        calls.append(t)
        if t == "boom":
            raise SystemExit(3)
    r = JobRunner(fn, Recorder(), workers=1)
    r.submit("X", "boom")
    assert r.wait_idle(5)
    assert r.last_failure("X").error.startswith("SystemExit")
    r.submit("X", "ok")
    assert r.wait_idle(5)
    assert calls == ["boom", "ok"]


def test_shutdown_stops_worker_threads():
    r = JobRunner(lambda s, t, f: None, Recorder(), workers=2)
    threads = list(r._threads)
    r.shutdown()
    for t in threads:
        t.join(2)
    assert not any(t.is_alive() for t in threads)


def test_force_merged_while_a_job_starts_is_not_lost():
    """submit() merges `force` into a job it sees as queued, under the lock; the worker must take
    that same lock to flip the job to running and read its force, or the merge lands after the read."""
    import time
    from monitor.server.jobs import Job, _now
    seen = []
    r = JobRunner(lambda s, t, force: seen.append(force), Recorder(), workers=1)
    job = Job("X", "quote", False, id=10_000, queued=_now())
    r._active[("X", "quote")] = job
    r._lock.acquire()                                    # a submit() is mid-merge: state still "queued"
    try:
        assert job.state == "queued"
        t = threading.Thread(target=r._run, args=(job,))
        t.start()
        time.sleep(0.2)                                  # the worker reaches the job now
        job.force = True                                 # ... and the merge lands
    finally:
        r._lock.release()
    t.join(5)
    assert seen == [True]
