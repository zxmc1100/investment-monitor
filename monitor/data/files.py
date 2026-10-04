"""Crash- and race-safe local files: a CSV written whole or not at all, and an exclusive lock shared by
processes that read-merge-replace the same file (e.g. the terminal's daily tier and `python -m monitor
export` both appending to one log)."""
from __future__ import annotations

import contextlib
import fcntl
import os
import tempfile
import time
from pathlib import Path

import pandas as pd


def write_csv_atomic(df: pd.DataFrame, path: Path, **kw) -> Path:
    """df.to_csv into a temp file beside the target, then os.replace: a reader sees the old file or the
    new one, never half of one; a failed write leaves the old file. A symlinked path keeps its link (the
    file it points to is replaced)."""
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    os.close(fd)
    try:
        df.to_csv(tmp, **kw)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return target


def write_text_atomic(path: Path, text: str) -> Path:
    """`text` into a temp file beside the target, then os.replace — like write_csv_atomic: the old file or
    the new one, never half of one; a failed write leaves the old file and no temp file."""
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    os.close(fd)
    try:
        Path(tmp).write_text(text)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return target


def write_csvs_atomic(items) -> None:
    """Several CSVs that belong together, [(df, path, to_csv kwargs)]: every temp file is written first,
    then each replaces its target — a failure while writing leaves every old file in place."""
    staged = []
    try:
        for df, path, kw in items:
            target = Path(path).resolve()
            target.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
            os.close(fd)
            staged.append((tmp, target))
            df.to_csv(tmp, **kw)
    except BaseException:
        for tmp, _ in staged:
            Path(tmp).unlink(missing_ok=True)
        raise
    for tmp, target in staged:
        os.replace(tmp, target)


def sweep_tmp(dirs, older_than_s: float = 3600.0) -> list[Path]:
    """Delete the temp files our atomic writers left behind (`.<name>.<random>.tmp`, a writer killed between
    write and replace) in `dirs`, once they are older than `older_than_s` — a fresh one may be another
    writer's. Returns what was removed."""
    gone, now = [], time.time()
    for d in {Path(x).resolve() for x in dirs}:
        for f in d.glob(".*.tmp") if d.is_dir() else ():
            try:
                if now - f.stat().st_mtime > older_than_s:
                    f.unlink()
                    gone.append(f)
            except OSError:
                pass
    return gone


@contextlib.contextmanager
def locked(path: Path):
    """Hold an exclusive advisory lock on `<path>.lock` (fcntl.flock: between processes and between
    threads alike); released on exit, or by the OS if the holder dies."""
    lock = Path(f"{path}.lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)
