"""The macOS monitor:// link (tools/macos): any web page can try to open a monitor:// link, so the link's
text must never reach a shell, and `stop` must never stop anything but a monitor server. No network:
the stand-in servers below listen on 127.0.0.1."""
import http.server
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
MAC = REPO / "tools" / "macos"
LINK = MAC / "monitor-link.sh"
POSIX = pytest.mark.skipif(os.name == "nt" or not shutil.which("bash"), reason="bash scripts")


def test_the_applet_hands_the_shell_only_fixed_words():
    src = (MAC / "link.applescript").read_text(encoding="utf-8")
    shell = [ln for ln in src.splitlines() if "do shell script" in ln]
    assert shell == ['\t\tdo shell script quoted form of linkScript & " " & action']
    assert set(re.findall(r'runLink\(([^)]*)\)', src)) == {'"open"', '"stop"', "action"}   # literals only
    assert not re.search(r"runLink\([^)]*theURL", src)
    assert re.findall(r'theURL is "([^"]+)"', src) == ["monitor://open", "monitor://open/", "monitor://stop",
                                                       "monitor://stop/"]


@POSIX
def test_scripts_parse_and_take_only_open_or_stop():
    for script in ("monitor-link.sh", "install-link.sh"):
        assert subprocess.run(["bash", "-n", str(MAC / script)]).returncode == 0, script
    for arg in ([], ["start"], ["open; touch /tmp/x"], ["--help"]):
        r = subprocess.run(["bash", str(LINK), *arg], capture_output=True, text=True, encoding="utf-8")
        assert r.returncode == 2 and "usage" in r.stderr, arg


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("osacompile"), reason="macOS")
def test_the_applet_compiles(tmp_path):
    src = (MAC / "link.applescript").read_text(encoding="utf-8").replace("__SCRIPT__", "/tmp/x.sh")
    (tmp_path / "l.applescript").write_text(src, encoding="utf-8")
    r = subprocess.run(["osacompile", "-o", str(tmp_path / "l.scpt"), str(tmp_path / "l.applescript")],
                       capture_output=True, text=True, encoding="utf-8")
    assert r.returncode == 0, r.stderr


class _Answer(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, *a):
        pass


@pytest.fixture
def other_server():
    """Something on a port that is not a monitor server: it answers like one."""
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Answer)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1]
    srv.shutdown()


def _run(args, port, tmp_path):
    stub = tmp_path / "opener.sh"
    stub.write_text(f'#!/bin/bash\necho "$@" >> "{tmp_path}/opened"\n', encoding="utf-8")
    stub.chmod(0o755)
    env = {**os.environ, "MONITOR_PORT": str(port), "MONITOR_LINK_OPENER": str(stub)}
    return subprocess.run(["bash", str(LINK), *args], env=env, capture_output=True, text=True,
                          encoding="utf-8", timeout=30)


@POSIX
@pytest.mark.skipif(not shutil.which("curl"), reason="curl")
def test_open_with_the_terminal_already_up_only_opens_it(other_server, tmp_path):
    log = REPO / "local" / "server.log"
    before = log.read_text(encoding="utf-8") if log.exists() else None
    assert _run(["open"], other_server, tmp_path).returncode == 0
    assert (tmp_path / "opened").read_text(encoding="utf-8").split() == [f"http://localhost:{other_server}"]
    assert (log.read_text(encoding="utf-8") if log.exists() else None) == before       # nothing started


@POSIX
@pytest.mark.skipif(not shutil.which("lsof"), reason="lsof")
def test_stop_never_stops_what_is_not_a_monitor_server(other_server, tmp_path):
    import urllib.request
    assert _run(["stop"], other_server, tmp_path).returncode == 0
    assert urllib.request.urlopen(f"http://127.0.0.1:{other_server}/", timeout=5).status == 200   # still up
