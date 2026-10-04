"""Package layout guards: one-way dependency direction, and the public core carries no
code of a local add-on (monitor.plugins loads one from the gitignored private/ directory)."""
import ast
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PKG = REPO / "monitor"

RANK = {"data": 0, "universe": 1, "portfolio": 1, "alerts": 2, "screens": 4, "server": 5}
COMMON = {"config", "plugins"}          # importable from any layer (plugins: config only)

# (importer, imported) pairs tolerated until the noted task removes them — none left.
KNOWN: set = set()


def _modules() -> dict[str, Path]:
    out = {}
    for f in sorted(PKG.rglob("*.py")):
        name = ".".join(f.relative_to(REPO).with_suffix("").parts)
        out[name.removesuffix(".__init__")] = f
    return out


def _imported(f: Path, known: dict) -> set[str]:
    out = set()
    for n in ast.walk(ast.parse(f.read_text())):
        if isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            for a in n.names:
                full = f"{n.module}.{a.name}"
                out.add(full if full in known else n.module)
        elif isinstance(n, ast.Import):
            out.update(a.name for a in n.names)
    return out


def _layer(mod: str) -> str | None:
    parts = mod.split(".")
    return parts[1] if len(parts) > 1 and parts[0] == "monitor" else None


def _edges():
    mods = _modules()
    for name, f in mods.items():
        for imp in _imported(f, mods):
            yield name, imp


def test_dependency_direction():
    bad = []
    for name, imp in _edges():
        src, dst = _layer(name), _layer(imp)
        if src not in RANK or dst is None or dst == src or dst in COMMON or (name, imp) in KNOWN:
            continue
        if dst not in RANK or RANK[dst] >= RANK[src]:
            bad.append(f"{name} -> {imp}")
    assert not bad, "dependency direction violated:\n" + "\n".join(bad)


def test_known_violations_are_still_real():
    edges = set(_edges())
    stale = [k for k in KNOWN if k not in edges]
    assert not stale, f"remove fixed entries from KNOWN: {stale}"


def test_no_package_module_imports_root_scripts():
    assert not [p.name for p in REPO.glob("*.py")], "the repo root holds no Python scripts"
    assert not (PKG / "legacy").exists()


ADDON = "monitor" + "_private"                         # spelled apart: this file is scanned too
RESEARCH = re.compile(r"\bmonitor[./](strategy|labs|quant)\b")


def test_no_public_module_imports_the_addon():
    """Only monitor/plugins.py names the add-on's package (by string, through importlib)."""
    imports = [f"{n} -> {i}" for n, i in _edges() if i.split(".")[0] == ADDON]
    assert not imports, imports
    named = [f.relative_to(REPO).as_posix() for f in PKG.rglob("*.py") if ADDON in f.read_text()]
    assert named == ["monitor/plugins.py"], named


def test_the_core_has_no_research_packages():
    """(Only source counts: a checkout that once had them may keep an untracked __pycache__ there.)"""
    assert not [p for p in ("strategy", "labs", "quant") if list((PKG / p).rglob("*.py"))]


def _tracked() -> list[Path]:
    try:
        out = subprocess.run(["git", "ls-files", "-z"], cwd=REPO, capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout (e.g. a downloaded archive)")
    return [REPO / p for p in out.decode().split("\0") if p]


def test_no_tracked_file_carries_addon_code():
    """The public repo holds only the core: no tracked file names an add-on's modules, and none lives under
    a private/ directory."""
    hits = []
    for f in _tracked():
        rel = f.relative_to(REPO).as_posix()
        if rel.startswith("private/"):
            hits.append(rel)
        elif f.suffix in (".py", ".js", ".mjs", ".md", ".toml", ".txt", ".html", ".css") and f.is_file() \
                and RESEARCH.search(f.read_text(errors="ignore")):
            hits.append(rel)
    assert not hits, hits
