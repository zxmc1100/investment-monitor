"""Month-start rebuilds.

While at least one terminal tab holds the event stream open, a screen flagged `monthly` (none in the
core; a local add-on's, monitor.plugins) whose build output predates this month's rebuild day is
rebuilt: one forced heavy job, i.e. the build child. The rebuild day is the month's SECOND business
day: a monthly model decides on the last session of the old period, and a build whose prices end
exactly there may not have acted on it yet — the first session of the new month must be in the
data. One attempt per screen per day, kept in local/buffer/schedule.json
once the attempt ends: a build that finished, or failed for real, waits for tomorrow; one a server reload
killed (BuildKilled) or forgot is submitted again on the next tick. None while a heavy job of that
screen runs or already ran today. A screen never built waits for its first BUILD (its cold view says
so): the schedule only keeps an existing build current. Its builds run with `--scheduled`
(Engine.build), so the child may skip work only a manual build needs. The rebuild day counts weekdays
only, so a holiday (1 Jan on a weekday) can leave a rebuild-day build without the new period's first
session: a build is current only when Screen.current also says so (e.g. its prices hold a session of
the new period) — until then it is tried again, once a day.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import Callable

from monitor.alerts.jsonfile import write_atomic
from monitor.screens.base import rebuild_day

log = logging.getLogger("monitor.schedule")


def _killed(job: dict) -> bool:
    return str(job.get("error") or "").startswith("BuildKilled")




class BuildSchedule:
    def __init__(self, engine, path: Path | None, clock: Callable[[], datetime] = datetime.now):
        self.engine, self.clock = engine, clock
        self.path = None if path is None else Path(path)
        self._mem: dict[str, str] = {}                   # path None (tests): attempts kept in memory
        self._pending: dict[str, tuple[int, date]] = {}   # sid -> (job id, day) submitted, not yet settled

    def _tried(self) -> dict:
        if self.path is None:
            return dict(self._mem)
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _mark(self, sid: str, day: date) -> None:
        data = {**self._tried(), sid: day.isoformat()}
        if self.path is None:
            self._mem = data
        else:
            write_atomic(self.path, data)

    def _ran_today(self, sid: str, day: date) -> bool:
        return any(j["screen"] == sid and j["tier"] == "heavy" and j.get("force") and not _killed(j)
                   and (j.get("started") or "")[:10] == day.isoformat() for j in self.engine.runner.jobs())

    def _job(self, sid: str, jid: int) -> dict | None:
        """The job deciding the submitted build's outcome: the newest heavy job of the screen with id >= the
        submitted one — a build submitted while an unforced reload ran attached to the reload, and runs as
        its forced follow-up. A queued / running one first (not settled yet), else the newest forced one,
        else the newest; None when this runner knows none of them."""
        run = self.engine.runner
        mine = sorted((j for j in run.jobs() if j.get("screen") == sid and j.get("tier") == "heavy"
                       and (j.get("id") or 0) >= jid), key=lambda j: j["id"], reverse=True)
        if not mine and (last := run.last_run(sid, "heavy")) is not None and last.id >= jid:
            mine = [last.as_dict()]
        return (next((j for j in mine if j.get("state") in ("queued", "running")), None)
                or next((j for j in mine if j.get("force")), None) or (mine[0] if mine else None))

    def _settle(self) -> None:
        """Mark the day of every submitted build that ended: done, or failed for real. A killed one (the
        server stopped it) or one this runner no longer knows is dropped unmarked — due again."""
        for sid, (jid, day) in list(self._pending.items()):
            job = self._job(sid, jid)
            if job is not None and job.get("state") in ("queued", "running"):
                continue
            del self._pending[sid]
            if job is not None and not _killed(job):
                self._mark(sid, day)

    def due(self, sid: str, now: datetime) -> bool:
        scr = self.engine.screens[sid]
        if not (scr.monthly and scr.build_cmd and scr.stamp is not None and scr.status == "live"):
            return False
        built = scr.stamp(self.engine.ctx)
        if built is None:                                 # never built: the first build is a REFRESH
            return False
        today = now.date()
        day = rebuild_day(today)
        if today < day:
            return False
        if datetime.fromtimestamp(built).date() >= day and (scr.current is None or scr.current(self.engine.ctx)):
            return False                                  # rebuilt this month and current (no missed rebalance)
        if sid in self._pending or self._tried().get(sid) == today.isoformat() or self._ran_today(sid, today):
            return False
        return "heavy" not in self.engine.runner.running(sid)

    def tick(self) -> list[str]:
        """Submit every due rebuild (only while a tab is connected). Returns the screens submitted."""
        self._settle()
        if not getattr(self.engine.broker, "subscribers", 0):
            return []
        now, out = self.clock(), []
        for sid in self.engine.screens:
            if self.due(sid, now):
                job = self.engine.build(sid, scheduled=True)  # the child fetches only when the rebalance is due
                self._pending[sid] = (getattr(job, "id", 0), now.date())
                out.append(sid)
        return out

    async def run(self, poll_s: float = 30.0) -> None:
        while True:
            try:
                self.tick()
            except Exception:                             # never let one tick end the loop
                log.exception("build schedule tick failed")
            await asyncio.sleep(poll_s)
