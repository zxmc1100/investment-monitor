"""Alert persistence: local/buffer/alerts.json =
{rules: [{id, kind, ticker, op, value}], state: {rule_id: {subject: {armed, day, at}}},
 log: [{id, rule, subject, text, ts, value, msg, down, ack}] newest first, capped at 200,
 seq: {rule, entry}}.

Rule ids (A1, A2, …) and entry ids (E1, E2, …) come from counters that never reuse a number.
A missing file means the default rules. A corrupt file means the defaults plus a logged warning
(the bad file is kept as alerts.json.bad); a hand-edited file keeps every rule that still parses
and drops the rest, and a log entry whose ts is not an ISO string keeps the entry with ts None.
Raises only when the file is there but cannot be read now (OSError) — never defaults saved over it.
path None = in-memory defaults, nothing saved.
Every helper except load/save is pure and returns new dicts.
"""
from __future__ import annotations

import json
import logging
import re
import shutil
from datetime import datetime
from pathlib import Path

from monitor.alerts.jsonfile import write_atomic
from monitor.alerts.rules import KINDS, describe, parse_rule, rule_text

log = logging.getLogger("monitor.alerts")
LOG_CAP = 200
DEFAULT_RULES = ("PORT DAY -2", "PORT DRIFT 10", "PORT WEIGHT 30", "MOVE * 5", "EVENT", "STALE")
_KEY = ("kind", "ticker", "op", "value")


def _no_constant(c):
    raise ValueError(f"non-finite constant {c} is not valid JSON")


def _safe(v) -> bool:
    return v is None or isinstance(v, (str, bool, int)) or (isinstance(v, float) and v == v and abs(v) != float("inf"))


def defaults() -> dict:
    rules = [{"id": f"A{i}", **parse_rule(t)} for i, t in enumerate(DEFAULT_RULES, 1)]
    return {"rules": rules, "state": {}, "log": [], "seq": {"rule": len(rules), "entry": 0}}


def _clean_rule(r) -> dict | None:
    if not isinstance(r, dict) or not re.fullmatch(r"A\d+", str(r.get("id", ""))) or r.get("kind") not in KINDS:
        return None
    try:
        again = parse_rule(rule_text(r))
    except ValueError:
        return None
    if any(again[k] != r.get(k) for k in _KEY):
        return None
    return {"id": r["id"], **again}


def _num(s: str) -> int:
    return int(s[1:]) if re.fullmatch(r"[AE]\d+", s or "") else 0


def _iso(ts) -> str | None:
    """A log timestamp as stored, or None unless it is an ISO string (a hand edit: 'yesterday 3pm')."""
    try:
        datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    return ts


def load(path: Path | None) -> dict:
    if path is None or not Path(path).exists():
        return defaults()
    path = Path(path)
    # A file there but unreadable right now (out of file handles, permissions) raises: the defaults read in
    # its place would be saved over your rules at the next check. Only a corrupt one reads as the defaults.
    text = path.read_text(encoding="utf-8")
    try:
        raw = json.loads(text, parse_constant=_no_constant)
        if not isinstance(raw, dict) or not isinstance(raw.get("rules"), list):
            raise ValueError("not an alerts file")
    except Exception as e:
        log.warning("unreadable alerts file %s (%s) — using the default rules; kept as %s.bad", path, e, path.name)
        try:
            shutil.copyfile(path, path.with_name(path.name + ".bad"))
        except OSError:
            pass
        return defaults()
    rules, seen = [], set()
    for r in raw["rules"]:
        c = _clean_rule(r)
        if c is None or c["id"] in seen:
            log.warning("alerts file: dropping invalid rule %r", r)
            continue
        seen.add(c["id"])
        rules.append(c)
    state = {}
    for rid, subs in (raw.get("state") if isinstance(raw.get("state"), dict) else {}).items():
        if rid in seen and isinstance(subs, dict):
            state[rid] = {s: {"armed": st["armed"],
                              "day": st.get("day") if isinstance(st.get("day"), str) else None,
                              "at": st.get("at") if isinstance(st.get("at"), str) else None}
                          for s, st in subs.items() if isinstance(st, dict) and isinstance(st.get("armed"), bool)}
    entries = [{**e, "ts": _iso(e.get("ts"))} for e in (raw.get("log") if isinstance(raw.get("log"), list) else [])
               if isinstance(e, dict) and _num(str(e.get("id", ""))) and isinstance(e.get("msg"), str)
               and isinstance(e.get("rule"), str) and all(_safe(v) for v in e.values())][:LOG_CAP]
    seq = raw.get("seq") if isinstance(raw.get("seq"), dict) else {}
    return {"rules": rules, "state": state, "log": entries,
            "seq": {"rule": _top(seq.get("rule"), [r["id"] for r in rules] + [e["rule"] for e in entries]),
                    "entry": _top(seq.get("entry"), [e["id"] for e in entries])}}


def _top(counter, ids) -> int:
    """The id counter: never below a number already in use (a hand-edited file may lag)."""
    return max([counter if isinstance(counter, int) else 0, *(_num(i) for i in ids)])


def save(path: Path | None, data: dict) -> None:
    if path is not None:
        write_atomic(path, data)


def add_rule(data: dict, rule: dict) -> tuple[dict, dict]:
    n = data["seq"]["rule"] + 1
    r = {"id": f"A{n}", **{k: rule[k] for k in _KEY}}
    return {**data, "rules": [*data["rules"], r], "seq": {**data["seq"], "rule": n}}, r


def remove_rule(data: dict, rid: str) -> dict:
    """Drop a rule, its state, and acknowledge its active alerts. KeyError if unknown."""
    rid = rid.upper()
    if not any(r["id"] == rid for r in data["rules"]):
        raise KeyError(rid)
    return {**data, "rules": [r for r in data["rules"] if r["id"] != rid],
            "state": {k: v for k, v in data["state"].items() if k != rid},
            "log": [{**e, "ack": True} if e.get("rule") == rid else e for e in data["log"]]}


def active(data: dict) -> list[dict]:
    return [e for e in data["log"] if not e.get("ack")]


def ack(data: dict, key: str) -> tuple[dict, int]:
    """Acknowledge ALL, one entry (E12) or every active alert of a rule (A3). Returns (data, n)."""
    key = key.upper()
    hit = lambda e: not e.get("ack") and (key == "ALL" or e.get("id") == key or e.get("rule") == key)
    n = sum(1 for e in data["log"] if hit(e))
    return {**data, "log": [{**e, "ack": True} if hit(e) else e for e in data["log"]]}, n


def record(data: dict, state: dict, events: list[dict]) -> tuple[dict, list[dict]]:
    """Store the evaluated state and append fired events as log entries (newest first)."""
    by_id = {r["id"]: r for r in data["rules"]}
    n, new = data["seq"]["entry"], []
    for ev in events:
        n += 1
        r = by_id.get(ev["rule"])
        new.append({"id": f"E{n}", **ev, "text": describe(r) if r else ev["rule"], "ack": False})
    return {**data, "state": state, "log": (new + data["log"])[:LOG_CAP], "seq": {**data["seq"], "entry": n}}, new


def view(data: dict) -> dict:
    """What the API and the ALRT screen show: rules (+ text, ARMED/FIRED, last fired), active, log."""
    last = {}
    for e in data["log"]:
        if e.get("ts") is not None:                   # newest entry that has a time (hand edits may blank it)
            last.setdefault(e.get("rule"), e["ts"])
    rules = [{**r, "text": describe(r),
              "state": "FIRED" if any(not st["armed"] for st in data["state"].get(r["id"], {}).values()) else "ARMED",
              "last": last.get(r["id"])} for r in data["rules"]]
    return {"rules": rules, "active": active(data), "log": data["log"]}
