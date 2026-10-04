"""A canonical, exact form for golden hashes (tests/portfolio/test_snapshot_golden.py)."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

import numpy as np
import pandas as pd


def canon(x):
    """JSON-ready and exact: floats by repr (round-trip), NaN/inf named, Series as index + values,
    datetimes as ISO strings, dicts sorted by key."""
    if isinstance(x, pd.Series):
        return {"i": [str(i) for i in x.index], "v": [canon(v) for v in x.tolist()]}
    if isinstance(x, pd.DataFrame):
        return {str(c): canon(x[c]) for c in sorted(x.columns, key=str)}
    if isinstance(x, np.ndarray):
        return [canon(v) for v in x.tolist()]
    if isinstance(x, dict):
        return {str(k): canon(v) for k, v in sorted(x.items(), key=lambda kv: str(kv[0]))}
    if isinstance(x, (list, tuple, set, frozenset)):
        items = sorted(x, key=str) if isinstance(x, (set, frozenset)) else x
        return [canon(v) for v in items]
    if isinstance(x, (bool, np.bool_)):
        return bool(x)
    if isinstance(x, (float, np.floating)):
        return repr(float(x))
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, (pd.Timestamp, datetime)):
        return x.isoformat()
    return x


def digest(x) -> str:
    return hashlib.sha256(json.dumps(canon(x), sort_keys=True).encode()).hexdigest()
