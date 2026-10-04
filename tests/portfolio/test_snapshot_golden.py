"""PORT's data assembly, pinned: on the fixture portfolio (fake Yahoo, frozen time, private buffer) the
snapshot tiers — positions, accounting, the ROI / benchmark / per-asset curves, metrics, staleness —
hash to a golden value, floats to 10 significant digits (stable across platforms and library
versions, which differ in the last bit). These are the very numbers the retired HTML portfolio report
gathered from the same functions; the golden was checked against that report's gather() before it was
deleted, and replaces its golden test. Regenerated once since, when two unread outputs left the pin
(the correlation matrix — its function was deleted — and daily_tier's `ytd`); no pinned number moved.
Regenerated again when asset_values gained "__twr__" (the benchmarks' time-weighted growth): with that
key removed the payload hashed to the previous golden, so again no pinned number moved.

Regenerate ONLY when a change to the numbers is intended:  UPDATE_GOLDEN=1 pytest <this file>
(DUMP_GOLDEN=<path> writes the canonical JSON for a diff)."""
import json
import os
from pathlib import Path

import pytest

from monitor.portfolio import snapshot
from tests import golden

FIX = Path(__file__).resolve().parents[1] / "fixtures"
GOLDEN = FIX / "golden" / "port_snapshot.sha256"


def port_data(csv: Path) -> dict:
    book = snapshot.load_book(csv)
    q = snapshot.quote_tier(book)
    dly = snapshot.daily_tier(book)
    return {**{k: q[k] for k in ("positions", "summary", "txns", "acct", "stale", "as_of")},
            **{k: dly[k] for k in ("roi_series", "bm_series", "asset_values", "metrics")}}


def _check(d: dict) -> None:
    if os.environ.get("DUMP_GOLDEN"):
        Path(os.environ["DUMP_GOLDEN"]).write_text(json.dumps(golden.canon(d), sort_keys=True, indent=1), encoding="utf-8")
    digest = golden.digest(d)
    if os.environ.get("UPDATE_GOLDEN") == "1":
        GOLDEN.write_text(digest + "\n", encoding="utf-8")
    assert GOLDEN.exists(), "golden missing — run once with UPDATE_GOLDEN=1"
    assert digest == GOLDEN.read_text(encoding="utf-8").strip()


def test_port_data_is_pinned(port_env):
    from datetime import datetime
    d = port_data(port_env)
    assert d["positions"] and not d["roi_series"].empty and d["asset_values"]
    # as_of is the frozen moment in the machine's local time: checked here, kept out of the pin, so the
    # golden is the same in every time zone (GitHub's runners are UTC)
    assert datetime.fromisoformat(d.pop("as_of")) == datetime.now().replace(microsecond=0)
    _check(d)
