"""IdleWatch: the macOS service's terminal stops by itself once nobody uses it — no tab open, no request,
no job running — for its minutes; the next visit starts it again (monitor.server.service)."""
import asyncio

from monitor.server.idle import IdleWatch


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def watch(tabs=0, busy=False):
    clock, stopped, state = Clock(), [], {"tabs": tabs, "busy": busy}
    w = IdleWatch(15, tabs=lambda: state["tabs"], busy=lambda: state["busy"], stop=lambda: stopped.append(clock.t),
                  clock=clock)
    return w, clock, state, stopped


def test_it_stops_once_after_its_minutes_without_a_tab_a_request_or_a_job():
    w, clock, _, stopped = watch()
    clock.t = 14 * 60
    assert not w.check() and not stopped
    clock.t = 15 * 60
    assert w.check() and stopped == [900]
    clock.t = 20 * 60
    assert w.check() and stopped == [900]                       # once


def test_an_open_tab_keeps_it_running_and_the_minutes_count_from_when_the_last_closes():
    w, clock, state, stopped = watch(tabs=1)
    clock.t = 3 * 3600
    assert not w.check()
    state["tabs"] = 0                                            # the last tab closed (seen at 3 h)
    clock.t = 3 * 3600 + 14 * 60
    assert not w.check()
    clock.t = 3 * 3600 + 15 * 60
    assert w.check() and stopped


def test_a_request_or_a_running_job_restarts_the_minutes():
    w, clock, state, stopped = watch()
    clock.t = 10 * 60
    w.touch()                                                    # e.g. a script asked /api/screens
    clock.t = 24 * 60
    assert not w.check()
    state["busy"] = True                                         # a BUILD runs past the minutes
    clock.t = 60 * 60
    assert not w.check()
    state["busy"] = False
    clock.t = 74 * 60
    assert not w.check()
    clock.t = 75 * 60
    assert w.check() and stopped == [4500]


def test_run_checks_until_it_stops():
    w, clock, _, stopped = watch()
    clock.t = 16 * 60
    asyncio.run(asyncio.wait_for(w.run(every=0.001), timeout=2))
    assert stopped == [960]
