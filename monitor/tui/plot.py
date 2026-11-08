"""Charts in cells: a braille canvas gives each cell 2 x 4 dots, the same lines uPlot and the SVG scatter draw, in the same
colours; a band or a heat cell is a background. Pure: Rasters, drawn onto a Canvas by monitor.tui.panels."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from monitor.tui.fmt import fmt, time_ticks
from monitor.tui.ranges import extent, nice_ticks, tick_decimals
from monitor.tui.term import AMBER, BG, DIM, blend

PALETTE = ["#ffa028", "#4fc3f7", "#e040fb", "#00e676", "#ffeb3b", "#ff7043", "#9575cd", "#26a69a", "#bdbdbd"]
ROLE = {"primary": "#ffffff", "buy": "#00e676", "sell": "#ff3d3d"}
SCATTER = {"cloud": "#5a3a10", "frontier": "#ffa028", "now": "#ffffff", "target": "#00e676", "port": "#4fc3f7",
           "asset": "#8a8a8a", "move": "#ffbf00"}
AXIS_FMT = {"eur": "eur:0", "eur+": "eur+:0", "pct": "pct:0", "pct+": "pct+:0"}
GRID = "#242424"
MARK = "#5a5a5a"
_BITS = ((0x01, 0x08), (0x02, 0x10), (0x04, 0x20), (0x40, 0x80))


def color_of(s: dict, i: int) -> str:
    return s.get("color") or ROLE.get(s.get("role")) or PALETTE[i % len(PALETTE)]


class Dots:
    """w x h cells of braille: 2w x 4h dots. A cell has one colour: the line drawn last through it."""

    def __init__(self, w: int, h: int):
        self.w, self.h = w, h
        self.bits = [[0] * w for _ in range(h)]
        self.fg: list = [[None] * w for _ in range(h)]

    def dot(self, x: int, y: int, c: str) -> None:
        if 0 <= x < 2 * self.w and 0 <= y < 4 * self.h:
            self.bits[y >> 2][x >> 1] |= _BITS[y & 3][x & 1]
            self.fg[y >> 2][x >> 1] = c

    def line(self, x0: int, y0: int, x1: int, y1: int, c: str, dash: bool = False) -> None:
        n = max(abs(x1 - x0), abs(y1 - y0))
        for t in range(n + 1):
            x, y = (x0 + (x1 - x0) * t // n, y0 + (y1 - y0) * t // n) if n else (x0, y0)
            if not dash or (x // 4) % 2 == 0:
                self.dot(x, y, c)

    def char(self, x: int, y: int) -> str:
        b = self.bits[y][x]
        return chr(0x2800 + b) if b else " "


def spark(values, w: int = 8) -> tuple[str, bool]:
    """A sparkline `w` cells wide: its text, and whether it ended up (green) or down (red)."""
    v = [x for x in values or [] if x is not None]
    if len(v) < 2 or w < 1:
        return "", True
    lo, hi = min(v), max(v)
    span = hi - lo or 1
    d = Dots(w, 1)
    pts = [(round(i / (len(v) - 1) * (2 * w - 1)), round(3 - (y - lo) / span * 3)) for i, y in enumerate(v)]
    for (a, b), (c, e) in zip(pts, pts[1:]):
        d.line(a, b, c, e, "")
    return "".join(d.char(x, 0) for x in range(w)), v[-1] >= v[0]


@dataclass
class Raster:
    """A plot as cells: `cells[y][x]` = (char, fg, bg); the plot itself starts at column `left`, `pw` x `ph` cells."""

    cells: list
    left: int
    pw: int
    ph: int
    xs: list

    def col_of(self, i: int) -> int:
        """The plot column of point i."""
        return self.left + _scale(self.xs[i], self.xs, self.pw * 2 - 1) // 2

    def index_at(self, col: int) -> int:
        """The point nearest plot column `col`."""
        if len(self.xs) < 2 or self.pw < 2:
            return 0
        t = self.xs[0] + (self.xs[-1] - self.xs[0]) * max(0, min(self.pw - 1, col - self.left)) / (self.pw - 1)
        return min(range(len(self.xs)), key=lambda i: abs(self.xs[i] - t))


def _scale(v: float, xs: list, top: int) -> int:
    span = xs[-1] - xs[0]
    return round((v - xs[0]) / span * top) if span else 0


def _month_ticks(lo: float, hi: float, step: int) -> list:
    d = datetime.fromtimestamp(lo)
    y, m = d.year, d.month
    out = []
    while True:
        t = datetime(y, m, 1).timestamp()
        if t > hi:
            return out
        if t >= lo and (m - 1) % step == 0:
            out.append(t)
        m += 1
        if m > 12:
            y, m = y + 1, 1


def time_axis(lo: float, hi: float, room: int) -> tuple[list, float]:
    """Ticks at local boundaries, as many as fit `room` labels: (ticks in s, their spacing in s)."""
    span = hi - lo
    room = max(2, room)
    for months in (1, 2, 3, 6, 12, 24, 60):
        incr = months * 30.44 * 86400
        if span / incr <= room and span >= incr * 1.2:
            ticks = (
                _month_ticks(lo, hi, months)
                if months < 12
                else [
                    t
                    for t in _month_ticks(lo, hi, 1)
                    if datetime.fromtimestamp(t).month == 1 and datetime.fromtimestamp(t).year % (months // 12) == 0
                ]
            )
            if len(ticks) >= 2:
                return ticks, incr
    for days in (1, 2, 7, 14):
        if span / (days * 86400) <= room:
            first = datetime.fromtimestamp(lo).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
            ticks = [first + k * days * 86400 for k in range(int(span / (days * 86400)) + 2)]
            return [t for t in ticks if lo <= t <= hi], days * 86400
    return [lo, hi], span


def chart_raster(x: list, series: list, w: int, h: int, yfmt: str | None, vlines=None, vlabels=None) -> Raster | None:
    """Lines, markers and bands over a time axis, y labels on the left, time labels along the bottom row."""
    values = []
    for s in series:
        values.extend(s.get("lo") or [] if s.get("kind") == "band" else s.get("y") or [])
        if s.get("kind") == "band":
            values.extend(s.get("hi") or [])
    rng = extent(values)
    if rng is None or len(x) < 2 or w < 12 or h < 3:
        return None
    pad_y = (rng[1] - rng[0]) * 0.05
    lo, hi = rng[0] - pad_y, rng[1] + pad_y
    ph = h - 1
    yt = nice_ticks(lo, hi, max(2, min(5, ph // 2)))
    ytext = [fmt(t, AXIS_FMT.get(yfmt, "num:0"))[0] for t in yt]
    left = max((len(t) for t in ytext), default=4) + 1
    pw = w - left
    if pw < 4:
        return None
    d = Dots(pw, ph)
    bgs: list = [[BG] * pw for _ in range(ph)]
    sx = lambda v: _scale(v, x, pw * 2 - 1)
    sy = lambda v: round((hi - v) / (hi - lo) * (ph * 4 - 1))
    for i, s in enumerate(series):  # bands first: they are what the lines stand on
        if s.get("kind") != "band":
            continue
        fill = blend(color_of(s, i), 0.15)
        pts = [
            (sx(x[k]) // 2, sy(b), sy(a))
            for k, (a, b) in enumerate(zip(s.get("lo") or [], s.get("hi") or []))
            if a is not None and b is not None
        ]
        for (c0, t0, b0), (c1, t1, b1) in zip(pts, pts[1:]):  # the area between its points, not just at them
            for col in range(c0, c1 + 1):
                f = (col - c0) / (c1 - c0) if c1 > c0 else 0
                for row in range(max(0, round(t0 + (t1 - t0) * f) // 4), min(ph, round(b0 + (b1 - b0) * f) // 4 + 1)):
                    bgs[row][col] = fill
    lines = [(i, s) for i, s in enumerate(series) if s.get("kind") not in ("band", "markers")]
    lines.sort(key=lambda t: t[1].get("role") == "primary")  # the one that matters is drawn last: a cell has one colour
    for i, s in lines:
        c, prev = color_of(s, i), None
        for k, y in enumerate(s.get("y") or []):
            if y is None or k >= len(x):
                prev = None
                continue
            p = (sx(x[k]), sy(y))
            if prev is not None:
                d.line(*prev, *p, c, bool(s.get("dash")))
            else:
                d.dot(*p, c)
            prev = p
    cells = [[(" ", DIM, BG)] * w for _ in range(h)]
    grid_rows = {sy(t) // 4: t for t in yt}
    ticks, incr = time_axis(x[0], x[-1], pw // 9)
    grid_cols = {sx(t) // 2 for t in ticks}
    marks = {sx(t) // 2 for t in vlines or [] if x[0] <= t <= x[-1]}
    for row in range(ph):
        out = cells[row]
        for col in range(pw):
            ch, fg, bg = d.char(col, row), d.fg[row][col] or GRID, bgs[row][col]
            if ch == " ":
                on_row, on_col = row in grid_rows, col in grid_cols
                ch = "┼" if on_row and on_col else "─" if on_row else "│" if on_col else " "
                if col in marks:
                    ch, fg = "┊", MARK
            out[left + col] = (ch, fg, bg)
    for i, s in enumerate(series):  # markers on top of everything
        if s.get("kind") != "markers":
            continue
        for k, y in enumerate(s.get("y") or []):
            if y is not None and k < len(x):
                row, col = min(ph - 1, max(0, sy(y) // 4)), sx(x[k]) // 2
                cells[row][left + col] = ("●", color_of(s, i), bgs[row][col])
    for t, text in zip(yt, ytext):
        row = sy(t) // 4
        if 0 <= row < ph:
            for j, c in enumerate(text.rjust(left - 1)):
                cells[row][j] = (c, DIM, BG)
    for t, text in zip(vlines or [], vlabels or []):
        if x[0] <= t <= x[-1]:
            col = left + sx(t) // 2 + 1
            for j, c in enumerate(text):
                if col + j < w:
                    cells[0][col + j] = (c, DIM, BG)
    end = 0
    for t, text in zip(ticks, time_ticks(ticks, incr)):
        col = left + sx(t) // 2 - len(text) // 2
        if col < end or col + len(text) > w:
            continue
        for j, c in enumerate(text):
            cells[ph][col + j] = (c, DIM, BG)
        end = col + len(text) + 2
    return Raster(cells, left, pw, ph, x)


def _pts(s: dict) -> list:
    return [
        (a, b)
        for a, b in zip(s.get("x") or [], s.get("y") or [])
        if a is not None and b is not None and math.isfinite(a) and math.isfinite(b)
    ]


SPOTS = ((1, 0), (None, 0), (1, -1), (None, -1), (1, 1), (None, 1), (0, -2), (0, 2), (None, -2), (None, 2))


def place_labels(groups: list, w: int, h: int, taken: set) -> list:
    """Marker labels: each tries spots around its own dot and takes the first that overlaps no label or dot. Cells for
    px: the idea of web/app/render/scatter.js placeLabels."""
    out = []
    for g in sorted(groups, key=lambda g: (g["row"], g["col"])):
        n = len(g["text"])
        best = None
        for dx, dy in SPOTS:
            col = g["col"] + (-n - 1 if dx is None else dx)
            row = g["row"] + dy
            if col < 0 or col + n > w or not 0 <= row < h:
                continue
            cells = {(row, c) for c in range(col, col + n)}
            if best is None:
                best = (col, row)
            if not cells & taken:
                best = (col, row)
                break
        if best:
            taken |= {(best[1], c) for c in range(best[0], best[0] + n)}
            out.append({**g, "at": best})
    return out


def scatter_raster(series: list, w: int, h: int, xfmt: str | None, yfmt: str | None,
                   xlabel: str | None = None, ylabel: str | None = None) -> Raster | None:
    """render/scatter.js in cells: axis names too — x on its own row under the ticks (room allowing), y in the plot's
    top-left corner."""
    xs = extent(v for s in series for v in s.get("x") or [])
    ys = extent(v for s in series for v in s.get("y") or [])
    if not xs or not ys or w < 14 or h < 4:
        return None
    x0, x1 = xs[0] - (xs[1] - xs[0]) * 0.04, xs[1] + (xs[1] - xs[0]) * 0.04
    y0, y1 = ys[0] - (ys[1] - ys[0]) * 0.08, ys[1] + (ys[1] - ys[0]) * 0.08
    yt = nice_ticks(y0, y1, max(2, min(5, (h - 1) // 2)))
    xt = nice_ticks(x0, x1, 6)
    yd, xd = tick_decimals(yt), tick_decimals(xt)
    ytext = [fmt(t, f"{(yfmt or 'num').split(':')[0]}:{yd}")[0] for t in yt]
    left = max((len(t) for t in ytext), default=4) + 1
    titled = bool(xlabel) and h >= 8
    pw, ph = w - left, h - 1 - titled
    d = Dots(pw, ph)
    sx = lambda v: round((v - x0) / (x1 - x0) * (pw * 2 - 1))
    sy = lambda v: round((y1 - v) / (y1 - y0) * (ph * 4 - 1))
    groups: list = []
    for s in series:
        c, kind, pts = SCATTER.get(s.get("role"), "#bdbdbd"), s.get("kind"), _pts(s)
        if kind == "line":
            for (a, b), (e, f) in zip(pts, pts[1:]):
                d.line(sx(a), sy(b), sx(e), sy(f), c)
        elif kind == "marker":
            for a, b in pts:
                col, row = sx(a) // 2, sy(b) // 4
                g = next((g for g in groups if abs(g["col"] - col) <= 1 and abs(g["row"] - row) <= 1), None)
                if g:
                    g["members"].append((str(s.get("name")), c))
                else:
                    groups.append({"col": col, "row": row, "members": [(str(s.get("name")), c)]})
        else:
            for a, b in pts:
                d.dot(sx(a), sy(b), c)
    cells = [[(" ", DIM, BG)] * w for _ in range(h)]
    grid_rows, grid_cols = {sy(t) // 4 for t in yt}, {sx(t) // 2 for t in xt}
    for row in range(ph):
        for col in range(pw):
            ch = d.char(col, row)
            if ch == " ":
                on_row, on_col = row in grid_rows, col in grid_cols
                ch = "┼" if on_row and on_col else "─" if on_row else "│" if on_col else " "
            cells[row][left + col] = (ch, d.fg[row][col] or GRID, BG)
    for g in groups:
        g["text"] = " · ".join(n for n, _ in g["members"])
        cells[g["row"]][left + g["col"]] = ("●", g["members"][0][1], BG)
    taken = {(g["row"], g["col"]) for g in groups}
    for g in place_labels(groups, pw, ph, taken):
        col, row = g["at"]
        for name, c in g["members"]:
            for ch in name:
                if col < pw:
                    cells[row][left + col] = (ch, c, BG)
                col += 1
            for ch in " · ":
                if col < pw and g["members"][-1][0] != name:
                    cells[row][left + col] = (ch, "#9e9e9e", BG)
                    col += 1
    for t, text in zip(yt, ytext):
        row = sy(t) // 4
        if 0 <= row < ph:
            for j, c in enumerate(text.rjust(left - 1)):
                cells[row][j] = (c, DIM, BG)
    end = 0
    for t in xt:
        text = fmt(t, f"{(xfmt or 'num').split(':')[0]}:{xd}")[0]
        col = left + sx(t) // 2 - len(text) // 2
        if col >= end and col + len(text) <= w:
            for j, c in enumerate(text):
                cells[ph][col + j] = (c, DIM, BG)
            end = col + len(text) + 2
    if titled:
        text = xlabel[:pw]
        for j, c in enumerate(text, start=left + (pw - len(text)) // 2):
            cells[h - 1][j] = (c, AMBER, BG)
    if ylabel and ph > 2:
        for j, c in enumerate(ylabel[: max(0, pw - 2)], start=left + 1):
            cells[0][j] = (c, AMBER, BG)
    return Raster(cells, left, pw, ph, [])
