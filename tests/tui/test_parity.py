"""The TUI's pure modules answer as web/app's do: the same cases through node and through Python. A change on one side only
fails here, so the two clients cannot drift apart."""

import json
import math
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest

from monitor.tui import app as A
from monitor.tui import cmd as C
from monitor.tui import fmt as F
from monitor.tui import panels as P
from monitor.tui import ranges as R
from monitor.tui import trades as T

HERE = Path(__file__).resolve().parent
TODAY = "2026-03-10"
pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")

SCREENS = [
    {"id": "PORT", "title": "Portfolio Monitor", "fkey": 1, "status": "live", "param": False},
    {"id": "OPT", "title": "Optimizer", "fkey": 2, "status": "live", "param": False},
    {
        "id": "SEC",
        "title": "Security",
        "fkey": None,
        "status": "live",
        "param": True,
        "param_name": "TICKER",
        "unknown_param": "NOT A TRADEABLE TICKER",
    },
    {
        "id": "LAB",
        "title": "Strategy Lab",
        "fkey": None,
        "status": "live",
        "param": True,
        "param_name": "ID",
        "unknown_param": "UNKNOWN STRATEGY",
    },
    {"id": "MOM", "title": "Momentum", "fkey": 5, "status": "live", "param": False, "build": "~10 MIN"},
    {"id": "SOON", "title": "Later", "fkey": 9, "status": "soon", "param": False},
    {"id": "ALRT", "title": "Alerts", "fkey": 7, "status": "live", "param": False},
]
CTX = {
    "screens": SCREENS,
    "params": {"SEC": ["ACME.DE", "GLOBEX.F"], "LAB": ["EDGE1"]},
    "targets": ["HRP", "EQUAL"],
    "cashflow": None,
}
JS_CTX = {**CTX, "private": []}
X = [1700000000 + 86400 * i for i in range(8)]
S1 = {
    "name": "YOU",
    "role": "primary",
    "y": [0.0, 1.0, None, 3.0, 2.5, 4.0, 5.0, 6.0],
    "twr": [1.0, 1.01, None, 1.03, 1.02, 1.04, 1.05, 1.06],
    "cash": [0, 0, 0, 10, 10, 10, 20, 20],
}
S2 = {
    "name": "SPX",
    "role": "bench",
    "y": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0],
    "twr": [1.0, 1.02, 1.03, 1.04, 1.05, 1.06, 1.07, 1.08],
}
INV = [100, 100, 150, 150, 200, 200, 200, 250]
ROWS = [
    {"t": "b", "v": 2.5, "n": "Beta"},
    {"t": "a", "v": None, "n": "alpha"},
    {"t": "c", "v": 10, "n": "Gamma"},
    {"t": "d", "v": 2.5, "n": "delta"},
]
COLS = [{"k": "t", "fmt": "tkr"}, {"k": "v", "fmt": "num:2"}, {"k": "s", "fmt": "spark"}, {"k": "n", "fmt": "text"}]
TABLE_COLS = [
    {"k": "tkr", "label": "TKR", "fmt": "tkr"},
    {"k": "name", "label": "NAME", "fmt": "text"},
    {"k": "rule", "label": "RULE"},
]
TABLE_ROWS = [
    {"tkr": "ACME.DE", "name": "Acme", "rule": "a much longer rule text"},
    {"tkr": "GLOBEX.F", "name": "Globex Corporation", "rule": "short"},
]
BOOK = [{"ticker": "ACME.DE", "name": "Acme"}, {"ticker": "GLOBEX.F", "name": "Globex"}]
FOUND = [
    {"ticker": "ACU", "name": "Acu US", "country": "USA"},
    {"ticker": "ACUR.DE", "name": "Acu Rhein", "country": "Germany"},
]
TXNS = [
    {"id": "x1", "date": "2026-03-01", "tkr": "ACME.DE", "action": "BUY", "shares": 4, "total": 961.0, "pps": 240.0},
    {
        "id": "x2",
        "date": "2026-02-01",
        "tkr": "GLOBEX.F",
        "action": "SELL",
        "shares": 1.5,
        "total": 300.5,
        "pps": 200.33,
    },
]
PAYLOAD_A = {
    "screen": "X",
    "panels": [
        {"id": "k", "type": "kpi", "items": [{"k": "A", "v": 1.0}, {"k": "B", "v": 2.0}]},
        {
            "id": "t",
            "type": "table",
            "key": "tkr",
            "cols": [{"k": "v"}, {"k": "s", "fmt": "spark"}],
            "rows": [{"tkr": "A", "v": 1, "s": [1]}],
            "total": {"v": 1},
        },
        {"id": "l", "type": "ledger", "lines": [{"label": "L", "v": 1}, {"sep": True}]},
        {"id": "h", "type": "heatmap", "labels": ["a", "b"], "cells": [[1, 0.5], [0.5, 1]]},
    ],
}
PAYLOAD_B = {
    "screen": "X",
    "panels": [
        {"id": "k", "type": "kpi", "items": [{"k": "A", "v": 1.0 + 1e-12}, {"k": "B", "v": 2.5}]},
        {
            "id": "t",
            "type": "table",
            "key": "tkr",
            "cols": [{"k": "v"}, {"k": "s", "fmt": "spark"}],
            "rows": [{"tkr": "A", "v": 3, "s": [2]}],
            "total": {"v": 3},
        },
        {"id": "l", "type": "ledger", "lines": [{"label": "L", "v": 9}, {"sep": True}]},
        {"id": "h", "type": "heatmap", "labels": ["a", "b"], "cells": [[1, 0.6], [0.5, 1]]},
    ],
}
INF = float("inf")


def _fmt_cases():
    vals = [
        0,
        1,
        -1,
        0.5,
        0.125,
        2.675,
        -2.675,
        1234.5678,
        -1234.5678,
        1e6,
        0.00001,
        None,
        "x",
        13.513513,
        4,
        0.15,
        33.33335,
        240,
    ]
    specs = [
        "eur",
        "eur:0",
        "eur+",
        "pct",
        "pct:2",
        "pct+",
        "pct+:2",
        "pct+r",
        "num",
        "num:4",
        "num+",
        "int",
        "bp+",
        "mult",
        "qty",
        "px",
        "text",
        "tkr",
    ]
    out = {}
    for i, v in enumerate(vals):
        for s in specs:
            out[f"fmt {i} {s}"] = (("fmt", "fmt", v, s), lambda v=v, s=s: dict(zip(("text", "cls"), F.fmt(v, s))))
    for v, s in (
        ("H", "mark"),
        ("HW", "mark"),
        ("Q", "mark"),
        ("2026-03-09", "date"),
        ("BUY", "side"),
        ("SELL", "side"),
        ("BONUS", "side"),
    ):
        out[f"fmt {v} {s}"] = (("fmt", "fmt", v, s), lambda v=v, s=s: dict(zip(("text", "cls"), F.fmt(v, s))))
    out["date"] = (("fmt", "fmtDate", "2026-09-29T10:00"), lambda: F.fmt_date("2026-09-29T10:00"))
    return out


def _range_cases():
    base = {"y": 0}
    out = {
        "start 1M": (("ranges", "rangeStart", X, "1M"), lambda: R.range_start(X, "1M")),
        "start YTD": (("ranges", "rangeStart", X, "YTD"), lambda: R.range_start(X, "YTD")),
        "default": (
            ("ranges", "defaultRange", {"range": "YTD", "ranges": ["1M", "YTD"]}),
            lambda: R.default_range({"range": "YTD", "ranges": ["1M", "YTD"]}),
        ),
        "canNorm": (("ranges", "canNorm", [S1, S2]), lambda: R.can_norm([S1, S2])),
        "normalize": (("ranges", "normalize", [S1, S2]), lambda: R.normalize([S1, S2])),
        "normalize0": (("ranges", "normalize", [S1, S2], True), lambda: R.normalize([S1, S2], True)),
        "roi": (("ranges", "windowRoi", [S1, S2], INV), lambda: R.window_roi([S1, S2], INV)),
        "mwr": (
            ("ranges", "windowMwr", [S1, S2], INV, X, X[0] - 3600),
            lambda: R.window_mwr([S1, S2], INV, X, X[0] - 3600),
        ),
        "mwr0": (
            ("ranges", "windowMwr", [S1, S2], INV, X, X[0], True),
            lambda: R.window_mwr([S1, S2], INV, X, X[0], True),
        ),
        "modes": (("ranges", "chartModes", {"inv": INV}, [S1, S2]), lambda: R.chart_modes({"inv": INV}, [S1, S2])),
        "modes2": (("ranges", "chartModes", {}, [S1, S2]), lambda: R.chart_modes({}, [S1, S2])),
        "modes3": (("ranges", "chartModes", {}, [base]), lambda: R.chart_modes({}, [base])),
        "valueAt": (("ranges", "valueAt", S1["y"], 2), lambda: R.value_at(S1["y"], 2)),
        "rebase": (
            ("ranges", "rebase", [{"name": "a", "y": [None, 2, 4]}, {"name": "b", "y": [None, None, 5]}]),
            lambda: R.rebase([{"name": "a", "y": [None, 2, 4]}, {"name": "b", "y": [None, None, 5]}]),
        ),
        "expand": (
            (
                "ranges",
                "expandAt",
                [
                    {"name": "f", "at": 2, "y": [1, 2]},
                    {"name": "b", "at": 1, "lo": [0], "hi": [1]},
                    {"name": "p", "y": [9]},
                ],
                5,
            ),
            lambda: R.expand_at(
                [
                    {"name": "f", "at": 2, "y": [1, 2]},
                    {"name": "b", "at": 1, "lo": [0], "hi": [1]},
                    {"name": "p", "y": [9]},
                ],
                5,
            ),
        ),
        "cut": (("ranges", "cutHorizon", X, [S2], 3, 2), lambda: R.cut_horizon(X, [S2], 3, 2)),
        "rest": (("ranges", "legendRest", 10, 4, 1), lambda: R.legend_rest(10, 4, 1)),
        "rest0": (("ranges", "legendRest", 10, None), lambda: R.legend_rest(10, None)),
        "legendValue": (
            ("ranges", "legendValue", [S1, {"name": "NOW →", "y": [9] * 8}], S1, 6, 3),
            lambda: R.legend_value([S1, {"name": "NOW →", "y": [9] * 8}], S1, 6, 3),
        ),
        "extent": (("scale", "extent", [1, None, 3]), lambda: R.extent([1, None, 3])),
        "extent1": (("scale", "extent", [2]), lambda: R.extent([2])),
        "ticks": (("scale", "niceTicks", 3.2, 47.1, 5), lambda: R.nice_ticks(3.2, 47.1, 5)),
        "ticks2": (("scale", "niceTicks", -0.4, 0.9, 6), lambda: R.nice_ticks(-0.4, 0.9, 6)),
        "dec": (("scatter", "tickDecimals", [2.5, 5]), lambda: R.tick_decimals([2.5, 5])),
    }
    for name, start, anchor in (("a", X[2], False), ("b", X[2], True), ("c", X[-1] + 9, False), ("d", -1, True)):

        def run(start=start, anchor=anchor):
            return R.slice_from(X, [S1, S2], start, INF, anchor)

        out[f"slice {name}"] = (("ranges", "sliceFrom", X, [S1, S2], start, "__INF__", anchor), run)
    return out


def _cmd_cases():
    inputs = [
        "",
        "port",
        "PORT GO",
        "port <GO>",
        "opt",
        "soon",
        "sec",
        "sec acme.de",
        "sec ??",
        "lab",
        "lab edge1",
        "lab x y",
        "acme.de",
        "1913.HK",
        "hlep",
        "edge1",
        "target",
        "target hrp",
        "target nope",
        "watch",
        "watch acme corp",
        "unwatch",
        "unwatch acme.de",
        "alert",
        "alert acme.de > 10",
        "unalert",
        "unalert r1",
        "ack",
        "ack all",
        "ack a1",
        "alerts",
        "refresh",
        "refresh quote",
        "refresh port",
        "refresh port daily",
        "build",
        "build mom",
        "closed",
        "buy",
        "buy acme.de 4 @ 240",
        "buy acme.de 4@240",
        "sell acme.de 1 = 99.5 2026-01-02",
        "buy acme.de 4 @ 240 fee 1",
        "buy acme.de 4 @ 240 fee",
        "bonus acme.de 1 @ 5 01.02.2026",
        "buy acme.de 4",
        "undo",
        "start",
        "start fresh",
        "start over",
        "full",
        "fs",
        "cash",
        "help",
        "?",
        "help opt",
        "help sec",
        "help sec acme.de",
        "help nope",
    ]
    out = {}
    for t in inputs:
        out[f"parse {t!r}"] = (("cmd", "parse", t, JS_CTX), lambda t=t: C.parse(t, CTX))
        out[f"enter {t!r}"] = (
            ("cmd", "enterText", t, JS_CTX, None, False),
            lambda t=t: C.enter_text(t, CTX, None, False),
        )
    item = {"label": "ACME.DE", "run": "ACME.DE"}
    for t, moved in (("ac", False), ("ac", True), ("alert", False), ("hlep", False), ("acme.de", False)):
        out[f"enter item {t} {moved}"] = (
            ("cmd", "enterText", t, JS_CTX, item, moved),
            lambda t=t, moved=moved: C.enter_text(t, CTX, item, moved),
        )
    fill = {"run": "WATCH ", "fill": True}
    for t in ("wat", "watch"):
        out[f"fills {t}"] = (("cmd", "fillsBar", t, fill, "WATCH "), lambda t=t: C.fills_bar(t, fill, "WATCH "))
    return out


def _misc_cases():
    items = [
        {"label": "PORT", "desc": "Portfolio"},
        {"label": "OPT", "desc": "Optimizer"},
        {"label": "POSITIONS", "desc": "x"},
        {"label": "ZZZ", "desc": "port"},
    ]
    out = {
        "rank": (("fuzzy", "rank", "po", items), lambda: C.rank("po", items)),
        "score": (("fuzzy", "score", "oz", "OPTIMIZER"), lambda: C.score("oz", "OPTIMIZER")),
        "watch one": (
            (
                "watch",
                "resolveWatch",
                "acme",
                [{"ticker": "ACME.DE", "name": "Acme"}, {"ticker": "A2", "name": "Acme (ADR)"}],
            ),
            lambda: C.resolve_watch(
                "acme", [{"ticker": "ACME.DE", "name": "Acme"}, {"ticker": "A2", "name": "Acme (ADR)"}]
            ),
        ),
        "watch several": (
            ("watch", "resolveWatch", "ac", [{"ticker": "B1", "name": "Ac 1"}, {"ticker": "B2", "name": "Ac 2"}]),
            lambda: C.resolve_watch("ac", [{"ticker": "B1", "name": "Ac 1"}, {"ticker": "B2", "name": "Ac 2"}]),
        ),
        "watch none": (("watch", "resolveWatch", "ac", []), lambda: C.resolve_watch("ac", [])),
        "watch items": (
            ("watch", "watchItems", [{"ticker": "B1", "name": "Ac", "sector": "—", "country": "DE"}]),
            lambda: C.watch_items([{"ticker": "B1", "name": "Ac", "sector": "—", "country": "DE"}]),
        ),
        "fold": (("watch", "fold", " Crème "), lambda: C.fold(" Crème ")),
        "ticks months": (
            ("fmt", "timeTicks", [1704067200 + 2629800 * i for i in range(14)], 2629800),
            lambda: F.time_ticks([1704067200 + 2629800 * i for i in range(14)], 2629800),
        ),
        "ticks days": (
            ("fmt", "timeTicks", [1704067200 + 86400 * i for i in range(3)], 86400),
            lambda: F.time_ticks([1704067200 + 86400 * i for i in range(3)], 86400),
        ),
        "ticks years": (
            ("fmt", "timeTicks", [1704067200, 1735689600], 366 * 86400),
            lambda: F.time_ticks([1704067200, 1735689600], 366 * 86400),
        ),
        "sort next": (("tablesort", "nextSortCol", COLS, "t", -1), lambda: C.next_sort_col(COLS, "t", -1)),
        "sort next hidden": (
            ("tablesort", "nextSortCol", COLS, "t", 1, {"__set__": ["v"]}),
            lambda: C.next_sort_col(COLS, "t", 1, {"v"}),
        ),
        "fit": (
            (
                "status",
                "fitHidden",
                [{"p": 10, "w": 20}, {"p": 100, "w": 8}, {"p": 20, "w": 12}, {"p": 90, "w": 9}],
                30,
                2,
            ),
            lambda: sorted(
                C.fit_hidden([{"p": 10, "w": 20}, {"p": 100, "w": 8}, {"p": 20, "w": 12}, {"p": 90, "w": 9}], 30, 2)
            ),
        ),
        "shares": (
            ("table", "textShares", TABLE_COLS, TABLE_ROWS),
            lambda: {k: round(v * 1000) / 10 for k, v in P.text_shares(TABLE_COLS, TABLE_ROWS).items()},
        ),
        "cursor": (
            ("table", "startCursor", TABLE_ROWS, "tkr", "GLOBEX.F", None),
            lambda: P.start_cursor(TABLE_ROWS, "tkr", "GLOBEX.F", None),
        ),
        "cursor hint": (
            ("table", "startCursor", TABLE_ROWS, "tkr", "gone", "GLOBEX.F"),
            lambda: P.start_cursor(TABLE_ROWS, "tkr", "gone", "GLOBEX.F"),
        ),
        "cursor first": (
            ("table", "startCursor", TABLE_ROWS, "tkr", None, None),
            lambda: P.start_cursor(TABLE_ROWS, "tkr", None, None),
        ),
        "followed": (
            (
                "table",
                "followed",
                {"follows": "t", "rows": [], "rows_by_key": {"a": {"rows": [1], "context": "c"}}},
                "a",
            ),
            lambda: dict(
                zip(
                    ("rows", "context"),
                    P.followed({"follows": "t", "rows": [], "rows_by_key": {"a": {"rows": [1], "context": "c"}}}, "a"),
                )
            ),
        ),
        "source": (
            (
                "chart",
                "chartSource",
                {
                    "follows": "t",
                    "x": [1, 2],
                    "series": [{"name": "s"}, {"name": "h"}],
                    "series_by_key": {"k": {"series": [{"name": "c"}], "hide": ["h"], "note": "N"}},
                },
                "k",
            ),
            lambda: P.chart_source(
                {
                    "follows": "t",
                    "x": [1, 2],
                    "series": [{"name": "s"}, {"name": "h"}],
                    "series_by_key": {"k": {"series": [{"name": "c"}], "hide": ["h"], "note": "N"}},
                },
                "k",
            ),
        ),
        "builds": (
            (
                "build",
                "buildsFromJobs",
                [
                    {
                        "id": 2,
                        "tier": "heavy",
                        "force": True,
                        "screen": "A",
                        "state": "running",
                        "progress": "2/7 GRID",
                    },
                    {
                        "id": 1,
                        "tier": "heavy",
                        "force": True,
                        "screen": "B",
                        "state": "failed",
                        "error": "BuildFailed: no",
                    },
                    {"id": 3, "tier": "heavy", "screen": "C", "state": "running"},
                ],
            ),
            lambda: A.builds_from_jobs(
                [
                    {
                        "id": 2,
                        "tier": "heavy",
                        "force": True,
                        "screen": "A",
                        "state": "running",
                        "progress": "2/7 GRID",
                    },
                    {
                        "id": 1,
                        "tier": "heavy",
                        "force": True,
                        "screen": "B",
                        "state": "failed",
                        "error": "BuildFailed: no",
                    },
                    {"id": 3, "tier": "heavy", "screen": "C", "state": "running"},
                ]
            ),
        ),
        "build req": (("build", "buildRequest", "MOM", SCREENS), lambda: A.build_request("MOM", SCREENS)),
        "build none": (("build", "buildRequest", "PORT", SCREENS), lambda: A.build_request("PORT", SCREENS)),
        "changed": (("diff", "changed", PAYLOAD_A, PAYLOAD_B), lambda: sorted(A.changed(PAYLOAD_A, PAYLOAD_B))),
        "rows line": (("ask", "rowLine", TXNS[0]), lambda: T.row_line(TXNS[0])),
        "fresh": (("ask", "freshText", {"rows": TXNS, "error": None, "lines": 2}), lambda: T.fresh_text(TXNS, None, 2)),
        "fresh err": (
            ("ask", "freshText", {"rows": [], "error": "BAD", "lines": 1}),
            lambda: T.fresh_text([], "BAD", 1),
        ),
        "replace": (
            ("ask", "replaceText", {"rows": TXNS, "error": None, "lines": 2}, {"ok": 3, "bad": 1}, "f.csv"),
            lambda: T.replace_text(TXNS, None, 2, {"ok": 3, "bad": 1}, "f.csv"),
        ),
        "replace none": (
            ("ask", "replaceText", {"rows": [], "error": None, "lines": 0}, {"ok": 1}, None),
            lambda: T.replace_text([], None, 0, {"ok": 1}, None),
        ),
        "undo": (
            ("ask", "undoText", {"what": "ADD X", "at": "2026-03-10T11:22:33"}),
            lambda: T.undo_text({"what": "ADD X", "at": "2026-03-10T11:22:33"}),
        ),
        "redo": (("ask", "undoText", {"what": "UNDO: ADD X"}), lambda: T.undo_text({"what": "UNDO: ADD X"})),
        "stands": (
            ("ask", "stands", {"route": "TRADES", "rows": ["x1"]}, {"route": "TRADES", "rows": ["x1", "x2"]}),
            lambda: T.stands({"route": "TRADES", "rows": ["x1"]}, "TRADES", ["x1", "x2"]),
        ),
        "stands gone": (
            ("ask", "stands", {"route": "TRADES", "rows": ["x9"]}, {"route": "TRADES", "rows": ["x1"]}),
            lambda: T.stands({"route": "TRADES", "rows": ["x9"]}, "TRADES", ["x1"]),
        ),
        "suggest": (("trades", "rankSuggestions", "ac", BOOK, FOUND), lambda: T.rank_suggestions("ac", BOOK, FOUND)),
        "edit values": (
            (
                "trades",
                "editValues",
                {"tkr": "ACME.DE", "action": "BUY", "shares": 4.5, "total": 961, "date": "2026-03-01", "pps": 213.5},
            ),
            lambda: T.edit_values(
                {"tkr": "ACME.DE", "action": "BUY", "shares": 4.5, "total": 961, "date": "2026-03-01", "pps": 213.5}
            ),
        ),
        "typed pps": (
            ("trades", "typed", {"pps": "", "total": "5", "keep": 3}, "pps", "9"),
            lambda: T.typed({"pps": "", "total": "5", "keep": 3}, "pps", "9"),
        ),
        "typed date": (
            ("trades", "typed", {"date": "", "keep": 3}, "date", "x"),
            lambda: T.typed({"date": "", "keep": 3}, "date", "x"),
        ),
        "paste sum": (
            (
                "trades",
                "pasteSummary",
                {
                    "rows": [{"line": 1}, {"line": 2, "error": "x"}, {"line": 3, "warnings": ["w"]}],
                    "ok": 2,
                    "bad": 1,
                    "delimiter": ";",
                    "decimal": "comma",
                    "header": True,
                    "notes": ["N"],
                },
            ),
            lambda: T.paste_summary(
                {
                    "rows": [{"line": 1}, {"line": 2, "error": "x"}, {"line": 3, "warnings": ["w"]}],
                    "ok": 2,
                    "bad": 1,
                    "delimiter": ";",
                    "decimal": "comma",
                    "header": True,
                    "notes": ["N"],
                }
            ),
        ),
        "paste err": (("trades", "pasteSummary", {"error": "NOPE"}), lambda: T.paste_summary({"error": "NOPE"})),
        "usage": (("trades", "tradeUsage", "bonus"), lambda: C.trade_usage("bonus")),
        "isEur": (("trades", "isEur", "acme.de"), lambda: T.is_eur("acme.de")),
    }
    for v in (-1, -0.5, 0, 0.5, 1, None):
        out[f"heat {v}"] = (
            ("heatmap", "heatColor", v),
            lambda v=v: v,
        )  # checked separately: colours differ in spelling
    return {k: x for k, x in out.items() if not k.startswith("heat")}


FORMS = [
    {"ticker": "acme.de", "action": "buy", "shares": "4", "pps": "240", "total": "", "date": "", "fee": ""},
    {"ticker": "acme.de", "action": "buy", "shares": "4", "pps": "240", "total": "", "date": "2026-03-01", "fee": "1"},
    {
        "ticker": "ACME.DE",
        "action": "sell",
        "shares": "1,5",
        "pps": "",
        "total": "300.50",
        "date": "01.02.2026",
        "fee": "",
    },
    {"ticker": "ACME.DE", "action": "bonus", "shares": "0.15", "pps": "100", "total": "", "date": "", "fee": ""},
    {
        "ticker": "ACME.DE",
        "action": "buy",
        "shares": "4",
        "pps": "",
        "total": "961",
        "date": "",
        "fee": "",
        "keep": 240.25,
    },
    {"ticker": "ACME.DE", "action": "buy", "shares": "4", "pps": "240", "total": "", "date": "2030-01-01", "fee": ""},
    {"ticker": "ACME.DE", "action": "buy", "shares": "x", "pps": "240", "total": "", "date": "", "fee": ""},
    {"ticker": "ACME.DE", "action": "buy", "shares": "1,234", "pps": "240", "total": "", "date": "", "fee": ""},
    {"ticker": "", "action": "buy", "shares": "4", "pps": "1", "total": "", "date": "", "fee": ""},
    {"ticker": "A B", "action": "buy", "shares": "4", "pps": "1", "total": "", "date": "", "fee": ""},
    {"ticker": "ACME.DE", "action": "buy", "shares": "0", "pps": "1", "total": "", "date": "", "fee": ""},
    {"ticker": "ACME.DE", "action": "sell", "shares": "1", "pps": "1", "total": "", "date": "", "fee": "5"},
    {"ticker": "ACME.DE", "action": "buy", "shares": "1", "pps": "", "total": "", "date": "", "fee": ""},
    {"ticker": "", "action": "buy", "shares": "", "pps": "", "total": "", "date": "", "fee": ""},
    {"ticker": "ACME.DE", "action": "buy", "shares": "", "pps": "", "total": "", "date": "", "fee": ""},
    {"ticker": "ACME.DE", "action": "buy", "shares": "4", "pps": "", "total": "961", "date": "2026-03-01", "fee": ""},
]


def _trade_cases():
    today = date.fromisoformat(TODAY)
    out = {}
    for i, v in enumerate(FORMS):
        out[f"status {i}"] = (
            ("trades", "formStatus", v, TODAY, TXNS, None),
            lambda v=v: T.form_status(v, today, TXNS, None),
        )
        out[f"status edit {i}"] = (
            ("trades", "formStatus", v, TODAY, TXNS, "x1"),
            lambda v=v: T.form_status(v, today, TXNS, "x1"),
        )
        out[f"from {i}"] = (("trades", "tradeFrom", v, TODAY), lambda v=v: T.trade_from(v, today))
    return out


def same(a, b, path="") -> list:
    if isinstance(a, float) or isinstance(b, float):
        if (
            isinstance(a, (int, float))
            and isinstance(b, (int, float))
            and not isinstance(a, bool)
            and not isinstance(b, bool)
        ):
            return [] if math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9) else [f"{path}: {a!r} != {b!r}"]
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in {*a, *b}:
            out += same(a.get(k), b.get(k), f"{path}.{k}")
        return out
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        if len(a) != len(b):
            return [f"{path}: length {len(a)} != {len(b)}"]
        return [e for i, (x, y) in enumerate(zip(a, b)) for e in same(x, y, f"{path}[{i}]")]
    return [] if a == b else [f"{path}: {a!r} != {b!r}"]


def _jsonable(v):
    return (
        json.loads(
            json.dumps(
                v, default=lambda o: sorted(o) if isinstance(o, (set, frozenset)) else str(o), allow_nan=False
            ).replace("Infinity", '"__INF__"')
        )
        if not isinstance(v, float) or math.isfinite(v)
        else ("__INF__" if v > 0 else "__-INF__")
    )


@pytest.fixture(scope="module")
def cases():
    return {**_fmt_cases(), **_range_cases(), **_cmd_cases(), **_misc_cases(), **_trade_cases()}


@pytest.fixture(scope="module")
def js(cases):
    payload = {name: list(call) for name, (call, _py) in cases.items()}
    r = subprocess.run(
        ["node", str(HERE / "parity.mjs")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_every_case_answers_alike(cases, js):
    bad = []
    for name, (_call, py) in cases.items():
        mine = _jsonable(py())
        bad += [f"{name} {e}" for e in same(js[name], mine)]
    assert not bad, f"{len(bad)} differences:\n" + "\n".join(bad[:60])


def test_help_lists_the_browsers_verbs():
    """HELP and the palette read one table on each side: the browser's verbs are all here, with its words, but for what a
    terminal does otherwise (FULL, CASH) and what only it has (IMPORT, QUIT)."""
    js_rows = subprocess.run(
        [
            "node",
            "--input-type=module",
            "-e",
            f"import {{ helpRows }} from {json.dumps((HERE.parents[1] / 'web/app/cmd.js').as_uri())};"
            f"console.log(JSON.stringify(helpRows({json.dumps({**JS_CTX, 'cashflow': 'http://x'})})))",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert js_rows.returncode == 0, js_rows.stderr
    theirs = {r["cmd"]: r["desc"] for r in json.loads(js_rows.stdout)}
    ours = {r["cmd"]: r["desc"] for r in C.help_rows({**CTX, "cashflow": "http://x"})}
    assert set(ours) - set(theirs) == {"IMPORT <FILE>", "QUIT"}
    assert not set(theirs) - set(ours)
    differ = {k for k in theirs if theirs[k] != ours[k]}
    assert differ == {"FULL", "CASH"}
