import asyncio
import threading

from monitor.server.stream import Broker, sse_stream


def test_publish_from_thread_reaches_subscriber():
    async def main():
        b = Broker()
        q = b.subscribe()
        threading.Thread(target=b.publish, args=({"type": "screen", "id": "PORT"},)).start()
        ev = await asyncio.wait_for(q.get(), 2)
        b.unsubscribe(q)
        return ev, b.subscribers
    ev, n = asyncio.run(main())
    assert ev == {"type": "screen", "id": "PORT"} and n == 0


def test_full_queue_drops_oldest():
    async def main():
        b = Broker(maxsize=2)
        q = b.subscribe()
        for i in range(3):
            b.publish({"type": "job", "i": i})
        await asyncio.sleep(0.05)               # let the threadsafe callbacks run
        return [q.get_nowait()["i"], q.get_nowait()["i"]]
    assert asyncio.run(main()) == [1, 2]


def test_sse_stream_formats_events_and_heartbeats():
    async def main():
        b = Broker()
        q = b.subscribe()
        gen = sse_stream(q, heartbeat_s=0.05)
        first = await gen.__anext__()
        ping = await gen.__anext__()
        b.publish({"type": "screen", "id": "PORT"})
        ev = await gen.__anext__()
        await gen.aclose()
        return first, ping, ev
    first, ping, ev = asyncio.run(main())
    assert first.startswith("retry:") and ping == ": ping\n\n"
    assert ev == 'event: screen\ndata: {"type":"screen","id":"PORT"}\n\n'
