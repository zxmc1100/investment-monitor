"""Alert rules — pure: parse the ALERT grammar, describe a rule, and
evaluate every rule against one market snapshot with a no-repeat state machine.

A rule is {id, kind, ticker, op, value}. Each rule watches one or more SUBJECTS (a ticker,
"PORT", or "ticker|kind|date" for events); per subject the state is {armed, day, at}:

  armed + condition true  → fires once (one event) and disarms
  disarmed → re-arms only when the condition is clearly false again:
      LEVEL / DD     0.5 % band back past the line
      PORT_*         value back past the threshold
      STALE          the quote is fresh again (it fires only once no good quote came for
                     STALE_ALERT_MIN minutes, or on an old bar — never on one missed minute)
      MOVE           never within a trading day — a new trading day re-arms (once per day)
      EVENT          never — every event date is its own subject
  MOVE and PORT_DAY are day-scoped: a new trading day re-arms them.

Grammar leniency: numbers take a decimal comma ('180,5') or no leading zero ('.5'); a LEVEL rule
also reads '<=' / '>=' — stored and described as '<' / '>' (the rule is "at or past the line"
either way: evaluate fires on p < v / p > v and re-arms past the 0.5 % band, so an exact touch
is not a separate case). The confirmation shows the normalized rule.

snapshot = {quotes: {t: {price, prev_close, date, stale, lapsed} | None}, high52: {t: float},
            held: [t], watched: [t], events: {t: [{date, kind, amount}]},
            port: {day_pct, weights: {t: %}, day} | None, drift: {value, target} | None}
A stale or missing quote never fires a price rule: alerts act on live prices only. `stale` = this
check's fetch failed or the bar is old; `lapsed` = no good quote for STALE_ALERT_MIN minutes or an old
bar (what the STALE rule reports).
"""
from __future__ import annotations

import math
import re
from datetime import datetime

import pandas as pd

KINDS = ("LEVEL", "MOVE", "DD", "PORT_DAY", "PORT_DRIFT", "PORT_WEIGHT", "EVENT", "STALE")
BAND = 0.005                     # LEVEL / DD re-arm band (0.5 %)
USAGE = ("ALERT <TKR> < x · <TKR> > x · <TKR|*> MOVE n · <TKR|*> DD n · PORT DAY -n · "
         "PORT DRIFT n · PORT WEIGHT n · EVENT · STALE")
_TKR = r"[A-Z0-9^][A-Z0-9.\-=^]{0,19}"
_NUM = r"[-+]?(?:\d+(?:[.,]\d+)?|\.\d+)"
_MON = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
_DOW = ("MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN")


def _rule(kind: str, ticker: str | None = None, op: str | None = None, value: float | None = None) -> dict:
    return {"kind": kind, "ticker": ticker, "op": op, "value": value}


def _f(s: str) -> float:
    """A decimal comma is fine ('180,5'), but '1,700' could be 1700 or 1.7 — refused, never guessed."""
    if re.fullmatch(r"[-+]?[1-9]\d{0,2},\d{3}", s):
        raise ValueError(f"{s}: AMBIGUOUS — WRITE {s.replace(',', '')} OR {s.replace(',', '.')}")
    return float(s.replace(",", "."))


def parse_rule(text: str) -> dict:
    """'SAP.DE < 180' → {kind: LEVEL, ticker: SAP.DE, op: '<', value: 180.0} (no id yet).
    Raises ValueError(USAGE) on anything else."""
    t = re.sub(r"\s+", " ", str(text or "").upper().replace("%", "").replace("\u2212", "-")).strip()
    t = re.sub(r"^ALERT ", "", t)
    m = re.fullmatch(rf"PORT (DAY|DRIFT|WEIGHT) ({_NUM})", t)
    if m:
        v = _f(m[2])
        if m[1] == "DAY" and v != 0:
            return _rule("PORT_DAY", None, "<=", -abs(v))
        if m[1] == "DRIFT" and 0 < v <= 100:
            return _rule("PORT_DRIFT", None, ">=", v)
        if m[1] == "WEIGHT" and 0 < v <= 100:
            return _rule("PORT_WEIGHT", None, ">=", v)
        raise ValueError(USAGE)
    if t in ("EVENT", "STALE"):
        return _rule(t, "*")
    m = re.fullmatch(rf"({_TKR}) ?([<>]=?) ?({_NUM})", t)
    if m and _f(m[3]) > 0:
        return _rule("LEVEL", m[1], m[2][0], _f(m[3]))
    m = (re.fullmatch(rf"({_TKR}|\*) (MOVE|DD) ({_NUM})", t)
         or re.fullmatch(rf"(MOVE|DD) ({_TKR}|\*) ({_NUM})", t))
    if m:
        kind, tkr = (m[2], m[1]) if m[2] in ("MOVE", "DD") else (m[1], m[2])
        v = _f(m[3])
        if 0 < v < 100:
            return _rule(kind, tkr, ">=", v)
    raise ValueError(USAGE)


def _g(v) -> str:
    return f"{v:g}"


def rule_text(rule: dict) -> str:
    """The ALERT text that parses back to this rule (used to validate hand-edited files)."""
    k, t, v = rule.get("kind"), rule.get("ticker"), rule.get("value")
    if k == "LEVEL":
        return f"{t} {rule.get('op')} {v}"
    if k in ("MOVE", "DD"):
        return f"{t} {k} {v}"
    if k in ("PORT_DAY", "PORT_DRIFT", "PORT_WEIGHT"):
        return f"PORT {k[5:]} {v}"
    return str(k)


def describe(rule: dict) -> str:
    k, t, v = rule["kind"], rule.get("ticker"), rule.get("value")
    who = "HELD+WATCHED" if t == "*" else t
    return {"LEVEL": lambda: f"{t} {rule.get('op')} {_g(v)}",
            "MOVE": lambda: f"{who} MOVE ±{_g(v)}%",
            "DD": lambda: f"{who} {_g(v)}% OFF 52W HIGH",
            "PORT_DAY": lambda: f"PORT DAY ≤ {_g(v)}%",
            "PORT_DRIFT": lambda: f"PORT DRIFT ≥ {_g(v)}pp",
            "PORT_WEIGHT": lambda: f"PORT WEIGHT ≥ {_g(v)}%",
            "EVENT": lambda: "EARNINGS / EX-DIV NEXT BUSINESS DAY",
            "STALE": lambda: "HELD QUOTE STALE"}[k]()


def _num(x) -> float | None:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return round(f, 4) if math.isfinite(f) else None


def _live(snap: dict, t: str) -> dict | None:
    q = (snap.get("quotes") or {}).get(t)
    return q if q and not q.get("stale") and _num(q.get("price")) else None


def _who(rule: dict, snap: dict) -> list[str]:
    if rule.get("ticker") == "*":
        return sorted(set(snap.get("held") or []) | set(snap.get("watched") or []))
    return [rule["ticker"]] if rule.get("ticker") else []


def _ddmon(iso: str) -> str:
    d = datetime.fromisoformat(iso)
    return f"{d.day:02d} {_MON[d.month - 1]}"


def _session(day: str | None, now: datetime) -> str:
    """'TODAY', or the session a day move belongs to ('FRI 02 OCT' on a weekend or before the open)
    — as MKT and PORT label DAY."""
    if not day or day == now.date().isoformat():
        return "TODAY"
    return f"{_DOW[datetime.fromisoformat(day).weekday()]} {_ddmon(day)}"


def _observe(rule: dict, snap: dict, now: datetime):
    """Yield (subject, cond, rearm, day, value, msg, down) for every subject observable now."""
    k, v = rule["kind"], rule.get("value")
    if k == "LEVEL":
        q = _live(snap, rule["ticker"])
        if q:
            p, lo = q["price"], rule["op"] == "<"
            yield (rule["ticker"], p < v if lo else p > v, p >= v * (1 + BAND) if lo else p <= v * (1 - BAND),
                   None, p, f"{rule['ticker']} {p:,.2f} {rule['op']} {_g(v)}", lo)
    elif k == "MOVE":
        for t in _who(rule, snap):
            q = _live(snap, t)
            if q and q.get("date") and _num(q.get("prev_close")):
                d = (q["price"] / q["prev_close"] - 1) * 100
                yield (t, abs(d) >= v, False, q.get("date") or now.date().isoformat(), d,
                       f"{t} {d:+.1f}% {_session(q.get('date'), now)}", d < 0)
    elif k == "DD":
        for t in _who(rule, snap):
            q, hi = _live(snap, t), _num((snap.get("high52") or {}).get(t))
            if q and hi:
                hi = max(hi, q["price"])
                dd = (1 - q["price"] / hi) * 100
                yield (t, dd >= v, dd < v - BAND * 100, None, dd,
                       f"{t} {dd:.1f}% BELOW 52W HIGH {hi:,.2f}", True)
    elif k == "PORT_DAY":
        port = snap.get("port") or {}
        d = _num(port.get("day_pct"))
        if d is not None:
            yield ("PORT", d <= v, d > v, port.get("day") or None, d,
                   f"PORTFOLIO {d:+.2f}% {_session(port.get('day'), now)}", True)
    elif k == "PORT_DRIFT":
        drift = snap.get("drift") or {}
        d = _num(drift.get("value"))
        if d is not None:
            yield ("PORT", d >= v, d < v, None, d, f"DRIFT {d:.1f}pp FROM {drift.get('target') or 'TARGET'}", False)
    elif k == "PORT_WEIGHT":
        for t, w in sorted(((snap.get("port") or {}).get("weights") or {}).items()):
            w = _num(w)
            if w is not None:
                yield (t, w >= v, w < v, None, w, f"{t} {w:.1f}% OF THE BOOK", False)
    elif k == "EVENT":
        nbd = (pd.Timestamp(now.date()) + pd.offsets.BDay(1)).date().isoformat()
        for t in _who({"ticker": "*"}, snap):
            for e in (snap.get("events") or {}).get(t) or []:
                if e.get("date") == nbd:
                    what = e.get("kind", "EVENT")
                    a = _num(e.get("amount"))
                    amt = f" {a:g}" if what == "EX-DIV" and a else ""
                    yield (f"{t}|{what}|{nbd}", True, False, None, None,
                           f"{t} {what}{amt} {_ddmon(nbd)} — NEXT BUSINESS DAY", False)
    elif k == "STALE":
        for t in sorted(snap.get("held") or []):
            q = (snap.get("quotes") or {}).get(t)
            bad = not q or bool(q.get("lapsed"))
            yield (t, bad, not bad, None, None, f"{t} {'NO PRICE' if not q else 'QUOTE STALE'}", False)


def evaluate(rules: list[dict], state: dict, snapshot: dict, now: datetime) -> tuple[dict, list[dict]]:
    """(new_state, new_events). Pure: inputs are not mutated. Events are
    {rule, subject, ts, value, msg, down}; the store gives them ids."""
    ts = now.isoformat(timespec="seconds")
    today = now.date().isoformat()
    new_state, events = {}, []
    for r in rules:
        prev = {s: dict(st) for s, st in (state.get(r["id"]) or {}).items()}
        if r["kind"] == "EVENT":                          # past event dates can never fire again
            prev = {s: st for s, st in prev.items() if s.rsplit("|", 1)[-1] >= today}
        cur = dict(prev)
        seen = set()
        for subject, cond, rearm, day, value, msg, down in _observe(r, snapshot, now):
            seen.add(subject)
            st = dict(prev.get(subject) or {"armed": True, "day": None, "at": None})
            if day is not None and st.get("day") != day:
                st["armed"] = True                        # a new trading day
            if st["armed"] and cond:
                st = {"armed": False, "day": day, "at": ts}
                events.append({"rule": r["id"], "subject": subject, "ts": ts, "value": _num(value),
                               "msg": msg, "down": bool(down)})
            elif not st["armed"] and rearm:
                st["armed"] = True
            cur[subject] = st
        if r["kind"] == "PORT_WEIGHT" and snapshot.get("port"):
            for subject in set(cur) - seen:               # sold since: a later buy-back may fire again
                cur[subject] = {**cur[subject], "armed": True}
        new_state[r["id"]] = cur
    return new_state, events
