"""The macOS service's terminal stops by itself once nobody uses it (monitor.server.service): no tab open (none
listening to the stream), no request, no job running, for `minutes`. launchd keeps the port and starts it
again on the next visit. A terminal started from a start file never stops this way."""
import asyncio
import logging
import os
import signal
import time
from typing import Callable

log = logging.getLogger("monitor.server")


class IdleWatch:
    def __init__(self, minutes: float, *, tabs: Callable[[], int], busy: Callable[[], bool],
                 stop: Callable[[], None], clock: Callable[[], float] = time.monotonic):
        self.limit, self.tabs, self.busy, self.stop, self.clock = minutes * 60, tabs, busy, stop, clock
        self.last = clock()
        self.stopped = False

    def touch(self) -> None:
        """A request came in."""
        self.last = self.clock()

    def check(self) -> bool:
        """Stop once the minutes have passed with nothing in use; True once stopped."""
        if self.stopped:
            return True
        if self.tabs() > 0 or self.busy():
            self.touch()                      # the minutes count from when the last tab / job ended
        elif self.clock() - self.last >= self.limit:
            self.stopped = True
            self.stop()
        return self.stopped

    async def run(self, every: float | None = None) -> None:
        every = min(30.0, self.limit / 3) if every is None else every
        while True:
            try:
                if self.check():
                    return
            except Exception:                       # never die silently: the service would never stop
                log.warning("idle check failed", exc_info=True)
            await asyncio.sleep(every)


def stop_server() -> None:
    """A graceful stop of the process holding the socket: uvicorn's reloader when there is one (it stops its
    worker — this process), else this process."""
    os.kill(os.getppid() if os.environ.get("MONITOR_SUPERVISED") else os.getpid(), signal.SIGTERM)
