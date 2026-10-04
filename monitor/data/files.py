"""Crash- and race-safe local files: a CSV written whole or not at all, and an exclusive lock shared by
processes that read-merge-replace the same file (e.g. the terminal's daily tier and `python -m monitor
export` both appending to one log). Portable: the lock is fcntl.flock on POSIX, msvcrt.locking on Windows."""
from __future__ import annotations

import contextlib
import errno
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
        Path(tmp).write_text(text, encoding="utf-8")
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


def _lock_functions():
    """(lock, unlock) for an open binary file: exclusive, blocking, held per open file — so between
    processes and between threads alike — and dropped by the OS if the holder dies."""
    if os.name == "nt":
        import msvcrt
        busy = {errno.EACCES, errno.EDEADLK, getattr(errno, "EDEADLOCK", errno.EDEADLK)}

        def lock(fh):
            while True:
                fh.seek(0)                               # the locked region: byte 0, whatever the file holds
                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)   # retries for ~10 s, then EDEADLOCK
                    return
                except OSError as e:
                    if e.errno not in busy:
                        raise

        def unlock(fh):
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
        return lock, unlock
    import fcntl
    return (lambda fh: fcntl.flock(fh, fcntl.LOCK_EX)), (lambda fh: fcntl.flock(fh, fcntl.LOCK_UN))


@contextlib.contextmanager
def locked(path: Path):
    """Hold an exclusive lock on `<path>.lock` (fcntl.flock on POSIX, msvcrt.locking on Windows: between
    processes and between threads alike); released on exit, or by the OS if the holder dies."""
    lock_fn, unlock_fn = _lock_functions()
    lock = Path(f"{path}.lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    with open(lock, "ab") as fh:
        lock_fn(fh)
        try:
            yield
        finally:
            unlock_fn(fh)
