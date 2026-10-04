"""Release layout: .gitignore keeps your data, the developer notes and a local add-on out while examples/
and the tracked universe stay in."""
import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _ignored(path: str) -> bool:
    return subprocess.run(["git", "check-ignore", "-q", "--no-index", path], cwd=REPO).returncode == 0


@pytest.mark.skipif(not (REPO / ".git").exists(), reason="not a git checkout (e.g. a downloaded archive)")
def test_gitignore_keeps_your_data_out_and_the_examples_in():
    for path in ("input/portfolio.csv", "input/settings.toml", "local/buffer/x.json", "CLAUDE.md",
                 "docs/superpowers/specs/x.md", "private/monitor_private/plugin.py", "private/data/x.json"):
        assert _ignored(path), path
    for path in ("examples/portfolio.example.csv", "examples/settings.example.toml", "monitor/plugins.py"):
        assert not _ignored(path), path


@pytest.mark.skipif(not (REPO / ".git").exists(), reason="not a git checkout (e.g. a downloaded archive)")
def test_gitignore_keeps_generated_data_and_a_linked_venv_out():
    for path in (".venv", "data/universe/universe_prices.csv", "data/universe/tr_universe.csv",
                 "data/universe/tr_ticker_map.json", "data/anything.csv", "data/some/dir/x.csv",
                 "docs/.export-ab12.tmp", "data/universe/.universe_meta.csv.x1.tmp"):
        assert _ignored(path), path
    for path in ("data/universe/universe_meta.csv", "data/universe/sector_map.json", "data/README.md",
                 "LICENSE", "README.md"):
        assert not _ignored(path), path


def test_the_license_is_mit():
    text = (REPO / "LICENSE").read_text(encoding="utf-8")
    assert text.startswith("MIT License") and "Copyright (c) 2026 the Investment Monitor contributors" in text


# import name -> the distribution that provides it (where they differ)
DIST = {"starlette": "fastapi", "time_machine": "time-machine"}


def _requirement_lines(name: str) -> list[str]:
    """A requirements file's own requirement lines (comments dropped; `-r` includes are not followed)."""
    lines = [line.split("#")[0].strip() for line in (REPO / name).read_text(encoding="utf-8").splitlines()]
    return [line for line in lines if line and not line.startswith("-")]


def _requirements(name: str) -> set[str]:
    """The distributions a requirements file names."""
    return {re.split(r"[\[<>=!~; ]", line, maxsplit=1)[0].lower() for line in _requirement_lines(name)}


def _third_party_imports(root: Path) -> set[str]:
    names = set()
    for f in root.rglob("*.py"):
        for n in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(n, ast.Import):
                names.update(a.name.split(".")[0] for a in n.names)
            elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
                names.add(n.module.split(".")[0])
    names -= set(sys.stdlib_module_names) | {"monitor", "tests", "__future__"}
    return {DIST.get(x, x).lower() for x in names}


def test_requirements_are_what_the_terminal_imports_and_nothing_else():
    """requirements.txt is what a user installs: exactly the runtime imports of monitor/ (uvicorn with its
    [standard] extras — the file watcher, faster HTTP — except on a Python they do not build for yet),
    each with a minimum version (and python_version marker) the bootstrap can check."""
    assert _requirements("requirements.txt") == _third_party_imports(REPO / "monitor")
    lines = _requirement_lines("requirements.txt")
    assert 'uvicorn[standard]>=0.30; python_version < "3.15"' in lines         # [standard] wherever it installs
    assert all(re.fullmatch(r'[a-z-]+(\[[a-z]+\])?>=[\d.]+(; python_version (<|>=) "3\.\d+")?', line)
               for line in lines), lines


def test_dev_requirements_add_what_the_tests_need():
    lines = (REPO / "requirements-dev.txt").read_text(encoding="utf-8").splitlines()
    assert lines[0] == "-r requirements.txt"
    dev = _requirements("requirements-dev.txt")
    assert {"pytest", "time-machine", "httpx"} <= dev
    assert _third_party_imports(REPO / "tests") <= dev | _requirements("requirements.txt")
