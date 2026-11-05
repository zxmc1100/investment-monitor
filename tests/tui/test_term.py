"""The terminal layer: keys and the mouse as events, text fit for the screen, painting by difference."""

from monitor.tui.term import BASE, Canvas, Keys, Painter, c256, clean, clip, pad, style, truecolor, tw, wrap


def keys(data: bytes) -> list:
    k = Keys()
    return k.feed(data) + k.timeout()


def test_plain_and_control_keys():
    assert keys(b"jk:") == ["j", "k", ":"]
    assert keys(b"\r\t\x7f\x04\x15") == ["enter", "tab", "backspace", "ctrl-d", "ctrl-u"]
    assert keys("€".encode()) == ["€"]


def test_csi_and_ss3():
    assert keys(b"\x1b[A\x1b[B\x1b[C\x1b[D") == ["up", "down", "right", "left"]
    assert keys(b"\x1bOA\x1bOP") == ["up", "f1"]
    assert keys(b"\x1b[5~\x1b[6~\x1b[3~\x1b[H\x1b[F\x1b[Z") == ["pgup", "pgdn", "del", "home", "end", "shift-tab"]
    assert keys(b"\x1b[1;2A\x1b[1;2D\x1b[1;3B") == ["shift-up", "shift-left", "alt-down"]


def test_alt_is_esc_then_key():
    assert keys(b"\x1b1\x1br") == ["alt-1", "alt-r"]


def test_a_lone_esc_waits_for_the_timeout():
    k = Keys()
    assert k.feed(b"\x1b") == []
    assert k.waiting
    assert k.timeout() == ["esc"]
    assert not k.waiting


def test_a_sequence_split_across_reads_stays_one_key():
    k = Keys()
    assert k.feed(b"\x1b[") == [] and k.feed(b"1;") == []
    assert k.feed(b"2A") == ["shift-up"]
    assert k.feed("é".encode()[:1]) == []
    assert k.feed("é".encode()[1:]) == ["é"]


def test_unknown_sequences_never_type():
    assert keys(b"\x1b[99~\x1b[?1;2cj") == ["j"]


def test_mouse_sgr():
    assert keys(b"\x1b[<0;10;5M") == [("mouse", "press", 0, 9, 4)]
    assert keys(b"\x1b[<0;10;5m") == [("mouse", "release", 0, 9, 4)]
    assert keys(b"\x1b[<32;11;5M") == [("mouse", "drag", 0, 10, 4)]
    assert keys(b"\x1b[<64;1;1M\x1b[<65;1;1M") == [("mouse", "wheel", -1, 0, 0), ("mouse", "wheel", 1, 0, 0)]


def test_bracketed_paste_is_one_event_whatever_it_holds():
    assert keys(b"\x1b[200~a\rb\x1b[Ac\x1b[201~j") == [("paste", "a\rb\x1b[Ac"), "j"]


def test_a_paste_split_mid_end_marker():
    k = Keys()
    assert k.feed(b"\x1b[200~hello\x1b[2") == []
    assert k.timeout() == []  # a paste has no timeout
    assert k.feed(b"01~") == [("paste", "hello")]


def test_clean_keeps_nothing_the_terminal_would_obey():
    """A name from Yahoo or a file must never reach the terminal as a command: ESC, BEL, C1, bidi overrides."""
    assert clean("A\x1b]52;c;aGk=\x07B") == "A]52;c;aGk=B"
    assert clean("x\x9b31my") == "x31my"
    assert clean("a‮b​c") == "abc"
    assert clean("a\tb\nc d") == "a b c d"
    assert clean("Moët") == "Moët"
    assert clean("plain ascii") == "plain ascii"


def test_widths_clip_pad_wrap():
    assert tw("€") == 1 and tw("株式") == 4
    assert clip("abcdef", 4) == "abc…" and clip("abc", 4) == "abc" and clip("abc", 0) == ""
    assert clip("株式会社", 5) == "株式…"
    assert pad("ab", 4) == "ab  " and pad("ab", 4, True) == "  ab"
    assert wrap("one two three", 7) == ["one two", "three"]
    assert wrap("abcdefgh", 3) == ["abc", "def", "gh"]


def test_canvas_clips_and_views_share_cells():
    c = Canvas(6, 2)
    v = c.view(2, 1, 3, 1)
    assert v.put(0, 0, "abcdef") == 3 + 1
    assert "".join(c.ch[1]) == "  abc "
    assert v.put(0, 5, "nope") == 0
    c.put(-1, 0, "xyz")
    assert "".join(c.ch[0]) == "yz    "


def test_overwriting_half_a_wide_character_leaves_no_ghost():
    c = Canvas(4, 1)
    c.put(0, 0, "株")
    assert c.ch[0][:2] == ["株", ""]
    c.put(1, 0, "x")
    assert c.ch[0][:2] == [" ", "x"]


def test_painter_writes_only_what_changed():
    out = []
    p = Painter(out.append, True)
    c = Canvas(10, 2)
    c.put(0, 0, "hello")
    p.paint(c)
    assert "hello" in out[0] and "\x1b[2J" in out[0]
    d = Canvas(10, 2)
    d.put(0, 0, "hullo")
    p.paint(d)
    assert "\x1b[1;2H" in out[1] and "u" in out[1] and "hullo" not in out[1] and "\x1b[2J" not in out[1]
    e = Canvas(10, 2)
    e.put(0, 0, "hullo")
    p.paint(e)
    assert len(out) == 2  # nothing changed: nothing written
    p.paint(e, (3, 1))
    assert out[2].endswith("\x1b[2;4H\x1b[?25h\x1b[?2026l")  # only the caret moved


def test_painter_colour_depth():
    t, c = [], []
    a = Canvas(1, 1, style("#ffa028", "#000000", True))
    Painter(t.append, True).paint(a)
    Painter(c.append, False).paint(a)
    assert "38;2;255;160;40" in t[0] and ";1m" in t[0]
    assert "38;5;" in c[0] and "38;2" not in c[0]
    assert c256("#000000") in (16, 232) and c256("#ffffff") in (231, 255)


def test_painter_starts_over_after_a_resize():
    out = []
    p = Painter(out.append, True)
    p.paint(Canvas(5, 2))
    p.paint(Canvas(6, 2))
    assert "\x1b[2J" in out[1]


def test_truecolor_detection():
    assert truecolor({"COLORTERM": "truecolor"})
    assert truecolor({"TERM_PROGRAM": "iTerm.app"})
    assert not truecolor({"TERM_PROGRAM": "Apple_Terminal", "TERM": "xterm-256color"})
    assert BASE == style()
