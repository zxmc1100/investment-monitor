"""Tiny JSON file helpers for user state under local/buffer: atomic writes (temp file +
os.replace), so a server killed mid-write — uvicorn's reload does exactly that — never leaves
a torn file."""
import json
import os
import tempfile
from pathlib import Path


def write_atomic(path: Path, obj) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(obj, f, allow_nan=False, indent=1)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
