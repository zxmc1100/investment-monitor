"""Transport: web/app/api.js over urllib — the server's own JSON routes and its event stream. Writes carry no Origin, as
curl's do: the server's CSRF guard is for pages in a browser."""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

# loopback (or the tailnet) needs no proxy, whatever the environment says
_fetch = urllib.request.build_opener(urllib.request.ProxyHandler({})).open
SSE_RETRY_S = 2.0


class ApiError(Exception):
    """A failed request: `detail` is the one line the server gave (or SERVER UNREACHABLE), `status` the HTTP status."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def detail_text(body, status: int) -> str:
    d = body.get("detail") if isinstance(body, dict) else None
    if isinstance(d, str) and d:
        return d.upper()
    if isinstance(d, dict) and isinstance(d.get("error"), str) and d["error"]:
        return d["error"]
    return f"HTTP {status}"


def why(e: BaseException) -> str:
    return e.detail if isinstance(e, ApiError) else str(e).upper()


def _q(s) -> str:
    return urllib.parse.quote(str(s), safe="")


class Api:
    def __init__(self, base: str):
        self.base = base.rstrip("/")

    def _json(self, path: str, method: str = "GET", body=None, timeout: float = 120.0):
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            self.base + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"} if data is not None else {},
        )
        try:
            with _fetch(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            try:
                parsed = json.load(e)
            except ValueError:
                parsed = None
            raise ApiError(e.code, detail_text(parsed, e.code)) from None
        except (urllib.error.URLError, OSError, ValueError):
            raise ApiError(0, "SERVER UNREACHABLE") from None

    @staticmethod
    def _path(base: str, sid: str, param=None) -> str:
        return f"{base}/{sid}" + (f"/{_q(param)}" if param else "")

    def registry(self, timeout: float = 120.0):
        return self._json("/api/screens", timeout=timeout)

    def screen(self, sid, param=None):
        return self._json(self._path("/api/screen", sid, param))

    def ensure(self, sid, param=None):
        return self._json(self._path("/api/ensure", sid, param), "POST")

    def refresh(self, sid, tier=None, param=None):
        return self._json(self._path("/api/refresh", sid, param) + (f"?tier={_q(tier)}" if tier else ""), "POST")

    def build(self, sid):
        return self._json(self._path("/api/build", sid), "POST")

    def prefs(self):
        return self._json("/api/prefs")

    def set_target(self, name):
        return self._json("/api/prefs", "POST", {"target": name})

    def jobs(self):
        return self._json("/api/jobs")

    def lookup(self, q):
        return self._json(f"/api/lookup?q={_q(q)}")

    def watch(self, ticker):
        return self._json("/api/watchlist", "POST", {"ticker": ticker})

    def unwatch(self, ticker):
        return self._json(f"/api/watchlist/{_q(ticker)}", "DELETE")

    def alerts(self):
        return self._json("/api/alerts")

    def add_alert(self, text):
        return self._json("/api/alerts", "POST", {"text": text})

    def del_alert(self, rid):
        return self._json(f"/api/alerts/{_q(rid)}", "DELETE")

    def ack(self, body):
        return self._json("/api/alerts/ack", "POST", body)

    def trades(self):
        return self._json("/api/trades")

    def add_trade(self, etag, trade):
        return self._json("/api/trades", "POST", {"etag": etag, "trade": trade})

    def edit_trade(self, rid, etag, trade):
        return self._json(f"/api/trades/{_q(rid)}", "PUT", {"etag": etag, "trade": trade})

    def del_trade(self, rid, etag):
        return self._json(f"/api/trades/{_q(rid)}", "DELETE", {"etag": etag})

    def preview_trades(self, text, mode):
        return self._json("/api/trades/preview", "POST", {"text": text, "mode": mode})

    def import_trades(self, etag, text, mode):
        return self._json("/api/trades/import", "POST", {"etag": etag, "text": text, "mode": mode})

    def reset_trades(self, etag):
        return self._json("/api/trades/reset", "POST", {"etag": etag})

    def undo_trades(self, etag):
        return self._json("/api/trades/undo", "POST", {"etag": etag})

    def subscribe(self, on_event) -> threading.Event:
        """The event stream on a daemon thread: on_event(type, data) per event, ("conn", {ok}) as it comes and goes, and a
        new connection after a drop (a server reload). Holding it open is also what keeps the macOS service from idling
        out, as an open tab does. Set the returned Event to stop."""
        stop = threading.Event()

        def run():
            while not stop.is_set():
                try:
                    with _fetch(self.base + "/api/stream", timeout=40) as r:  # a ping every 15 s
                        on_event("conn", {"ok": True})
                        for ev, data in sse_events(r):
                            if stop.is_set():
                                return
                            on_event(ev, data)
                except (OSError, ValueError, urllib.error.URLError):
                    pass
                if not stop.is_set():
                    on_event("conn", {"ok": False})
                stop.wait(SSE_RETRY_S)

        threading.Thread(target=run, daemon=True, name="tui-sse").start()
        return stop


def sse_events(lines):
    """(event, data) from an SSE byte stream's lines; a line that is not JSON is skipped, never fatal."""
    ev, data = None, []
    for raw in lines:
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if not line:
            if ev and data:
                try:
                    yield ev, json.loads("\n".join(data))
                except ValueError:
                    pass
            ev, data = None, []
        elif line.startswith("event:"):
            ev = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].strip())


def wait_up(api: Api, seconds: float) -> dict | None:
    """The registry once the server answers with one (a foreign program on the port never does); None after `seconds`."""
    end = time.monotonic() + seconds
    while True:
        try:
            reg = api.registry(timeout=max(1.0, min(seconds, 30.0)))
            if isinstance(reg, dict) and "screens" in reg:
                return reg
        except ApiError:
            pass
        if time.monotonic() >= end:
            return None
        time.sleep(0.25)
