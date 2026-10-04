"""`python -m monitor serve` — uvicorn on 127.0.0.1 with auto-reload on monitor/ edits (and a local
add-on's, monitor.plugins), so a code change never needs a manual restart. With the macOS service installed
for this folder (monitor.server.service) a start file opens the service's terminal instead of a second one;
`--launchd` is the service's own start, on the socket launchd holds."""
import argparse
import os
import threading
import webbrowser

import uvicorn

from monitor import config, plugins
from monitor.server import service

APP = "monitor.server.app:create_app"


def _reload_dirs() -> list[str]:
    return [str(config.REPO_ROOT / "monitor"), *plugins.reload_dirs()]


def serve(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m monitor serve")
    ap.add_argument("--port", type=int, default=None, help=f"default {config.PORT}")
    ap.add_argument("--no-reload", action="store_true", help="don't restart on code edits")
    ap.add_argument("--no-open", action="store_true", help="don't open a browser tab")
    ap.add_argument("--launchd", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.launchd:
        return _serve_launchd(reload=not args.no_reload)
    if args.port is None and service.installed_here():
        # one terminal at a time: two would both write the stored screens and the MOM paper track
        print(f"The terminal runs as a service: {service.URL} (output: local/server.log).", flush=True)
        if not args.no_open:
            webbrowser.open(service.URL)
        return 0
    port = config.PORT if args.port is None else args.port
    url = f"http://localhost:{port}"
    print(f"Investment Monitor terminal at {url} — keep this window open while you use it; "
          "close it or press Ctrl-C to stop.", flush=True)
    if not args.no_open:
        threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    uvicorn.run(APP, factory=True, host=config.HOST, port=port, reload=not args.no_reload,
                reload_dirs=_reload_dirs(), log_level="warning", timeout_graceful_shutdown=2)
    return 0


def _serve_launchd(reload: bool) -> int:
    """The service's terminal: uvicorn on launchd's socket, stopping itself when idle (MONITOR_IDLE_EXIT_MIN,
    read by create_app). With reload, uvicorn's reloader owns the socket and the idle stop signals it."""
    from uvicorn.supervisors import ChangeReload
    socks = service.launchd_sockets()
    cfg = uvicorn.Config(APP, factory=True, reload=reload, reload_dirs=_reload_dirs(), log_level="warning",
                         timeout_graceful_shutdown=2)
    server = uvicorn.Server(cfg)
    if cfg.should_reload:
        os.environ["MONITOR_SUPERVISED"] = "1"           # the worker's idle stop signals the reloader
        ChangeReload(cfg, target=server.run, sockets=socks).run()
    else:
        server.run(sockets=socks)
    return 0
