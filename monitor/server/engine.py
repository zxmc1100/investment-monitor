"""Engine — ties screens, store, jobs and the broker together and owns the freshness
policy. A tier is due when it has never run, was computed by different
code, or is older than its max age; one whose last run failed is not retried automatically
for FAILED_RETRY_MIN (a manual refresh runs at once). Payloads assemble only when EVERY tier
exists and was computed by the current code, so a half-updated screen is never shown.
"""
import collections
import contextlib
import logging
import os
import re
import subprocess
import sys
import threading
from pathlib import Path
from dataclasses import replace
from datetime import datetime
from typing import Any, Callable

from monitor import config
from monitor.portfolio.trades import book_error, has_trades
from monitor.screens.base import Ctx, Screen, no_trades, screen_key, split_key, trades_error
from monitor.server import prefs as prefs_mod
from monitor.server.jobs import Job, JobRunner
from monitor.server.store import Store
from monitor.server.stream import Broker

log = logging.getLogger("monitor.engine")
MAX_AGE_S = {"quote": config.QUOTE_INTERVAL_S, "daily": config.DAILY_TTL_H * 3600}   # heavy: never auto
# A tier is due slightly early: the client pokes every MAX_AGE_S, and the part's age is measured
# after the compute, so a strict '>' would skip every other poke (~2x cadence).
DUE_SLACK = 0.9
# yfinance is not thread-safe and throttles bursts: tier computes — all of which hit
# the network — run one at a time even though the job pool has two workers.
_NET_LOCK = threading.Lock()
STAGE = re.compile(r"^STAGE (\d+/\d+ \S.*)$")      # a build child's progress line


class BuildFailed(RuntimeError):
    """A heavy build child exited non-zero; the message is its last stderr line."""


class BuildKilled(BuildFailed):
    """The server stopped a build child (shutdown or reload) — not the build's own failure."""


class Engine:
    def __init__(self, screens: dict[str, Screen], store: Store, broker: Broker, *,
                 ctx: Ctx | None = None, workers: int = 2, prefs_path: Path | None = None):
        self.screens, self.store, self.broker = screens, store, broker
        self.ctx = ctx or Ctx()
        self.prefs_path = prefs_path
        self.prefs = prefs_mod.load(prefs_path)
        # (screen, tier) jobs that are not screen tiers, e.g. ("ALRT", "watch") — the task does its
        # own locking and storing (monitor.server.alerting)
        self.tasks: dict[tuple[str, str], Callable[[bool], Any]] = {}
        self.runner = JobRunner(self._run_tier, broker, workers)
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        self._children: set[subprocess.Popen] = set()      # running build children (shutdown kills them)
        self._scheduled: set[str] = set()                    # keys whose next build the schedule asked for
        self._stopping = False                               # shutdown() began: a child's death is a kill
        self._settle: threading.Timer | None = None          # inputs_changed's pending recompute (a burst)

    @classmethod
    def default(cls, *, prefs_path: Path | None = config.PREFS_FILE,
                watchlist: Path | None = config.WATCHLIST_FILE,
                alerts: Path | None = config.ALERTS_FILE,
                paths: dict | None = None) -> "Engine":
        """The terminal's engine: every screen (a local add-on's too), the screen store under local/, and
        the add-on's named state files (Ctx.paths) — `paths` overrides them."""
        from monitor import plugins
        from monitor.screens import SCREENS
        return cls(SCREENS, Store(config.SCREENS_DIR), Broker(), prefs_path=prefs_path,
                   ctx=Ctx(watchlist=watchlist, alerts=alerts, paths={**plugins.ctx_paths(), **(paths or {})}))

    def _lock(self, key: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    def params(self, sid: str) -> list[str] | None:
        scr = self.screens[sid]
        return None if scr.params is None else list(scr.params(self.ctx))

    def accepts(self, sid: str, param: str) -> bool:
        return self.screens[sid].accepts(self.ctx, param)

    def prune_params(self, max_age_days: int = 7, now: datetime | None = None) -> list[str]:
        """Drop stored keys of parametrized screens (SEC~TKR) whose param is neither traded nor watched
        and whose newest part is older than `max_age_days` — every universe ticker ever opened would
        otherwise persist forever. Returns the dropped keys."""
        now, dropped = now or datetime.now(), []
        for sid, scr in self.screens.items():
            if scr.params is None:
                continue
            keep = set(self.params(sid) or [])
            for key in sorted(self.store.keys(f"{sid}~", scr.tiers)):
                if split_key(key)[1] in keep:
                    continue
                ats = [i["at"] for t in scr.tiers if (i := self.store.part_info(key, t))]
                if ats and (now - datetime.fromisoformat(max(ats))).total_seconds() <= max_age_days * 86400:
                    continue
                self.store.drop(key, scr.tiers)
                dropped.append(key)
        return dropped

    def cold_reason(self, sid: str) -> str | None:
        """Why `sid` cannot compute at all right now (no trades yet — no input/portfolio.csv, or a header
        alone —, a trades file that cannot be read, or the screen's own `cold` reason, e.g. fewer than two
        priced positions), else None. Such a screen runs no jobs — nothing fails, nothing is logged — and
        computes once the reason is gone."""
        scr = self.screens[sid]
        if scr.needs_portfolio:
            csv = Path(self.ctx.portfolio_csv)
            if not has_trades(csv):
                return no_trades(self.screens)
            if error := book_error(csv):
                return trades_error(self.screens, error)
        return scr.cold(self.ctx) if scr.cold is not None else None

    @staticmethod
    def _net(scr: Screen):
        """The network lock a tier compute holds — none for an inline (local) screen."""
        return contextlib.nullcontext() if scr.inline else _NET_LOCK

    def _compute_inline(self, sid: str, param: str | None, tiers: list[str], *, force: bool = False) -> None:
        """An inline screen's tiers, here and now, then assemble (and publish) it."""
        scr, key = self.screens[sid], screen_key(sid, param)
        for tier in tiers:
            version = scr.version(self.ctx)
            self._put(key, tier, scr.run(tier, replace(self.ctx, force=force), param), version)
        if tiers:
            self.assemble(sid, param)

    def _run_tier(self, key: str, tier: str, force: bool) -> None:
        task = self.tasks.get((key, tier))
        if task is not None:
            task(force)
            return
        sid, param = split_key(key)
        scr = self.screens[sid]
        version = scr.version(self.ctx)          # before the compute: inputs edited meanwhile -> due again
        if tier == "heavy" and scr.build_cmd:
            if force:                                       # BUILD / schedule: rebuild first
                with self._locks_guard:
                    scheduled = key in self._scheduled
                    self._scheduled.discard(key)
                self._spawn_build(key, scr, ("--scheduled",) if scheduled else ())
            part = scr.run(tier, replace(self.ctx, force=force), param)   # load: no network, no lock
        else:
            with self._net(scr):
                part = scr.run(tier, replace(self.ctx, force=force), param)
        self._put(key, tier, part, version)
        self.assemble(sid, param)

    def _spawn_build(self, key: str, scr: Screen, extra: tuple[str, ...] = ()) -> None:
        """Run the screen's build as a child process — another interpreter, so it holds neither
        _NET_LOCK nor the GIL — relaying its `STAGE i/n NAME` lines as job progress. A non-zero exit
        raises BuildFailed(last stderr line): the job fails and the stored part (last good) stays. A
        child running past config.BUILD_TIMEOUT_MIN is killed (BuildFailed: timed out); on any way out
        of here a child still running is killed and reaped."""
        proc = subprocess.Popen([sys.executable, *scr.build_cmd, *extra], cwd=config.REPO_ROOT, text=True, bufsize=1,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace",
                                env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"})
        with self._locks_guard:
            self._children.add(proc)
        tail: collections.deque[str] = collections.deque(maxlen=20)
        drain = threading.Thread(target=lambda: tail.extend(ln.rstrip() for ln in proc.stderr), daemon=True)
        drain.start()
        limit = config.BUILD_TIMEOUT_MIN
        timed_out = threading.Event()

        def overdue() -> None:
            timed_out.set()
            proc.kill()                                      # ends the stdout loop below
        timer = threading.Timer(limit * 60, overdue)
        timer.daemon = True
        timer.start()
        try:
            for line in proc.stdout:
                m = STAGE.match(line.strip())
                if m:
                    self.runner.progress(key, "heavy", m.group(1))
            code = proc.wait()
            drain.join(5)
        finally:
            timer.cancel()
            if proc.poll() is None:                          # we are leaving on an exception: never orphan it
                proc.kill()
                proc.wait()
            with self._locks_guard:
                self._children.discard(proc)
        if self._stopping and code != 0:
            raise BuildKilled("the server stopped this build")
        if timed_out.is_set():
            raise BuildFailed(f"build timed out after {limit:g} min — killed")
        if code != 0:
            raise BuildFailed(next((ln for ln in reversed(tail) if ln.strip()), f"exit status {code}"))

    def shutdown(self) -> None:
        """Stop the job workers and terminate any build child (server shutdown or reload); the build's
        job fails as BuildKilled, which the month-start schedule retries."""
        self._stopping = True
        with self._locks_guard:
            if self._settle is not None:
                self._settle.cancel()
                self._settle = None
        self.runner.shutdown()
        with self._locks_guard:
            kids = list(self._children)
        for p in kids:
            p.terminate()
        for p in kids:
            try:
                p.wait(3)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait()

    def _put(self, key: str, tier: str, part: Any, code: str) -> None:
        """Store a part under the screen's lock. assemble() re-reads an unreadable part and drops it
        under the same lock, so a part landing mid-check is never dropped."""
        with self._lock(key):
            self.store.put_part(key, tier, part, code)

    def _build(self, sid: str, param: str | None, *, repair: bool) -> tuple[dict, dict] | None:
        """(payload, meta) from the stored parts under this engine's prefs, or None unless every
        tier exists at the current code. `repair` drops unreadable parts (a store write)."""
        scr, key = self.screens[sid], screen_key(sid, param)
        code = scr.version(self.ctx)
        recs = {t: self.store.get_part(key, t) for t in scr.tiers}
        if repair:
            for t, r in recs.items():
                if r is None and self.store.part_info(key, t) is not None \
                        and self.store.get_part(key, t) is None:
                    self.store.invalidate_part(key, t)  # re-read: a concurrent put_part may just have landed
        # an in-process heavy part survives a code edit (it never auto-reruns); a build screen's heavy
        # part is a cheap reload of the build's output, so it must be current like the others
        if any(r is None or (r["code"] != code and (t != "heavy" or scr.build_cmd)) for t, r in recs.items()):
            return None
        meta = {"computed_at": datetime.now().isoformat(timespec="seconds"),
                "tiers": {t: r["at"] for t, r in recs.items()}, "code_version": scr.code_version(),
                "prefs": dict(self.prefs)}
        return scr.build({t: r["data"] for t, r in recs.items()}, meta, param), meta

    def assemble(self, sid: str, param: str | None = None) -> dict | None:
        key = screen_key(sid, param)
        with self._lock(key):
            built = self._build(sid, param, repair=True)
            if built is None:
                return None
            payload, meta = built
            self.store.put_payload(key, payload)
        self.broker.publish({"type": "screen", "id": sid, "key": key, "param": param,
                             "computed_at": meta["computed_at"]})
        return payload

    def put_and_assemble(self, sid: str, tier: str, part: Any, param: str | None = None) -> dict | None:
        """Store a part computed outside the job pool (cheap, no network — e.g. the alert state
        after an ACK) at the current code version, then re-assemble and publish the screen."""
        self._put(screen_key(sid, param), tier, part, self.screens[sid].version(self.ctx))
        return self.assemble(sid, param)

    def build_stored(self, sid: str, param: str | None = None) -> dict | None:
        """Assemble from the stored parts WITHOUT persisting or publishing (export --cached):
        the store is only read. None unless every tier is stored at the current code."""
        with self._lock(screen_key(sid, param)):
            built = self._build(sid, param, repair=False)
        return None if built is None else built[0]

    def due_tiers(self, sid: str, param: str | None = None, now: datetime | None = None) -> list[str]:
        scr, key = self.screens[sid], screen_key(sid, param)
        code, now, due = scr.version(self.ctx), now or datetime.now(), []
        stamp = scr.stamp(self.ctx) if scr.stamp is not None else None
        for tier in scr.tiers:
            if tier == "heavy" and not scr.build_cmd:          # never auto-runs
                continue
            info = self.store.part_info(key, tier)
            if info is None or info["code"] != code:
                due.append(tier)                           # a build screen's heavy: a load, not a build
                continue
            if stamp is not None and stamp > datetime.fromisoformat(info["at"]).timestamp():
                due.append(tier)                           # its input (an artifact) is newer
                continue
            limit = None if scr.inline else MAX_AGE_S.get(tier)
            if limit is not None and (now - datetime.fromisoformat(info["at"])).total_seconds() >= limit * DUE_SLACK:
                due.append(tier)
        return due

    def backing_off(self, key: str, tier: str, now: datetime | None = None) -> bool:
        """The last run of (key, tier) failed less than FAILED_RETRY_MIN ago: no automatic retry yet,
        so a failing network compute does not run again (holding _NET_LOCK) on every client poke."""
        job = self.runner.last_run(key, tier)
        if job is None or job.state != "failed" or not job.finished:
            return False
        age = ((now or datetime.now()) - datetime.fromisoformat(job.finished)).total_seconds()
        return age < config.FAILED_RETRY_MIN * 60

    def ensure_fresh(self, sid: str, param: str | None = None) -> list[Job]:
        """Submit the due tiers, except one whose last run failed recently (see backing_off) — a
        manual refresh() still runs at once."""
        if self.cold_reason(sid):
            return []
        if self.screens[sid].inline:                            # cheap and local: now, in this request
            self._compute_inline(sid, param, self.due_tiers(sid, param))
            return []
        key, now = screen_key(sid, param), datetime.now()
        return [self.runner.submit(key, t, force=(t == "quote")) for t in self.due_tiers(sid, param, now)
                if not self.backing_off(key, t, now)]

    def inputs_changed(self, settle: float = 0.0) -> list[Job]:
        """Your trades were just written (TRADES): every screen that reads them brings itself up to date — an
        inline one at once, any other that was computed before queues its due tiers (its current payload stays
        until the new one assembles); a screen never opened computes when it is. With `settle` > 0 the others
        wait until no further change came for `settle` s: a burst of writes recomputes them once, on the last."""
        jobs: list[Job] = []
        for sid, scr in self._readers():
            if scr.inline:
                jobs += self.ensure_fresh(sid)
        if settle <= 0:
            return jobs + self._queue_readers()
        with self._locks_guard:
            if self._settle is not None:
                self._settle.cancel()
            self._settle = threading.Timer(settle, self._settled)
            self._settle.daemon = True
            self._settle.start()
        return jobs

    def _readers(self):
        return [(sid, scr) for sid, scr in self.screens.items()
                if scr.status == "live" and scr.uses_inputs and scr.params is None]

    def _queue_readers(self) -> list[Job]:
        return [j for sid, scr in self._readers() if not scr.inline
                and any(self.store.part_info(sid, t) for t in scr.tiers) for j in self.ensure_fresh(sid)]

    def _settled(self) -> None:
        with self._locks_guard:
            self._settle = None
        try:
            self._queue_readers()
        except Exception:                                    # a timer thread: log, never die loudly
            log.warning("recompute after a trades change failed", exc_info=True)

    def build(self, sid: str, *, scheduled: bool = False) -> Job:
        """Run a build screen's build child now, then reload (BUILD <screen>, or the month-start schedule).
        A scheduled build passes `--scheduled` to the child (it may then skip work only a manual build needs);
        a manual request before the job starts makes it a manual build."""
        scr = self.screens[sid]
        if not scr.build_cmd:
            raise ValueError(f"{sid} has no build")
        with self._locks_guard:
            if scheduled:
                self._scheduled.add(sid)
            else:
                self._scheduled.discard(sid)
        return self.runner.submit(sid, "heavy", force=True)

    def refresh(self, sid: str, tiers: list[str] | None = None, *, param: str | None = None) -> list[Job]:
        """Recompute the tiers now (forced). A build screen's heavy tier is only reloaded from its
        artifact — REFRESH never starts a multi-GB build; build() does."""
        if self.cold_reason(sid):
            return []
        scr, key = self.screens[sid], screen_key(sid, param)
        return [self.runner.submit(key, t, force=not (t == "heavy" and scr.build_cmd))
                for t in (tiers or scr.tiers)]

    def payload(self, sid: str, param: str | None = None) -> dict | None:
        return self.store.get_payload(screen_key(sid, param))

    def live(self, sid: str, param: str | None = None) -> dict:
        key = screen_key(sid, param)
        payload = self.store.get_payload(key)
        fail = self.runner.last_failure(key)
        return {"running": self.runner.running(key),
                "code_changed": bool(payload) and
                payload.get("meta", {}).get("code_version") != self.screens[sid].code_version(),
                "error": None if fail is None else
                {"tier": fail.tier, "error": fail.error, "trace": fail.trace, "at": fail.finished}}

    def watchlist_changed(self) -> list[Job]:
        """MKT's MY NAMES and EVENTS follow the watchlist: recompute both tiers, forced, and rerun
        any MKT job already computing — it read the old watchlist (JobRunner.submit rerun). Forcing
        also refetches the board's 1y history once; the movers keep their own 15-minute cache."""
        scr = self.screens.get("MKT")
        if scr is None or scr.status != "live":
            return []
        return [self.runner.submit("MKT", t, force=True, rerun=True) for t in scr.tiers]

    def set_target(self, name: str) -> dict:
        self.prefs = prefs_mod.with_target(self.prefs, name)       # ValueError on unknown
        prefs_mod.save(self.prefs_path, self.prefs)
        for sid, scr in self.screens.items():
            if scr.status == "live" and scr.params is None:
                self.assemble(sid)                                   # cheap: parts unchanged
        return dict(self.prefs)

    def compute_now(self, sid: str, *, force: bool = True, param: str | None = None) -> dict:
        """All tiers inline, then assemble — for export and tests."""
        if reason := self.cold_reason(sid):
            raise RuntimeError(reason)
        scr, key = self.screens[sid], screen_key(sid, param)
        for tier in scr.tiers:
            version = scr.version(self.ctx)
            with self._net(scr):
                part = scr.run(tier, replace(self.ctx, force=force), param)
            self._put(key, tier, part, version)
        payload = self.assemble(sid, param)
        if payload is None:
            raise RuntimeError(f"{key}: assemble produced no payload")
        return payload
