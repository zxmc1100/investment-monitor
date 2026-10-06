"""The macOS service (`python -m monitor service install`): launchd holds 127.0.0.1:SERVICE_PORT — loopback
only, no process while idle — and starts the terminal on the first visit; it stops by itself when idle."""
import socket
import sys
from pathlib import Path

import pytest

from monitor import config
from monitor.server import service


def test_the_service_listens_on_loopback_only_on_its_own_port_and_starts_on_demand():
    p = service.plist(Path("/r/.venv/bin/python"), Path("/r"), port=47800, idle_min=15)
    assert p["Label"] == service.LABEL
    assert p["ProgramArguments"] == ["/r/.venv/bin/python", "-m", "monitor", "serve", "--launchd"]
    assert p["WorkingDirectory"] == "/r"
    assert p["Sockets"] == {"Listeners": {"SockNodeName": "127.0.0.1", "SockServiceName": "47800",
                                          "SockType": "stream", "SockFamily": "IPv4"}}
    assert p["EnvironmentVariables"] == {"PYTHONUTF8": "1", "MONITOR_IDLE_EXIT_MIN": "15"}
    assert p["StandardOutPath"] == p["StandardErrorPath"] == "/r/local/server.log"
    assert "KeepAlive" not in p and "RunAtLoad" not in p             # nothing runs until a visit


def test_its_port_is_not_the_development_default():
    assert config.SERVICE_PORT != config.PORT and 1024 < config.SERVICE_PORT < 49152


@pytest.mark.skipif(sys.platform != "darwin", reason="launchd")
def test_outside_launchd_there_is_no_socket_to_take():
    with pytest.raises(OSError, match="launchd"):
        service.launchd_sockets()


def test_serve_on_the_default_port_opens_the_service_when_it_is_installed_here(monkeypatch, tmp_path):
    from monitor.server import run
    opened = []
    monkeypatch.setattr(service, "installed_here", lambda: True)
    monkeypatch.setattr(run.webbrowser, "open", opened.append)
    monkeypatch.setattr(run.uvicorn, "run", lambda *a, **k: pytest.fail("a second server started"))
    assert run.serve([]) == 0 and opened == [f"http://localhost:{config.SERVICE_PORT}"]
    assert run.serve(["--no-open"]) == 0 and len(opened) == 1


def test_an_explicit_port_still_runs_its_own_server(monkeypatch):
    from monitor.server import run
    ran = []
    monkeypatch.setattr(service, "installed_here", lambda: True)
    monkeypatch.setattr(run.uvicorn, "run", lambda *a, **k: ran.append(k["port"]))
    assert run.serve(["--port", "8017", "--no-open"]) == 0 and ran == [8017]


def test_installed_here_reads_the_agent_file(monkeypatch, tmp_path):
    import plistlib
    agent = tmp_path / "agent.plist"
    monkeypatch.setattr(service, "AGENT", agent)
    assert not service.installed_here()
    agent.write_bytes(plistlib.dumps({"WorkingDirectory": str(config.REPO_ROOT)}))
    assert service.installed_here()
    agent.write_bytes(plistlib.dumps({"WorkingDirectory": "/another/clone"}))
    assert not service.installed_here()


def test_a_free_port_is_free_and_a_taken_one_is_not():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen()
    try:
        assert not service.port_free(s.getsockname()[1])
    finally:
        s.close()


def test_serve_lifts_the_open_file_limit_launchd_leaves_at_256(monkeypatch):
    """launchd starts the service with 256 open files; MKT's ~280-ticker fetch ran out, the page went OFFLINE and
    reads failed. serve raises its own soft limit (to the hard one, at most 65536), never lowers it."""
    import resource
    from monitor.server import run
    lim = {"v": (256, resource.RLIM_INFINITY)}
    monkeypatch.setattr(resource, "getrlimit", lambda which: lim["v"])
    monkeypatch.setattr(resource, "setrlimit", lambda which, v: lim.update(v=v))
    run._lift_open_files()
    assert lim["v"] == (65536, resource.RLIM_INFINITY)
    lim["v"] = (256, 10240)
    run._lift_open_files()
    assert lim["v"] == (10240, 10240)
    lim["v"] = (1048576, resource.RLIM_INFINITY)
    run._lift_open_files()
    assert lim["v"] == (1048576, resource.RLIM_INFINITY)                 # never lowered
