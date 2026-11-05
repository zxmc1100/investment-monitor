"""`python -m monitor tui` for real: a pseudo-terminal, a stub of the server over HTTP, the bytes that come back."""

import json
import os
import select
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from monitor import config
from monitor.tui import run as R
from monitor.tui.client import Api, ApiError, detail_text, sse_events, wait_up
from tests.tui.conftest import PORT, REGISTRY

REPO = config.REPO_ROOT


class Stub(BaseHTTPRequestHandler):
    hold = threading.Event()

    def log_message(self, *_a):
        pass

    def _send(self, body: dict, status: int = 200) -> None:
        data = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api/screens":
            self._send(REGISTRY)
        elif self.path.startswith("/api/screen/PORT"):
            self._send(PORT)
        elif self.path.startswith("/api/screen/") and self.hold.is_set():
            time.sleep(60)
        elif self.path == "/api/alerts":
            self._send({"active": []})
        elif self.path == "/api/jobs":
            self._send({"jobs": []})
        elif self.path == "/api/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b"retry: 2000\n\n: ping\n\n")
            self.wfile.flush()
            time.sleep(1.0)  # after the client's first look at /api/alerts
            self.wfile.write(b'event: alerts\ndata: {"active":3,"down":false}\n\n')
            self.wfile.flush()
            time.sleep(30)
        else:
            self._send({"detail": {"error": "NOPE"}}, 404)

    def do_POST(self):
        self._send({"jobs": [], "live": {}})


@pytest.fixture
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Stub)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


def test_client_reads_answers_and_refusals(server):
    api = Api(server)
    assert api.registry()["targets"] == REGISTRY["targets"]
    with pytest.raises(ApiError) as e:
        api.screen("NOPE")
    assert e.value.status == 404 and e.value.detail == "NOPE"


def test_client_says_unreachable():
    with pytest.raises(ApiError) as e:
        Api("http://127.0.0.1:9").registry(timeout=1)
    assert e.value.detail == "SERVER UNREACHABLE"
    assert wait_up(Api("http://127.0.0.1:9"), 0.0) is None


def test_detail_text_as_the_browser_reads_it():
    assert detail_text({"detail": {"error": "A"}}, 400) == "A"
    assert detail_text({"detail": "no"}, 400) == "NO"
    assert detail_text(None, 500) == "HTTP 500"


def test_sse_lines():
    lines = [
        b"retry: 2000\n",
        b"\n",
        b": ping\n",
        b"\n",
        b"event: job\n",
        b'data: {"a":1}\n',
        b"\n",
        b"event: x\n",
        b"data: not json\n",
        b"\n",
    ]
    assert list(sse_events(lines)) == [("job", {"a": 1})]


def test_subscribe_delivers_then_stops(server):
    got = []
    stop = Api(server).subscribe(lambda t, ev: got.append((t, ev)))
    end = time.monotonic() + 5
    while len(got) < 2 and time.monotonic() < end:
        time.sleep(0.02)
    stop.set()
    assert got[0] == ("conn", {"ok": True}) and got[1] == ("alerts", {"active": 3, "down": False})


def test_it_needs_a_terminal():
    r = subprocess.run(
        [sys.executable, "-m", "monitor", "tui", "--url", "http://127.0.0.1:9"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=REPO,
        stdin=subprocess.DEVNULL,
        timeout=30,
    )
    assert r.returncode == 2 and "real terminal" in r.stderr


def test_it_lists_itself_in_the_help():
    r = subprocess.run(
        [sys.executable, "-m", "monitor", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=REPO,
        timeout=60,
    )
    assert "tui" in r.stdout and "vim keys" in r.stdout


def test_connect_reuses_a_running_server_and_never_starts_a_second(server, monkeypatch, capsys):
    port = int(server.rsplit(":", 1)[1])
    monkeypatch.setattr(R.service, "installed_here", lambda: False)
    monkeypatch.setattr(R.config, "PORT", port)
    started = []
    monkeypatch.setattr(R, "_start", lambda *a: started.append(a))
    api, reg, child = R.connect(None)
    assert api.base.endswith(str(port)) and reg["targets"] == REGISTRY["targets"] and child is None and not started


def test_connect_says_when_a_url_does_not_answer(capsys, monkeypatch):
    monkeypatch.setattr(R, "wait_up", lambda *_a: None)
    assert R.connect("http://127.0.0.1:9") == (None, None, None)
    assert "No terminal answers" in capsys.readouterr().err


def test_connect_uses_the_service_and_starts_no_server_when_it_is_silent(capsys, monkeypatch):
    monkeypatch.setattr(R.service, "installed_here", lambda: True)
    monkeypatch.setattr(R, "wait_up", lambda *_a: None)
    monkeypatch.setattr(R, "_start", lambda *_a: pytest.fail("a second server"))
    assert R.connect(None) == (None, None, None)
    assert "does not answer" in capsys.readouterr().err


class Pty:
    """A pseudo-terminal running the client; a thread keeps reading it, as any terminal does."""

    def __init__(self, server, env=None):
        import pty
        import termios

        self.master, self.slave = pty.openpty()
        termios.tcsetwinsize(self.slave, (40, 140))
        self.before = termios.tcgetattr(self.slave)
        env = {**os.environ, "MONITOR_PRIVATE": "0", "PYTHONUTF8": "1", **(env or {})}
        self.p = subprocess.Popen(
            [sys.executable, "-m", "monitor", "tui", "--url", server],
            cwd=REPO,
            env=env,
            stdin=self.slave,
            stdout=self.slave,
            stderr=self.slave,
            close_fds=True,
        )
        self.out = b""
        self.stop = threading.Event()
        self.t = threading.Thread(target=self._read, daemon=True)
        self.t.start()

    def _read(self):
        while not self.stop.is_set():
            ready, _, _ = select.select([self.master], [], [], 0.05)
            if ready:
                try:
                    self.out += os.read(self.master, 65536)
                except OSError:
                    return

    def until(self, marker: bytes, seconds: float = 20.0) -> None:
        end = time.monotonic() + seconds
        while marker not in self.out and time.monotonic() < end:
            time.sleep(0.02)
        assert marker in self.out, self.out[-1500:]

    def restored(self) -> bool:
        import termios

        pendin = getattr(termios, "PENDIN", 0)  # the kernel's own bookkeeping: macOS sets it on any tcsetattr

        def flags(a):
            return [*a[:3], a[3] & ~pendin, *a[4:]]

        return flags(termios.tcgetattr(self.slave)) == flags(self.before)

    def close(self):
        if self.p.poll() is None:
            self.p.kill()
        self.stop.set()
        self.t.join(2)
        os.close(self.master)
        os.close(self.slave)


@pytest.mark.skipif(os.name == "nt", reason="no pty on Windows")
def test_a_real_session_in_a_pty(server):
    """Starts, draws, takes keys, and leaves the terminal as it found it."""
    t = Pty(server, {"COLORTERM": "truecolor"})
    try:
        t.until(b"POSITIONS")
        assert b"\x1b[?1049h" in t.out and b"\x1b[?1006h" in t.out and b"\x1b[?2004h" in t.out
        assert b"38;2;255;160;40" in t.out  # the amber, in 24 bits
        t.until(b"3 ALERTS")  # the event stream reached the screen
        os.write(t.master, b"z3")
        t.until(b"TK25.DE")  # keys reached the app: POSITIONS maximized, its last rows shown
        os.write(t.master, b"\x1b")
        time.sleep(0.3)
        os.write(t.master, b":q\r")
        t.until(b"\x1b[?1049l")
        assert t.p.wait(timeout=10) == 0
        assert b"\x1b[?1002l" in t.out and b"\x1b[?25h" in t.out and b"\x1b[?2004l" in t.out
        assert t.restored()  # raw mode undone
    finally:
        t.close()


@pytest.mark.skipif(os.name == "nt", reason="no pty on Windows")
def test_q_never_waits_for_a_request_in_flight(server):
    """A server sitting on an answer (POST /api/ensure here) must not hold up leaving."""
    t = Pty(server)
    try:
        t.until(b"POSITIONS")
        Stub.hold.set()
        os.write(t.master, b"6")  # TRADES: a request that will not be answered
        time.sleep(0.6)
        os.write(t.master, b":q\r")
        assert t.p.wait(timeout=8) == 0
    finally:
        Stub.hold.clear()
        t.close()


@pytest.mark.skipif(os.name == "nt", reason="no pty on Windows")
def test_sigterm_gives_the_terminal_back(server):
    import signal

    t = Pty(server)
    try:
        t.until(b"POSITIONS")
        t.p.send_signal(signal.SIGTERM)
        assert t.p.wait(timeout=10) == 0
        t.until(b"\x1b[?1049l", 2)
        assert t.restored()
    finally:
        t.close()
