"""The typed panels, in cells: web/app/render/* and the panel grid of terminal.css. A flow panel (kpi, table, ledger, bars,
heatmap, notes, form, paste, banner) is a list of Lines, so its height is known before it is placed; a chart or a scatter
fills what it is given. `ui` is the App: it holds what the browser keeps in S (cursors, ranges, forms)."""

from __future__ import annotations

from datetime import datetime, timezone

from monitor.tui.cmd import sort_rows
from monitor.tui.fmt import fmt, fmt_date, js_str
from monitor.tui.plot import chart_raster, color_of, scatter_raster
from monitor.tui.ranges import (
    chart_modes,
    cut_horizon,
    default_range,
    expand_at,
    legend_rest,
    legend_value,
    normalize,
    range_end,
    range_start,
    rebase,
    slice_from,
    value_at,
    window_mwr,
    window_roi,
)
from monitor.tui.term import (
    AMBER,
    BG,
    CLS,
    CMD,
    CURSOR,
    DIM,
    DN,
    DN_BG,
    FG,
    FIELD,
    GHOST,
    NOTE_BG,
    RULE,
    SEL_BG,
    TEXT,
    UP,
    Canvas,
    blend,
    clip,
    pad,
    style,
    tw,
    wrap,
)
from monitor.tui.trades import form_status, paste_summary

COLS = 12
NARROW = 100  # cells: below it every panel takes the whole width (terminal.css @media)
GROWS = {"chart", "scatter", "heatmap"}
LO_BELOW = 60  # a `lo` column gives way in a panel narrower than this (@container 420px)
SPARK_W, MIN_TEXT = 8, 6
CHART_MIN, SCATTER_MIN = 6, 8  # plot rows a chart keeps; terminal rows are dearer than 190px
LEGEND_W = 21
FIELD_ON = "#1f1f1f"
PREVIEW_ROWS, BOX_ROWS = 8, 6
PLACEHOLDER = (
    "Paste rows here — a header is optional; tab, ; or , between fields:\n"
    "Date,Ticker,Action,Shares,Price,PricePerShare\n2025-01-15,SAP.DE,buy,4,961.00,240.00\n"
    "15.01.2025;SAP.DE;buy;4;@240,00   (@ = price per share)"
)


class Line(list):
    """One row: segments (text, fg, bg, bold, act). `act` on a segment or the line: what a click there does; `bg` fills the
    row; `key` a table row's key; `cur` the cursor's row; `sticky` stays on top when scrolled; `caret` the x of the text
    caret; `pop` (x, lines) a list floating under this row."""

    act = bg = key = caret = pop = None
    cur = sticky = False


def seg(text, fg: str = FG, bg: str | None = None, bold: bool = False, act=None) -> tuple:
    return (text, fg, bg, bold, act)


def line(*segs, **attrs) -> Line:
    ln = Line(segs)
    for k, v in attrs.items():
        setattr(ln, k, v)
    return ln


def seg_w(ln) -> int:
    return sum(tw(s[0]) for s in ln)


def cls_fg(cls: str, default: str = FG) -> str:
    return CLS.get(cls) or default


def paint_lines(v: Canvas, lines: list, ui, pid: str, start: int = 0) -> None:
    for y, ln in enumerate(lines[start : start + v.h]):
        bg = ln.bg or BG
        if ln.bg:
            v.fill(0, y, v.w, 1, style(FG, bg))
        if ln.act:
            ui.hit(v.ox, v.oy + y, v.ox + v.w, v.oy + y + 1, ln.act)
        x = 0
        for text, fg, sbg, bold, act in ln:
            nx = v.put(x, y, text, (fg, sbg or bg, bold))
            if act:
                ui.hit(v.ox + x, v.oy + y, v.ox + min(nx, v.w), v.oy + y + 1, act)
            x = nx
        if ln.key is not None:
            ui.row_ys.setdefault(pid, []).append((v.oy + y, ln.key))
        if ln.caret is not None:
            ui.caret = (v.ox + ln.caret, v.oy + y)
        if ln.pop:
            ui.floats.append((v.ox + ln.pop[0], v.oy + y + 1, ln.pop[1]))


def paint_window(v: Canvas, lines: list, ui, pid: str) -> None:
    """A maximized panel: what does not fit scrolls, around the cursor when it has one, its sticky header staying."""
    head = lines[:1] if lines and lines[0].sticky else []
    body = lines[len(head) :]
    room = max(1, v.h - len(head))
    top = ui.pscroll.get(pid, 0)
    cur = next((i for i, ln in enumerate(body) if ln.cur), None)
    if cur is not None:
        top = cur if cur < top else cur - room + 1 if cur >= top + room else top
    top = max(0, min(top, max(0, len(body) - room)))
    ui.pscroll[pid] = top
    paint_lines(v, head, ui, pid)
    paint_lines(v.view(0, len(head), v.w, room), body, ui, pid, top)


# ── kpi ──────────────────────────────────────────────────────────────────────────────────────────
def years_lines(rows: list, w: int) -> list:
    """The calendar-year table a maximized SUMMARY adds: GROWTH and TIME-WEIGHTED always, the euro columns when the rows
    carry them (the private build)."""
    priv = any("gain" in r for r in rows)
    cols = [
        ("YEAR", 7, False),
        *([("FIRST DAY €", 14, True), ("LAST DAY €", 14, True)] if priv else []),
        ("GROWTH", 10, True),
        ("TIME-WEIGHTED", 15, True),
        *([("GAIN €", 14, True)] if priv else []),
    ]
    out = [line(seg(""))]
    out.append(line(seg(" "), *[seg(pad(n, wd, r) + " ", AMBER) for n, wd, r in cols]))

    def cell(v, spec, wd):
        text, cls = fmt(v, spec)
        return seg(pad(text, wd, True) + " ", cls_fg(cls))

    for r in rows:
        segs = [seg(" "), seg(pad(r.get("label"), 7) + " ")]
        if priv:
            segs += [cell(r.get("first"), "eur", 14), cell(r.get("end"), "eur", 14)]
        segs += [cell(r.get("v"), r.get("fmt"), 10), cell(r.get("v2"), r.get("fmt"), 15)]
        if priv:
            segs.append(cell(r.get("gain"), "eur+", 14))
        out.append(line(*segs))
    return out


def kpi_lines(p: dict, w: int, ui) -> list:
    n = 4 if w >= 48 else 2 if w >= 24 else 1
    cw = max(1, (w - 2) // n)
    out, row = [], []

    def flush():
        if row:
            out.append(line(seg(" "), *[seg(pad(it["k"], cw), AMBER) for it in row]))
            # a value longer than its cell (TOP CORR's names) wraps in it, as the browser's does
            texts = [fmt(it.get("v"), it.get("fmt")) for it in row]
            rows = [wrap(t, max(1, cw - 1)) for t, _ in texts]
            for i in range(max(map(len, rows))):
                vals = [seg(" ")]
                for it, (_t, cls), lines in zip(row, texts, rows):
                    vals.append(
                        seg(
                            pad(lines[i] if i < len(lines) else "", cw),
                            cls_fg(cls),
                            ui.flash_bg(f"{p['id']}|{it['k']}|v"),
                            True,
                        )
                    )
                out.append(line(*vals))
            row.clear()

    for it in p.get("items") or []:
        if it.get("wide"):
            flush()
            out.append(line(seg(" " + clip(it["k"], w - 1), AMBER)))
            text, cls = fmt(it.get("v"), it.get("fmt"))
            out += [line(seg(" " + t, cls_fg(cls))) for t in wrap(text, w - 2)]
            continue
        row.append(it)
        if len(row) == n:
            flush()
    flush()
    if p.get("years") and ui.is_max(p):
        out += years_lines(p["years"], w)
    return out


# ── table ────────────────────────────────────────────────────────────────────────────────────────
def followed(p: dict, key) -> tuple:
    """A table following another's cursor shows that key's rows and summary; anything else its own."""
    hit = (p.get("rows_by_key") or {}).get(key) if p.get("follows") and key is not None else None
    if hit:
        return hit.get("rows") or [], hit.get("context") or ""
    return p.get("rows") or [], (p.get("context") or {}).get("text") or ""


def start_cursor(rows: list, key: str, current, hint):
    def find(v):
        return None if v is None else next((r for r in rows if js_str(r.get(key)) == js_str(v)), None)

    hit = find(current) or find(hint)
    return hit[key] if hit else rows[0].get(key) if rows else None


def is_text(c: dict) -> bool:
    return not c.get("fmt") or c.get("fmt") == "text"


def cell_fmt(r: dict, c: dict):
    return (r.get("_fmt") or {}).get(c["k"]) or c.get("fmt")


def text_shares(cols: list, rows: list) -> dict:
    """Two or more text columns share the width the numbers leave in proportion to the longest text each holds."""
    tx = [c for c in cols if is_text(c)]
    if len(tx) < 2:
        return {}
    need = [
        max([tw(c.get("label") or "") + 2, *(tw(fmt(r[c["k"]], cell_fmt(r, c))[0]) for r in rows if c["k"] in r)]) + 2
        for c in tx
    ]
    return {c["k"]: n / sum(need) for c, n in zip(tx, need)}


def col_widths(cols: list, rows: list, w: int, sort_col: str, extra_first: int = 0) -> tuple:
    """Which columns show and how wide, in `w` cells: a table never scrolls sideways. Numbers keep their full width; text
    columns take what is left; `lo` columns go first, then the padding, then whatever is rightmost. (columns, widths, gap)"""
    need = {}
    for c in cols:
        head = tw(c.get("label") or "") + (2 if sort_col == c["k"] else 0)
        if c.get("fmt") == "spark":
            need[c["k"]] = max(SPARK_W, head)
        else:
            need[c["k"]] = max([head, *(tw(fmt(r[c["k"]], cell_fmt(r, c))[0]) for r in rows if c["k"] in r)])
    if cols:
        need[cols[0]["k"]] += extra_first

    def size(cs, gap):
        return sum(MIN_TEXT if is_text(c) else need[c["k"]] for c in cs) + gap * max(0, len(cs) - 1)

    shown = [c for c in cols if not c.get("lo")] if w < LO_BELOW else list(cols)
    gap = 2
    if size(shown, gap) > w:
        shown = [c for c in shown if not c.get("lo")]
        gap = 1
    while len(shown) > 1 and size(shown, gap) > w:
        shown = shown[:-1]
    fixed = sum(need[c["k"]] for c in shown if not is_text(c)) + gap * max(0, len(shown) - 1)
    room = max(0, w - fixed)
    texts = [c for c in shown if is_text(c)]
    widths = {c["k"]: need[c["k"]] for c in shown if not is_text(c)}
    if texts:
        share = text_shares(texts, rows)
        for c in texts:
            widths[c["k"]] = max(MIN_TEXT, int(room * share[c["k"]]) if share else room)
        widths[texts[-1]["k"]] += max(0, room - sum(widths[c["k"]] for c in texts))
    elif shown:  # numbers only: spread them across the panel, as a 100% table does
        spare = room // len(shown)
        for c in shown:
            widths[c["k"]] += spare
    return shown, widths, gap


def table_lines(p: dict, w: int, ui) -> list:
    st = ui.table_state(p)
    src, context = followed(p, ui.follow_key(p))
    closed = sum(1 for r in src if r.get("_closed"))
    rows = sort_rows([r for r in src if st["show_closed"] or not r.get("_closed")], st["sort"][0], st["sort"][1])
    st["cursor"] = start_cursor(rows, p["key"], st["cursor"], p.get("cursor"))
    st["rows"] = rows
    if not rows and (p.get("follows") or p.get("empty")):
        return [
            line(seg(" " + t, DIM)) for t in wrap((p.get("follows") and context) or p.get("empty") or "NO ROWS", w - 2)
        ]
    total = p.get("total")
    pid, cols = p["id"], p.get("cols") or []
    every = [*rows, total] if total else rows
    stale = any(r.get("_stale") for r in rows)
    shown, widths, gap = col_widths(cols, every, w - 2, st["sort"][0], 2 if stale else 0)
    st["hidden"] = {c["k"] for c in cols} - {c["k"] for c in shown}
    sep = seg(" " * gap)
    head = line(seg(" "), sticky=True)
    for i, c in enumerate(shown):
        on = st["sort"][0] == c["k"]
        label = (c.get("label") or "") + ((" ▼" if st["sort"][1] == "desc" else " ▲") if on else "")
        head.append(
            seg(
                pad(label, widths[c["k"]], c.get("align") == "r"),
                CMD if on or c.get("hl") else AMBER,
                act=("sort", pid, c["k"]),
            )
        )
        if i < len(shown) - 1:
            head.append(sep)
    out = [head]

    def cell(r, c, row_key, row_fg, first):
        wd = widths[c["k"]]
        if c.get("fmt") == "spark":
            text, up = ui.spark_of(r.get(c["k"]), min(wd, SPARK_W))
            return seg(pad(text, wd), UP if up else DN)
        text, cls = fmt(r.get(c["k"]), cell_fmt(r, c))
        fg = CMD if c.get("hl") else row_fg or cls_fg(cls)
        flash = ui.flash_bg(f"{pid}|{js_str(row_key)}|{c['k']}")
        if first and r.get("_stale"):
            return [seg(pad(text, wd - 2), fg, flash), seg(" ●", DN)]
        return seg(pad(text, wd, c.get("align") == "r"), fg, flash)

    def cells(ln, r, row_key, row_fg):
        ln.append(seg(" "))
        for i, c in enumerate(shown):
            s = cell(r, c, row_key, row_fg, i == 0)
            ln.extend(s if isinstance(s, list) else [s])
            if i < len(shown) - 1:
                ln.append(sep)
        return ln

    for r in rows:
        k = r.get(p["key"])
        cur = js_str(k) == js_str(st["cursor"])
        fg = DIM if r.get("_closed") else CMD if r.get("_hot") else DN if r.get("_dn") else None
        out.append(cells(line(act=("row", pid, k), key=k, cur=cur, bg=CURSOR if cur else None), r, k, fg))
    if total:
        ln = line(seg(" "))
        for i, c in enumerate(shown):
            if c["k"] in total:
                s = cell(total, c, "__total__", AMBER, False)
                ln.extend(s if isinstance(s, list) else [s])
            else:
                ln.append(seg(" " * widths[c["k"]]))
            if i < len(shown) - 1:
                ln.append(sep)
        out.append(ln)
    if closed:
        out.append(
            line(seg(f" {'− HIDE' if st['show_closed'] else '+ SHOW'} {closed} CLOSED", DIM), act=("closed", pid))
        )
    return out


# ── ledger, bars, heatmap, notes, banner ─────────────────────────────────────────────────────────
def ledger_lines(p: dict, w: int, ui) -> list:
    rows = [(ln, *fmt(ln.get("v"), ln.get("fmt"))) for ln in p.get("lines") or [] if not ln.get("sep")]
    vw = max((tw(t) for _, t, _ in rows), default=0)
    out = []
    for ln in p.get("lines") or []:
        if ln.get("sep"):
            out.append(line(seg("─" * w, RULE)))
            continue
        text, cls = fmt(ln.get("v"), ln.get("fmt"))
        fg = AMBER if ln.get("strong") else FG
        out.append(
            line(
                seg(" " + pad(ln.get("op") or "", 2), DIM),
                seg(pad(ln.get("label"), max(1, w - vw - 4)), fg),
                seg(
                    pad(text, vw, True),
                    AMBER if ln.get("strong") else cls_fg(cls),
                    ui.flash_bg(f"{p['id']}|{ln.get('label')}|v"),
                ),
            )
        )
    return out


def bar_text(frac: float, w: int) -> str:
    """A thin bar, half a cell tall as the browser's 7px one, to the half cell."""
    halves = round(max(0.0, min(1.0, frac)) * w * 2)
    return ("▄" * (halves // 2) + ("▖" if halves % 2 else "")).ljust(w)


def _bar_rows(pid: str, rows: list, w: int, ui, flash: bool, parts: bool) -> list:
    top = max([1e-9, *(r.get("v") or 0 for r in rows)])
    texts = [fmt(r.get("v"), r.get("fmt"))[0] for r in rows]
    vw = max((tw(t) for t in texts), default=0)
    lw = min(max((tw(r.get("label")) for r in rows), default=0), max(8, (w - vw - 3) // 2))
    bw = max(1, (w - lw - vw - 3) if not parts else min(12, (w - lw - vw - 3) // 3))
    out = []
    for r, text in zip(rows, texts):
        zero = (r.get("v") or 0) == 0
        fg = DIM if zero else FG
        ln = line(
            seg(" " + pad(r.get("label"), lw) + " ", fg),
            seg(pad(text, vw, True), fg, ui.flash_bg(f"{pid}|{r.get('label')}|v") if flash else None),
            seg(" "),
            seg(bar_text((r.get("v") or 0) / top, bw), AMBER),
        )
        if parts and r.get("text"):
            indent = lw + vw + bw + 3
            room = max(8, w - indent - 1)
            first, *more = wrap(r["text"], room)
            ln.append(seg(" " + first, DIM))
            out.append(ln)
            out += [line(seg(" " * (indent + 1) + t, DIM)) for t in more]
        else:
            out.append(ln)
    return out


def bars_lines(p: dict, w: int, ui) -> list:
    sectors, countries, regions = p.get("sectors") or [], p.get("countries") or [], p.get("regions") or []
    if not ui.is_max(p) or (not sectors and not countries):
        return _bar_rows(p["id"], p.get("items") or [], w, ui, True, False)
    half = (w - 2) // 2

    def titled(name, rows):
        return [line(seg(f" {name}", AMBER)), *_bar_rows(p["id"], rows, half, ui, False, True)]

    left = titled("SECTORS", sectors)
    right = [*(titled("REGIONS", regions) if regions else []), *titled("COUNTRIES", countries)]
    out = []
    for i in range(max(len(left), len(right))):
        a = left[i] if i < len(left) else line()
        b = right[i] if i < len(right) else line()
        out.append(line(*a, seg(" " * max(0, half + 2 - seg_w(a))), *b))
    return out


def heat_color(v) -> str | None:
    """Diverging green / red over black, up to 85 %: render/heatmap.js heatColor."""
    if v is None or (isinstance(v, float) and v != v):
        return None
    a = round(min(1, abs(v)) * 85) / 100
    return blend("#00e676" if v >= 0 else "#ff3d3d", a)


def heatmap_lines(p: dict, w: int, ui) -> list:
    labels, cells = p.get("labels") or [], p.get("cells") or []
    if not labels:
        return [line(seg(" NO DATA", DIM))]
    lw = min(max(tw(x) for x in labels), max(6, w // 5))
    cw = max(4, (w - lw - 2) // len(labels))
    out = [line(seg(" " + " " * lw), *[seg(pad(x, cw, True), AMBER) for x in labels])]
    for i, r in enumerate(labels):
        ln = line(seg(" " + pad(r, lw), AMBER))
        for j, c in enumerate(labels):
            v = cells[i][j] if i < len(cells) and j < len(cells[i]) else None
            ln.append(
                seg(
                    pad(fmt(v, p.get("fmt") or "num:2")[0], cw, True),
                    FG,
                    ui.flash_bg(f"{p['id']}|{r}|{c}") or heat_color(v),
                )
            )
        out.append(ln)
    return out


def notes_lines(p: dict, w: int, _ui) -> list:
    out = []
    for it in p.get("items") or []:
        out.append(line(seg(" " + clip(it.get("k"), w - 1), AMBER)))
        out += [line(seg("   " + t, TEXT)) for t in wrap("—" if it.get("v") is None else it.get("v"), max(8, w - 4))]
    return out


def banner_lines(p: dict, w: int, ui) -> list:
    dn = p.get("tone") == "dn"
    fg, bg = (DN, DN_BG) if dn else (AMBER, NOTE_BG)
    text = str(p.get("text") or "")
    title = p.get("title") if p.get("title") and not text.startswith(p["title"]) else None
    right = (p.get("context") or {}).get("text") or ""
    button = seg(f" {p.get('button') or p.get('run')} ", BG, AMBER, True, ("run", p["run"])) if p.get("run") else None
    room = w - 2 - (tw(button[0]) + 1 if button else 0)
    msg = [seg(" ")]
    if title:
        msg += [seg(title, DN if dn else CMD, None, True), seg(" — ", fg)]
    left = room - seg_w(msg)
    msg.append(seg(clip(text, left), fg))
    msg += [seg(" "), button] if button else []
    gap = w - seg_w(msg) - 1
    if right and gap > 8:
        msg += [seg(" " * max(1, gap - min(tw(right), gap - 2))), seg(clip(right, gap - 2), DIM)]
    return [line(*msg, bg=bg)]


# ── form, paste ──────────────────────────────────────────────────────────────────────────────────
def button(text: str, act, ghost: bool = False, on: bool = False) -> tuple:
    if on:
        return seg(f" {text} ", BG, CMD, True, act)
    return seg(f" {text} ", FG, GHOST, False, act) if ghost else seg(f" {text} ", BG, AMBER, True, act)


def _input(ln: Line, x: int, w: int, value: str, on: bool, caret: int, placeholder: str, act) -> None:
    """A one-line text box at x: its text scrolled to keep the caret in view; the placeholder while it is empty."""
    bg = FIELD_ON if on else FIELD
    w = max(1, w)
    if not value and not on:
        ln.append(seg(pad(placeholder, w), DIM, bg, False, act))
        return
    off = max(0, caret - w + 1) if on else 0
    ln.append(seg(pad(value[off:], w) if tw(value[off:]) <= w else value[off : off + w], FG, bg, False, act))
    if on:
        ln.caret = x + caret - off


def form_lines(p: dict, w: int, ui) -> list:
    st, pid = ui.form_state(p), p["id"]
    fields = p.get("fields") or []
    by = {f["k"]: f for f in fields}
    order = [f["k"] for f in fields]
    edit = st.get("edit")
    status = form_status(st["values"], ui.today(), ui.rows_of(p), edit.get("id") if edit else None)
    out = [line(seg(" " + clip(f"EDITING {edit['label']} — SAVE REPLACES IT", w - 2), CMD))] if edit else []
    shown = [f for f in fields if not (f.get("pair") and order.index(f["pair"]) < order.index(f["k"]))]
    lw = max((tw(f.get("label")) for f in shown), default=0) + 1

    def box(ln, f, x, width):
        k = f["k"]
        on, act = ui.field == (pid, k), ("field", pid, k)
        if f.get("kind") == "choice":
            value = str(st["values"].get(k) or (f.get("options") or [""])[0]).upper()
            ln.append(seg(pad(f"{value} ▾", width), CMD, FIELD_ON if on else FIELD, False, act))
            return
        hint = status["derived"].get(k)
        _input(
            ln,
            x,
            width,
            str(st["values"].get(k) or ""),
            on,
            st.get("caret", 0),
            f"= {hint}" if hint else f.get("placeholder") or "",
            act,
        )

    for f in shown:
        ln = line(seg(" " + pad(f.get("label"), lw), AMBER))
        x = 1 + lw
        room = w - x - 1
        if f.get("pair"):
            other = by[f["pair"]]
            word = f" or {other.get('label')} "
            one = max(4, (room - tw(word)) // 2)
            box(ln, f, x, one)
            ln.append(seg(word, AMBER))
            box(ln, other, x + one + tw(word), room - one - tw(word))
        else:
            box(ln, f, x, room)
        sug = st.get("sug") or {}
        if ui.field == (pid, f["k"]) and sug.get("items"):
            ln.pop = (x, suggestion_lines(pid, sug, room))
        out.append(ln)
    if status["text"]:
        fg = DN if status["cls"] == "err" else CMD if status["cls"] == "warn" else DIM if status["cls"] == "dim" else FG
        out += [line(seg(" " + t, fg)) for t in wrap(status["text"], w - 2)]
    else:
        out.append(line())
    field = ui.field
    out.append(
        line(
            seg(" "),
            button("SAVE CHANGES ↵" if edit else "SAVE ↵", ("btn", pid, "@save"), on=field == (pid, "@save")),
            seg(" "),
            button("CANCEL EDIT" if edit else "CLEAR", ("btn", pid, "@clear"), True, field == (pid, "@clear")),
        )
    )
    return out


def suggestion_lines(pid: str, sug: dict, w: int) -> list:
    out = []
    for i, s in enumerate(sug["items"]):
        on = i == sug.get("i", -1)
        bg, fg, hi = (AMBER, BG, BG) if on else (FIELD, FG, CMD)
        tk = pad(s["ticker"], 10)
        out.append(
            line(
                seg(tk + " ", hi, bg),
                seg(pad(s.get("name") or "", max(1, w - 11 - min(tw(s.get("note") or ""), w // 3))), fg, bg),
                seg(clip(s.get("note") or "", w // 3), BG if on else DIM, bg),
                act=("sug", pid, i),
                bg=bg,
            )
        )
    return out


def preview_lines(res: dict, w: int) -> list:
    """The paste preview: one row per pasted row — what will be stored, or the error; warnings in amber."""
    widths = [
        ("LINE", 4, True),
        ("", 1, False),
        ("DATE", 9, False),
        ("TKR", 10, False),
        ("ACTION", 6, False),
        ("SHARES", 10, True),
        ("€/SHARE", 9, True),
        ("TOTAL €", 11, True),
    ]
    used = sum(wd for _, wd, _ in widths) + len(widths) + 2
    note_w = max(4, w - used)
    out = [line(seg(" "), *[seg(pad(n, wd, r) + " ", AMBER) for n, wd, r in widths], seg("NOTE", AMBER))]
    for r in res.get("rows") or []:
        t, err, warn = r.get("trade"), r.get("error"), r.get("warnings") or []
        note = err or " · ".join(warn)
        base = DN if err else CMD if warn else FG
        ln = line(seg(" "), seg(pad(r.get("line"), 4, True) + " ", DIM), seg(("✗" if err else "✓") + " ", base))
        if t:
            ln += [
                seg(pad(fmt(t["date"], "date")[0], 9) + " ", DIM),
                seg(pad(t["ticker"], 10) + " ", base),
                seg(pad(t["action"].upper(), 6) + " ", base),
                seg(pad(fmt(t["shares"], "qty")[0], 10, True) + " ", base),
                seg(pad(fmt(t["pps"], "px")[0], 9, True) + " ", base),
                seg(pad(fmt(t["price"], "eur")[0], 11, True) + " ", base),
            ]
        else:
            ln.append(seg(pad(r.get("text") or "", 9 + 10 + 6 + 10 + 9 + 11 + 5) + " ", base))
        ln.append(seg(clip(note, note_w), base))
        out.append(ln)
    return out


def paste_lines(p: dict, w: int, ui) -> list:
    st, pid = ui.form_state(p), p["id"]
    field = ui.field
    on = field == (pid, "text")
    text, caret = st.get("text") or "", st.get("caret", 0)
    rows = text.split("\n")
    before = text[:caret].split("\n")
    row, col = len(before) - 1, len(before[-1])
    top = st.get("top", 0)
    top = row if row < top else row - BOX_ROWS + 1 if row >= top + BOX_ROWS else top
    st["top"] = top = max(0, min(top, max(0, len(rows) - BOX_ROWS)))
    box_w = max(4, w - 2)
    bg = FIELD_ON if on else FIELD
    act = ("field", pid, "text")
    out = []
    hints = PLACEHOLDER.split("\n") if not text and not on else []
    for i in range(BOX_ROWS):
        ln = line(seg(" "))
        if hints:
            ln.append(seg(pad(hints[i] if i < len(hints) else "", box_w), DIM, bg, False, act))
        else:
            k = top + i
            content = rows[k] if k < len(rows) else ""
            off = max(0, col - box_w + 1) if on and k == row else 0
            ln.append(seg(pad(content[off:], box_w), FG, bg, False, act))
            if on and k == row:
                ln.caret = 1 + col - off
        out.append(ln)
    src = f"FROM {st['file']}" if st.get("file") else ""
    out.append(
        line(
            seg(" "),
            button("PREVIEW", ("btn", pid, "@preview"), on=field == (pid, "@preview")),
            seg(" "),
            button("IMPORT CSV FILE…", ("btn", pid, "@file"), True, field == (pid, "@file")),
            seg(" " + clip(src, max(0, w - 30)), DIM),
        )
    )
    res = st.get("preview")
    if res:
        out += [line(seg(" " + t, DN if res.get("error") else DIM)) for t in wrap(paste_summary(res), w - 2)]
        if not res.get("error"):
            table = preview_lines(res, w)
            if len(table) > PREVIEW_ROWS + 1 and not ui.is_max(p):
                more = len(table) - PREVIEW_ROWS - 1
                table = [*table[: PREVIEW_ROWS + 1], line(seg(f" + {more} MORE — z{p.get('n')} SHOWS THEM ALL", DIM))]
            out += table
        if res.get("ok"):
            n = res["ok"]
            btns = [
                seg(" "),
                button(f"ADD {n} TRADE{'' if n == 1 else 'S'}", ("btn", pid, "@add"), on=field == (pid, "@add")),
            ]
            if st.get("file"):
                btns += [
                    seg(" "),
                    button(
                        f"REPLACE {ui.your_file()} WITH THESE {n}",
                        ("btn", pid, "@replace"),
                        True,
                        field == (pid, "@replace"),
                    ),
                ]
            out.append(line(*btns))
    return out


LINES = {
    "kpi": kpi_lines,
    "table": table_lines,
    "ledger": ledger_lines,
    "bars": bars_lines,
    "heatmap": heatmap_lines,
    "notes": notes_lines,
    "banner": banner_lines,
    "form": form_lines,
    "paste": paste_lines,
}


def panel_lines(p: dict, w: int, ui) -> list | None:
    """A flow panel's lines; None for a chart or a scatter; a line saying so for a type this client does not know."""
    if p.get("type") in ("chart", "scatter"):
        return None
    render = LINES.get(p.get("type"))
    if render is None:
        return [line(seg(f" UNKNOWN PANEL TYPE {p.get('type')}", DIM))]
    return render(p, w, ui)


def failed_text(p: dict, e: Exception) -> str:
    return f"PANEL {p.get('id')} FAILED: {type(e).__name__}: {e}"


def safe_lines(p: dict, w: int, ui) -> list | None:
    """One panel that cannot draw (a payload this client does not understand) says so; the rest of the screen stands."""
    try:
        return panel_lines(p, w, ui)
    except Exception as e:
        if ui.strict:
            raise
        return [line(seg(" " + failed_text(p, e), DN))]


# ── chart ────────────────────────────────────────────────────────────────────────────────────────
def chart_source(p: dict, key):
    """What a chart draws: its own x/series, or — following a table — the cursor row's block."""
    if not p.get("follows"):
        return p
    blk = (p.get("series_by_key") or {}).get(key)
    if not blk:
        return None
    if blk.get("x"):
        return blk
    hide = set(blk.get("hide") or [])
    return {
        "x": p.get("x"),
        "series": [*(blk.get("series") or []), *(s for s in p.get("series") or [] if s.get("name") not in hide)],
        "note": blk.get("note"),
    }


def chart_model(p: dict, ui) -> dict | None:
    """render/chart.js's data stage: horizon, range, view. Kept until the payload, the row or a chip changes."""
    pid, key = p["id"], ui.follow_key(p)
    memo = ("model", pid, ui.gen, key, repr(ui.ranges.get(pid)), ui.modes.get(pid), ui.horizons.get(pid))
    if memo in ui.memo:
        return ui.memo[memo]
    m = ui.memo[memo] = _chart_model(p, ui, key)
    return m


def _chart_model(p: dict, ui, key) -> dict | None:
    raw = chart_source(p, key)
    if not raw or not raw.get("x") or len(raw["x"]) < 2 or not raw.get("series"):
        return None
    pid = p["id"]
    horizon = None
    if p.get("horizons"):
        horizon = ui.horizons.get(pid) if ui.horizons.get(pid) in p["horizons"] else p.get("horizon")
    src = {**raw, "series": expand_at(raw["series"], len(raw["x"]))}
    if horizon:
        src = {
            **src,
            **cut_horizon(src["x"], src["series"], p.get("today_idx"), {"6M": 6, "1Y": 12, "3Y": 36}[horizon]),
        }
    dflt = default_range(p)
    saved = ui.ranges.get(pid, dflt)
    rng = (saved.get("prev") or dflt) if isinstance(saved, dict) and saved.get("key") != key else saved
    dragged = isinstance(rng, dict)
    modes = chart_modes(p, src["series"]) if src["x"] is p.get("x") else []
    mode = ui.modes.get(pid) if ui.modes.get(pid) in modes else modes[0] if modes else None
    start = range_start(src["x"], rng)
    inception = start <= src["x"][0]
    has_inv = isinstance(p.get("inv"), list)
    twr, mwr = mode == "TWR", mode == "MWR" and has_inv
    roi_win = mode == "ROI" and rng != "ALL" and has_inv
    sliced = slice_from(src["x"], src["series"], start, range_end(rng), twr or mwr or roi_win)
    x = sliced["x"]
    inv = p["inv"][sliced["start"] : sliced["start"] + len(x)] if mwr or roi_win else None
    series = (
        normalize(sliced["series"], sliced["start"] == 0)
        if twr
        else window_mwr(sliced["series"], inv, x, start, inception)
        if mwr
        else window_roi(sliced["series"], inv, inception)
        if roi_win
        else rebase(sliced["series"])
        if p.get("rebase")
        else sliced["series"]
    )
    lines = [s for s in series if s.get("kind") not in ("markers", "band") and not s.get("nolegend")]
    today = p.get("today_idx") if p.get("horizons") else None
    rest = legend_rest(len(x), today, sliced["start"])
    legend = []
    if p.get("legend") == "rank":
        legend = sorted(
            lines, key=lambda s: -(value_at(s["y"], rest) if value_at(s["y"], rest) is not None else float("-inf"))
        )
    return {
        "x": x,
        "series": series,
        "start": sliced["start"],
        "rest": rest,
        "today": today - sliced["start"] if today is not None else None,
        "legend": legend,
        "lines": lines,
        "range": rng,
        "dragged": dragged,
        "default": dflt,
        "modes": modes,
        "mode": mode,
        "horizon": horizon,
        "key": key,
    }


def iso_at(x: float) -> str:
    return datetime.fromtimestamp(x, timezone.utc).date().isoformat()


def chips_line(p: dict, m: dict) -> Line | None:
    pid = p["id"]
    segs: list = []
    for r in p.get("ranges") or []:
        on = r == m["range"]
        segs += [seg(f" {r} ", FG if on else DIM, GHOST if on else None, False, ("range", pid, r)), seg(" ")]
    if m["dragged"]:
        segs += [seg(f" {fmt_date(iso_at(m['x'][0]))}–{fmt_date(iso_at(m['x'][-1]))} ", FG, GHOST), seg(" ")]
    if segs and (m["modes"] or p.get("horizons")):
        segs.append(seg(" "))
    for mode in m["modes"]:
        on = mode == m["mode"]
        segs += [seg(f" {mode} ", BG if on else DIM, AMBER if on else None, False, ("mode", pid, mode)), seg(" ")]
    if m["modes"] and p.get("horizons"):
        segs.append(seg(" "))
    for h in p.get("horizons") or []:
        on = h == m["horizon"]
        segs += [seg(f" {h} ", BG if on else DIM, AMBER if on else None, False, ("hz", pid, h)), seg(" ")]
    return line(*segs) if segs else None


def blit(v: Canvas, x: int, y: int, cells: list) -> None:
    for r, row in enumerate(cells):
        if y + r >= v.h:
            return
        chs, sts, i = v.ch[v.oy + y + r], v.st[v.oy + y + r], v.ox + x
        for c, (ch, fg, bg) in enumerate(row[: max(0, v.w - x)]):
            chs[i + c], sts[i + c] = ch, (fg, bg, False)


def draw_chart(v: Canvas, p: dict, ui) -> None:
    pid = p["id"]
    m = chart_model(p, ui)
    if not m:
        v.put(1, 0, "NO DATA", style(DIM))
        return
    top = 0
    chips = chips_line(p, m)
    if chips:
        paint_lines(v.view(0, 0, v.w, 1), [chips], ui, pid)
        top = 1
    legend_w = LEGEND_W if m["legend"] and v.w >= 44 else 0
    w, h = v.w - legend_w, v.h - top
    memo = ("raster", pid, ui.gen, m["key"], repr(m["range"]), m["mode"], m["horizon"], w, h)
    raster = ui.memo.get(memo)
    if raster is None:
        raster = ui.memo[memo] = chart_raster(
            m["x"], m["series"], w, h, p.get("yfmt"), p.get("vlines"), p.get("vlabels")
        )
    if raster is None:
        v.put(1, top, "NO DATA", style(DIM))
        return
    plot = v.view(0, top, w, h)
    blit(plot, 0, 0, raster.cells)
    ui.plots[pid] = {"raster": raster, "x": v.ox, "model": m}
    ui.hit(v.ox + raster.left, v.oy + top, v.ox + raster.left + raster.pw, v.oy + top + raster.ph, ("plot", pid))
    sel = ui.sel.get(pid)
    if sel:
        a, b = sorted(raster.col_of(min(i, len(m["x"]) - 1)) for i in sel)
        for r in range(raster.ph):
            plot.paint_bg(a, r, b - a + 1, SEL_BG)
    idx = ui.cross.get(pid)
    if idx is not None:
        idx = min(idx, len(m["x"]) - 1)
        col = raster.col_of(idx)
        for r in range(raster.ph):
            if plot.ch[plot.oy + r][plot.ox + col] == " ":
                plot.put(col, r, "│", style(AMBER))
            plot.paint_bg(col, r, 1, "#2a2015")
    at = m["rest"] if idx is None else idx
    if legend_w:
        lv = v.view(w, top, legend_w, h)
        lv.put(0, 0, fmt_date(iso_at(m["x"][at])), style(DIM))
        vw = 9
        for r, s in enumerate(m["legend"][: h - 1], 1):
            lv.put(0, r, pad(s.get("name"), legend_w - vw - 1), style(color_of(s, m["series"].index(s))))
            text, cls = fmt(legend_value(m["series"], s, at, m["today"]), p.get("yfmt"))
            lv.put(legend_w - vw - 1, r, pad(text, vw, True), style(cls_fg(cls)))
    elif idx is not None and m["lines"]:  # no legend to read it from: the point under the cursor, in the plot
        text = f" {fmt_date(iso_at(m['x'][idx]))} {fmt(value_at(m['lines'][0]['y'], idx), p.get('yfmt'))[0]} "
        plot.put(max(raster.left, w - tw(text)), 0, text, style(AMBER, "#1a1200"))


def draw_scatter(v: Canvas, p: dict, ui) -> None:
    memo = ("scatter", p["id"], ui.gen, v.w, v.h)
    if memo not in ui.memo:
        ui.memo[memo] = scatter_raster(p.get("series") or [], v.w, v.h, p.get("xfmt"), p.get("yfmt"))
    raster = ui.memo[memo]
    if raster is None:
        v.put(1, 0, "NO DATA", style(DIM))
        return
    blit(v, 0, 0, raster.cells)


# ── the grid ─────────────────────────────────────────────────────────────────────────────────────
def place(panels: list, cols: int = COLS) -> list:
    """CSS grid auto-placement, sparse: each panel at the first spot after the last that holds its span. (panel, row, col,
    span, rows) in the panels' order."""
    taken: set = set()
    r = c = 0
    out = []
    for p in panels:
        span = min(cols, int(p.get("span") or cols))
        rows = int(p.get("rows_span") or 1)
        while True:
            if c + span > cols:
                r, c = r + 1, 0
                continue
            if all((r + i, c + j) not in taken for i in range(rows) for j in range(span)):
                break
            c += 1
        taken |= {(r + i, c + j) for i in range(rows) for j in range(span)}
        out.append((p, r, c, span, rows))
        c += span
    return out


def row_heights(placed: list, needs: dict, room: int) -> list:
    """Every row at least what its panels need; spare height to rows holding a chart, frontier or heatmap (none: the last
    row), levelled as 1fr rows are. app.js fitRows, terminal.css #grid."""
    n = max((r + rows for _, r, _, _, rows in placed), default=0)
    h = [0] * n
    for p, r, _, _, rows in placed:
        if rows == 1:
            h[r] = max(h[r], needs[p["id"]])
    for p, r, _, _, rows in placed:
        short = needs[p["id"]] - sum(h[r : r + rows])
        for i in range(rows if short > 0 else 0):
            h[r + i] += short // rows + (1 if i < short % rows else 0)
    grow = [False] * n
    for p, r, _, _, _ in placed:
        grow[r] = grow[r] or p.get("type") in GROWS
    if n and not any(grow):
        grow[-1] = True
    spare = room - sum(h)
    while spare > 0 and any(grow):
        rows_up = [i for i in range(n) if grow[i]]
        low = min(h[i] for i in rows_up)
        lowest = [i for i in rows_up if h[i] == low]
        above = min((h[i] for i in rows_up if h[i] > low), default=None)
        step = max(1, min(spare // len(lowest), (above - low) if above is not None else spare))
        for i in lowest:
            if spare <= 0:
                break
            h[i] += step
            spare -= step
    return h


def layout(panels: list, w: int, room: int, ui) -> tuple:
    """Where every panel goes and what it holds: ([(panel, x, y, w, h, lines)], total height)."""
    cols = COLS if w >= NARROW else 1
    single = [{**p, "span": 1, "rows_span": 1} for p in panels] if cols == 1 else panels
    placed = place(single, cols)
    edge = [round(i * (w + 1) / cols) for i in range(cols + 1)]

    def width(c, span):
        return edge[c + span] - edge[c] - 1

    # tables a chart or a table follows first: they need their cursor
    first = sorted(
        range(len(placed)), key=lambda i: placed[i][0].get("type") != "table" or bool(placed[i][0].get("follows"))
    )
    lines, needs = {}, {}
    for i in first:
        p, _, c, span, _ = placed[i]
        pid = p["id"]
        lines[pid] = safe_lines(p, width(c, span), ui)
        head = 0 if p.get("type") == "banner" else 1
        if lines[pid] is not None:
            needs[pid] = head + len(lines[pid])
        elif p.get("type") == "scatter":
            needs[pid] = head + SCATTER_MIN + 1
        else:
            needs[pid] = head + CHART_MIN + 1 + (1 if p.get("ranges") or p.get("horizons") or p.get("inv") else 0)
    h = row_heights(placed, needs, room)
    top = [sum(h[:i]) for i in range(len(h) + 1)]
    out = [
        (p, edge[c], top[r], width(c, span), top[r + rows] - top[r], lines[p["id"]]) for p, r, c, span, rows in placed
    ]
    return out, sum(h)


def draw_panel(v: Canvas, p: dict, lines: list | None, ui, windowed: bool = False) -> None:
    pid = p["id"]
    key = ui.follow_key(p)
    if p.get("type") != "banner":
        title = str(p.get("title") or "").replace("{key}", js_str(key) if key is not None else "—")
        note = (chart_source(p, key) or {}).get("note") if p.get("follows") and p.get("series_by_key") else None
        base = (
            followed(p, key)[1]
            if p.get("follows") and p.get("rows_by_key")
            else (p.get("context") or {}).get("text") or ""
        )
        ctx = f"{note} · {base}" if note else base
        bg = CMD if ui.focus == pid else AMBER
        v.fill(0, 0, v.w, 1, style(BG, bg))
        left = f" {p.get('n')}) {title}"
        ui.hit(v.ox, v.oy, v.ox + v.w, v.oy + 1, ("head", pid))
        x = v.put(0, 0, clip(left, v.w), style(BG, bg, True))
        room = v.w - x - 2
        if ctx and room > 3:
            text = clip(ctx, room)
            v.put(v.w - tw(text) - 1, 0, text, style(BG, bg))
        body = v.view(0, 1, v.w, v.h - 1)
    else:
        body = v
    if p.get("type") == "chart":
        draw_chart(body, p, ui)
    elif p.get("type") == "scatter":
        draw_scatter(body, p, ui)
    elif windowed:
        paint_window(body, lines or [], ui, pid)
    else:
        paint_lines(body, lines or [], ui, pid)
