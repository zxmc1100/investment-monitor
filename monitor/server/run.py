"""`python -m monitor serve` — uvicorn on 127.0.0.1 with auto-reload on monitor/ edits (and a local
add-on's, monitor.plugins), so a code change never needs a manual restart."""
import argparse
import threading
import webbrowser

import uvicorn

from monitor import config, plugins


def serve(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m monitor serve")
    ap.add_argument("--port", type=int, default=config.PORT)
    ap.add_argument("--no-reload", action="store_true", help="don't restart on code edits")
    ap.add_argument("--no-open", action="store_true", help="don't open a browser tab")
    args = ap.parse_args(argv)
    url = f"http://localhost:{args.port}"
    print(f"Investment Monitor terminal at {url} — keep this window open while you use it; "
          "close it or press Ctrl-C to stop.", flush=True)
    if not args.no_open:
        threading.Timer(1.5, webbrowser.open, args=(url,)).start()
    uvicorn.run("monitor.server.app:create_app", factory=True, host=config.HOST, port=args.port,
                reload=not args.no_reload, reload_dirs=[str(config.REPO_ROOT / "monitor"), *plugins.reload_dirs()],
                log_level="warning", timeout_graceful_shutdown=2)
    return 0
