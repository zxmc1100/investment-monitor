"""The terminal itself: raw mode, keys and the mouse as events, and a canvas of cells painted by difference.
ANSI escapes rather than curses: curses has no Windows build and no 24-bit colour."""

from __future__ import annotations

import codecs
import os
import re
import sys
import time
import unicodedata

# web/app/terminal.css :root
BG, FG, AMBER, CMD, UP, DN, DIM, RULE = (
    "#000000",
    "#ffffff",
    "#ffa028",
    "#ffbf00",
    "#00e676",
    "#ff3d3d",
    "#8a8a8a",
    "#2b2b2b",
)
CURSOR, FLASH, FIELD, GHOST, NOTE_BG, DN_BG, TEXT = (
    "#3a2a00",
    "#55360d",
    "#111111",
    "#222222",
    "#1a1200",
    "#1a0000",
    "#dddddd",
)
BAR_BG, STRIP_BG, OVER_BG, SEL_BG = "#1a1a1a", "#0d0d0d", "#050505", "#2e1d07"
CLS = {"up": UP, "dn": DN, "dim": DIM, "na": DIM, "am": AMBER, "": None}


def style(fg: str = FG, bg: str = BG, bold: bool = False) -> tuple:
    return (fg, bg, bold)


BASE = style()


def blend(color: str, alpha: float, over: str = BG) -> str:
    """`color` at `alpha` over `over`: what a translucent CSS colour looks like, as a solid one."""
    a, b = rgb(color), rgb(over)
    return "#%02x%02x%02x" % tuple(round(x * alpha + y * (1 - alpha)) for x, y in zip(a, b))


def rgb(color: str) -> tuple:
    h = color.lstrip("#")
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))


# ── text ─────────────────────────────────────────────────────────────────────────────────────────
def clean(text) -> str:
    """Text fit for the screen. Names come from Yahoo and your own files: a control character in one must never reach
    the terminal, where ESC starts a command. White space becomes a space; invisible and combining ones go."""
    s = str(text)
    if s.isascii() and s.isprintable():
        return s
    out = []
    for c in unicodedata.normalize("NFC", s):
        if c.isspace():
            out.append(" ")
        elif c.isprintable() and unicodedata.category(c) not in ("Mn", "Me", "Cf"):
            out.append(c)
    return "".join(out)


def _cell_width(c: str) -> int:
    return 1 if ord(c) < 0x300 else 2 if unicodedata.east_asian_width(c) in "WF" else 1


def tw(text) -> int:
    """Width on screen, in cells."""
    s = clean(text)
    return len(s) if s.isascii() else sum(_cell_width(c) for c in s)


def clip(text, n: int) -> str:
    """At most `n` cells, an ellipsis marking what was cut (text-overflow: ellipsis)."""
    s = clean(text)
    if n <= 0:
        return ""
    if tw(s) <= n:
        return s
    out, w = [], 0
    for c in s:
        w += _cell_width(c)
        if w > n - 1:
            break
        out.append(c)
    return "".join(out) + "…"


def pad(text, n: int, right: bool = False) -> str:
    s = clip(text, n)
    gap = " " * max(0, n - tw(s))
    return gap + s if right else s + gap


def wrap(text, n: int) -> list[str]:
    """Words onto lines of at most `n` cells; a word longer than a line is cut across lines."""
    n = max(1, n)
    out, cur = [], ""
    for word in clean(text).split(" "):
        while tw(word) > n:
            if cur:
                out.append(cur)
                cur = ""
            out.append(word[:n])
            word = word[n:]
        if cur and tw(cur) + 1 + tw(word) > n:
            out.append(cur)
            cur = word
        else:
            cur = f"{cur} {word}" if cur else word
    out.append(cur)
    return out


# ── canvas ───────────────────────────────────────────────────────────────────────────────────────
class Canvas:
    """A rectangle of cells: `ch` one string per cell ("" behind a wide character), `st` its style. A view shares its
    parent's cells and clips to its own rectangle."""

    def __init__(self, w: int, h: int, fill: tuple = BASE):
        self.w, self.h, self.ox, self.oy = w, h, 0, 0
        self.ch = [[" "] * w for _ in range(h)]
        self.st = [[fill] * w for _ in range(h)]

    def view(self, x: int, y: int, w: int, h: int) -> "Canvas":
        v = Canvas.__new__(Canvas)
        v.ch, v.st, v.ox, v.oy = self.ch, self.st, self.ox + x, self.oy + y
        v.w, v.h = max(0, min(w, self.w - x)), max(0, min(h, self.h - y))
        return v

    def put(self, x: int, y: int, text, st: tuple = BASE) -> int:
        """Write `text` at x, y (clipped); the x it ends at."""
        if not 0 <= y < self.h:
            return x
        i = self.oy + y
        ch, sts = self.ch[i], self.st[i]
        for c in clean(text):
            n = _cell_width(c)
            if x + n > self.w:
                return x + n
            if x >= 0:
                j = self.ox + x
                if ch[j] == "" and j:  # half of a wide character goes: the other half must too
                    ch[j - 1] = " "
                if j + n < len(ch) and ch[j + n] == "":
                    ch[j + n] = " "
                ch[j], sts[j] = c, st
                if n == 2:
                    ch[j + 1], sts[j + 1] = "", st
            x += n
        return x

    def fill(self, x: int, y: int, w: int, h: int, st: tuple = BASE, c: str = " ") -> None:
        for r in range(max(0, y), min(self.h, y + h)):
            i = self.oy + r
            for j in range(self.ox + max(0, x), self.ox + min(self.w, x + w)):
                self.ch[i][j], self.st[i][j] = c, st

    def paint_bg(self, x: int, y: int, w: int, bg: str) -> None:
        """Give cells a background, keeping their text and colour (a cursor row, a selection)."""
        if not 0 <= y < self.h:
            return
        row = self.st[self.oy + y]
        for j in range(self.ox + max(0, x), self.ox + min(self.w, x + w)):
            s = row[j]
            row[j] = (s[0], bg, s[2])


# ── painting ─────────────────────────────────────────────────────────────────────────────────────
def truecolor(env=os.environ) -> bool:
    """Does this terminal take 24-bit colour? Those that do mostly say so; Apple's Terminal has long said nothing."""
    if env.get("COLORTERM", "").lower() in ("truecolor", "24bit") or env.get("WT_SESSION"):
        return True
    if env.get("TERM_PROGRAM") in ("iTerm.app", "WezTerm", "ghostty", "vscode", "Hyper", "rio", "tmux"):
        return True
    return any(t in env.get("TERM", "") for t in ("kitty", "alacritty", "ghostty", "foot", "wezterm"))


def c256(color: str) -> int:
    """The nearest of xterm's 256 colours: the grey ramp or the 6x6x6 cube, whichever is closer."""
    r, g, b = rgb(color)
    cube = [0, 95, 135, 175, 215, 255]
    near = lambda v: min(range(6), key=lambda i: abs(cube[i] - v))
    ci = (near(r), near(g), near(b))
    grey = min(range(24), key=lambda i: abs(8 + 10 * i - (r + g + b) / 3))
    dist = lambda a: sum((x - y) ** 2 for x, y in zip(a, (r, g, b)))
    if dist((8 + 10 * grey,) * 3) < dist(tuple(cube[i] for i in ci)):
        return 232 + grey
    return 16 + 36 * ci[0] + 6 * ci[1] + ci[2]


class Painter:
    """Turns a canvas into the escapes that change the screen from the last one: only cells that differ are written."""

    def __init__(self, write, color24: bool = True):
        self.write, self.color24 = write, color24
        self.prev: tuple | None = None
        self.cursor: tuple | None = None
        self.codes: dict = {}

    def invalidate(self) -> None:
        self.prev = None

    def sgr(self, s: tuple) -> str:
        code = self.codes.get(s)
        if code is None:

            def part(prefix, color):
                return f"{prefix};2;{';'.join(map(str, rgb(color)))}" if self.color24 else f"{prefix};5;{c256(color)}"

            code = f"\x1b[0;{part(38, s[0])};{part(48, s[1])}{';1' if s[2] else ''}m"
            self.codes[s] = code
        return code

    def paint(self, c: Canvas, cursor: tuple | None = None) -> None:
        same = (
            self.prev if self.prev and len(self.prev[0]) == c.h and (not c.h or len(self.prev[0][0]) == c.w) else None
        )
        out = [] if same else ["\x1b[2J"]
        cur = None
        for y in range(c.h):
            ch, st = c.ch[y], c.st[y]
            pch, pst = (same[0][y], same[1][y]) if same else (None, None)
            if same and pch == ch and pst == st:
                continue
            x = 0
            while x < c.w:
                if same and ch[x] == pch[x] and st[x] == pst[x]:
                    x += 1
                    continue
                if ch[x] == "" and x:  # the cell behind a wide character: rewrite from its start
                    x -= 1
                out.append(f"\x1b[{y + 1};{x + 1}H")
                while x < c.w and not (same and ch[x] == pch[x] and st[x] == pst[x]):
                    if st[x] != cur:
                        cur = st[x]
                        out.append(self.sgr(cur))
                    out.append(ch[x])
                    x += 1
        self.prev = ([r[:] for r in c.ch], [r[:] for r in c.st])
        if not out and cursor == self.cursor:  # nothing changed: nothing to write (the caret stays still, unblinking)
            return
        self.cursor = cursor
        # synchronized where the terminal takes it: no half-painted frame
        self.write(
            "\x1b[?2026h\x1b[?25l"
            + "".join(out)
            + (f"\x1b[{cursor[1] + 1};{cursor[0] + 1}H\x1b[?25h" if cursor else "")
            + "\x1b[?2026l"
        )


# ── keys ─────────────────────────────────────────────────────────────────────────────────────────
CSI_FINAL = {"A": "up", "B": "down", "C": "right", "D": "left", "H": "home", "F": "end", "Z": "shift-tab", "P": "f1"}
TILDE = {
    "1": "home",
    "2": "insert",
    "3": "del",
    "4": "end",
    "5": "pgup",
    "6": "pgdn",
    "7": "home",
    "8": "end",
    "11": "f1",
}
MODS = {2: "shift-", 3: "alt-", 4: "alt-shift-", 5: "ctrl-", 6: "ctrl-shift-", 7: "ctrl-alt-"}
CTRL = {
    0x09: "tab",
    0x0A: "enter",
    0x0D: "enter",
    0x08: "backspace",
    0x7F: "backspace",
    0x1B: "esc",
    0x00: "ctrl-space",
    0x1E: "ctrl-^",
    0x1F: "ctrl-_",
    0x1C: "ctrl-\\",
    0x1D: "ctrl-]",
}
_CSI = re.compile(r"\x1b\[([<?]?[\d;:]*)([A-Za-z~])")
_PASTE_END = "\x1b[201~"
ESC_WAIT_S = 0.03  # a lone ESC this long with nothing behind it is the Esc key


def _plain(c: str) -> str:
    o = ord(c)
    if o in CTRL:
        return CTRL[o]
    if o < 0x20:
        return f"ctrl-{chr(o + 96)}"
    return c


class Keys:
    """Bytes from the terminal -> events: a key's name ("j", "ctrl-d", "alt-1", "shift-up", "esc" …), ("paste", text) or
    ("mouse", kind, button, x, y) with kind press | drag | release | wheel, x and y from 0."""

    def __init__(self):
        self.buf = ""
        self.dec = codecs.getincrementaldecoder("utf-8")(errors="replace")
        self.paste: list | None = None

    def feed(self, data: bytes) -> list:
        self.buf += self.dec.decode(data)
        return self._parse()

    def timeout(self) -> list:
        """Nothing more came: a held back ESC was the key itself; a sequence cut off is dropped with it."""
        if self.paste is None and self.buf.startswith("\x1b"):
            self.buf = self.buf[1:]
            return ["esc", *self._parse()]
        return []

    @property
    def waiting(self) -> bool:
        return self.paste is None and self.buf.startswith("\x1b")

    def _parse(self) -> list:
        out = []
        while self.buf:
            if self.paste is not None:
                end = self.buf.find(_PASTE_END)
                if end < 0:  # keep a possibly split end marker for the next read
                    cut = max(0, len(self.buf) - len(_PASTE_END) + 1)
                    self.paste.append(self.buf[:cut])
                    self.buf = self.buf[cut:]
                    return out
                self.paste.append(self.buf[:end])
                self.buf = self.buf[end + len(_PASTE_END) :]
                out.append(("paste", "".join(self.paste)))
                self.paste = None
                continue
            c = self.buf[0]
            if c != "\x1b":
                self.buf = self.buf[1:]
                out.append(_plain(c))
                continue
            if len(self.buf) == 1:
                return out  # wait: ESC alone, or the start of a sequence
            n = self.buf[1]
            if n == "[":
                m = _CSI.match(self.buf)
                if not m:
                    if re.fullmatch(r"\x1b\[[<?\d;:]*", self.buf):
                        return out  # incomplete: more is on its way
                    self.buf = self.buf[2:]  # not a sequence we can read: never let it type
                    continue
                self.buf = self.buf[m.end() :]
                ev = self._csi(m.group(1), m.group(2))
                if ev == "paste-start":
                    self.paste = []
                elif ev is not None:
                    out.append(ev)
            elif n == "O":
                if len(self.buf) < 3:
                    return out
                f = self.buf[2]
                self.buf = self.buf[3:]
                k = CSI_FINAL.get(f)
                if k:
                    out.append(k)
            elif n == "\x1b":
                self.buf = self.buf[1:]
                out.append("esc")
            else:
                self.buf = self.buf[2:]
                out.append("alt-" + _plain(n))
        return out

    @staticmethod
    def _csi(params: str, final: str):
        if params.startswith("<"):
            return Keys._mouse(params[1:], final)
        nums = params.lstrip("?").split(";")
        if final == "~":
            if nums[0] == "200":
                return "paste-start"
            name = TILDE.get(nums[0])
        else:
            name = CSI_FINAL.get(final)
        if name is None:
            return None
        try:
            mod = int(nums[1]) if len(nums) > 1 and nums[1] else 1
        except ValueError:
            mod = 1
        return MODS.get(mod, "") + name

    @staticmethod
    def _mouse(params: str, final: str):
        try:
            b, x, y = (int(v) for v in params.split(";")[:3])
        except ValueError:
            return None
        if final not in "Mm":
            return None
        if b & 64:
            return ("mouse", "wheel", -1 if b & 1 == 0 else 1, x - 1, y - 1)
        kind = "release" if final == "m" else "drag" if b & 32 else "press"
        return ("mouse", kind, b & 3, x - 1, y - 1)


# ── the terminal ─────────────────────────────────────────────────────────────────────────────────
ENTER = "\x1b[?1049h\x1b[?25l\x1b[?7l\x1b[?2004h\x1b[?1002h\x1b[?1006h\x1b]0;Investment Monitor\x07\x1b[6 q"
LEAVE = "\x1b[0 q\x1b[?1006l\x1b[?1002l\x1b[?2004l\x1b[?7h\x1b[0m\x1b[?25h\x1b[?1049l"


class Terminal:
    """Raw mode for as long as it is entered; everything it changed is put back on the way out, whatever happened."""

    def __init__(self, fd_in: int = 0, fd_out: int = 1):
        self.fd_in, self.fd_out = fd_in, fd_out
        self.saved = None
        self.wake_r, self.wake_w = os.pipe() if os.name != "nt" else (None, None)

    def write(self, text: str) -> None:
        data, n = text.encode("utf-8", "replace"), 0
        while n < len(data):
            n += os.write(self.fd_out, data[n:])

    def size(self) -> tuple[int, int]:
        try:
            s = os.get_terminal_size(self.fd_out)
            return max(s.columns, 1), max(s.lines, 1)
        except OSError:
            return 120, 40

    def __enter__(self) -> "Terminal":
        self._raw()
        self.write(ENTER)
        return self

    def __exit__(self, *_exc) -> None:
        try:
            self.write(LEAVE)
        except OSError:  # the terminal is already gone
            pass
        self._cooked()

    def suspend(self) -> None:
        """Ctrl-Z: hand the terminal back to the shell, as vim does; `fg` brings the screen back."""
        import signal

        self.write(LEAVE)
        self._cooked()
        os.kill(os.getpid(), signal.SIGTSTP)
        self._raw()
        self.write(ENTER)

    def wake(self) -> None:
        """Another thread has something for the main loop: stop waiting for a key."""
        if self.wake_w is not None:
            os.write(self.wake_w, b"\0")

    def read(self, timeout: float) -> bytes | None:
        """What the keyboard sent, waiting at most `timeout` s: b"" when a wake or the time came first, None when the
        terminal is gone."""
        if os.name == "nt":
            return self._read_nt(timeout)
        import select

        ready, _, _ = select.select([self.fd_in, self.wake_r], [], [], timeout)
        if self.wake_r in ready:
            os.read(self.wake_r, 4096)
        if self.fd_in not in ready:
            return b""
        try:
            return os.read(self.fd_in, 65536) or None
        except OSError:
            return None

    def _raw(self) -> None:
        if os.name == "nt":
            self._raw_nt()
            return
        import termios
        import tty

        self.saved = termios.tcgetattr(self.fd_in)
        tty.setraw(self.fd_in)

    def _cooked(self) -> None:
        if os.name == "nt":
            return
        import termios

        if self.saved is not None:
            try:
                termios.tcsetattr(self.fd_in, termios.TCSADRAIN, self.saved)
            except termios.error:  # the terminal is already gone
                pass

    # shortcut: Windows goes by the documented console modes, never run on a real console; CI has no terminal there.
    # Upgrade to ReadConsoleInput if a key misbehaves.
    def _raw_nt(self) -> None:
        import ctypes

        k = ctypes.windll.kernel32
        k.SetConsoleOutputCP(65001)
        k.SetConsoleMode(k.GetStdHandle(-11), 0x0001 | 0x0004)  # processed output, VT processing
        k.SetConsoleMode(k.GetStdHandle(-10), 0x0200)  # VT input: keys arrive as the escapes above

    def _read_nt(self, timeout: float) -> bytes:
        import msvcrt

        end = time.monotonic() + timeout
        while not msvcrt.kbhit() and time.monotonic() < end:
            time.sleep(0.01)
        out = []
        while msvcrt.kbhit():
            out.append(msvcrt.getwch())
        return "".join(out).encode("utf-8", "surrogatepass")


def stdin_is_terminal() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()
