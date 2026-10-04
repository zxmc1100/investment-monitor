"""Release layout: .gitignore keeps your data, the developer notes and a local add-on out while examples/
and the tracked universe stay in."""
import subprocess
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
    text = (REPO / "LICENSE").read_text()
    assert text.startswith("MIT License") and "Copyright (c) 2026 the Investment Monitor contributors" in text
