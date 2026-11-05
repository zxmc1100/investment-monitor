"""The command grammar: web/app/cmd.js, fuzzy.js, watch.js and route.js's back stack (tests/tui/test_parity.py keeps them
equal). Pure: text -> action. What differs from the browser is only what a terminal does otherwise: FULL hides the bars,
CASH opens a browser, and :q leaves."""

from __future__ import annotations

import re
import unicodedata
from functools import cmp_to_key

from monitor.tui.fmt import js_str

TIERS = ["QUOTE", "DAILY", "HEAVY"]
TICKER = re.compile(r"[A-Z0-9][A-Z0-9.\-]{0,14}")
DATE = re.compile(r"(\d{4}-\d{2}-\d{2}|\d{1,2}([./])\d{1,2}\2\d{4})")
ALERT_USAGE = (
    "ALERT <TKR> < x · <TKR> > x · <TKR|*> MOVE n · <TKR|*> DD n · PORT DAY -n · "
    "PORT DRIFT n · PORT WEIGHT n · EVENT · STALE"
)
QUIT = {"Q", "QA", "QUIT", "WQ", "X", "Q!", "QA!"}


def ticker_shaped(w: str) -> bool:
    return bool(TICKER.fullmatch(w)) and bool(re.search(r"[.\d]", w))


def _err(msg: str) -> dict:
    return {"type": "error", "msg": msg}


def _one_of(names: list, none: str) -> str:
    return f"<{'|'.join(names or [none])}>"


def trade_usage(action: str) -> str:
    return (
        "BONUS <TICKER> <SHARES> @ <PRICE> | = <VALUE> [DATE]"
        if action == "bonus"
        else f"{action.upper()} <TICKER> <SHARES> @ <PRICE> | = <TOTAL> [DATE] [FEE x]"
    )


def trade_command(action: str, words: list) -> dict:
    """BUY|SELL|BONUS <TICKER> <SHARES> @ <PRICE> | = <TOTAL> [DATE] [FEE x] -> {type: "trade", values} | an error."""
    usage = trade_usage(action)
    flat = [p for w in words for p in re.split(r"([@=])", w) if p]
    fee = date = ""
    if "FEE" in flat:
        f = flat.index("FEE")
        if f + 1 >= len(flat) or DATE.fullmatch(flat[f + 1]):
            return _err(f"FEE <AMOUNT> — {usage}")
        fee = flat[f + 1]
        del flat[f : f + 2]
    if flat and DATE.fullmatch(flat[-1]):
        date = flat.pop()
    if len(flat) != 4 or flat[2] not in ("@", "="):
        return _err(usage)
    tk, shares, op, value = flat
    return {
        "type": "trade",
        "values": {
            "action": action,
            "ticker": tk,
            "shares": shares,
            "pps": value if op == "@" else "",
            "total": value if op == "=" else "",
            "date": date,
            "fee": fee,
        },
    }


def _screen_of(sid: str, c: dict) -> dict:
    scr = next((s for s in c.get("screens", []) if s["id"] == sid), None)
    if not scr:
        return _err(f"UNKNOWN SCREEN {sid}")
    return {"type": "screen", "id": sid, "param": None} if scr["status"] == "live" else {"type": "soon", "id": sid}


def _help_of(rest: list, c: dict) -> dict:
    if not rest:
        return {"type": "help", "screen": None, "param": None}
    scr = next((s for s in c.get("screens", []) if s["id"] == rest[0]), None)
    if not scr:
        return _err(f"UNKNOWN SCREEN {rest[0]}")
    return {
        "type": "help",
        "screen": scr["id"],
        "param": (rest[1] if len(rest) > 1 else None) if scr.get("param") else None,
    }


def _target(rest, c):
    if not rest:
        return {"type": "target", "name": None}
    if not c.get("targets") or rest[0] in c["targets"]:
        return {"type": "target", "name": rest[0]}
    return _err(VERB["TARGET"]["usage"](c))


def _refresh(rest, _c):
    if rest and rest[0] in TIERS:
        return {"type": "refresh", "screen": None, "tier": rest[0].lower()}
    return {"type": "refresh", "screen": rest[0] if rest else None, "tier": rest[1].lower() if len(rest) > 1 else None}


def _ack(rest, _c):
    if not rest:
        return _err(VERB["ACK"]["usage"](None))
    return (
        {"type": "ack", "all": True, "id": None} if rest[0] == "ALL" else {"type": "ack", "all": False, "id": rest[0]}
    )


def _fixed(s):
    return lambda _c=None: s


def _trade_verb(verb: str, desc: str) -> dict:
    action = verb.lower()
    return {
        "verb": verb,
        "usage": _fixed(trade_usage(action)),
        "local": True,
        "arg": True,
        "desc": desc,
        "parse": lambda rest, _c: trade_command(action, rest),
    }


# Every command word besides screen mnemonics and tickers — the one list the parser, HELP and the palette all read.
VERBS = [
    {
        "verb": "TARGET",
        "usage": lambda c: f"TARGET {_one_of(c.get('targets') or [], 'NAME')}",
        "local": True,
        "arg": True,
        "desc": "the optimizer's target portfolio (alone: shows it; Enter on OPT's PORTFOLIOS sets it too)",
        "parse": _target,
    },
    {
        "verb": "WATCH",
        "usage": _fixed("WATCH <NAME|TICKER>"),
        "local": True,
        "arg": True,
        "desc": "add a name to MKT's watchlist",
        "parse": lambda rest, _c: (
            {"type": "watch", "query": " ".join(rest)} if rest else _err(VERB["WATCH"]["usage"]())
        ),
    },
    {
        "verb": "UNWATCH",
        "usage": _fixed("UNWATCH <TICKER>"),
        "local": True,
        "arg": True,
        "desc": "drop it from the watchlist",
        "parse": lambda rest, _c: {"type": "unwatch", "ticker": rest[0]} if rest else _err(VERB["UNWATCH"]["usage"]()),
    },
    {
        "verb": "ALERT",
        "usage": _fixed("ALERT <RULE>"),
        "local": True,
        "arg": True,
        "desc": "add an alert rule (ALERT alone lists the forms)",
        "parse": lambda rest, _c: {"type": "alert", "text": " ".join(rest)} if rest else _err(ALERT_USAGE),
    },
    {
        "verb": "UNALERT",
        "usage": _fixed("UNALERT <ID>"),
        "local": True,
        "arg": True,
        "desc": "remove an alert rule",
        "parse": lambda rest, _c: {"type": "unalert", "id": rest[0]} if rest else _err(VERB["UNALERT"]["usage"]()),
    },
    {
        "verb": "ACK",
        "usage": _fixed("ACK <ID|ALL>"),
        "local": True,
        "arg": True,
        "desc": "acknowledge an alert (its id), every alert of a rule (the rule id) or ALL",
        "parse": _ack,
    },
    {
        "verb": "ALERTS",
        "usage": _fixed("ALERTS"),
        "local": True,
        "desc": "open ALRT",
        "parse": lambda rest, c: _screen_of("ALRT", c),
    },
    {
        "verb": "REFRESH",
        "usage": _fixed("REFRESH [SCREEN] [TIER]"),
        "local": True,
        "desc": "recompute now: this screen or SCREEN, every tier or TIER (QUOTE · DAILY · HEAVY)",
        "parse": _refresh,
    },
    {
        "verb": "BUILD",
        "usage": lambda c: f"BUILD {_one_of([s['id'] for s in c.get('screens', []) if s.get('build')], 'SCREEN')}",
        "local": True,
        "arg": True,
        "desc": "rerun a screen's multi-minute build now (REFRESH only reloads it)",
        "when": lambda c: any(s.get("build") for s in c.get("screens", [])),
        "parse": lambda rest, _c: {"type": "build", "screen": rest[0] if rest else None},
    },
    {
        "verb": "CLOSED",
        "usage": _fixed("CLOSED"),
        "local": True,
        "desc": "show / hide closed positions",
        "parse": lambda rest, _c: {"type": "toggle-closed"},
    },
    _trade_verb("BUY", "add a buy: @ price per share, or = the total paid (fees in it); today unless a date ends it"),
    _trade_verb(
        "SELL", "add a sale: @ price per share, or = the total received; FEE x only for a fee not in the price"
    ),
    _trade_verb("BONUS", "add bonus shares (Saveback): @ price per share or = their booked value"),
    {
        "verb": "UNDO",
        "usage": _fixed("UNDO"),
        "local": True,
        "desc": "put your trades file back as it was before the last TRADES change (asks first; UNDO again redoes it)",
        "parse": lambda rest, _c: {"type": "undo"},
    },
    {
        "verb": "IMPORT",
        "usage": _fixed("IMPORT <FILE>"),
        "local": True,
        "arg": True,
        "desc": "read a CSV file into TRADES' paste box (Tab completes the path); previewed before anything is added",
        "parse": lambda rest, _c: {"type": "import"},
    },
    {
        "verb": "START",
        "usage": _fixed("START FRESH"),
        "local": True,
        "run": "START FRESH",
        "desc": "empty your trades file to enter your own (asks first; a backup is kept)",
        "parse": lambda rest, _c: {"type": "fresh"} if rest == ["FRESH"] else _err("START FRESH"),
    },
    {
        "verb": "FULL",
        "alias": ["FS"],
        "usage": _fixed("FULL"),
        "desc": "the title strip and the key bar off / on",
        "parse": lambda rest, _c: {"type": "fullscreen"},
    },
    {
        "verb": "CASH",
        "usage": _fixed("CASH"),
        "local": True,
        "desc": "open Cashflow Monitor in your browser",
        "when": lambda c: bool(c.get("cashflow")),
        "parse": lambda rest, c: (
            {"type": "goto", "url": c["cashflow"]}
            if c.get("cashflow")
            else _err("CASH: SET cashflow_url IN input/settings.toml")
        ),
    },
    {
        "verb": "HELP",
        "alias": ["?"],
        "usage": _fixed("HELP [SCREEN]"),
        "desc": "this help, or another screen's",
        "parse": lambda rest, c: _help_of(rest, c),
    },
    {
        "verb": "QUIT",
        "alias": sorted(QUIT - {"QUIT"}),
        "usage": _fixed("QUIT"),
        "desc": "leave (:q) — the server keeps running if it did before",
        "parse": lambda rest, _c: {"type": "quit"},
    },
]
VERB = {v["verb"]: v for v in VERBS}
BY_WORD = {w: v for v in VERBS for w in [v["verb"], *v.get("alias", [])]}


def parse(text, c: dict) -> dict:
    s = re.sub(r"\s+GO$", "", re.sub(r"\s*<GO>$", "", str(text).strip().upper()))
    words = s.split()
    if not words:
        return {"type": "noop"}
    head, rest = words[0], words[1:]
    verb = BY_WORD.get(head)
    if verb:
        return verb["parse"](rest, c)
    scr = next((x for x in c.get("screens", []) if x["id"] == head), None)
    if scr:
        if scr["status"] != "live" or not scr.get("param"):
            return _screen_of(scr["id"], c)
        p, what = (rest[0] if rest else None), scr.get("param_name") or "TICKER"
        if p and (p in c.get("params", {}).get(scr["id"], []) or TICKER.fullmatch(p)):
            return {"type": "screen", "id": scr["id"], "param": p}
        miss = "NOT A TICKER" if what == "TICKER" else scr.get("unknown_param") or f"UNKNOWN {scr['id']}"
        return _err(f"{p}: {miss}" if p else f"{scr['id']} <{what}>")
    for sid, names in c.get("params", {}).items():
        if head in names:
            return {"type": "screen", "id": sid, "param": head}
    if ticker_shaped(head) and any(s["id"] == "SEC" and s["status"] == "live" for s in c.get("screens", [])):
        return {"type": "screen", "id": "SEC", "param": head}
    return _err(f"UNKNOWN: {head} — NOT A COMMAND OR A TICKER IN YOUR BOOK")


def help_rows(c: dict) -> list:
    """HELP's COMMANDS table: the screens the registry serves, then every verb."""
    screens = c.get("screens", [])
    plain = sorted((s for s in screens if not s.get("param")), key=lambda s: 99 if s.get("fkey") is None else s["fkey"])
    rows = []
    if plain:
        rows.append({"cmd": " · ".join(s["id"] for s in plain), "desc": "open a screen (or its number key)"})
    for s in screens:
        if s.get("param") and s["status"] == "live":
            desc = (
                "open a security (a ticker alone opens it too)"
                if s["id"] == "SEC"
                else f"open a {str(s.get('title') or s['id']).lower()}"
            )
            rows.append({"cmd": f"{s['id']} <{s.get('param_name') or 'TICKER'}>", "desc": desc})
    for v in VERBS:
        if "when" not in v or v["when"](c):
            rows.append({"cmd": v["usage"](c), "desc": v["desc"]})
    return rows


def verb_items(c: dict) -> list:
    """The verbs the palette offers, less TARGET and BUILD (it lists TARGET <name> and BUILD <screen> one by one)."""
    return [
        {
            "label": v["usage"](c),
            "desc": v["desc"],
            "run": v.get("run") or (f"{v['verb']} " if v.get("arg") else v["verb"]),
            "fill": bool(v.get("arg")),
        }
        for v in VERBS
        if v["verb"] not in ("TARGET", "BUILD") and ("when" not in v or v["when"](c))
    ]


def enter_text(typed: str, c: dict, item, moved: bool) -> str:
    """What Enter runs, given the highlighted palette row and whether the user moved to it: the typed text, except a row
    moved to, or a partial ticker / unknown word whose top suggestion starts with it."""
    raw = str(typed).strip().upper()
    a = parse(raw, c)
    kind = a["type"]
    unknown = kind == "screen" and a.get("param") and a["param"] not in c.get("params", {}).get(a["id"], [])
    words = raw.split()
    verb = bool(words) and words[0] in BY_WORD
    pick = item and (
        moved or (not verb and (kind in ("soon", "error") or unknown) and item["label"].upper().startswith(raw))
    )
    return item["run"] if pick else typed


def fills_bar(typed: str, item, text: str) -> bool:
    return bool(item and item.get("fill")) and text == item["run"] and str(typed).strip().upper() != item["run"].strip()


# ── fuzzy.js ─────────────────────────────────────────────────────────────────────────────────────
def score(query, text) -> float:
    q, t = str(query).strip().upper(), str(text).upper()
    if not q:
        return float("-inf")
    if t == q:
        return 1000
    if t.startswith(q):
        return 800 - len(t)
    at = t.find(q)
    if at >= 0:
        return 600 - at
    start = gaps = 0
    for ch in q:
        j = t.find(ch, start)
        if j < 0:
            return float("-inf")
        gaps += j - start
        start = j + 1
    return 400 - gaps


def rank(query, items: list, keys=lambda x: [x["label"]], limit: int = 8) -> list:
    scored = [(max(score(query, k) - 300 * j for j, k in enumerate(keys(it))), n, it) for n, it in enumerate(items)]
    hits = [x for x in scored if x[0] > float("-inf")]
    hits.sort(key=lambda x: (-x[0], x[1]))
    return [it for _, _, it in hits[:limit]]


# ── watch.js ─────────────────────────────────────────────────────────────────────────────────────
def fold(s) -> str:
    text = unicodedata.normalize("NFKD", "" if s is None else str(s))
    return "".join(c for c in text if not 0x300 <= ord(c) <= 0x36F).upper().strip()


def resolve_watch(query: str, results: list) -> dict:
    rows = results or []
    if not rows:
        return {"kind": "none"}
    q = fold(query)
    exact = next((r for r in rows if fold(r["ticker"]) == q), rows[0] if len(rows) == 1 else None)
    if exact:
        return {"kind": "one", "ticker": exact["ticker"], "name": exact.get("name")}
    named = [r for r in rows if fold(r.get("name")) == q]
    if len(named) == 1:
        return {"kind": "one", "ticker": named[0]["ticker"], "name": named[0].get("name")}
    return {"kind": "several", "rows": rows}


def watch_items(results: list) -> list:
    return [
        {
            "label": f"WATCH {r['ticker']}",
            "run": f"WATCH {r['ticker']}",
            "desc": " · ".join(x for x in (r.get("name"), r.get("sector"), r.get("country")) if x and x != "—"),
        }
        for r in results or []
    ]


# ── route.js ─────────────────────────────────────────────────────────────────────────────────────
BACK_MAX = 20


def key_of(sid: str, param: str | None) -> str:
    return f"{sid}~{param}" if param else sid


def split_key(key: str) -> tuple:
    sid, _, param = key.partition("~")
    return sid, param or None


def push_back(stack: list, came_from: str | None) -> list:
    if not came_from:
        return stack
    return ([k for k in stack if k != came_from] + [came_from])[-BACK_MAX:]


def pop_back(stack: list, current: str) -> tuple:
    rest = [k for k in stack if k != current]
    return (rest[-1] if rest else "PORT"), rest[:-1]


# ── tablesort.js, status.js ──────────────────────────────────────────────────────────────────────
def _missing(v) -> bool:
    return v is None or (isinstance(v, float) and v != v)


def sort_rows(rows: list, col: str, direction: str = "desc") -> list:
    """Stable, missing values last in both directions."""
    sgn = 1 if direction == "asc" else -1

    def cmp(a, b):
        (x, i), (y, j) = (a[0].get(col), a[1]), (b[0].get(col), b[1])
        if _missing(x) or _missing(y):
            return (i - j) if _missing(x) and _missing(y) else 1 if _missing(x) else -1
        if (
            isinstance(x, (int, float))
            and isinstance(y, (int, float))
            and not isinstance(x, bool)
            and not isinstance(y, bool)
        ):
            d = x - y
        else:
            s, t = fold(js_str(x)), fold(js_str(y))
            d = (s > t) - (s < t)
        return (d * sgn > 0) - (d * sgn < 0) or i - j

    return [r for r, _ in sorted(((r, i) for i, r in enumerate(rows)), key=cmp_to_key(cmp))]


def next_sort_col(cols: list, current: str, step: int, hidden=frozenset()) -> str:
    keys = [c["k"] for c in cols if c.get("fmt") != "spark" and c["k"] not in hidden]
    if not keys:
        return current
    i = keys.index(current) if current in keys else 0
    return keys[(i + step) % len(keys)]


KEEP = 90


def fit_hidden(items: list, room: int, gap: int) -> set:
    """The status line's items that do not fit `room`: least important first, ties from the right; those that fit again
    in the room left come back; alerts and LIVE / OFFLINE (p >= KEEP) never go. [{p, w}] -> set of indexes."""

    def width(shown):
        return sum(items[i]["w"] for i in shown) + gap * max(0, len(shown) - 1)

    shown = list(range(len(items)))
    hidden = []
    for i in sorted(shown, key=lambda i: (items[i]["p"], -i)):
        if width(shown) <= room or items[i]["p"] >= KEEP:
            break
        shown.remove(i)
        hidden.append(i)
    for i in sorted(hidden, key=lambda i: (-items[i]["p"], i)):
        if width(sorted([*shown, i])) <= room:
            shown = sorted([*shown, i])
            hidden.remove(i)
    return set(hidden)
