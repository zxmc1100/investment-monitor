"""User preferences: a tiny JSON file under local/buffer with atomic writes.
Anything missing, unreadable or invalid falls back to the defaults — never an error."""
import json
import logging
import os
import tempfile
from pathlib import Path

from monitor import config

log = logging.getLogger("monitor.prefs")
DEFAULTS = {"target": config.DEFAULT_TARGET}


def load(path: Path | None) -> dict:
    prefs = dict(DEFAULTS)
    if path is None or not Path(path).exists():
        return prefs
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("unreadable prefs %s (%s) — using defaults", path, e)
        return prefs
    if isinstance(raw, dict) and raw.get("target") in config.PORTFOLIOS:
        prefs["target"] = raw["target"]
    return prefs


def save(path: Path | None, prefs: dict) -> None:
    if path is None:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(prefs, f)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def with_target(prefs: dict, name: str) -> dict:
    target = str(name).upper()
    if target not in config.PORTFOLIOS:
        raise ValueError(f"unknown target {name!r}; one of {', '.join(config.PORTFOLIOS)}")
    return {**prefs, "target": target}
