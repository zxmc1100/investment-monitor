"""Background refresh jobs.

One job per (screen, tier) at a time — a repeat request attaches to the running job.
Lifecycle events (queued → running → done|failed) go to the broker without the trace;
jobs() keeps the trace for the browser's error overlay. `heavy` jobs (screen builds that run
for minutes) have their own lane — one worker, one build at a time — so they never hold
a worker the quote and daily tiers need. A running job may report progress
(a build's `STAGE 2/7 GRID`), published as another job event.
"""
import itertools
import logging
import queue
import threading
import traceback
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Callable

log = logging.getLogger("monitor.jobs")
_ids = itertools.count(1)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class Job:
    screen: str
    tier: str
    force: bool
    id: int = 0
    state: str = "queued"
    queued: str = ""
    started: str | None = None
    finished: str | None = None
    error: str | None = None
    trace: str | None = None
    progress: str | None = None             # the running job's current stage, e.g. "2/7 GRID"

    def as_dict(self) -> dict:
        return asdict(self)


HEAVY = "heavy"


class JobRunner:
    def __init__(self, fn: Callable[[str, str, bool], None], broker, workers: int = 2,
                 heavy_workers: int | None = None):
        self._fn = fn
        self._broker = broker
        # Daemon workers (not ThreadPoolExecutor, whose threads are joined at exit): a reload or
        # Ctrl-C must not wait on an in-flight job. Parts are written atomically, so dying is safe.
        self._q: queue.Queue = queue.Queue()
        self._hq: queue.Queue = queue.Queue()          # the heavy lane
        self._closed = False
        self._active: dict[tuple[str, str], Job] = {}
        self._recent: deque[Job] = deque(maxlen=50)
        self._last: dict[tuple[str, str], Job] = {}    # the latest finished job per (screen, tier)
        self._lock = threading.Lock()
        self._idle = threading.Condition(self._lock)
        self._rerun: set[tuple[str, str]] = set()      # requests a running job cannot serve: run again after it
        heavy_workers = (1 if workers else 0) if heavy_workers is None else heavy_workers
        self._lanes = [(threading.Thread(target=self._worker, args=(self._q,), name=f"job-{i}", daemon=True),
                        self._q) for i in range(workers)]
        self._lanes += [(threading.Thread(target=self._worker, args=(self._hq,), name=f"heavy-{i}", daemon=True),
                         self._hq) for i in range(heavy_workers)]
        self._threads = [t for t, _ in self._lanes]
        for t in self._threads:
            t.start()

    def submit(self, screen: str, tier: str, force: bool = False, rerun: bool = False) -> Job:
        """Queue a job, or attach to the one queued / running. A running job cannot serve a forced
        request if it is unforced, nor a `rerun` one (its inputs changed — e.g. a WATCH — after it
        read them): either way it runs once more, forced, when it finishes."""
        with self._lock:
            job = self._active.get((screen, tier))
            if job is not None:
                if job.state == "queued":
                    job.force = job.force or force
                elif rerun or (force and not job.force):
                    self._rerun.add((screen, tier))
                return job
            job = Job(screen, tier, force, id=next(_ids), queued=_now())
            self._active[(screen, tier)] = job
        self._start(job)
        return job

    def _start(self, job: Job) -> None:
        self._emit(job)
        if not self._closed:
            (self._hq if job.tier == HEAVY else self._q).put(job)

    def _worker(self, q: queue.Queue) -> None:
        while True:
            job = q.get()
            if job is None:
                return
            try:
                self._run(job)
            except BaseException:                 # one bad job must never kill a worker
                log.exception("job worker survived %s/%s", job.screen, job.tier)

    def _run(self, job: Job) -> None:
        with self._lock:                      # submit() merges `force` into a queued job under this
            job.state, job.started = "running", _now()      # lock: flip + read force atomically,
            force = job.force                               # so a merge is never lost between them
        self._emit(job)
        try:
            self._fn(job.screen, job.tier, force)
            job.state = "done"
        except BaseException as e:
            job.state, job.error, job.trace = "failed", f"{type(e).__name__}: {e}", traceback.format_exc()
        finally:
            job.finished = _now()
            self._emit(job)                  # final event first: wait_idle() must not beat it
            follow = None
            with self._lock:
                key = (job.screen, job.tier)
                self._active.pop(key, None)
                self._recent.appendleft(job)
                self._last[key] = job
                if key in self._rerun:
                    self._rerun.discard(key)
                    follow = Job(job.screen, job.tier, True, id=next(_ids), queued=_now())
                    self._active[key] = follow
                self._idle.notify_all()
            if follow is not None:
                self._start(follow)

    def progress(self, screen: str, tier: str, text: str) -> None:
        """Record a running job's stage and publish it (a heavy build relays its STAGE lines)."""
        with self._lock:
            job = self._active.get((screen, tier))
            if job is None or job.state != "running":
                return
            job.progress = text
        self._emit(job)

    def _emit(self, job: Job) -> None:
        self._broker.publish({"type": "job", **{k: v for k, v in job.as_dict().items() if k != "trace"}})

    def running(self, screen: str) -> list[str]:
        with self._lock:
            return sorted(t for (s, t) in self._active if s == screen)

    def last_run(self, screen: str, tier: str) -> Job | None:
        """The latest finished job for (screen, tier), however long ago — not bounded like jobs()."""
        with self._lock:
            return self._last.get((screen, tier))

    def last_failure(self, screen: str) -> Job | None:
        """Newest failure whose tier has not succeeded since."""
        seen: set[str] = set()
        with self._lock:
            for job in self._recent:
                if job.screen != screen or job.tier in seen:
                    continue
                seen.add(job.tier)
                if job.state == "failed":
                    return job
        return None

    def jobs(self) -> list[dict]:
        with self._lock:
            return [j.as_dict() for j in [*self._active.values(), *self._recent]]

    def wait_idle(self, timeout: float = 10.0) -> bool:
        with self._idle:
            return self._idle.wait_for(lambda: not self._active, timeout)

    def shutdown(self) -> None:
        self._closed = True
        for q in (self._q, self._hq):        # drop queued work
            while True:
                try:
                    q.get_nowait()
                except queue.Empty:
                    break
        for _, q in self._lanes:             # one sentinel per worker: idle workers exit now,
            q.put(None)                      # a busy one exits after its current job
