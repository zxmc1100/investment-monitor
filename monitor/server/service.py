"""macOS: the terminal as a launchd service — `python -m monitor service install` once, then bookmark
http://localhost:47800 (config.SERVICE_PORT). launchd holds that port on 127.0.0.1 only (loopback: no network
can reach it) and runs nothing until a visit; the first visit starts the terminal (a few seconds) and it
stops by itself SERVICE_IDLE_MIN minutes after the last tab closes (monitor.server.idle). Port 8000 stays
free for other projects. Output: local/server.log. `python -m monitor service uninstall` removes it."""
import ctypes
import os
import plistlib
import re
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from monitor import config

LABEL = "local.investment-monitor"
AGENT = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
URL = f"http://localhost:{config.SERVICE_PORT}"


def plist(python: Path, repo: Path, port: int, idle_min: int) -> dict:
    """The launch agent: a socket on 127.0.0.1:port that starts `monitor serve --launchd` on demand."""
    log = str(repo / "local" / "server.log")
    return {"Label": LABEL,
            "ProgramArguments": [str(python), "-m", "monitor", "serve", "--launchd"],
            "WorkingDirectory": str(repo),
            "EnvironmentVariables": {"PYTHONUTF8": "1", "MONITOR_IDLE_EXIT_MIN": str(idle_min)},
            "Sockets": {"Listeners": {"SockNodeName": "127.0.0.1", "SockServiceName": str(port),
                                      "SockType": "stream", "SockFamily": "IPv4"}},
            "StandardOutPath": log, "StandardErrorPath": log}


def launchd_sockets(name: str = "Listeners") -> list[socket.socket]:
    """The listening sockets launchd holds for this process (launch_activate_socket)."""
    lib = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    fn = lib.launch_activate_socket
    fn.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.POINTER(ctypes.c_int)), ctypes.POINTER(ctypes.c_size_t)]
    fn.restype = ctypes.c_int
    fds, n = ctypes.POINTER(ctypes.c_int)(), ctypes.c_size_t(0)
    err = fn(name.encode(), ctypes.byref(fds), ctypes.byref(n))
    if err:
        raise OSError(err, f"no socket from launchd ({os.strerror(err)}): `serve --launchd` is for the service "
                           "`python -m monitor service install` sets up")
    socks = [socket.socket(fileno=fds[i]) for i in range(n.value)]
    lib.free(fds)
    return socks


def installed_here() -> bool:
    """The service is installed for this folder (a start file then opens it instead of a second server)."""
    try:
        return plistlib.loads(AGENT.read_bytes()).get("WorkingDirectory") == str(config.REPO_ROOT)
    except (OSError, plistlib.InvalidFileException):
        return False


def port_free(port: int) -> bool:
    s = socket.socket()
    try:
        s.bind((config.HOST, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, encoding="utf-8")


def _stop_started_by_hand(port: int) -> list[int]:
    """Stop a `monitor serve` running from a start file on `port` (one terminal at a time: the service
    replaces it). Only a monitor server is stopped, never another program on the port."""
    out = subprocess.run(["lsof", "-nP", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"], capture_output=True,
                         text=True, encoding="utf-8").stdout.split()
    stopped = []
    for pid in map(int, out):
        cmd = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True,
                             encoding="utf-8").stdout
        if re.search(r"-m monitor serve", cmd) and "--launchd" not in cmd:
            os.kill(pid, 15)
            stopped.append(pid)
    return stopped


def install() -> int:
    if sys.platform != "darwin":
        print("The service is for macOS.", file=sys.stderr)
        return 1
    repo = config.REPO_ROOT
    venv = repo / ".venv" / "bin" / "python"
    python = venv if venv.exists() else Path(sys.executable)
    _launchctl("bootout", f"gui/{os.getuid()}/{LABEL}")             # an older install of it, if any
    if _stop_started_by_hand(config.PORT):
        print(f"Stopped the terminal running on :{config.PORT} — the service replaces it (its window can close).")
    if not port_free(config.SERVICE_PORT):
        print(f"Port {config.SERVICE_PORT} is in use by another program: change SERVICE_PORT in "
              "monitor/config.py and install again.", file=sys.stderr)
        return 1
    (repo / "local").mkdir(exist_ok=True)
    AGENT.parent.mkdir(parents=True, exist_ok=True)
    AGENT.write_bytes(plistlib.dumps(plist(python, repo, config.SERVICE_PORT, config.SERVICE_IDLE_MIN)))
    r = _launchctl("bootstrap", f"gui/{os.getuid()}", str(AGENT))
    if r.returncode:
        print(f"launchctl could not load the service: {r.stderr.strip()}", file=sys.stderr)
        return 1
    deadline = time.monotonic() + 90                                 # the first visit starts it
    while time.monotonic() < deadline:
        try:
            if urllib.request.urlopen(f"{URL}/api/screens", timeout=30).status == 200:
                print(f"Installed. Bookmark {URL} — it starts the terminal when you open it and stops it "
                      f"{config.SERVICE_IDLE_MIN} min after the last tab closes. Output: local/server.log.")
                return 0
        except OSError:
            time.sleep(1)
    print(f"Installed, but {URL} did not answer yet — see local/server.log.", file=sys.stderr)
    return 1


def uninstall() -> int:
    _launchctl("bootout", f"gui/{os.getuid()}/{LABEL}")
    AGENT.unlink(missing_ok=True)
    print(f"Removed the service: {URL} answers no more (start files work as before).")
    return 0


def main(argv) -> int:
    cmd = argv[0] if argv else ""
    if cmd == "install":
        return install()
    if cmd == "uninstall":
        return uninstall()
    print("python -m monitor service install | uninstall   (macOS: open the terminal from a bookmark)",
          file=sys.stderr)
    return 2
