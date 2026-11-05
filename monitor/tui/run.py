"""`python -m monitor tui`: find the terminal's server — the macOS service if installed for this folder, one already running —
or start one for as long as this runs, then the screen. Two servers would both write the stored screens: never a second."""

from __future__ import annotations

import argparse
import signal
import subprocess
import sys

from monitor import config
from monitor.server import service
from monitor.tui.app import App
from monitor.tui.client import Api, wait_up
from monitor.tui.term import Terminal, stdin_is_terminal

STARTUP_S = 90.0
PORTS_TRIED = 10


def connect(url: str | None) -> tuple:
    """(Api, its registry, the server process this started or None); (None, None, None) with the reason printed."""
    if url:
        api = Api(url)
        reg = wait_up(api, 10.0)
        if reg is None:
            print(f"No terminal answers at {url}.", file=sys.stderr)
            return None, None, None
        return api, reg, None
    if service.installed_here():
        api = Api(f"http://127.0.0.1:{config.SERVICE_PORT}")
        reg = wait_up(api, 30.0)  # launchd starts it on this first connection
        if reg is None:
            print(f"The service at {service.URL} does not answer: see local/server.log.", file=sys.stderr)
            return None, None, None
        return api, reg, None
    for port in range(config.PORT, config.PORT + PORTS_TRIED):
        api = Api(f"http://127.0.0.1:{port}")
        if service.port_free(port):
            return _start(api, port)
        reg = wait_up(api, 5.0)  # a busy server answers late; a second one would write over it
        if reg is not None:
            return api, reg, None
    print(
        f"Ports {config.PORT}–{config.PORT + PORTS_TRIED - 1} are all taken by other programs: "
        "start the server yourself and use --url.",
        file=sys.stderr,
    )
    return None, None, None


def _start(api: Api, port: int) -> tuple:
    log = config.REPO_ROOT / "local" / "server.log"
    log.parent.mkdir(exist_ok=True)
    print(f"Starting the terminal's server on port {port} (output: {log.relative_to(config.REPO_ROOT)})…", flush=True)
    with open(log, "ab") as out:
        child = subprocess.Popen(
            [sys.executable, "-m", "monitor", "serve", "--no-open", "--no-reload", "--port", str(port)],
            cwd=config.REPO_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=out,
            stderr=out,
        )
    reg = wait_up(api, STARTUP_S)
    if reg is None:
        child.terminate()
        print(f"The server did not start: see {log}.", file=sys.stderr)
        return None, None, None
    return api, reg, child


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m monitor tui", description="the terminal, in a terminal, with vim keys (? shows them)"
    )
    ap.add_argument("--url", help="a terminal already running elsewhere, e.g. https://your-mac.your-tailnet.ts.net")
    args = ap.parse_args(argv)
    if not stdin_is_terminal():
        print("python -m monitor tui needs a real terminal (it reads keys and draws the screen).", file=sys.stderr)
        return 2
    api, reg, child = connect(args.url)
    if api is None:
        return 1
    for name in ("SIGTERM", "SIGHUP"):  # the screen back, and any server this started stopped, whatever ends it
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), lambda *_: sys.exit(0))
    try:
        with Terminal() as term:
            app = App(api, term)
            app.start(reg)
            app.loop()
    finally:
        if child is not None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
    return 0
