"""The app, key by key: vim's navigation over the browser's behaviour."""

from datetime import date

import pytest

from monitor.tui import app as A
from monitor.tui.app import App, edit_line
from monitor.tui.client import ApiError
from tests.tui.conftest import press, screen, typed


def settle(app, clock):
    """The moment a lone digit waits for a motion has passed."""
    clock.t += 1
    app.tick()
    app.drain()
    app.draw()


def cursor(app, pid="positions"):
    return app.tables[pid]["cursor"]


# ── rows ─────────────────────────────────────────────────────────────────────────────────────────
def test_j_k_move_the_cursor_and_stop_at_the_ends(app):
    assert cursor(app) == "ACME.DE"
    press(app, "j")
    assert cursor(app) == "GLOBEX.F" and app.focus == "positions"
    press(app, "k", "k", "k")
    assert cursor(app) == "ACME.DE"
    press(app, "down", "up", "down")
    assert cursor(app) == "GLOBEX.F"


def test_a_count_moves_that_many_rows_and_opens_no_screen(app, clock):
    press(app, "2", "j")
    assert cursor(app) == "INITECH.DE"
    settle(app, clock)
    assert app.id == "PORT"
    press(app, "1", "2", "j")
    assert app.table_state(app.panel("positions"))["rows"][14]["tkr"] == cursor(app)
    settle(app, clock)
    assert app.id == "PORT"


def test_gg_G_and_counts(app):
    press(app, "G")
    assert cursor(app) == "TK25.DE"
    press(app, "g", "g")
    assert cursor(app) == "ACME.DE"
    press(app, "3", "G")
    assert cursor(app) == "INITECH.DE"
    press(app, "2", "g", "g")
    assert cursor(app) == "GLOBEX.F"
    press(app, "end")
    assert cursor(app) == "TK25.DE"
    press(app, "home")
    assert cursor(app) == "ACME.DE"


def test_half_pages_and_pages_use_the_screen(app):
    rows = [r["tkr"] for r in app.table_state(app.panel("positions"))["rows"]]
    page = app.grid_h - 2
    press(app, "ctrl-d")
    assert cursor(app) == rows[page // 2]
    press(app, "ctrl-u")
    assert cursor(app) == rows[0]
    press(app, "ctrl-f")
    assert cursor(app) == rows[min(page, len(rows) - 1)]
    press(app, "ctrl-b")
    assert cursor(app) == rows[0]
    press(app, "pgdn", "pgup")
    assert cursor(app) == rows[0]


def test_the_cursor_is_kept_in_view_and_H_M_L_stay_in_it(app):
    app.size = (160, 20)
    app.draw()
    press(app, "G")
    assert app.scroll > 0
    assert "TK25.DE" in screen(app)
    press(app, "H")
    top = cursor(app)
    press(app, "L")
    assert cursor(app) != top
    press(app, "g", "g")
    assert app.scroll <= 8


def test_ctrl_e_y_scroll_and_zz_centres(app):
    app.size = (160, 20)
    app.draw()
    press(app, "ctrl-e", "ctrl-e")
    assert app.scroll == 2
    press(app, "ctrl-y")
    assert app.scroll == 1
    press(app, "1", "5", "G", "z", "t")
    y = next(y for y, k in app.row_ys["positions"] if k == cursor(app))
    assert y == app.grid_top
    press(app, "z", "b")
    y = next(y for y, k in app.row_ys["positions"] if k == cursor(app))
    assert y == app.grid_top + app.grid_h - 1
    press(app, "z", "z")
    y = next(y for y, k in app.row_ys["positions"] if k == cursor(app))
    assert y == app.grid_top + app.grid_h // 2
    press(app, "G", "z", "t")  # the last row cannot reach the top: the page ends
    assert "TK25.DE" in screen(app)


# ── screens ──────────────────────────────────────────────────────────────────────────────────────
def test_a_lone_digit_opens_its_screen_after_a_moment(app, clock):
    press(app, "2")
    assert app.id == "PORT"
    assert ": 2" in screen(app)  # shown while it waits, as vim's showcmd
    settle(app, clock)
    assert app.id == "OPT" and "Optimizer" in screen(app)


def test_gt_gT_and_a_count(app):
    press(app, "g", "t")
    assert app.id == "OPT"
    press(app, "g", "T", "g", "T")
    assert app.id == "MOM"
    press(app, "2", "g", "t")
    assert app.id == "OPT"


def test_esc_and_ctrl_o_go_back(app, clock):
    typed(app, ":acme.de")
    press(app, "enter")
    assert (app.id, app.param) == ("SEC", "ACME.DE")
    assert "NO DATA YET" in screen(app)
    press(app, "esc")
    assert (app.id, app.param) == ("PORT", None)
    press(app, "2")
    settle(app, clock)
    assert app.id == "OPT"
    press(app, "ctrl-o")
    assert app.id == "PORT"


def test_a_refused_ticker_says_so_and_stays(app):
    typed(app, ":nope.de")
    press(app, "enter")
    assert app.id == "PORT" and "NOT A TRADEABLE TICKER" in screen(app)


def test_enter_drills_into_a_security(app):
    press(app, "j", "enter")
    assert (app.id, app.param) == ("SEC", "GLOBEX.F")


def test_enter_runs_a_tables_command(app, api):
    press(app, "g", "t", "j", "enter")
    assert ("target", "HRP") in api.calls or ("target", "EQUAL") in api.calls


# ── the command bar ──────────────────────────────────────────────────────────────────────────────
def test_colon_opens_the_bar_with_a_palette(app):
    typed(app, ":op")
    assert app.mode == "command" and app.ac["items"][0]["label"] == "OPT"
    s = screen(app)
    assert ":OP" in s and "Optimizer" in s and "Tab complete" in s
    press(app, "enter")
    assert app.id == "OPT" and app.mode == "normal"


def test_palette_keys(app):
    typed(app, ":s")
    first = app.ac["items"][0]
    press(app, "ctrl-n")
    assert app.ac["i"] == 1 and app.ac["moved"]
    press(app, "ctrl-p", "tab")
    assert app.bar == first["run"]
    press(app, "esc")
    assert app.mode == "normal" and app.bar == ""


def test_a_verb_with_an_argument_fills_the_bar_then_shows_its_usage(app):
    typed(app, ":wat")
    press(app, "enter")
    assert app.mode == "command" and app.bar == "WATCH "


def test_history_on_an_empty_bar(app):
    typed(app, ":opt")
    press(app, "enter")
    typed(app, ":port")
    press(app, "enter")
    press(app, ":", "up")
    assert app.bar == "port"
    press(app, "up")
    assert app.bar == "opt"
    press(app, "down", "down")
    assert app.bar == ""
    typed(app, "ma")
    assert app.hi == -1
    press(app, "ctrl-u", "backspace")
    assert app.mode == "normal"


def test_bar_editing_keys():
    assert edit_line("abc", 1, "x") == ("axbc", 2)
    assert edit_line("abc", 1, "backspace") == ("bc", 0)
    assert edit_line("abc", 0, "backspace") == ("abc", 0)
    assert edit_line("abc", 1, "del") == ("ac", 1)
    assert edit_line("ab cd", 5, "ctrl-w") == ("ab ", 3)
    assert edit_line("ab cd", 2, "ctrl-u") == (" cd", 0)
    assert edit_line("ab cd", 2, "ctrl-k") == ("ab", 2)
    assert edit_line("abc", 1, "ctrl-a") == ("abc", 0)
    assert edit_line("abc", 1, "ctrl-e") == ("abc", 3)
    assert edit_line("abc", 1, "up") is None


@pytest.mark.parametrize(
    "text,call",
    [
        ("alert acme.de > 10", ("alert", "ACME.DE > 10")),
        ("unalert r1", ("unalert", "R1")),
        ("ack a1", ("ack", {"id": "A1"})),
        ("ack all", ("ack", {"all": True})),
        ("refresh", ("refresh", "PORT", None, None)),
        ("refresh quote", ("refresh", "PORT", "quote", None)),
        ("build mom", ("build", "MOM")),
        ("target equal", ("target", "EQUAL")),
        ("unwatch acme.de", ("unwatch", "ACME.DE")),
    ],
)
def test_commands_reach_the_server(app, api, text, call):
    typed(app, ":" + text)
    press(app, "enter")
    assert call in api.calls


def test_watch_one_match_adds_several_lets_you_pick(app, api):
    typed(app, ":watch acu.de")
    press(app, "enter")
    assert ("watch", "ACU.DE") in api.calls
    api.calls.clear()
    typed(app, ":watch ac")
    press(app, "enter")
    assert not api.writes("watch") and app.mode == "command" and "2 MATCHES" in screen(app)
    assert [i["label"] for i in app.ac["items"]] == ["WATCH ACU.DE", "WATCH ACU2"]


def test_quit_variants(app):
    for keys in ([":", "q", "enter"], [":", "w", "q", "enter"], ["Z", "Z"], ["Z", "Q"]):
        app.quit = False
        press(app, *keys)
        assert app.quit


def test_ctrl_c_and_q_do_not_quit(app):
    press(app, "ctrl-c")
    assert not app.quit and "TYPE :q TO QUIT" in screen(app)
    press(app, "q")
    assert not app.quit


def test_an_error_is_a_notice(app):
    typed(app, ":hlep")
    press(app, "enter")
    assert "UNKNOWN: HLEP" in screen(app)


def test_full_hides_the_bars(app):
    typed(app, ":full")
    press(app, "enter")
    assert app.zen and "F1" not in screen(app) and app.grid_top == 1
    typed(app, ":fs")
    press(app, "enter")
    assert not app.zen and "F1" in screen(app)


def test_cash_opens_a_browser(app, monkeypatch):
    opened = []
    monkeypatch.setattr(A.webbrowser, "open", opened.append)
    app.reg["cashflow_url"] = "http://localhost:9"
    typed(app, ":cash")
    press(app, "enter")
    assert opened == ["http://localhost:9"]


# ── search ───────────────────────────────────────────────────────────────────────────────────────
def test_slash_finds_as_you_type_enter_keeps_n_repeats(app):
    press(app, "/")
    assert app.mode == "search"
    typed(app, "tk1")
    assert cursor(app) == "TK1.DE"
    assert "/tk1" in screen(app)
    press(app, "enter")
    assert app.mode == "normal" and app.last_search == "tk1"
    press(app, "n")
    assert cursor(app) == "TK10.DE"
    press(app, "N")
    assert cursor(app) == "TK1.DE"


def test_esc_in_search_goes_back(app):
    press(app, "/")
    typed(app, "tk1")
    press(app, "esc")
    assert app.mode == "normal" and cursor(app) == "ACME.DE"


def test_n_with_nothing_found_says_so(app):
    app.last_search = "zzzz"
    press(app, "n")
    assert "NOT FOUND" in screen(app)


# ── sort, panels ─────────────────────────────────────────────────────────────────────────────────
def test_sort_keys(app):
    st = app.table_state(app.panel("positions"))
    assert st["sort"] == ["wt", "desc"]
    press(app, ">")
    assert st["sort"] == ["pnl", "desc"]
    press(app, "<", "<")
    assert st["sort"] == ["name", "desc"]
    press(app, "s")
    assert st["sort"] == ["name", "asc"]
    press(app, "shift-down")
    assert st["sort"] == ["name", "desc"]
    press(app, "shift-up", "shift-right")
    assert st["sort"] == ["wt", "desc"]


def test_the_cursor_keeps_its_row_through_a_sort(app):
    press(app, "j")
    press(app, ">", "s")
    assert cursor(app) == "GLOBEX.F"


def test_tab_and_ctrl_w_move_focus(app):
    press(app, "tab")
    assert app.focus == "summary"
    press(app, "shift-tab")
    assert app.focus == "corr"
    press(app, "ctrl-w", "w")
    assert app.focus == "summary"
    press(app, "ctrl-w", "l")
    assert app.focus == "acct"
    press(app, "ctrl-w", "j")
    assert app.focus in ("roi",)
    press(app, "ctrl-w", "h")
    assert app.focus == "positions"
    press(app, "ctrl-w", "k")
    assert app.focus == "summary"
    press(app, "ctrl-w", "j", "l")
    assert app.focus == "roi"


def test_maximize_restores_and_keeps_the_keys_on_what_shows(app):
    press(app, "z", "3")
    assert app.max == 3 and app.focus == "positions" and "SUMMARY" not in screen(app)
    press(app, "G")
    assert "TK25.DE" in screen(app) and "WT%" in screen(app)  # the header stays, the row scrolled to
    press(app, "esc")
    assert app.max is None and "SUMMARY" in screen(app)
    press(app, "alt-4")
    assert app.max == 4 and app.focus == "roi"
    press(app, "alt-4")
    assert app.max is None
    press(app, "ctrl-w", "o")
    assert app.max == 4
    press(app, "ctrl-w", "q")
    assert app.max is None
    press(app, "¡")  # a Mac's Option+1
    assert app.max == 1


def test_an_unknown_panel_number_does_nothing(app):
    press(app, "z", "9")
    assert app.max is None


# ── charts ───────────────────────────────────────────────────────────────────────────────────────
def focus_roi(app):
    press(app, "ctrl-w", "w")
    while app.focus != "roi":
        press(app, "tab")


def test_range_and_view_keys(app):
    press(app, "]")
    assert app.ranges["roi"] == "ALL"
    press(app, "[", "[")
    assert app.ranges["roi"] == "1M"
    assert app.modes.get("roi") is None
    press(app, "m")
    assert app.modes["roi"] == "MWR"
    press(app, "m", "m")
    assert app.modes["roi"] == "ROI"
    press(app, "alt-t")
    assert app.modes["roi"] == "TWR"
    press(app, "®", "µ")
    assert app.modes["roi"] == "MWR"


def test_horizon_keys_and_their_absence(app):
    press(app, "{")
    assert "NO CHART WITH A HORIZON" in screen(app)
    press(app, "g", "t", "}")
    assert app.horizons["path"] == "6M"
    press(app, "{")
    assert app.horizons["path"] == "1Y"


def test_the_chart_cursor_moves_the_legend(app):
    focus_roi(app)
    before = screen(app)
    assert "│" in before
    press(app, "h")
    assert app.cross["roi"] < 399
    press(app, "0")
    assert app.cross["roi"] == 0
    press(app, "$")
    assert app.cross["roi"] == len(app.plots["roi"]["model"]["x"]) - 1
    press(app, "b")
    at = app.cross["roi"]
    press(app, "w")
    assert app.cross["roi"] > at
    press(app, "esc")
    assert "roi" not in app.cross


def test_h_l_leave_a_table_but_stay_on_a_chart(app):
    press(app, "ctrl-w", "w")
    while app.focus != "positions":
        press(app, "tab")
    press(app, "l")
    assert app.focus == "roi"
    press(app, "l", "h")
    assert app.focus == "roi" and "roi" in app.cross


def test_visual_selects_a_period_and_zooms(app):
    focus_roi(app)
    press(app, "]")
    press(app, "0", "v")
    assert app.mode == "visual" and "-- VISUAL --" in screen(app)
    press(app, "w", "w")
    a, b = app.sel["roi"]
    assert a == 0 and b > 0
    press(app, "enter")
    assert app.mode == "normal" and isinstance(app.ranges["roi"], dict)
    x = app.plots["roi"]["model"]["x"]
    assert len(x) < 400
    assert "–" in screen(app)  # the chip naming the period
    press(app, "-")
    assert app.ranges["roi"] == "ALL"


def test_esc_leaves_visual_without_zooming(app):
    focus_roi(app)
    press(app, "v", "h", "esc")
    assert app.mode == "normal" and not isinstance(app.ranges.get("roi"), dict) and not app.sel


def test_a_zoom_of_nothing_says_so(app):
    focus_roi(app)
    press(app, "v", "enter")
    assert "SELECT A PERIOD" in screen(app)


def test_a_following_chart_follows_and_says_when_it_has_nothing(app):
    assert "ACME.DE · VALUE" in screen(app)
    press(app, "j")
    assert "GLOBEX.F · VALUE" in screen(app)
    assert "NO DATA" in screen(app)


def test_a_dragged_range_goes_when_the_row_changes(app):
    while app.focus != "posval":
        press(app, "tab")
    press(app, "0", "v", "w", "enter")
    assert isinstance(app.ranges["posval"], dict)
    from monitor.tui.panels import chart_model

    assert chart_model(app.panel("posval"), app)["dragged"]
    press(app, "j", "k")
    assert chart_model(app.panel("posval"), app)["dragged"]  # back on its row: its zoom is its own


# ── overlays, questions ──────────────────────────────────────────────────────────────────────────
def test_help_lists_definitions_commands_and_vim_keys(app):
    press(app, "?")
    s = screen(app)
    assert "HELP PORT" in s and "Total P&L over every euro" in s and "COMMANDS" in s
    press(app, "G")
    s = screen(app)
    assert ":q · ZZ" in s
    press(app, "g", "g")
    assert "COMMANDS" in screen(app)
    press(app, "esc")
    assert app.overlay is None
    press(app, "f1", "?")
    assert app.overlay is None


def test_help_of_another_screen(app):
    typed(app, ":help trades")
    press(app, "enter")
    assert "HELP TRADES" in screen(app)
    press(app, "q")
    typed(app, ":help sec")
    press(app, "enter")
    assert "HELP SEC <TICKER>" in screen(app)


def test_an_error_opens_in_full(app, api):
    press(app, "g", "e")
    assert "NO ERROR ON THIS SCREEN" in screen(app)
    app.live = {"error": {"tier": "quote", "error": "ValueError: boom", "trace": "line 1\nline 2"}}
    app.draw()
    assert "ERR PORT QUOTE" in screen(app)
    press(app, "g", "e")
    s = screen(app)
    assert "ERROR PORT QUOTE" in s and "ValueError: boom" in s and "line 2" in s


def to_trades(app, clock):
    press(app, "6")
    settle(app, clock)
    assert app.id == "TRADES"


def test_dd_asks_then_any_other_key_keeps(app, api, clock):
    to_trades(app, clock)
    press(app, "j", "k")
    press(app, "d", "d")
    assert "DELETE THIS TRADE?" in screen(app) and "01 FEB 26" in screen(app)
    press(app, "x")
    assert app.overlay is None and not api.writes("del_trade") and "NOTHING CHANGED" in screen(app)


def test_an_answer_within_400ms_is_no_answer(app, api, clock):
    to_trades(app, clock)
    press(app, "j", "k", "d", "d")
    clock.t += 0.1
    press(app, "enter")
    assert app.ask_state and not api.writes("del_trade")
    clock.t += 0.5
    press(app, "enter")
    assert not app.ask_state and api.writes("del_trade") == [("del_trade", "r1", "e1")]
    assert "DELETED · GONE" in screen(app)


def test_esc_closes_a_question_silently(app, api, clock):
    to_trades(app, clock)
    press(app, "j", "k", "d", "d", "esc")
    assert not app.ask_state and not api.writes("del_trade")


def test_del_only_right_after_a_cursor_move(app, api, clock):
    to_trades(app, clock)
    press(app, "del")
    assert not app.ask_state  # the table not focused yet
    press(app, "j", "del")
    assert app.ask_state
    press(app, "esc", ":", "esc", "del")
    assert not app.ask_state  # typed in the bar since


def test_dd_on_a_table_that_deletes_nothing(app):
    press(app, "j", "d", "d")
    assert "NOTHING TO DELETE HERE" in screen(app)


def test_a_row_a_question_names_vanishing_closes_it(app, api, clock):
    to_trades(app, clock)
    press(app, "j", "k", "d", "d")
    api.screens["TRADES"]["panels"][3]["rows"] = api.screens["TRADES"]["panels"][3]["rows"][1:]
    api.events and api.events("screen", {"key": "TRADES"})
    app.drain()
    app.draw()
    assert not app.ask_state and "THE TRADES CHANGED" in screen(app)


def test_a_screen_change_closes_a_question(app, api, clock):
    to_trades(app, clock)
    press(app, "j", "k", "d", "d")
    app.go("PORT")
    assert not app.ask_state


def test_undo_and_redo_ask(app, api, clock):
    press(app, "u")
    assert "UNDO THE LAST CHANGE?" in screen(app)
    clock.t += 1
    press(app, "enter")
    assert api.writes("undo") == [("undo", "e-now")]
    api.undo = {"what": "UNDO: ADD BUY", "ready": True}
    press(app, "ctrl-r")
    assert "REDO THE CHANGE" in screen(app)
    press(app, "esc")
    api.undo = {"what": "x", "ready": False}
    press(app, "u")
    assert "THE FILE CHANGED" in screen(app) and not app.ask_state
    api.undo = None
    press(app, "u")
    assert "NOTHING TO UNDO" in screen(app)


def test_start_fresh_asks_with_what_it_empties(app, api, clock):
    typed(app, ":start fresh")
    press(app, "enter")
    assert "START FRESH?" in screen(app) and "all 1 trade" in screen(app)
    clock.t += 1
    press(app, "enter")
    assert api.writes("reset") == [("reset", "e-now")]
    assert app.id == "TRADES"


# ── forms ────────────────────────────────────────────────────────────────────────────────────────
def test_o_steps_into_add_with_a_real_caret(app, clock):
    to_trades(app, clock)
    press(app, "o")
    assert app.mode == "insert" and app.field == ("add", "ticker") and "-- INSERT --" in screen(app)
    assert app.caret is not None
    press(app, "esc")
    assert app.mode == "normal" and app.field is None


def test_letters_type_in_a_field_never_navigate(app, api, clock):
    to_trades(app, clock)
    press(app, "o")
    typed(app, "jkG:")
    st = app.form_state(app.panel("add"))
    assert st["values"]["ticker"] == "jkG:" and app.mode == "insert"


def test_a_trade_from_the_form(app, api, clock):
    to_trades(app, clock)
    press(app, "o")
    typed(app, "ac")
    st = app.form_state(app.panel("add"))
    assert [s["ticker"] for s in st["sug"]["items"]][0] == "ACME.DE"  # your book first
    assert "IN YOUR BOOK" in screen(app)
    press(app, "ctrl-n", "enter")
    assert st["values"]["ticker"] == "ACME.DE" and app.field == ("add", "action")
    press(app, "l")
    assert st["values"]["action"] == "sell"
    press(app, "h", "tab")
    typed(app, "4")
    press(app, "tab")
    typed(app, "240")
    assert "= 960.00" in screen(app) and "BUY 4 ACME.DE · €240.00/sh = €960.00" in screen(app)
    press(app, "enter")
    ((_n, etag, body),) = api.writes("add_trade")
    assert (
        etag == "e1"
        and body["ticker"] == "ACME.DE"
        and body["pps"] == 240.0
        and body["shares"] == 4.0
        and body["action"] == "buy"
    )
    assert "SAVED · BUY 4 ACME.DE" in screen(app)
    fresh = app.form_state(app.panel("add"))
    assert fresh["values"]["ticker"] == "" and app.field == ("add", "ticker")  # the next trade


def test_typing_one_price_empties_the_other(app, clock):
    to_trades(app, clock)
    press(app, "o", "tab", "tab", "tab")
    typed(app, "9")
    press(app, "tab")
    typed(app, "5")
    st = app.form_state(app.panel("add"))
    assert st["values"]["total"] == "5" and st["values"]["pps"] == ""


def test_a_bad_form_says_why_and_stays(app, api, clock):
    to_trades(app, clock)
    press(app, "o")
    typed(app, "ACME.DE")
    press(app, "esc", "enter")  # the list closed, then out; Enter on the form panel steps back in
    assert app.mode == "insert"
    press(app, "enter")
    assert not api.writes("add_trade") and "SHARES" in screen(app)


def test_enter_on_a_row_edits_it(app, api, clock):
    to_trades(app, clock)
    press(app, "j", "k", "enter")
    assert app.field == ("add", "ticker") and "EDITING 01 FEB 26 ACME.DE BUY 4" in screen(app)
    assert "SAVE CHANGES" in screen(app)
    press(app, "enter")
    ((_n, rid, etag, body),) = api.writes("edit_trade")
    assert rid == "r1" and etag == "e1" and body["total"] == 961.0 and body["keep_pps"] is True and body["pps"] == 240.0


def test_cancel_edit_keeps_the_trade(app, api, clock):
    to_trades(app, clock)
    press(app, "j", "k", "enter", "shift-tab", "enter")
    assert app.form_state(app.panel("add"))["edit"] is None and not api.writes("edit_trade")


def test_a_trade_from_the_bar_uses_the_files_etag_elsewhere(app, api):
    typed(app, ":buy acme.de 4 @ 240 fee 1")
    press(app, "enter")
    ((_n, etag, body),) = api.writes("add_trade")
    assert etag == "e-now" and body["fee"] == 1.0
    assert "SAVED" in screen(app)


def test_a_conflict_reloads_trades(app, api, clock):
    to_trades(app, clock)
    press(app, "o")
    typed(app, "ACME.DE")
    press(app, "esc", "tab", "tab")
    typed(app, "1")
    press(app, "tab")
    typed(app, "1")
    api.fail_next = ApiError(409, "YOUR TRADES CHANGED MEANWHILE — RELOADED: CHECK AND SAVE AGAIN")
    n = len(api.writes("screen"))
    press(app, "enter")
    assert "CHANGED MEANWHILE" in screen(app) and len(api.writes("screen")) == n + 1
    assert app.form_state(app.panel("add"))["values"]["ticker"] == "ACME.DE"  # what you typed stays


def test_a_refresh_keeps_what_you_typed(app, api, clock):
    to_trades(app, clock)
    press(app, "o")
    typed(app, "AC")
    api.events("screen", {"key": "TRADES"})
    app.drain()
    app.draw()
    assert app.form_state(app.panel("add"))["values"]["ticker"] == "AC" and app.mode == "insert"


# ── paste ────────────────────────────────────────────────────────────────────────────────────────
def into_paste(app, clock):
    to_trades(app, clock)
    while app.focus != "paste":
        press(app, "tab")
    press(app, "i")
    assert app.field == ("paste", "text")


def test_a_paste_is_previewed_and_never_runs_as_keys(app, api, clock):
    into_paste(app, clock)
    press(app, ("paste", "a,b\r\nc:q\n"))
    st = app.form_state(app.panel("paste"))
    assert st["text"] == "a,b\nc:q\n" and not app.quit
    assert api.writes("preview") == [("preview", "a,b\nc:q\n", "append")]
    s = screen(app)
    assert "2 ROWS · 1 TO ADD · 1 WITH AN ERROR" in s and "ADD 1 TRADE" in s and "SHARES: bad" in s


def test_typing_in_the_box_enter_is_a_newline(app, api, clock):
    into_paste(app, clock)
    typed(app, "ab")
    press(app, "enter")
    typed(app, "c")
    st = app.form_state(app.panel("paste"))
    assert st["text"] == "ab\nc" and st["caret"] == 4
    press(app, "up")
    assert st["caret"] == 1
    press(app, "down")
    assert st["caret"] == 4
    press(app, "ctrl-g")
    assert api.writes("preview")[-1][1] == "ab\nc"


def test_add_sends_exactly_what_was_previewed(app, api, clock):
    into_paste(app, clock)
    press(app, ("paste", "row1"))
    press(app, "tab", "tab", "tab")
    assert app.field == ("paste", "@add")
    press(app, "enter")
    ((_n, etag, text, mode),) = api.writes("import")
    assert (etag, text, mode) == ("e1", "row1", "append")
    assert "ADDED 1 TRADE · 1 LINE LEFT IN THE BOX TO FIX" in screen(app)


def test_add_after_the_box_changed_previews_again(app, api, clock):
    into_paste(app, clock)
    press(app, ("paste", "row1"))
    app.form_state(app.panel("paste"))["text"] = "row2"
    press(app, "tab", "tab", "tab", "enter")
    assert not api.writes("import") and "PREVIEWED AGAIN" in screen(app)


def test_import_reads_a_file_utf8_or_excel(app, api, clock, tmp_path):
    (tmp_path / "broker.csv").write_bytes("Crème;1\n".encode("cp1252"))
    into_paste(app, clock)
    press(app, "esc")
    typed(app, f":import {tmp_path}/brok")
    press(app, "tab")
    assert app.bar == f"IMPORT {tmp_path / 'broker.csv'}"  # the system's own separator, whichever was typed
    press(app, "enter")
    st = app.form_state(app.panel("paste"))
    assert st["text"] == "Crème;1\n" and st["file"] == "broker.csv" and "FROM broker.csv" in screen(app)
    assert "REPLACE MY 2 TRADES WITH THESE 1" in screen(app)


def test_replace_asks_first(app, api, clock, tmp_path):
    (tmp_path / "b.csv").write_text("x", encoding="utf-8")
    into_paste(app, clock)
    press(app, "esc")
    typed(app, f":import {tmp_path}/b.csv")
    press(app, "enter", "i", "tab", "tab", "tab", "tab")
    assert app.field == ("paste", "@replace")
    press(app, "enter")
    assert "REPLACE YOUR TRADES?" in screen(app) and not api.writes("import")
    clock.t += 1
    press(app, "enter")
    assert api.writes("import") == [("import", "e-now", "x", "replace")]


def test_import_refuses_what_it_cannot_read(app, clock, tmp_path):
    typed(app, f":import {tmp_path}/none.csv")
    press(app, "enter")
    assert "OPEN TRADES FIRST" in screen(app)
    to_trades(app, clock)
    typed(app, f":import {tmp_path}/none.csv")
    press(app, "enter")
    assert "CANNOT READ none.csv" in screen(app)


# ── the mouse ────────────────────────────────────────────────────────────────────────────────────
def click(app, x, y):
    press(app, ("mouse", "press", 0, x, y), ("mouse", "release", 0, x, y))


def find(app, text):
    for y, row in reversed(list(enumerate(app.canvas.ch))):  # from the bottom: the key bar before the strip
        line = "".join(c or " " for c in row)
        x = line.find(text)
        if x >= 0:
            return x, y
    raise AssertionError(f"{text!r} not on screen")


def test_click_a_row_a_header_a_chip_a_key(app, clock):
    click(app, *find(app, "GLOBEX.F"))
    assert cursor(app) == "GLOBEX.F" and app.focus == "positions"
    x, y = find(app, "P&L")
    click(app, x, y)
    assert app.table_state(app.panel("positions"))["sort"] == ["pnl", "desc"]
    click(app, x, y)
    assert app.table_state(app.panel("positions"))["sort"] == ["pnl", "asc"]
    click(app, *find(app, " MWR "))
    assert app.modes["roi"] == "MWR"
    click(app, *find(app, " ALL "))
    assert app.ranges["roi"] == "ALL"
    click(app, *find(app, " OPT "))
    assert app.id == "OPT"
    click(app, *find(app, " OPT "))  # its own key again closes it: back
    assert app.id == "PORT"
    click(app, *find(app, " PORT "))  # home's own key at home: nothing
    assert app.id == "PORT"


def test_click_a_panel_title_focuses_it(app):
    click(app, *find(app, "ACCOUNTING"))
    assert app.focus == "acct"


def test_drag_on_a_chart_zooms_and_a_double_click_goes_back(app, clock):
    click(app, *find(app, " ALL "))
    assert "roi" not in app.plots or app.plots["roi"]
    plot = app.plots["roi"]
    r = plot["raster"]
    y = app.rects["roi"][1] + app.grid_top + 3 - app.scroll
    x0, x1 = plot["x"] + r.left + 2, plot["x"] + r.left + 12
    press(app, ("mouse", "press", 0, x0, y), ("mouse", "drag", 0, x1, y))
    assert "roi" in app.sel
    press(app, ("mouse", "release", 0, x1, y))
    assert isinstance(app.ranges["roi"], dict) and not app.sel
    clock.t += 1
    press(app, ("mouse", "press", 0, x0, y), ("mouse", "release", 0, x0, y))
    clock.t += 0.1
    press(app, ("mouse", "press", 0, x0, y), ("mouse", "release", 0, x0, y))
    assert app.ranges["roi"] == "ALL"


def test_a_click_on_a_question(app, api, clock):
    press(app, "u")
    x, y = find(app, " UNDO ↵ ")
    click(app, x, y)
    assert app.ask_state and not api.writes("undo")  # a double-click is no answer
    clock.t += 1
    click(app, x, y)
    assert api.writes("undo")
    press(app, "u")
    click(app, *find(app, " KEEP · Esc "))
    assert not app.ask_state and len(api.writes("undo")) == 1
    press(app, "u")
    click(app, 0, app.size[1] - 2)
    assert not app.ask_state and "NOTHING CHANGED" in screen(app)


def test_click_a_form_field_and_a_palette_row(app, clock):
    typed(app, ":op")
    x, y = find(app, "Optimizer")
    click(app, x, y)
    assert app.id == "OPT"
    press(app, "g", "T")
    to_trades(app, clock)
    click(app, *find(app, "how many"))
    assert app.field == ("add", "shares") and app.mode == "insert"
    click(app, *find(app, " CLEAR "))
    assert app.field == ("add", "ticker")


def test_clicks_land_on_an_overlay_not_under_it(app):
    press(app, "?")
    press(app, ("mouse", "press", 0, app.size[0] // 2, app.size[1] // 2))
    assert app.overlay and app.focus is None and cursor(app) == "ACME.DE"
    click(app, *find(app, " HELP "))  # HELP's key again closes it
    assert not app.overlay


def test_the_wheel_scrolls(app):
    app.size = (160, 20)
    app.draw()
    press(app, ("mouse", "wheel", 1, 5, 5))
    assert app.scroll == 3
    press(app, "?", ("mouse", "wheel", 1, 5, 5))
    assert app.overlay["top"] == 3


# ── the server's events ──────────────────────────────────────────────────────────────────────────
def test_a_screen_event_reloads_and_changed_cells_flash(app, api):
    api.screens["PORT"]["panels"][0]["items"][0]["v"] = 2000.0
    api.events("screen", {"key": "PORT"})
    app.drain()
    assert "summary|VALUE|v" in app.flashes
    app.draw()
    assert "€2,000.00" in screen(app)


def test_another_screens_event_is_ignored(app, api):
    n = len(api.writes("screen"))
    api.events("screen", {"key": "OPT"})
    app.drain()
    assert len(api.writes("screen")) == n


def test_a_flash_fades_and_ends(app, clock):
    app.flashes["k"] = clock.t + 1.2
    assert app.flash_bg("k") is not None
    clock.t += 2
    assert app.flash_bg("k") is None and "k" not in app.flashes


def test_a_flash_out_of_view_ends_too(app, clock):
    app.flashes["never-drawn"] = clock.t + 1.2
    clock.t += 2
    app.tick()
    assert not app.flashes


def test_status_line(app, api):
    s = screen(app)
    assert "▲ 1 ALERT" in s and "● LIVE" in s and "STALE 1 INITECH.DE" in s and "CCY USD: X" in s
    api.events("conn", {"ok": False})
    app.drain()
    app.draw()
    assert "● OFFLINE" in screen(app)
    n = len(api.writes("screen"))
    api.events("conn", {"ok": True})
    app.drain()
    app.draw()
    assert "● LIVE" in screen(app) and len(api.writes("screen")) == n + 1  # back: re-synced


def test_a_narrow_window_drops_the_clock_first_never_live_or_alerts(app):
    app.size = (70, 30)
    app.draw()
    top = "".join(app.canvas.ch[0])
    assert "● LIVE" in top and "▲ 1 ALERT" in top and "26 " not in top[-15:]


def test_alert_events(app, api):
    api.events("alerts", {"active": 2, "down": False, "new": [{"id": "a2", "msg": "ACME UP", "down": False}]})
    app.drain()
    app.draw()
    assert "▲ 2 ALERTS" in screen(app) and "ALERT a2 · ACME UP" in screen(app)


def test_job_events(app, api):
    api.events("job", {"screen": "PORT", "tier": "quote", "state": "running"})
    app.drain()
    app.draw()
    assert "UPDATING QUOTE" in screen(app)
    api.events("job", {"screen": "PORT", "tier": "quote", "state": "failed", "error": "boom"})
    app.drain()
    app.draw()
    assert "ERR PORT QUOTE" in screen(app)
    api.events("job", {"screen": "PORT", "tier": "quote", "state": "done"})
    app.drain()
    app.draw()
    assert "ERR PORT QUOTE" not in screen(app)
    api.events("job", {"screen": "MOM", "tier": "heavy", "force": True, "state": "running", "progress": "2/7 GRID"})
    app.drain()
    app.draw()
    assert "MOM BUILD 2/7 GRID" in screen(app)
    api.events(
        "job", {"screen": "MOM", "tier": "heavy", "force": True, "state": "failed", "error": "BuildFailed: out of disk"}
    )
    app.drain()
    app.draw()
    assert "ERR MOM · out of disk" in screen(app)


def test_a_cold_screen_says_why(app, api):
    api.screens["ALRT"] = {"screen": "ALRT", "cold": True, "reason": "NO TRADES YET — press 6", "live": {}}
    app.go("ALRT")
    app.draw()
    assert "NO TRADES YET — press 6" in screen(app) and "NO STORED DATA YET" in screen(app)


def test_a_failed_load_keeps_the_screen_and_says_so(app, api):
    api.fail_next = ApiError(0, "SERVER UNREACHABLE")
    app.load()
    app.draw()
    assert "LOAD PORT FAILED: SERVER UNREACHABLE" in screen(app) and "SUMMARY" in screen(app)


def test_a_panel_that_cannot_draw_never_takes_the_screen(api, clock):
    a = App(api, sync=True)
    a.size = (160, 48)
    api.screens["PORT"]["panels"][2]["cols"] = "nonsense"
    a.start()
    a.draw()
    assert "PANEL positions FAILED" in "\n".join("".join(r) for r in a.canvas.ch)
    assert "SUMMARY" in "\n".join("".join(r) for r in a.canvas.ch)


def test_tick_pokes_the_server_on_its_interval(app, api, clock):
    calls = []
    api.ensure = lambda sid, param=None: calls.append(sid) or {"jobs": [{"tier": "daily"}], "live": {"running": []}}
    clock.t += 61
    app.tick()
    app.drain()
    assert calls == ["PORT"] and "daily" in app.running
    app.tick()
    assert calls == ["PORT"]


def test_today_is_a_date(app):
    assert isinstance(app.today(), date)
