"""monitor.bootstrap — the one-click start (start-mac.command, start-windows.bat, start.sh call it): stdlib only,
runs on the Python that launched it. Python 3.11+ or a friendly message; a working .venv (created, or
recreated when broken); requirements installed when missing or changed (hash in .venv/.monitor-requirements);
`monitor init`; then `monitor serve` with your arguments, PYTHONUTF8=1. Pure parts tested per OS; the real
venv + pip run is checked by hand and in CI's launcher job (it needs the network)."""
import ast
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from monitor import bootstrap as B

REPO = Path(__file__).resolve().parent.parent


# ── it must run before anything is installed, on an old Python too (to say it is too old) ───────────
def test_stdlib_only_and_parses_on_old_pythons():
    src = Path(B.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src, feature_version=(3, 7))                  # no walrus, match, X | Y at runtime …
    mods = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    mods |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.level == 0}
    assert mods <= set(sys.stdlib_module_names), mods - set(sys.stdlib_module_names)
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module == "__future__"]


# ── pure parts ───────────────────────────────────────────────────────────────────────────────────────
def test_the_venv_python_per_os(tmp_path):
    assert B.venv_python(tmp_path / ".venv", "nt") == tmp_path / ".venv" / "Scripts" / "python.exe"
    assert B.venv_python(tmp_path / ".venv", "posix") == tmp_path / ".venv" / "bin" / "python"


class _NtOs:
    """`os` as the bootstrap sees it on Windows: only the name differs."""
    name = "nt"

    def __getattr__(self, attr):
        return getattr(os, attr)


def test_the_venv_python_follows_os_name(tmp_path, monkeypatch):
    monkeypatch.setattr(B, "os", _NtOs())
    assert B.venv_python(tmp_path).parts[-2:] == ("Scripts", "python.exe")
    assert 'tick "Add python.exe to PATH"' in B.version_problem((3, 9, 6), "python.exe")


@pytest.mark.parametrize("info, ok", [((3, 9, 6), False), ((3, 10, 14), False), ((3, 11, 0), True), ((3, 13, 7), True)])
def test_too_old_a_python_gets_a_friendly_message(info, ok):
    msg = B.version_problem(info, "/usr/bin/python3", "posix")
    if ok:
        assert msg is None
        return
    assert "3.11" in msg and ".".join(map(str, info)) in msg and "python.org" in msg and "/usr/bin/python3" in msg
    assert "PATH" not in msg
    win = B.version_problem(info, r"C:\Python39\python.exe", "nt")
    assert 'tick "Add python.exe to PATH"' in win


def test_the_requirements_hash_ignores_comments_blank_lines_and_line_endings():
    a = "yfinance>=0.2.40\npandas>=2.0.0\n"
    assert B.requirements_hash(a) == B.requirements_hash("# runtime\r\nyfinance>=0.2.40\r\n\r\npandas>=2.0.0  # x\r\n")
    assert B.requirements_hash(a) != B.requirements_hash("yfinance>=0.2.41\npandas>=2.0.0\n")


def test_requirements_parse_into_names_minimums_and_markers():
    got = B.parse_requirements((REPO / "requirements.txt").read_text(encoding="utf-8"))
    names = [r[0] for r in got]
    assert {"yfinance", "pandas", "numpy", "scipy", "fastapi", "uvicorn"} == set(names)
    assert ["yfinance", [0, 2, 40], None] in got
    assert ["uvicorn", [0, 30], ["<", [3, 15]]] in got
    assert B.parse_requirements("pandas==2.0\n") is None                   # a form the probe cannot check
    assert B.parse_requirements("x>=1; sys_platform == 'win32'\n") is None


@pytest.mark.parametrize("state, stamp, plan", [
    (None, None, "create"),                                              # no .venv, or it does not run
    ({"python": [3, 9], "missing": []}, "h", "create"),                  # too old a Python inside
    ({"python": [3, 13], "missing": []}, "h", "ok"),
    ({"python": [3, 13], "missing": []}, None, "ok"),                    # a developer's own venv: left alone
    ({"python": [3, 13], "missing": []}, "old", "install"),              # requirements.txt changed
    ({"python": [3, 13], "missing": ["scipy"]}, "h", "install"),
    ({"python": [3, 13], "missing": ["scipy"]}, None, "install"),        # a first install that broke off
    ({"python": [3, 13], "missing": None}, None, "install"),             # cannot check: install
])
def test_the_plan(state, stamp, plan):
    assert B.plan(state, stamp, "h") == plan


def test_the_probe_reports_python_and_what_is_missing():
    reqs = [["pytest", [1], None], ["surely-not-installed-x7", [1], None], ["pytest", [999], None],
            ["surely-not-installed-y8", [1], ["<", [3, 0]]]]                # marker false: not needed
    out = subprocess.run([sys.executable, "-c", B.PROBE, json.dumps(reqs)], capture_output=True, text=True,
                         encoding="utf-8", check=True).stdout
    got = json.loads(out)
    assert got == {"python": list(sys.version_info[:2]), "missing": ["surely-not-installed-x7", "pytest"]}


def test_a_missing_or_broken_venv_inspects_as_none(tmp_path):
    assert B.inspect_venv(tmp_path / "nope" / "python", []) is None
    broken = tmp_path / "python"
    broken.write_text("not a program", encoding="utf-8")
    assert B.inspect_venv(broken, []) is None


def test_serve_gets_every_argument():
    assert B.serve_command(Path("py"), ["--port", "8781", "--no-open"]) == \
        [str(Path("py")), "-m", "monitor", "serve", "--port", "8781", "--no-open"]


def test_init_output_is_shown_only_when_it_created_something():
    kept = "kept input/portfolio.csv (already there — never overwritten)\nnext: …\n"
    assert B.init_report(kept) is None
    made = "created input/portfolio.csv from examples/portfolio.example.csv\nnext: …\n"
    assert B.init_report(made) == made.strip()


def test_a_pip_failure_is_one_friendly_line_and_the_tail():
    out = "\n".join(f"line {i}" for i in range(40)) + "\nERROR: Could not find a version that satisfies yfinance"
    msg = B.pip_failure(out, (3, 13))
    head, *tail = msg.splitlines()
    assert "internet" in head and len(tail) <= 15 and tail[-1].startswith("ERROR: Could not find")
    assert "3.13" not in head
    assert "Python 3.15" in B.pip_failure(out, (3, 15)) and "3.13" in B.pip_failure(out, (3, 15))
    assert "uv pip install" in B.pip_failure("/x/.venv/bin/python: No module named pip", (3, 13))


# ── the flow, with the slow parts faked ──────────────────────────────────────────────────────────────
@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A repo root with requirements.txt and no .venv; the venv/pip/init/serve steps recorded, not run."""
    (tmp_path / "requirements.txt").write_text("pandas>=2.0.0\n", encoding="utf-8")
    monkeypatch.setattr(B, "ROOT", tmp_path)
    monkeypatch.setattr(B, "VENV", tmp_path / ".venv")
    calls, state = [], {"venv": None}

    def create_venv(venv):
        calls.append("create")
        (venv / "bin").mkdir(parents=True, exist_ok=True)
        state["venv"] = {"python": list(sys.version_info[:2]), "missing": ["pandas"]}

    def install(py, req):
        calls.append("install")
        state["venv"]["missing"] = []

    def run(cmd, **kw):
        calls.append(" ".join(cmd[1:]))
        first = not (tmp_path / "input").exists()
        (tmp_path / "input").mkdir(exist_ok=True)
        return subprocess.CompletedProcess(cmd, 0, ("created" if first else "kept") + " input/portfolio.csv …\n", "")

    monkeypatch.setattr(B, "inspect_venv", lambda py, reqs: state["venv"])
    monkeypatch.setattr(B, "create_venv", create_venv)
    monkeypatch.setattr(B, "install", install)
    monkeypatch.setattr(B.subprocess, "run", run)
    monkeypatch.setattr(B, "hand_over", lambda cmd, env: calls.append(("serve", cmd[1:], env.get("PYTHONUTF8"))) or 0)
    monkeypatch.setattr(B, "version_problem", lambda *a: None)
    return tmp_path, calls, state


def test_first_start_creates_installs_inits_and_serves_with_the_arguments(repo, capsys):
    root, calls, _ = repo
    assert B.main(["--port", "8781", "--no-open"]) == 0
    assert calls == ["create", "install", "-m monitor init", ("serve", ["-m", "monitor", "serve", "--port", "8781", "--no-open"], "1")]
    out = capsys.readouterr().out
    assert "First start: installing (about 2 minutes)" in out and "created input/portfolio.csv" in out
    stamp = (root / ".venv" / ".monitor-requirements").read_text(encoding="utf-8").strip()
    assert stamp == B.requirements_hash("pandas>=2.0.0\n")


def test_the_second_start_goes_straight_to_the_server(repo, capsys):
    root, calls, state = repo
    B.main([])
    calls.clear()
    capsys.readouterr()
    assert B.main([]) == 0
    assert calls == ["-m monitor init", ("serve", ["-m", "monitor", "serve"], "1")]
    assert capsys.readouterr().out == ""                                 # init kept everything: quiet


def test_changed_requirements_reinstall(repo, capsys):
    root, calls, _ = repo
    B.main([])
    (root / "requirements.txt").write_text("pandas>=2.0.0\nscipy>=1.11\n", encoding="utf-8")
    calls.clear()
    B.main([])
    assert calls[0] == "install" and "create" not in calls
    assert "requirements.txt changed" in capsys.readouterr().out


def test_a_developers_own_venv_is_used_as_is(repo):
    root, calls, state = repo
    (root / ".venv" / "bin").mkdir(parents=True)
    state["venv"] = {"python": [3, 13], "missing": []}
    B.main([])
    assert "create" not in calls and "install" not in calls
    assert not (root / ".venv" / ".monitor-requirements").exists()       # nothing written into it


def test_too_old_a_python_stops_with_the_message(repo, monkeypatch, capsys):
    _, calls, _ = repo
    monkeypatch.setattr(B, "version_problem", lambda *a: "needs Python 3.11")
    assert B.main([]) == 1 and calls == []
    assert "needs Python 3.11" in capsys.readouterr().err


def test_a_failed_install_stops_with_the_message(repo, monkeypatch, capsys):
    _, calls, _ = repo

    def fail(py, req):
        raise B.Failure("Installing the required packages failed — check the internet connection")
    monkeypatch.setattr(B, "install", fail)
    assert B.main([]) == 1
    assert "internet" in capsys.readouterr().err and not any(isinstance(c, tuple) for c in calls)


def test_a_failed_init_stops_with_its_message(repo, monkeypatch, capsys):
    monkeypatch.setattr(B.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1, "", "input exists but is not a folder\n"))
    assert B.main([]) == 1
    assert "not a folder" in capsys.readouterr().err


# ── creating and handing over ────────────────────────────────────────────────────────────────────────
def test_a_broken_symlinked_venv_is_unlinked_never_followed(tmp_path, monkeypatch):
    """A developer's .venv may link to another checkout's: replace the link, never touch what it points to."""
    target = tmp_path / "main-venv"
    target.mkdir()
    (target / "keep.txt").write_text("x", encoding="utf-8")
    venv = tmp_path / ".venv"
    try:
        venv.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("this account may not create symlinks")
    made = []
    monkeypatch.setattr(B.subprocess, "run", lambda cmd, **kw: made.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""))
    B.create_venv(venv)
    assert (target / "keep.txt").exists() and not venv.is_symlink()
    assert made[0][1:] == ["-m", "venv", str(venv)]


def test_a_venv_that_cannot_be_created_says_why(tmp_path, monkeypatch):
    monkeypatch.setattr(B.subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(
        cmd, 1, "", "The virtual environment was not created successfully because ensurepip is not available.\n"))
    with pytest.raises(B.Failure) as e:
        B.create_venv(tmp_path / ".venv")
    assert "python3-venv" in str(e.value) and "ensurepip" in str(e.value)


def test_hand_over_replaces_the_process_on_posix(monkeypatch):
    seen = []
    monkeypatch.setattr(B.os, "execve", lambda path, args, env: seen.append((path, args, env["PYTHONUTF8"])))
    monkeypatch.setattr(B.os, "chdir", lambda p: None)
    B.hand_over(["/v/bin/python", "-m", "monitor", "serve"], {"PYTHONUTF8": "1"}, "posix")
    assert seen == [("/v/bin/python", ["/v/bin/python", "-m", "monitor", "serve"], "1")]


def test_hand_over_on_windows_waits_for_the_server_through_ctrl_c(monkeypatch):
    """os.exec* on Windows leaves the console to two processes; there the server is a child we wait for —
    Ctrl-C reaches both, and the server stops itself."""
    class Child:
        waits = 0

        def __init__(self, cmd, **kw):
            self.cmd, self.kw = cmd, kw

        def wait(self):
            Child.waits += 1
            if Child.waits == 1:
                raise KeyboardInterrupt
            return 0
    monkeypatch.setattr(B.subprocess, "Popen", Child)
    assert B.hand_over(["py.exe", "-m", "monitor", "serve"], {"PYTHONUTF8": "1"}, "nt") == 0
    assert Child.waits == 2


def test_the_server_gets_pythonutf8(repo):
    root, calls, _ = repo
    B.main([])
    assert calls[-1][2] == "1"


def test_the_requirements_stamp_is_a_sha256(repo):
    root, _, _ = repo
    B.main([])
    stamp = (root / ".venv" / ".monitor-requirements").read_text(encoding="utf-8").strip()
    assert len(stamp) == len(hashlib.sha256().hexdigest())


@pytest.mark.skipif(os.name == "nt", reason="the POSIX launchers")
@pytest.mark.parametrize("script", ["start.sh", "start-mac.command"])
def test_the_shell_launchers_parse(script):
    subprocess.run(["bash", "-n", str(REPO / script)], check=True)


def _git(*args) -> str:
    try:
        return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, encoding="utf-8",
                              check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout (e.g. a downloaded archive)")


def test_the_launchers_are_executable_in_git_and_keep_their_line_endings():
    """A ZIP from GitHub keeps the executable bit (macOS runs start-mac.command on a double-click) and
    the line endings .gitattributes sets: CRLF for cmd.exe, LF for bash."""
    rows = _git("ls-files", "-s", "start.sh", "start-mac.command", "start-windows.bat").splitlines()
    assert {r.split()[3]: r.split()[0] for r in rows} == \
        {"start.sh": "100755", "start-mac.command": "100755", "start-windows.bat": "100644"}
    attrs = _git("check-attr", "eol", "start.sh", "start-mac.command", "start-windows.bat")
    for line in ("start-windows.bat: eol: crlf", "start.sh: eol: lf", "start-mac.command: eol: lf"):
        assert line in attrs, attrs


def test_the_windows_launcher():
    bat = (REPO / "start-windows.bat").read_text(encoding="utf-8")
    assert 'cd /d "%~dp0"' in bat and "set PYTHONUTF8=1" in bat
    assert bat.index("call :probe py -3") < bat.index("call :probe python")       # the launcher first
    assert "%PY% -m monitor.bootstrap %*" in bat
    assert "pause" in bat and "python.org" in bat and "Add python.exe to PATH" in bat
    assert "monitor\\bootstrap.py" in bat                                          # run from inside the ZIP


def test_the_mac_launcher_hands_over_to_start_sh_and_waits_on_failure():
    cmd = (REPO / "start-mac.command").read_text(encoding="utf-8")
    assert 'cd "$(dirname "$0")"' in cmd and "bash ./start.sh" in cmd and "read -r" in cmd
    sh = (REPO / "start.sh").read_text(encoding="utf-8")
    assert "-m monitor.bootstrap" in sh and "/usr/bin/python3" in sh and "PYTHONUTF8=1" in sh
