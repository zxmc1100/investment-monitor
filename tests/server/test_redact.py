"""public_view is an allow-list: only nodes explicitly marked vis=public survive."""
import copy
import json

from monitor.server.redact import public_view


def P(**kw):
    return {"vis": "public", **kw}


PAYLOAD = {
    "screen": "T", "title": "Test", "context": P(text="3 POS"),
    "meta": {"computed_at": "2026-06-30T14:00:00", "tiers": {"quote": "2026-06-30T14:00:00"},
             "stale": {"AAA": "2026-06-29T10:00:00"}, "code_version": "c0dec0dec0de"},
    "help": [P(h="ROI", body="public help"), {"h": "NET", "body": "SECRET-HELP"}],
    "panels": [
        P(id="k", n=1, title="K", type="kpi", span=6,
          items=[P(k="ROI", v=12.5, fmt="pct+"), {"k": "VALUE", "v": 987654.32, "fmt": "eur"},
                 P(k="X", v=1.0, fmt="num", sub="SECRET-SUB")]),
        P(id="t", n=2, title="T", type="table", span=6, key="tkr", sort=["value", "desc"],
          cols=[P(k="tkr", label="TKR"), {"k": "shrs", "label": "SHRS"},
                {"k": "value", "label": "VALUE", "vis": "private"}, P(k="wt", label="WT%")],
          rows=[{"tkr": "AAA", "shrs": 4242.4242, "value": 987654.32, "wt": 60.0,
                 "_closed": False, "_note": "SECRET-ROW"}],
          total={"tkr": "TOTAL", "value": 987654.32, "wt": 100.0}),
        P(id="c", n=3, title="C", type="chart", span=6, x=[1, 2],
          series=[P(name="YOU", y=[1.0, 2.0]), {"name": "EUR", "y": [987654.32, 1.0]}]),
        P(id="f", n=4, title="F", type="chart", follows="t",
          series_by_key={"AAA": {"x": [1, 2], "series": [{"name": "AAA", "y": [987654.32, 3.0]}]}}),
        {"id": "l", "n": 5, "title": "L", "type": "ledger", "lines": [P(label="Gross", v=987654.32)]},
        P(id="b", n=6, title="B", type="bars", items=[P(label="Tech", v=40.0, fmt="pct")]),
        P(id="u", n=7, title="U", type="mystery", items=[P(k="Q", v=1.0)]),
        P(id="e", n=8, title="E", type="kpi", items=[{"k": "ONLY", "v": 987654.32}]),
    ],
}


def test_canary_private_values_never_survive():
    s = json.dumps(public_view(PAYLOAD))
    for secret in ("987654.32", "4242.4242", "SECRET", "2026-06-29T10:00:00", "c0dec0dec0de"):
        assert secret not in s, secret


def test_public_nodes_survive_with_structure():
    v = public_view(PAYLOAD)
    assert [p["id"] for p in v["panels"]] == ["k", "t", "c", "b"]
    k, t, c, b = v["panels"]
    assert [i["k"] for i in k["items"]] == ["ROI", "X"]
    assert [col["k"] for col in t["cols"]] == ["tkr", "wt"]
    assert t["rows"] == [{"tkr": "AAA", "wt": 60.0}]
    assert t["total"] == {"tkr": "TOTAL", "wt": 100.0}
    assert [s["name"] for s in c["series"]] == ["YOU"] and c["x"] == [1, 2]
    assert b["items"][0]["label"] == "Tech"
    assert v["context"] == {"text": "3 POS", "vis": "public"}
    assert [h["h"] for h in v["help"]] == ["ROI"]


def test_meta_is_allowlisted():
    assert set(public_view(PAYLOAD)["meta"]) == {"computed_at", "tiers"}


def test_item_keys_are_allowlisted():
    x = public_view(PAYLOAD)["panels"][0]["items"][1]
    assert "sub" not in x and x == {"k": "X", "v": 1.0, "fmt": "num", "vis": "public"}


def test_sort_on_private_column_falls_back_to_key():
    assert public_view(PAYLOAD)["panels"][1]["sort"] == ["tkr", "asc"]


def test_table_without_public_key_column_is_dropped():
    p = {"panels": [P(id="t", type="table", key="tkr", cols=[{"k": "tkr"}, P(k="wt")], rows=[])]}
    assert public_view(p)["panels"] == []


def test_input_is_not_mutated():
    before = copy.deepcopy(PAYLOAD)
    public_view(PAYLOAD)
    assert PAYLOAD == before


def test_nested_private_values_never_survive():
    p = {
        "screen": "T", "title": "Test",
        "context": P(text="3 POS"),
        "meta": {"computed_at": "2026-06-30T14:00:00", "tiers": {"daily": {"stale": "LEAK-1"}}},
        "help": [P(h="ROI", body="public help")],
        "panels": [
            # Nested dict in kpi item v
            P(id="k", n=1, title="K", type="kpi", span=6,
              items=[P(k="ROI", v={"nested": "LEAK-1"}, fmt="pct+"),
                     P(k="X", v=[{"vis": "private", "x": "LEAK-1"}], fmt="num")]),
            # Table with invalid sort (should fall back to key+asc)
            P(id="t", n=2, title="T", type="table", span=6, key="tkr",
              sort=["tkr", "LEAK-1"],
              cols=[P(k="tkr", label="TKR"), P(k="wt", label="WT%")],
              rows=[{"tkr": "AAA", "wt": 60.0}]),
            # Chart panel with sort (should be dropped entirely)
            P(id="c", n=3, title="C", type="chart", span=6,
              sort=[{"secret": "LEAK-1"}, "LEAK-1"],
              series=[P(name="YOU", y=[1.0, 2.0])]),
            # series_by_key block with vis="private" but public series inside
            P(id="f", n=4, title="F", type="chart", span=6,
              series_by_key={"AAA": {"vis": "private", "x": [1, 2],
                                     "series": [P(name="LEAK-1", y=[1.0, 2.0])]}}),
        ],
    }
    p["title"] = {"value": "LEAK-1"}
    p["context"]["text"] = {"value": "LEAK-1"}
    s = json.dumps(public_view(p))
    assert "LEAK-1" not in s


def test_closed_rows_dropped_and_stale_is_boolean():
    p = {
        "screen": "T", "title": "Test",
        "panels": [
            P(id="t", n=1, title="T", type="table", span=6, key="tkr",
              cols=[P(k="tkr", label="TKR"), P(k="wt", label="WT%")],
              rows=[
                  {"tkr": "OPEN", "wt": 40.0, "_stale": "2026-06-29T10:00:00"},
                  {"tkr": "SOLD", "wt": 60.0, "_closed": True},
              ]),
        ],
    }
    v = public_view(p)
    s = json.dumps(v)
    assert "SOLD" not in s
    assert "2026-06-29T10:00:00" not in s
    assert len(v["panels"][0]["rows"]) == 1
    assert v["panels"][0]["rows"][0] == {"tkr": "OPEN", "wt": 40.0, "_stale": True}


def test_series_by_key_block_without_vis_is_dropped():
    p = {"panels": [P(id="f", n=1, title="F", type="chart", follows="t",
                      series_by_key={"AAA": {"x": [1, 2], "series": [P(name="LEAK-B", y=[1.0, 2.0])]}})]}
    assert public_view(p)["panels"] == []


def test_scatter_panel_keeps_public_series_with_x():
    p = {"panels": [P(id="s", n=1, title="S", type="scatter", xfmt="pct", yfmt="pct",
                      series=[P(name="NOW", kind="marker", x=[10.0], y=[5.0]),
                              {"name": "SECRET", "kind": "points", "x": [987654.32], "y": [1.0]}])]}
    out = public_view(p)["panels"][0]
    assert out["xfmt"] == "pct" and [s["name"] for s in out["series"]] == ["NOW"]
    assert out["series"][0]["x"] == [10.0] and "987654.32" not in json.dumps(out)


def test_heatmap_panel_scalar_checked_and_public_only():
    good = P(id="h", n=1, title="H", type="heatmap", labels=["A", "B"], cells=[[1.0, 0.5], [0.5, 1.0]], fmt="num:2")
    bad = P(id="x", n=2, title="X", type="heatmap", labels=["A"], cells=[[{"leak": 987654.32}]])
    priv = {"id": "p", "n": 3, "title": "P", "type": "heatmap", "labels": ["A"], "cells": [[1.0]]}
    out = public_view({"panels": [good, bad, priv]})["panels"]
    assert [q["id"] for q in out] == ["h", "x"]
    assert out[0]["cells"] == [[1.0, 0.5], [0.5, 1.0]] and out[0]["labels"] == ["A", "B"]
    assert out[1]["cells"] == [[None]] and "987654.32" not in json.dumps(out)


def test_bars_detail_lists_are_public_only_when_marked():
    p = P(id="a", n=1, title="A", type="bars", items=[P(label="IT", v=50.0, fmt="pct")],
          sectors=[P(label="IT", v=50.0, fmt="pct", text="NVD.F 50.0"), {"label": "SECRET", "v": 987654.32}],
          countries=[{"label": "X", "v": 1.0}])
    out = public_view({"panels": [p]})["panels"][0]
    assert [r["label"] for r in out["sectors"]] == ["IT"] and out["sectors"][0]["text"] == "NVD.F 50.0"
    assert "countries" not in out and "987654.32" not in json.dumps(out)


def test_kpi_year_table_keeps_percentages_and_drops_euro_gain():
    p = {"panels": [P(id="s", n=1, title="S", type="kpi", items=[P(k="YTD", v=21.5, fmt="pct+")],
                      years=[P(label="2026", v=21.5, v2=27.1, gain=987654.32, fmt="pct+"),
                             {"label": "2025", "v": 25.0, "v2": 20.0, "gain": 987654.32, "fmt": "pct+"}])]}
    out = public_view(p)["panels"][0]
    assert out["years"] == [{"label": "2026", "v": 21.5, "v2": 27.1, "fmt": "pct+", "vis": "public"}]
    assert "987654.32" not in json.dumps(out)


def test_row_format_override_survives_for_public_columns_only():
    cols = [P(k="name", label="NAME", fmt="text"), P(k="lvl", label="LAST", fmt="num:2"),
            {"k": "secret", "label": "S", "fmt": "num"}]
    p = {"panels": [P(id="fx", n=1, title="FX", type="table", key="name", cols=cols,
                      rows=[{"name": "EUR/USD", "lvl": 1.17, "secret": 9, "_fmt": {"lvl": "num:4", "secret": "eur", "bad": 3}},
                            {"name": "US 10Y", "lvl": 4.1, "_fmt": "num:3"}])]}
    rows = public_view(p)["panels"][0]["rows"]
    assert rows == [{"name": "EUR/USD", "lvl": 1.17, "_fmt": {"lvl": "num:4"}}, {"name": "US 10Y", "lvl": 4.1}]


def test_surviving_panels_keep_their_numbers():
    """Panel numbers are the same in the local and the public view: text such as PORT's "ALT+7 DETAIL"
    names a panel by number, so a private panel leaves a gap rather than renumbering the rest."""
    priv = {"id": "mine", "n": 2, "title": "MINE", "type": "ledger", "lines": [P(label="x", v=1.0)]}
    p = {"panels": [P(id="a", n=1, title="A", type="ledger", lines=[P(label="x", v=1.0)]), priv,
                    P(id="c", n=3, title="C", type="ledger", lines=[P(label="x", v=1.0)]),
                    P(id="e", n=5, title="E", type="ledger", lines=[P(label="x", v=1.0)])]}
    assert [(q["id"], q["n"]) for q in public_view(p)["panels"]] == [("a", 1), ("c", 3), ("e", 5)]
