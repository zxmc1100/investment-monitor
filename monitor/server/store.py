"""Payload + tier-part store under local/buffer/screens.

Writes are atomic (temp file + os.replace) so a server killed mid-write — uvicorn's
reload does exactly that — never leaves a torn file. Anything unreadable reads as absent
(cold) and is logged, never raised. Payloads are strict JSON: NaN is a bug, not data.
"""
import json
import logging
import os
import pickle
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("monitor.store")


def _atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class Store:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _read(self, path: Path, loader):
        if not path.exists():
            return None
        try:
            return loader(path.read_bytes())
        except Exception as e:
            log.warning("unreadable %s (%s) — treating as cold", path.name, e)
            return None

    def get_payload(self, sid: str) -> dict | None:
        return self._read(self.root / f"{sid}.json", json.loads)

    def put_payload(self, sid: str, payload: dict) -> None:
        data = json.dumps(payload, allow_nan=False, separators=(",", ":")).encode()
        _atomic(self.root / f"{sid}.json", data)

    def part_info(self, sid: str, tier: str) -> dict | None:
        info = self._read(self.root / f"{sid}.{tier}.json", json.loads)
        return info if isinstance(info, dict) and {"at", "code"} <= info.keys() else None

    def get_part(self, sid: str, tier: str) -> dict | None:
        rec = self._read(self.root / f"{sid}.{tier}.pkl", pickle.loads)
        return rec if isinstance(rec, dict) and {"data", "at", "code"} <= rec.keys() else None

    def invalidate_part(self, sid: str, tier: str) -> None:
        """Drop an unreadable part (and its sidecar) so the engine sees the tier as never run."""
        (self.root / f"{sid}.{tier}.json").unlink(missing_ok=True)
        (self.root / f"{sid}.{tier}.pkl").unlink(missing_ok=True)

    def put_part(self, sid: str, tier: str, data: Any, code: str) -> dict:
        rec = {"data": data, "at": datetime.now().isoformat(timespec="seconds"), "code": code}
        _atomic(self.root / f"{sid}.{tier}.pkl", pickle.dumps(rec))
        _atomic(self.root / f"{sid}.{tier}.json", json.dumps({"at": rec["at"], "code": code}).encode())
        return rec

    def keys(self, prefix: str, tiers: tuple[str, ...]) -> set[str]:
        """Store keys that start with `prefix` (e.g. 'SEC~'), from any of their files. Keys hold dots
        (tickers), so a trailing '.<tier>' is stripped only for the screen's own tiers."""
        out = set()
        for f in self.root.glob(f"{prefix}*"):
            if f.suffix not in (".json", ".pkl"):
                continue
            key = f.name[: -len(f.suffix)]
            for t in tiers:
                if key.endswith(f".{t}"):
                    key = key[: -len(t) - 1]
                    break
            out.add(key)
        return out

    def drop(self, key: str, tiers: tuple[str, ...]) -> None:
        """Delete a key's payload and every tier part (a pruned parametrized screen)."""
        (self.root / f"{key}.json").unlink(missing_ok=True)
        for t in tiers:
            self.invalidate_part(key, t)
