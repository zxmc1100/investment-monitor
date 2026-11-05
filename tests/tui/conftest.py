"""A fake server for the app tests: fictional trades, every write recorded, nothing on the network."""

import copy

import pytest

from monitor.tui.app import App
from monitor.tui.client import ApiError

X = [1704067200 + 86400 * i for i in range(400)]
SCREENS = [
    {"id": "PORT", "title": "Portfolio Monitor", "fkey": 1, "status": "live", "param": False, "tiers": ["quote"]},
    {"id": "OPT", "title": "Optimizer", "fkey": 2, "status": "live", "param": False, "tiers": ["daily"]},
    {"id": "ALRT", "title": "Alerts", "fkey": 5, "status": "live", "param": False, "tiers": []},
    {"id": "TRADES", "title": "Trades", "fkey": 6, "status": "live", "param": False, "tiers": ["quote"]},
    {
        "id": "SEC",
        "title": "Security",
        "fkey": None,
        "status": "live",
        "param": True,
        "param_name": "TICKER",
        "unknown_param": "NOT A TRADEABLE TICKER",
    },
    {"id": "MOM", "title": "Momentum", "fkey": 7, "status": "live", "param": False, "build": "~10 MIN"},
]
REGISTRY = {
    "screens": SCREENS,
    "quote_interval_s": 60,
    "params": {"SEC": ["ACME.DE", "GLOBEX.F"]},
    "targets": ["HRP", "EQUAL"],
    "cashflow_url": None,
    "settings_error": None,
}


def positions(n=30):
    names = ["ACME.DE", "GLOBEX.F", "INITECH.DE", "UMBRELLA.F"] + [f"TK{i}.DE" for i in range(n - 4)]
    rows = [
        {"tkr": t, "name": f"Company {t}", "wt": 100 - i, "pnl": 50 - i * 7.5, "spark": [1, 2, 3, 2 + i]}
        for i, t in enumerate(names)
    ]
    rows.append({"tkr": "GONE.DE", "name": "Sold Out", "wt": None, "pnl": 3.0, "_closed": True})
    return rows


PORT = {
    "screen": "PORT",
    "title": "Portfolio Monitor",
    "context": {"text": "4 POS"},
    "meta": {"tiers": {"quote": "2026-03-10T10:00:00"}, "stale": {"INITECH.DE": 1.0}, "warn": ["CCY USD: X"]},
    "help": [{"h": "ROI", "body": "Total P&L over every euro."}],
    "live": {"running": []},
    "panels": [
        {
            "id": "summary",
            "n": 1,
            "title": "SUMMARY",
            "type": "kpi",
            "span": 6,
            "items": [{"k": "VALUE", "v": 1000.5, "fmt": "eur"}, {"k": "DAY %", "v": -1.5, "fmt": "pct+:2"}],
            "years": [{"label": "2026", "v": 1.0, "v2": 2.0, "fmt": "pct+"}],
            "context": {"text": "AS OF 10:00"},
        },
        {
            "id": "acct",
            "n": 2,
            "title": "ACCOUNTING",
            "type": "ledger",
            "span": 6,
            "lines": [
                {"label": "In", "v": 5.0, "fmt": "eur", "op": ""},
                {"sep": True},
                {"label": "Out", "v": -2.0, "fmt": "eur+", "op": "=", "strong": True},
            ],
        },
        {
            "id": "positions",
            "n": 3,
            "title": "POSITIONS",
            "type": "table",
            "span": 7,
            "rows_span": 2,
            "key": "tkr",
            "sort": ["wt", "desc"],
            "drives": "posval",
            "cols": [
                {"k": "tkr", "label": "TKR", "fmt": "tkr"},
                {"k": "name", "label": "NAME", "fmt": "text"},
                {"k": "wt", "label": "WT%", "fmt": "pct", "align": "r"},
                {"k": "pnl", "label": "P&L", "fmt": "eur+", "align": "r"},
                {"k": "spark", "label": "1Y", "fmt": "spark", "lo": True},
            ],
            "rows": positions(),
            "total": {"tkr": "TOTAL", "wt": 100.0},
        },
        {
            "id": "roi",
            "n": 4,
            "title": "ROI",
            "type": "chart",
            "span": 5,
            "yfmt": "pct+",
            "legend": "rank",
            "ranges": ["1M", "YTD", "ALL"],
            "range": "YTD",
            "x": X,
            "inv": [100.0 + i for i in range(400)],
            "series": [
                {
                    "name": "YOU",
                    "role": "primary",
                    "kind": "line",
                    "y": [i / 10 for i in range(400)],
                    "twr": [1 + i / 1000 for i in range(400)],
                    "cash": [0.0] * 400,
                },
                {
                    "name": "SPX",
                    "role": "bench",
                    "kind": "line",
                    "y": [i / 20 for i in range(400)],
                    "twr": [1 + i / 2000 for i in range(400)],
                },
            ],
        },
        {
            "id": "posval",
            "n": 5,
            "title": "{key} · VALUE",
            "type": "chart",
            "span": 5,
            "yfmt": "eur",
            "follows": "positions",
            "series_by_key": {
                "ACME.DE": {
                    "x": X,
                    "series": [
                        {"name": "ACME.DE", "role": "primary", "kind": "line", "y": [float(i) for i in range(400)]},
                        {
                            "name": "BUY",
                            "role": "buy",
                            "kind": "markers",
                            "y": [5.0 if i == 10 else None for i in range(400)],
                        },
                    ],
                }
            },
        },
        {
            "id": "alloc",
            "n": 6,
            "title": "ALLOCATION",
            "type": "bars",
            "span": 6,
            "items": [{"label": "Tech", "v": 60.0, "fmt": "pct"}, {"label": "Other", "v": 0, "fmt": "pct"}],
            "sectors": [{"label": "Tech", "v": 60.0, "fmt": "pct", "text": "ACME.DE 40 · GLOBEX.F 20"}],
            "countries": [{"label": "Germany", "v": 60.0, "fmt": "pct"}],
        },
        {
            "id": "corr",
            "n": 7,
            "title": "CORRELATION",
            "type": "heatmap",
            "span": 6,
            "labels": ["AA", "BB"],
            "cells": [[1.0, -0.5], [-0.5, 1.0]],
            "fmt": "num:2",
        },
    ],
}
FIELDS = [
    {"k": "ticker", "label": "TICKER", "kind": "ticker", "placeholder": "name"},
    {"k": "action", "label": "ACTION", "kind": "choice", "options": ["buy", "sell", "bonus"]},
    {"k": "shares", "label": "SHARES", "kind": "number", "placeholder": "how many"},
    {"k": "pps", "label": "€ / SHARE", "kind": "number", "pair": "total", "placeholder": "each"},
    {"k": "total", "label": "TOTAL €", "kind": "number", "pair": "pps", "placeholder": "all"},
    {"k": "date", "label": "DATE", "kind": "date", "placeholder": "today"},
    {"k": "fee", "label": "FEE €", "kind": "number", "placeholder": "fee"},
]
TXN_COLS = [
    {"k": "date", "label": "DATE", "fmt": "date"},
    {"k": "tkr", "label": "TKR", "fmt": "tkr"},
    {"k": "action", "label": "ACTION", "fmt": "side"},
    {"k": "shares", "label": "SHARES", "fmt": "qty", "align": "r"},
    {"k": "total", "label": "TOTAL €", "fmt": "eur", "align": "r"},
]
TXNS = [
    {
        "id": "r1",
        "when": "2",
        "date": "2026-02-01",
        "tkr": "ACME.DE",
        "action": "BUY",
        "shares": 4.0,
        "pps": 240.0,
        "total": 961.0,
    },
    {
        "id": "r2",
        "when": "1",
        "date": "2026-01-01",
        "tkr": "GLOBEX.F",
        "action": "SELL",
        "shares": 1.0,
        "pps": 50.0,
        "total": 50.0,
    },
]
TRADES = {
    "screen": "TRADES",
    "title": "Trades",
    "context": {"text": "2 TRADES"},
    "meta": {},
    "help": [],
    "etag": "e1",
    "error": None,
    "lines": 2,
    "actions": [],
    "panels": [
        {
            "id": "fresh",
            "n": 4,
            "title": "EXAMPLE",
            "type": "banner",
            "span": 12,
            "text": "EXAMPLE PORTFOLIO — START FRESH clears it",
            "run": "START FRESH",
            "button": "START FRESH",
            "context": {"text": "A BACKUP IS KEPT"},
        },
        {
            "id": "add",
            "n": 1,
            "title": "ADD",
            "type": "form",
            "span": 5,
            "form": "trade",
            "fields": FIELDS,
            "book": [{"ticker": "ACME.DE", "name": "Acme"}],
        },
        {"id": "paste", "n": 2, "title": "PASTE", "type": "paste", "span": 7},
        {
            "id": "txns",
            "n": 3,
            "title": "TRANSACTIONS",
            "type": "table",
            "span": 12,
            "key": "id",
            "sort": ["when", "desc"],
            "cols": TXN_COLS,
            "rows": TXNS,
            "edit": "add",
            "remove": True,
            "empty": "NO TRADES YET",
        },
    ],
}
OPT = {
    "screen": "OPT",
    "title": "Optimizer",
    "meta": {},
    "help": [],
    "panels": [
        {
            "id": "frontier",
            "n": 1,
            "title": "FRONTIER",
            "type": "scatter",
            "span": 7,
            "xfmt": "pct",
            "yfmt": "pct",
            "series": [
                {"name": "cloud", "role": "cloud", "kind": "dots", "x": [10, 12, 14], "y": [5, 6, 7]},
                {"name": "front", "role": "frontier", "kind": "line", "x": [10, 20], "y": [5, 15]},
                {"name": "NOW", "role": "now", "kind": "marker", "x": [15], "y": [9]},
                {"name": "HRP", "role": "target", "kind": "marker", "x": [15.05], "y": [9.02]},
            ],
        },
        {
            "id": "portfolios",
            "n": 2,
            "title": "PORTFOLIOS",
            "type": "table",
            "span": 5,
            "key": "id",
            "sort": ["id", "asc"],
            "cursor": "HRP",
            "enter": "TARGET {key}",
            "cols": [{"k": "id", "label": "ID", "fmt": "tkr"}],
            "rows": [{"id": "EQUAL"}, {"id": "HRP"}],
        },
        {
            "id": "path",
            "n": 3,
            "title": "PATH",
            "type": "chart",
            "span": 12,
            "yfmt": "pct+",
            "legend": "rank",
            "follows": "portfolios",
            "x": X[:50],
            "series": [],
            "horizons": ["6M", "1Y"],
            "horizon": "1Y",
            "today_idx": 30,
            "vlines": [X[30]],
            "vlabels": ["TODAY"],
            "series_by_key": {
                "HRP": {
                    "series": [
                        {
                            "name": "HRP",
                            "kind": "line",
                            "role": "port",
                            "y": [float(i) for i in range(31)] + [None] * 19,
                        },
                        {
                            "name": "HRP →",
                            "kind": "line",
                            "role": "port",
                            "dash": True,
                            "nolegend": True,
                            "at": 30,
                            "y": [30.0 + i for i in range(20)],
                        },
                        {
                            "name": "band",
                            "kind": "band",
                            "color": "#4fc3f7",
                            "at": 30,
                            "lo": [29.0] * 20,
                            "hi": [40.0] * 20,
                        },
                    ]
                }
            },
        },
        {
            "id": "ticket",
            "n": 4,
            "title": "TICKET → {key}",
            "type": "table",
            "span": 12,
            "key": "t",
            "follows": "portfolios",
            "sort": ["t", "asc"],
            "cols": [{"k": "t", "label": "T", "fmt": "tkr"}],
            "rows": [],
            "rows_by_key": {
                "HRP": {"rows": [{"t": "ACME.DE"}], "context": "1 ORDER"},
                "EQUAL": {"rows": [], "context": "NOTHING TO DO"},
            },
        },
    ],
}


class FakeApi:
    def __init__(self):
        self.screens = {
            "PORT": PORT,
            "TRADES": TRADES,
            "OPT": OPT,
            "ALRT": {"screen": "ALRT", "title": "Alerts", "meta": {}, "panels": []},
        }
        self.screens = copy.deepcopy(self.screens)
        self.calls = []
        self.events = None
        self.fail_next = None
        self.undo = {"what": "ADD BUY", "ready": True, "at": "2026-03-10T11:22:33"}

    def _record(self, name, *args):
        self.calls.append((name, *args))
        if self.fail_next:
            e, self.fail_next = self.fail_next, None
            raise e

    def registry(self, timeout=0):
        return copy.deepcopy(REGISTRY)

    def subscribe(self, fn):
        self.events = fn

    def screen(self, sid, param=None):
        self._record("screen", sid, param)
        if sid == "SEC":
            if param == "NOPE.DE":
                raise ApiError(404, f"{param}: NOT A TRADEABLE TICKER")
            return {
                "screen": "SEC",
                "param": param,
                "cold": True,
                "reason": "NO DATA YET",
                "live": {"running": ["daily"]},
            }
        return copy.deepcopy(self.screens[sid])

    def ensure(self, sid, param=None):
        return {"jobs": [], "live": {}}

    def alerts(self):
        return {"active": [{"id": "a1", "down": True}]}

    def jobs(self):
        return {"jobs": []}

    def lookup(self, q):
        self.calls.append(("lookup", q))
        return [
            {"ticker": "ACU.DE", "name": "Acu Rhein", "country": "Germany"},
            {"ticker": "ACU2", "name": "Acu Two", "country": "USA"},
        ]

    def refresh(self, sid, tier=None, param=None):
        self._record("refresh", sid, tier, param)
        return {"jobs": [{"tier": "quote"}]}

    def build(self, sid):
        self._record("build", sid)
        return {"jobs": []}

    def prefs(self):
        return {"target": "HRP"}

    def set_target(self, name):
        self._record("target", name)
        return {"target": name}

    def watch(self, t):
        self._record("watch", t)
        return []

    def unwatch(self, t):
        self._record("unwatch", t)
        return []

    def add_alert(self, text):
        self._record("alert", text)
        return {"id": "r9", "text": text}

    def del_alert(self, rid):
        self._record("unalert", rid)
        return {}

    def ack(self, body):
        self._record("ack", body)
        return {"acked": 1}

    def trades(self):
        return {
            "etag": "e-now",
            "rows": [
                {"id": "r1", "date": "2026-02-01", "ticker": "ACME.DE", "action": "buy", "shares": 4.0, "price": 961.0}
            ],
            "error": None,
            "lines": 1,
            "undo": self.undo,
        }

    def add_trade(self, etag, trade):
        self._record("add_trade", etag, trade)
        return {"etag": "e2", "text": "BUY 4 ACME.DE", "warnings": []}

    def edit_trade(self, rid, etag, trade):
        self._record("edit_trade", rid, etag, trade)
        return {"etag": "e2", "text": "EDITED", "warnings": []}

    def del_trade(self, rid, etag):
        self._record("del_trade", rid, etag)
        return {"etag": "e2", "text": "GONE"}

    def preview_trades(self, text, mode):
        self._record("preview", text, mode)
        return {
            "rows": [
                {
                    "line": 1,
                    "trade": {
                        "date": "2026-01-01",
                        "ticker": "ACME.DE",
                        "action": "buy",
                        "shares": 1.0,
                        "pps": 2.0,
                        "price": 2.0,
                    },
                },
                {"line": 2, "error": "SHARES: bad", "text": "x"},
            ],
            "ok": 1,
            "bad": 1,
            "delimiter": ",",
            "decimal": "point",
            "header": False,
        }

    def import_trades(self, etag, text, mode):
        self._record("import", etag, text, mode)
        return {"etag": "e2", "added": 1, "skipped": 1, "left": "x"}

    def reset_trades(self, etag):
        self._record("reset", etag)
        return {"etag": "e2", "removed": 1}

    def undo_trades(self, etag):
        self._record("undo", etag)
        return {"etag": "e2", "text": "UNDID"}

    def writes(self, name):
        return [c for c in self.calls if c[0] == name]


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def api():
    return FakeApi()


@pytest.fixture
def app(api, clock):
    a = App(api, sync=True, strict=True)
    a.now = clock
    a.size = (160, 48)
    a.start()
    a.draw()
    return a


def press(app, *keys):
    """Keys as the loop gives them, with the screen drawn after each (what a key acts on is what it sees)."""
    for k in keys:
        app.on_input(k)
        app.drain()
        app.draw()


def typed(app, text):
    press(app, *text)


def screen(app) -> str:
    return "\n".join("".join(r) for r in app.canvas.ch)
