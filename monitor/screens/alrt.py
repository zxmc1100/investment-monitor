"""ALRT — Alerts: the active (unacknowledged) alerts — Enter acknowledges the row —,
every rule with its state, and the log. Private, never exported.

One tier, no network: it reads local/buffer/alerts.json. The alert service pushes a fresh part
after every check, ACK or rule edit (Engine.put_and_assemble), so this screen follows at once.
"""
from __future__ import annotations

from datetime import datetime

from monitor import config
from monitor.alerts import store as astore
from monitor.screens.base import Ctx, Screen
from monitor.screens.common import PRIV, num

DEPS = ("monitor.screens.alrt", "monitor.screens.common", "monitor.screens.base", "monitor.alerts.store",
        "monitor.alerts.rules", "monitor.alerts.jsonfile", "monitor.config")
LOG_ROWS = 50
MON = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")

HELP = [
    {"h": "ACTIVE", "vis": PRIV, "body": "Alerts not yet acknowledged; they keep the ▲ badge lit. "
     "Enter on a row (or ACK <id>) acknowledges it, ACK <rule id> every alert of that rule, ACK ALL all."},
    {"h": "RULES", "vis": PRIV, "body": "ALERT <TKR> < x | > x (price crosses a level) · <TKR|*> MOVE n "
     "(day move of n % or more) · <TKR|*> DD n (n % below the 52-week high) · PORT DAY -n · PORT DRIFT n "
     "(pp from your TARGET) · PORT WEIGHT n (one position ≥ n % of the book) · EVENT (earnings or "
     "ex-dividend on the next business day) · STALE (no good quote for a held name in "
     f"{config.STALE_ALERT_MIN} minutes, or its last bar is days old). * = every held "
     "and watched name. UNALERT <id> removes a rule."},
    {"h": "ARMED / FIRED", "vis": PRIV, "body": "A rule fires once, then stays FIRED until its condition "
     "is clearly false again (0.5 % back past a level or drawdown line; back past a PORT threshold; a "
     "fresh quote). MOVE fires at most once per trading day per name. Checks run every minute while a "
     "terminal tab is open — never while it is closed."},
]


def compute(tier: str, ctx: Ctx) -> dict:
    if tier == "quote":
        return astore.load(ctx.alerts)
    raise ValueError(f"ALRT has no {tier!r} tier")


def _when(ts) -> str | None:
    try:
        d = datetime.fromisoformat(ts)
    except (TypeError, ValueError):                 # None, or a hand-edited file: no time shown
        return None
    return f"{d.day:02d} {MON[d.month - 1]} {d:%H:%M}"


def _entry(e: dict) -> dict:
    row = {"id": e["id"], "ts": e.get("ts"), "time": _when(e.get("ts")), "rule": f"{e.get('rule')} · {e['text']}" if e.get("text") else str(e.get("rule")),
           "value": num(e.get("value")), "msg": e.get("msg", ""), "ack": "ACK" if e.get("ack") else "ACTIVE"}
    if e.get("down") and not e.get("ack"):
        row["_dn"] = True
    return row


def assemble(parts: dict, meta: dict) -> dict:
    v = astore.view(parts["quote"])
    act = [_entry(e) for e in v["active"]]
    entry_cols = [{"k": "time", "label": "TIME", "fmt": "text", "vis": PRIV},
                  {"k": "id", "label": "ID", "fmt": "tkr", "vis": PRIV},
                  {"k": "rule", "label": "RULE", "fmt": "text", "vis": PRIV},
                  {"k": "value", "label": "VALUE", "fmt": "num:2", "vis": PRIV, "align": "r"},
                  {"k": "msg", "label": "MESSAGE", "fmt": "text", "vis": PRIV}]
    rules = [{"id": r["id"], "no": int(r["id"][1:]), "text": r["text"], "state": r["state"],
              "last": _when(r["last"]) or "—", **({"_hot": True} if r["state"] == "FIRED" else {})}
             for r in v["rules"]]
    return {"screen": "ALRT", "title": "Alerts",
            "context": {"text": f"{len(act)} ACTIVE · {len(rules)} RULES · CHECKED EVERY MINUTE WHILE A TAB IS OPEN"},
            "meta": meta, "help": HELP,
            "panels": [
                {"id": "active", "n": 1, "title": "ACTIVE", "type": "table", "span": 7, "vis": PRIV,
                 "key": "id", "sort": ["ts", "desc"], "cols": entry_cols, "rows": act, "enter": "ACK {key}",
                 "context": {"text": "ENTER ACKNOWLEDGES · ACK ALL" if act else "NO ACTIVE ALERTS"}},
                {"id": "rules", "n": 2, "title": "RULES", "type": "table", "span": 5, "vis": PRIV,
                 "key": "id", "sort": ["no", "asc"], "rows": rules,
                 "cols": [{"k": "id", "label": "ID", "fmt": "tkr", "vis": PRIV},
                          {"k": "text", "label": "RULE", "fmt": "text", "vis": PRIV},
                          {"k": "state", "label": "STATE", "fmt": "text", "vis": PRIV},
                          {"k": "last", "label": "LAST FIRED", "fmt": "text", "vis": PRIV}],
                 "context": {"text": "ALERT <RULE> ADDS · UNALERT <ID> REMOVES"}},
                {"id": "log", "n": 3, "title": "LOG", "type": "table", "span": 12, "vis": PRIV,
                 "key": "id", "sort": ["ts", "desc"], "rows": [_entry(e) for e in v["log"][:LOG_ROWS]],
                 "cols": [*entry_cols, {"k": "ack", "label": "STATUS", "fmt": "text", "vis": PRIV}],
                 "context": {"text": f"LAST {LOG_ROWS} · ACKNOWLEDGED INCLUDED"}}]}


SCREEN = Screen(id="ALRT", title="Alerts", fkey=5, status="live", public=False,
                tiers=("quote",), deps=DEPS, compute=compute, assemble=assemble)
