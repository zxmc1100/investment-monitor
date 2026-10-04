"""PORT's data assembly, pinned: on the fixture portfolio (fake Yahoo, frozen time, private buffer) the
snapshot tiers — positions, accounting, the ROI / benchmark / per-asset curves, metrics, staleness —
hash to a golden value, floats exact. These are the very numbers the retired HTML portfolio report
gathered from the same functions; the golden was checked against that report's gather() before it was
deleted, and replaces its golden test. Regenerated once since, when two unread outputs left the pin
(the correlation matrix — its function was deleted — and daily_tier's `ytd`); no pinned number moved.

Regenerate ONLY when a change to the numbers is intended:  UPDATE_GOLDEN=1 pytest <this file>
(DUMP_GOLDEN=<path> writes the canonical JSON for a diff)."""
import json
import os
from pathlib import Path

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
        Path(os.environ["DUMP_GOLDEN"]).write_text(json.dumps(golden.canon(d), sort_keys=True, indent=1))
    digest = golden.digest(d)
    if os.environ.get("UPDATE_GOLDEN") == "1":
        GOLDEN.write_text(digest + "\n")
    assert GOLDEN.exists(), "golden missing — run once with UPDATE_GOLDEN=1"
    assert digest == GOLDEN.read_text().strip()


def test_port_data_is_pinned(port_env):
    d = port_data(port_env)
    assert d["positions"] and not d["roi_series"].empty and d["asset_values"]
    _check(d)
