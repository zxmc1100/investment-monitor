"""The typed panels in cells: what each shows, and the grid they sit in."""

import json

from monitor.tui import panels as P
from monitor.tui.plot import chart_raster, scatter_raster, spark, time_axis
from monitor.tui.term import AMBER, CURSOR, DN, STRIPE, UP, Canvas
from tests.tui.conftest import X, press, screen


def text(lines) -> list:
    return ["".join(s[0] for s in ln) for ln in lines]


def test_grid_places_as_css_auto_placement():
    panels = [
        {"id": "a", "span": 6},
        {"id": "b", "span": 6},
        {"id": "c", "span": 7, "rows_span": 2},
        {"id": "d", "span": 5},
        {"id": "e", "span": 5},
        {"id": "f", "span": 3},
    ]
    assert [(p["id"], r, c) for p, r, c, _, _ in P.place(panels)] == [
        ("a", 0, 0),
        ("b", 0, 6),
        ("c", 1, 0),
        ("d", 1, 7),
        ("e", 2, 7),
        ("f", 3, 0),
    ]


def test_spare_height_goes_to_the_chart_rows_levelled():
    placed = P.place(
        [
            {"id": "k", "span": 12, "type": "kpi"},
            {"id": "c1", "span": 12, "type": "chart"},
            {"id": "c2", "span": 12, "type": "chart"},
        ]
    )
    assert P.row_heights(placed, {"k": 5, "c1": 8, "c2": 12}, 35) == [5, 15, 15]
    assert P.row_heights(placed, {"k": 5, "c1": 8, "c2": 12}, 10) == [5, 8, 12]  # no room: the grid scrolls


def test_no_chart_row_the_last_row_takes_the_spare():
    placed = P.place([{"id": "a", "span": 12, "type": "table"}, {"id": "b", "span": 12, "type": "table"}])
    assert P.row_heights(placed, {"a": 3, "b": 3}, 10) == [3, 7]


def test_a_spanning_panel_gets_its_height():
    placed = P.place(
        [
            {"id": "t", "span": 7, "rows_span": 2, "type": "table"},
            {"id": "a", "span": 5, "type": "ledger"},
            {"id": "b", "span": 5, "type": "ledger"},
        ]
    )
    h = P.row_heights(placed, {"t": 20, "a": 3, "b": 3}, 0)
    assert sum(h) == 20


COLS = [
    {"k": "t", "label": "TKR", "fmt": "tkr"},
    {"k": "n", "label": "NAME", "fmt": "text"},
    {"k": "a", "label": "AAA", "fmt": "num:2", "align": "r", "lo": True},
    {"k": "b", "label": "BBB", "fmt": "num:2", "align": "r"},
]
ROWS = [{"t": "ACME.DE", "n": "A very long company name indeed", "a": 1234.5, "b": 1.0}]


def test_text_takes_what_numbers_leave():
    shown, w, gap = P.col_widths(COLS, ROWS, 60, "t")
    assert [c["k"] for c in shown] == ["t", "n", "a", "b"]
    assert sum(w.values()) + gap * 3 == 60 and w["n"] > 6


def test_lo_columns_give_way_then_the_padding_then_the_right():
    assert "a" not in [c["k"] for c in P.col_widths(COLS, ROWS, 30, "t")[0]]
    shown, w, gap = P.col_widths(COLS, ROWS, 19, "t")
    assert gap == 1 and sum(w[c["k"]] for c in shown) + gap * (len(shown) - 1) <= 19
    assert len(P.col_widths(COLS, ROWS, 8, "t")[0]) == 1


def test_positions_table_marks_cursor_closed_stale(app):
    p = app.panel("positions")
    p["rows"][0]["_stale"] = True
    lines = P.table_lines(p, 80, app)
    assert "WT% ▼" in text(lines)[0]
    cur = next(ln for ln in lines if ln.cur)
    assert cur.bg == CURSOR and cur.key == "ACME.DE"
    assert any(s[0] == " ●" and s[1] == DN for s in lines[1])
    assert text(lines)[-1].strip() == "+ SHOW 1 CLOSED"
    assert "TOTAL" in text(lines)[-2] and all(s[1] == AMBER for s in lines[-2] if s[0].strip())
    press(app, "z", "a")
    assert "− HIDE 1 CLOSED" in screen(app) and "GONE.DE" in screen(app)


def test_a_table_following_another_shows_its_rows_or_says_why_not(app):
    press(app, "2")
    app.count_at = -1e9
    app.tick()
    app.draw()
    assert app.id == "OPT"
    s = screen(app)
    assert "TICKET → HRP" in s and "1 ORDER" in s
    press(app, "gg"[0], "g")
    assert "TICKET → EQUAL" in screen(app) and "NOTHING TO DO" in screen(app)


def test_kpi_two_lines_each_and_years_only_when_maximized(app):
    p = app.panel("summary")
    lines = text(P.kpi_lines(p, 60, app))
    assert "VALUE" in lines[0] and "€1,000.50" in lines[1] and "-1.50%" in lines[1]
    assert not any("YEAR" in t for t in lines)
    press(app, "z", "1")
    assert "YEAR" in screen(app) and "TIME-WEIGHTED" in screen(app)


def test_a_wide_kpi_value_wraps_and_a_long_one_wraps_in_its_cell():
    class Ui:
        def flash_bg(self, _k):
            return None

        def is_max(self, _p):
            return False

    lines = text(P.kpi_lines({"id": "x", "items": [{"k": "WHY", "v": "word " * 30, "wide": True}]}, 40, Ui()))
    assert lines[0].strip() == "WHY" and len(lines) > 3
    lines = text(
        P.kpi_lines(
            {"id": "x", "items": [{"k": "A", "v": "AAAA.DE 0.27, BBBB.DE 0.25"}, {"k": "B", "v": 1.0, "fmt": "num:1"}]},
            40,
            Ui(),
        )
    )
    assert "AAAA.DE" in lines[1] and "1.0" in lines[1] and "BBBB.DE" in lines[-1]


def test_ledger_rule_and_strong(app):
    lines = P.ledger_lines(app.panel("acct"), 40, app)
    assert set(lines[1][0][0]) == {"─"}
    assert lines[2][1][1] == AMBER and "-€2.00" in lines[2][2][0]


def test_banding_skips_header_and_rules_keeps_the_cursor_and_fills_the_panel():
    head, a, rule, b, c = (
        P.line(sticky=True),
        P.line(),
        P.line(rule=True),
        P.line(bg=CURSOR, cur=True),
        P.line(),
    )
    out = P.band([head, a, rule, b, c], 7)
    assert [ln.bg for ln in out] == [None, None, None, CURSOR, None, STRIPE, None]


def test_tables_paint_their_stripes(app):
    assert any(st[1] == STRIPE for row in app.canvas.st for st in row)


def test_bars_compact_then_detail_when_maximized(app):
    assert "ACME.DE 40" not in screen(app)
    press(app, "z", "6")
    s = screen(app)
    assert "SECTORS" in s and "COUNTRIES" in s and "ACME.DE 40" in s
    assert (
        P.bar_text(1.0, 4) == "▄▄▄▄"
        and P.bar_text(0.5, 4) == "▄▄  "
        and P.bar_text(0.125, 4) == "▖   "
        and P.bar_text(0, 2) == "  "
    )


def test_heatmap_colours_diverge():
    assert P.heat_color(1.0) == "#00c464" and P.heat_color(-1.0) == "#d93434" and P.heat_color(None) is None


def test_notes_wrap_and_banner_carries_its_button(app):
    class Ui:
        pass

    lines = text(P.notes_lines({"items": [{"k": "A", "v": "word " * 20}, {"k": "B", "v": None}]}, 30, Ui()))
    assert lines[0].strip() == "A" and len(lines) > 3 and lines[-1].strip() == "—"
    press(app, "6")
    app.count_at = -1e9
    app.tick()
    app.draw()
    s = screen(app)
    assert "EXAMPLE PORTFOLIO — START FRESH clears it" in s and " START FRESH " in s and "A BACKUP IS KEPT" in s


def test_unknown_panel_type_says_so(app):
    assert "UNKNOWN PANEL TYPE sankey" in text(P.panel_lines({"id": "x", "type": "sankey"}, 40, app))[0]


def test_spark():
    t, up = spark([1, 2, 3, 4], 4)
    assert len(t) == 4 and up and all(0x2800 <= ord(c) <= 0x28FF for c in t)
    assert spark([4, 3], 4)[1] is False
    assert spark([1], 4)[0] == "" and spark(None, 4)[0] == ""


def test_time_axis_picks_what_fits():
    ticks, incr = time_axis(X[0], X[-1], 8)
    assert 2 <= len(ticks) <= 9 and incr >= 28 * 86400
    ticks, incr = time_axis(X[0], X[10], 12)
    assert incr <= 7 * 86400 and ticks


def test_chart_raster_draws_lines_markers_band_labels():
    r = chart_raster(
        X[:50],
        [
            {"name": "a", "role": "primary", "y": [float(i) for i in range(50)]},
            {"name": "m", "role": "buy", "kind": "markers", "y": [10.0 if i == 20 else None for i in range(50)]},
            {"name": "b", "kind": "band", "color": "#4fc3f7", "lo": [5.0] * 50, "hi": [15.0] * 50},
        ],
        60,
        12,
        "eur",
        [X[25]],
        ["TODAY"],
    )
    flat = [c for row in r.cells for c in row]
    assert any(c[0] == "●" and c[1] == "#00e676" for c in flat)
    assert any(0x2801 <= ord(c[0]) <= 0x28FF and c[1] == "#ffffff" for c in flat)
    assert any(c[2] != "#000000" for c in flat)  # the band
    assert "TODAY" in "".join(c[0] for c in r.cells[0])
    assert "€" in "".join(row[0][0] + row[1][0] + row[2][0] for row in r.cells)
    assert r.index_at(r.left) == 0 and r.index_at(r.left + r.pw - 1) == 49
    assert r.col_of(0) == r.left


def test_chart_gaps_stay_gaps():
    r = chart_raster(X[:40], [{"name": "a", "y": [1.0] * 10 + [None] * 20 + [1.0] * 10}], 42, 6, "num")
    row = max(r.cells, key=lambda cs: sum(0x2800 < ord(c[0]) <= 0x28FF for c in cs))
    mid = row[r.left + r.pw // 2]
    assert not 0x2800 < ord(mid[0]) <= 0x28FF


def test_chart_too_small_or_empty_is_none():
    assert chart_raster(X[:5], [{"y": [None] * 5}], 40, 8, "num") is None
    assert chart_raster(X[:5], [{"y": [1.0] * 5}], 5, 8, "num") is None


def test_scatter_labels_overlapping_markers_as_one():
    p = {
        "series": [
            {"name": "front", "role": "frontier", "kind": "line", "x": [10, 20], "y": [5, 15]},
            {"name": "NOW", "role": "now", "kind": "marker", "x": [15], "y": [9]},
            {"name": "HRP", "role": "target", "kind": "marker", "x": [15.01], "y": [9.01]},
        ]
    }
    r = scatter_raster(p["series"], 60, 14, "pct", "pct")
    all_text = "\n".join("".join(c[0] for c in row) for row in r.cells)
    assert "NOW · HRP" in all_text or "HRP · NOW" in all_text
    assert scatter_raster([], 60, 14, None, None) is None


def test_every_panel_type_draws_at_every_size(app, api):
    """The grid never raises and never writes outside itself, however small the window."""
    for sid in ("PORT", "OPT", "TRADES"):
        app.go(sid)
        for size in ((200, 60), (120, 40), (99, 30), (60, 20), (30, 10), (12, 4), (1, 1)):
            app.size = size
            app.draw()
            assert len(app.canvas.ch) == size[1] and all(len(r) == size[0] for r in app.canvas.ch)


def test_a_name_with_escapes_never_reaches_the_terminal(api, clock):
    """Names come from Yahoo: the bytes painted hold no ESC but those the painter itself writes."""
    from monitor.tui.app import App

    out = []
    bad = "Evil\x1b]52;c;aGk=\x07\x1b[2J\x9b31m"
    api.screens["PORT"]["panels"][2]["rows"][0]["name"] = bad
    api.screens["PORT"]["panels"][0]["items"][0]["k"] = bad
    api.screens["PORT"]["title"] = bad
    a = App(api, sync=True, strict=True)
    a.painter.write = out.append
    a.size = (160, 48)
    a.start()
    a.draw()
    assert "]52;" in out[0].replace("\x1b]52;", "")  # the words stay, harmless
    assert "\x1b]52" not in out[0] and "\x07" not in out[0] and "\x9b" not in out[0]


def test_payload_fixture_is_valid_json():
    from tests.tui.conftest import PORT

    assert json.loads(json.dumps(PORT))["screen"] == "PORT"


def test_cells_outside_a_view_are_untouched():
    c = Canvas(10, 3)
    v = c.view(2, 1, 4, 1)
    P.paint_lines(v, [P.line(P.seg("abcdefgh"))], type("U", (), {"hit": lambda *a: None, "row_ys": {}})(), "p")
    assert "".join(c.ch[0]) == " " * 10 and "".join(c.ch[1]) == "  abcd    " and "".join(c.ch[2]) == " " * 10
    assert UP != DN


def test_period_chips_switch_the_pair_and_carry_the_sort(app):
    p = app.panel("positions")
    p["picks"], p["pick"] = ["DAY", "1W"], "DAY"
    p["cols"][2:2] = [
        {"k": "d", "label": "DAY%", "fmt": "pct+", "align": "r", "pick": "DAY"},
        {"k": "w", "label": "WEEK%", "fmt": "pct+", "align": "r", "pick": "1W"},
    ]
    app.table_state(p)["sort"] = ["d", "desc"]
    app.draw()
    assert "DAY%" in screen(app) and "WEEK%" not in screen(app) and " DAY  1W " in screen(app)
    press(app, "p")
    assert "WEEK%" in screen(app) and "DAY%" not in screen(app)
    assert app.table_state(p)["sort"] == ["w", "desc"]          # the sort moved to the new period's column
    press(app, "π")
    assert "DAY%" in screen(app)
