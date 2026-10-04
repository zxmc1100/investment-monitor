"""Append-only daily equity log.

Each build appends one snapshot of the portfolio's accounting state. This gives
an auditable time series of true daily equity (and the cash flows around it) so
calendar-year / YTD returns can be measured from logged reality instead of being
re-reconstructed from price history on every run.

Stored in local/ (gitignored) — it contains euro amounts.
"""

import csv
import os
import tempfile
from datetime import date
from pathlib import Path

FIELDS = ["date", "current_value", "net_cost_basis", "gross_deposits",
          "cash_returned", "realized_pnl", "unrealized_pnl", "total_pnl"]


def append_snapshot(path: str | Path, row: dict) -> None:
    """Append today's snapshot. If today already logged, overwrite that row."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    row = {k: row.get(k) for k in FIELDS}
    row["date"] = row["date"] or str(date.today())

    rows = load(path)
    rows = [r for r in rows if r["date"] != row["date"]]
    rows.append(row)
    rows.sort(key=lambda r: r["date"])

    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            w.writeheader()
            for r in rows:
                w.writerow(r)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def load(path: str | Path) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    with open(path, newline="") as f:
        out = []
        for r in csv.DictReader(f):
            for k in FIELDS:
                if k != "date" and r.get(k) not in (None, ""):
                    r[k] = float(r[k])
            out.append(r)
        return out
