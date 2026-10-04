"""`python -m monitor init` — a first input/ from examples/: portfolio.csv, interest.csv and
settings.toml, each copied only where it is missing (an existing file, even a dangling symlink,
is never touched). Prints what it did and what to do next."""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from monitor import config

EXAMPLES_DIR = config.EXAMPLES_DIR
FILES = (("portfolio.example.csv", "portfolio.csv"), ("interest.example.csv", "interest.csv"),
         ("settings.example.toml", "settings.toml"))


class InitError(Exception):
    """A one-line reason init cannot run (printed, never a traceback)."""


def init(input_dir: Path | None = None, examples_dir: Path | None = None) -> list[str]:
    """Create `input_dir` and fill its gaps from `examples_dir`; returns the report lines.
    InitError when `input_dir` exists but is no folder, or `examples_dir` is missing."""
    input_dir = Path(input_dir or config.INPUT_DIR)
    examples_dir = Path(examples_dir or EXAMPLES_DIR)
    if input_dir.exists() and not input_dir.is_dir():
        raise InitError(f"{input_dir} exists but is not a folder — move it away and run init again")
    if not examples_dir.is_dir():
        raise InitError(f"{examples_dir} is missing — restore it (git checkout -- examples) and run init again")
    try:
        input_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        raise InitError(f"cannot create {input_dir}: {e.strerror or e}") from None
    lines = []
    for src, dest in FILES:
        target, shown = input_dir / dest, f"{input_dir.name}/{dest}"
        if target.exists() or target.is_symlink():
            lines.append(f"kept {shown} (already there — never overwritten)")
            continue
        try:
            shutil.copyfile(examples_dir / src, target)
        except OSError as e:
            raise InitError(f"cannot copy {examples_dir.name}/{src} to {shown}: {e.strerror or e}") from None
        lines.append(f"created {shown} from {examples_dir.name}/{src}")
    lines.append("next: start the terminal (start-mac.command / start-windows.bat / ./start.sh) and press 6 "
                 "(TRADES): START FRESH clears the example, then add your trades — one by one, pasted, or your "
                 f"broker's CSV (the file format: {examples_dir.name}/README.md)")
    return lines


def main(argv=None) -> int:
    argparse.ArgumentParser(prog="python -m monitor init",
                            description="Create input/ from examples/ (never overwrites).").parse_args(argv)
    try:
        lines = init()
    except InitError as e:
        print(e, file=sys.stderr)
        return 1
    for line in lines:
        print(line)
    return 0
