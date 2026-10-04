"""Server → browser events over SSE.

Job threads publish; each subscriber's asyncio loop delivers. publish() is thread-safe;
a slow subscriber loses its OLDEST events (the browser re-fetches state on 'screen' anyway).
"""
import asyncio
import json
import threading


def _offer(q: asyncio.Queue, event: dict) -> None:
    if q.full():
        try:
            q.get_nowait()
        except asyncio.QueueEmpty:
            pass
    q.put_nowait(event)


class Broker:
    def __init__(self, maxsize: int = 100):
        self._subs: dict[asyncio.Queue, asyncio.AbstractEventLoop] = {}
        self._lock = threading.Lock()
        self._maxsize = maxsize

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._maxsize)
        with self._lock:
            self._subs[q] = asyncio.get_running_loop()
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        with self._lock:
            self._subs.pop(q, None)

    @property
    def subscribers(self) -> int:
        return len(self._subs)

    def publish(self, event: dict) -> None:
        with self._lock:
            subs = list(self._subs.items())
        for q, loop in subs:
            try:
                loop.call_soon_threadsafe(_offer, q, event)
            except RuntimeError:                     # loop closed: subscriber is gone
                self.unsubscribe(q)


def format_sse(event: dict) -> str:
    return f"event: {event['type']}\ndata: {json.dumps(event, separators=(',', ':'))}\n\n"


async def sse_stream(q: asyncio.Queue, heartbeat_s: float = 15.0):
    yield "retry: 2000\n\n"                          # browser reconnect delay after a server reload
    while True:
        try:
            event = await asyncio.wait_for(q.get(), heartbeat_s)
        except asyncio.TimeoutError:
            yield ": ping\n\n"
            continue
        yield format_sse(event)
