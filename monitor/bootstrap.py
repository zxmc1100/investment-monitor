"""One-click start: `python3 -m monitor.bootstrap [serve args]` (Windows: `py -3 -m monitor.bootstrap`). The
launchers start-mac.command, start-windows.bat and start.sh call it; it runs on whatever Python launched it,
so it imports nothing outside the standard library and parses on old Pythons (to say they are too old).

1. Python 3.11 or newer, else a friendly message (where to get it) and exit 1.
2. .venv next to monitor/: created when missing; created again when it does not run (moved folder, its
   Python removed or updated) or holds a Python older than 3.11, or when a first install broke off under
   another Python. A symlinked .venv (a developer's link to another checkout's) is unlinked, never followed.
3. requirements.txt installed into it when a requirement is missing there, or when the file changed since
   the last install made here (its hash is kept in .venv/.monitor-requirements). A venv that already has
   everything and no hash (a developer's own) is used as it is — nothing is installed or written into it.
4. `<venv python> -m monitor init` — input/ from examples/ wherever a file is missing (shown only when it
   created something).
5. `<venv python> -m monitor serve [your args]` (it opens the browser) replaces this process, with
   PYTHONUTF8=1. On Windows it runs as a child this process waits for (os.exec* misbehaves in a console)."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

MIN = (3, 11)
NEWEST_TESTED = (3, 14)          # a newer Python may lack some packages' ready-made builds (wheels) at first
ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
STAMP = ".monitor-requirements"
GET_PYTHON = "https://www.python.org/downloads/"

# Runs inside the venv: its Python version, and which requirements are absent or older than their minimum
# (a requirement whose python_version marker does not hold is not needed). argv[1] = parse_requirements().
PROBE = r"""
import json, sys
from importlib.metadata import PackageNotFoundError, version

def release(v):
    out = []
    for part in v.split("."):
        digits = ""
        for ch in part:
            if not ch.isdigit():
                break
            digits += ch
        if not digits:
            break
        out.append(int(digits))
        if len(digits) != len(part):
            break
    return out

OPS = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b,
       ">=": lambda a, b: a >= b, "==": lambda a, b: a == b, "!=": lambda a, b: a != b}
here = list(sys.version_info[:2])
missing = []
for name, low, marker in json.loads(sys.argv[1]):
    if marker and not OPS[marker[0]](here, marker[1]):
        continue
    try:
        have = version(name)
    except PackageNotFoundError:
        missing.append(name)
        continue
    if low and release(have) < low:
        missing.append(name)
print(json.dumps({"python": here, "missing": missing}))
"""


class Failure(Exception):
    """A step failed: the message says what to do (printed, never a traceback)."""


# ── pure parts ───────────────────────────────────────────────────────────────────────────────────────
def venv_python(venv, os_name=None):
    """The venv's interpreter: Scripts\\python.exe on Windows, bin/python elsewhere."""
    return Path(venv) / ("Scripts/python.exe" if (os_name or os.name) == "nt" else "bin/python")


def version_problem(version_info=None, executable=None, os_name=None):
    """None when this Python is new enough, else the message to show."""
    info = tuple((version_info or sys.version_info)[:3])
    if info[:2] >= MIN:
        return None
    tip = ' — in the installer, tick "Add python.exe to PATH"' if (os_name or os.name) == "nt" else ""
    return ("Investment Monitor needs Python {}.{} or newer; this is Python {} ({}).\n"
            "Get it from {}{}, then start again.").format(
        MIN[0], MIN[1], ".".join(str(x) for x in info), executable or sys.executable, GET_PYTHON, tip)


def _lines(text):
    return [line.split("#")[0].strip() for line in text.splitlines() if line.split("#")[0].strip()]


def requirements_hash(text):
    """sha256 of the requirement lines: comments, blank lines and line endings do not change it."""
    return hashlib.sha256("\n".join(_lines(text)).encode("utf-8")).hexdigest()


_REQ = re.compile(r'([A-Za-z0-9_.-]+)(\[[A-Za-z0-9_,-]+\])?>=([0-9.]+)'
                  r'(?:\s*;\s*python_version\s*(<=|>=|==|!=|<|>)\s*["\'](\d+)\.(\d+)["\'])?')


def parse_requirements(text):
    """[[name, minimum version as ints, [op, [major, minor]] | None]] of `name[extras]>=X.Y[; python_version
    op "A.B"]` lines — the form requirements.txt keeps; None when a line has another form (then the probe
    cannot tell what is missing, and the bootstrap installs)."""
    out = []
    for line in _lines(text):
        m = _REQ.fullmatch(line)
        if not m:
            return None
        marker = [m.group(4), [int(m.group(5)), int(m.group(6))]] if m.group(4) else None
        out.append([m.group(1).lower(), [int(x) for x in m.group(3).split(".") if x], marker])
    return out


def plan(state, stamp, req_hash, running=None):
    """What .venv needs: "create" (then install), "install" or "ok". `state` = inspect_venv() (None: no
    .venv or it does not run), `stamp` = the hash of the last install made here (None: never)."""
    if state is None or tuple(state["python"]) < MIN:
        return "create"
    missing = state["missing"]
    if missing is None or missing or (stamp is not None and stamp != req_hash):
        if stamp is None and missing and tuple(state["python"]) != tuple((running or sys.version_info)[:2]):
            return "create"              # a first install that broke off under another Python: start over with this one
        return "install"
    return "ok"


def serve_command(py, argv):
    return [str(py), "-m", "monitor", "serve"] + list(argv)


def init_report(stdout):
    """init's output when it created something (the first start), else None — later starts stay quiet."""
    lines = stdout.strip().splitlines()
    return stdout.strip() if any(line.startswith("created") for line in lines) else None


def _tail(output, n=15):
    return "\n".join(output.strip().splitlines()[-n:])


def pip_failure(output, venv_version):
    """One plain line saying what to do, then the end of pip's output."""
    v = "{}.{}".format(*venv_version[:2])
    if "No module named pip" in output:
        head = (".venv has no pip (it was made without one, e.g. by uv) — install the packages yourself "
                "(uv pip install -r requirements.txt --python .venv), or delete the .venv folder and start again.")
    elif tuple(venv_version[:2]) > NEWEST_TESTED:
        head = ("Installing the required packages failed. Python {v} may be too new for some of them yet: "
                "install Python 3.13 from {url} (it can stay next to {v}), delete the .venv folder here and "
                "start again. pip said:").format(v=v, url=GET_PYTHON)
    else:
        head = ("Installing the required packages failed — check the internet connection and start again. "
                "pip said:")
    return head + "\n" + _tail(output)


# ── steps ─────────────────────────────────────────────────────────────────────────────────────────────
def _env():
    return dict(os.environ, PYTHONUTF8="1")


def inspect_venv(py, reqs):
    """{"python": [major, minor], "missing": [...] | None} from the venv's own interpreter; None when there
    is none or it does not run."""
    py = Path(py)
    if not py.is_file():
        return None
    try:
        r = subprocess.run([str(py), "-c", PROBE, json.dumps(reqs or [])], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=120, env=_env())
        state = json.loads(r.stdout.strip().splitlines()[-1]) if r.returncode == 0 else None
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None
    if state is not None and reqs is None:
        state["missing"] = None
    return state


def create_venv(venv):
    venv = Path(venv)
    try:
        if venv.is_symlink() or venv.is_file():
            venv.unlink()                                 # a link to another checkout's venv: drop the link only
        elif venv.exists():
            shutil.rmtree(venv)
    except OSError as e:
        raise Failure("Cannot remove the old .venv folder ({}) — delete it yourself and start again.".format(e))
    r = subprocess.run([sys.executable, "-m", "venv", str(venv)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=_env())
    if r.returncode != 0:
        raise Failure("Could not create the .venv folder (on Debian/Ubuntu: sudo apt install python3-venv, "
                      "then start again):\n" + _tail(r.stdout + r.stderr))


def install(py, requirements):
    r = subprocess.run([str(py), "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
                        "-r", str(requirements)], cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=_env())
    if r.returncode != 0:
        state = inspect_venv(py, [])
        raise Failure(pip_failure(r.stdout + r.stderr, state["python"] if state else sys.version_info[:2]))


def hand_over(cmd, env, os_name=None):
    """Run the server in place of this process (POSIX), or as a child we wait for (Windows)."""
    if (os_name or os.name) == "nt":
        proc = subprocess.Popen(cmd, cwd=str(ROOT), env=env)
        while True:
            try:
                return proc.wait()
            except KeyboardInterrupt:                     # the server got the same Ctrl-C: let it stop
                continue
    os.chdir(str(ROOT))
    os.execve(cmd[0], cmd, env)


def prepare():
    """Steps 2-4; returns the venv's interpreter."""
    req_file = ROOT / "requirements.txt"
    text = req_file.read_text(encoding="utf-8")
    req_hash, reqs = requirements_hash(text), parse_requirements(text)
    py, stamp_file = venv_python(VENV), VENV / STAMP
    stamp = stamp_file.read_text(encoding="utf-8").strip() if stamp_file.is_file() else None
    state = inspect_venv(py, reqs)
    todo = plan(state, stamp, req_hash)
    if todo == "create":
        if VENV.exists() or VENV.is_symlink():
            print("Setting up the .venv folder again with Python {}.{} (about 2 minutes)…".format(*sys.version_info[:2]),
                  flush=True)
        else:
            print("First start: installing (about 2 minutes)…", flush=True)
        create_venv(VENV)
        stamp = None
    elif todo == "install":
        print("requirements.txt changed: updating the installed packages…" if stamp is not None
              else "Installing the required packages (about 2 minutes)…", flush=True)
    if todo != "ok":
        install(py, req_file)
        stamp_file.write_text(req_hash + "\n", encoding="utf-8")
    r = subprocess.run([str(py), "-m", "monitor", "init"], cwd=str(ROOT), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=_env())
    if r.returncode != 0:
        raise Failure((r.stderr or r.stdout).strip() or "python -m monitor init failed")
    report = init_report(r.stdout)
    if report:
        print(report, flush=True)
    return py


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    problem = version_problem()
    if problem:
        print(problem, file=sys.stderr)
        return 1
    try:
        py = prepare()
    except Failure as e:
        print(e, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        return 130
    return hand_over(serve_command(py, argv), _env())


if __name__ == "__main__":
    sys.exit(main())
