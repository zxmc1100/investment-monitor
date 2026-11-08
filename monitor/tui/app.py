"""The app: web/app/app.js and keys.js with vim's modes in place of a browser's focus. One thread owns the state and the screen;
requests run on a pool and post their answers back. NORMAL keys navigate, `:` is the command bar, `/` finds a row, `i` steps
into a form, `v` selects a period on a chart. What it does on data is what the browser does; only the keys differ."""

from __future__ import annotations

import glob
import os
import queue
import threading
import time
import webbrowser
from datetime import date, datetime
from pathlib import Path

from monitor.tui import cmd as C
from monitor.tui import panels as P
from monitor.tui import trades as T
from monitor.tui.client import ApiError, why
from monitor.tui.fmt import fmt, fmt_clock, fmt_date, fmt_stamp, js_str
from monitor.tui.panels import line, seg
from monitor.tui.plot import spark
from monitor.tui.term import (
    AMBER,
    BAR_BG,
    BG,
    CMD,
    DIM,
    DN,
    ESC_WAIT_S,
    FG,
    FLASH,
    OVER_BG,
    RULE,
    STRIP_BG,
    TEXT,
    Canvas,
    Keys,
    Painter,
    blend,
    clip,
    pad,
    style,
    truecolor,
    tw,
    wrap,
)

ASK_S = 0.4  # an Enter sooner than this after a question: a double-tap, no answer
COUNT_S = 0.3  # a lone digit waits this long for a motion (5j) before it opens screen 5
CHORD_FIRST = ("g", "z", "d", "Z", "ctrl-w")
NOTICE_S = 5.0
FLASH_S = 1.2
LOOKUP_S, PREVIEW_S = 0.2, 0.5
MAX_IMPORT = 8 * 1024 * 1024
BAR_W = 36
# a Mac's Option+key types these unless the terminal sends it as Meta: the same key either way
OPTION = {
    "®": "alt-r",
    "π": "alt-p",
    "µ": "alt-m",
    "†": "alt-t",
    "¡": "alt-1",
    "™": "alt-2",
    "£": "alt-3",
    "¢": "alt-4",
    "∞": "alt-5",
    "§": "alt-6",
    "¶": "alt-7",
    "•": "alt-8",
    "ª": "alt-9",
}
MODE_KEYS = {"alt-r": "ROI", "alt-m": "MWR", "alt-t": "TWR"}
PRIO = {"clock": 10, "tier": 20, "code": 30, "meta": 50, "build": 60, "err": 80, "alert": 90, "live": 100}
HINT = "j k row · < > sort · Tab panel · z<n> max · : command · ? help"


def key_rows(top: int) -> list:
    """HELP's KEYS table (its COMMANDS table is cmd.help_rows)."""
    return [
        (
            ":",
            "the command bar: type, ↑↓ or Ctrl-N/P pick from the list, Tab completes, Enter runs, Esc leaves; ↑↓ on an empty bar: history",
        ),
        ("j k · ↓ ↑", "row cursor (5j: five rows)"),
        ("gg G · Home End", "first / last row (12G: row 12)"),
        ("Ctrl-D Ctrl-U", "half a page"),
        ("Ctrl-F Ctrl-B · PgDn PgUp", "a page"),
        ("H M L", "the top, middle, bottom row in view"),
        ("Ctrl-E Ctrl-Y", "scroll the page a line"),
        ("zz zt zb", "the cursor row to the middle, top, bottom of the view"),
        ("/ n N", "find a row in the table (Enter keeps it, Esc goes back) · next · previous"),
        (
            "Enter",
            "open the cursor row (e.g. a security), set TARGET on PORTFOLIOS, acknowledge on ALRT, edit a trade on TRADES — "
            "or step into a focused form",
        ),
        ("h l · ← →", "the panel to the left, right — on a focused chart: its cursor, the legend follows"),
        ("Ctrl-W h j k l · Tab Shift-Tab", "the panel that way · panels in turn"),
        ("z1…z9 · Alt+1…9 · Ctrl-W o", "maximize panel n · the focused one (again or Esc restores)"),
        (f"1–{top}", "jump to screen (a moment later: 5j moves five rows)"),
        ("gt gT · 3gt", "next, previous screen · screen 3"),
        ("Ctrl-O", "back to where you came from"),
        ("< > · Shift+← →", "sort by previous / next column"),
        ("s · Shift+↑ ↓", "flip the sort · ascending / descending"),
        ("[ ] · { }", "a chart's range · its horizon (the focused chart, else the first that has them)"),
        ("p · Alt+P", "POSITIONS' period: DAY · 1W · 1M · YTD — which % and € pair it shows (or its title's chips)"),
        (
            "m · Alt+R M T",
            "the ROI chart's view — ROI: the gain over every euro in it (default) · MWR: money-weighted (Modified "
            "Dietz) · TWR: time-weighted, buys and sales moving no line",
        ),
        ("w b 0 $", "a focused chart's cursor: ten steps on, back, to its start, its end"),
        (
            "v … Enter · -",
            "select a period on a focused chart and zoom into it · back out (a mouse drag and a double-click too)",
        ),
        ("i · o", "step into the focused form · into TRADES' ADD (Tab next field, Enter saves, Esc back to keys)"),
        (
            "dd · Del",
            "delete the cursor row on TRADES, the table focused (asks first; Del only right after a cursor move)",
        ),
        ("u · Ctrl-R", "UNDO, REDO on TRADES (asks first)"),
        ("za", "show / hide closed positions"),
        ("ge", "the failed tier's error, in full"),
        ("F1 or ?", "this help"),
        ("Esc", "leave a form field · clear bar · close overlay · restore panel · back from a security"),
        (
            "the mouse",
            "click a row, a header, a chip, a key; drag a chart, double-click it back; the wheel scrolls. To select "
            "text, hold Option (elsewhere Shift) while dragging",
        ),
        ("Ctrl-L · Ctrl-Z", "redraw the screen · suspend to the shell (fg comes back)"),
        (":q · ZZ", "leave"),
    ]


def edit_line(text: str, caret: int, k: str) -> tuple | None:
    """A key on a one-line text field: (text, caret), or None when it is not an editing key."""
    if k == "backspace":
        return (text[: caret - 1] + text[caret:], caret - 1) if caret else (text, caret)
    if k == "del":
        return text[:caret] + text[caret + 1 :], caret
    if k == "left":
        return text, max(0, caret - 1)
    if k == "right":
        return text, min(len(text), caret + 1)
    if k in ("home", "ctrl-a"):
        return text, 0
    if k in ("end", "ctrl-e"):
        return text, len(text)
    if k == "ctrl-u":
        return text[caret:], 0
    if k == "ctrl-k":
        return text[:caret], caret
    if k == "ctrl-w":
        head = text[:caret].rstrip()
        cut = head.rfind(" ") + 1
        return text[:cut] + text[caret:], cut
    if len(k) == 1 and k.isprintable():
        return text[:caret] + k + text[caret:], caret + 1
    return None


class App:
    def __init__(self, api, term=None, sync: bool = False, strict: bool = False):
        self.api, self.term, self.sync, self.strict = api, term, sync, strict
        self.q: queue.Queue = queue.Queue()
        self.painter = Painter(term.write if term else (lambda _s: None), self._color24())
        self.reg = {"screens": []}
        self.id = self.param = self.key = None
        self.back: list = []
        self.payload = self.live = self.cold = None
        self.conn, self.quit, self.dirty, self.zen = True, False, True, False
        self.running: set = set()
        self.mode = "normal"
        self.bar, self.bar_caret = "", 0
        self.ac = {"items": [], "i": 0, "moved": False}
        self.lookup_state = {"q": "", "items": []}
        self.history: list = []
        self.hi = -1
        self.count, self.count_at, self.pending = "", 0.0, None
        self.search = None
        self.last_search = ""
        self.typed = False
        # what panels read (the browser's S)
        self.tables: dict = {}
        self.ranges: dict = {}
        self.modes: dict = {}
        self.horizons: dict = {}
        self.cross: dict = {}
        self.sel: dict = {}
        self.forms: dict = {}
        self.field = None
        self.focus = self.max = None
        self.memo: dict = {}
        self.gen = 0
        self.flashes: dict = {}
        self.row_ys: dict = {}
        self.pscroll: dict = {}
        self.plots: dict = {}
        self.rects: dict = {}
        self.floats: list = []
        self.caret = None
        self.hits: list = []
        self.scroll, self.grid_top, self.grid_h = 0, 2, 20
        self.reveal_panel = None
        self.drag = None
        self.last_press = (0.0, None)
        self.overlay = self.ask_state = None
        self.notice_text, self.notice_err, self.notice_until = "", False, 0.0
        self.alerts = {"active": 0, "down": False}
        self.alert_err = None
        self.builds: dict = {}
        self.build_touched: list = []
        self.timers: dict = {}
        self.next_poke = 0.0
        self.last_sec = -1
        self.size = term.size() if term else (160, 48)

    @staticmethod
    def _color24() -> bool:
        return truecolor()

    # ── plumbing ─────────────────────────────────────────────────────────────────────────────────
    now = staticmethod(time.monotonic)

    def bg(self, fn, then=None, fail=None) -> None:
        """Run a request off the main thread; its answer comes back on it."""

        def on_fail(e):
            (fail or (lambda x: self.note(why(x), True)))(e)

        def work():
            try:
                r = fn()
            except Exception as e:  # ApiError, or a bug: the screen stays, a notice says so
                self.post(lambda err=e: on_fail(err))
                return
            if then:
                self.post(lambda: then(r))

        if self.sync:
            work()
            self.drain()
        else:  # a daemon: :q never waits for a request the server is sitting on
            threading.Thread(target=work, daemon=True).start()

    def post(self, fn) -> None:
        self.q.put(fn)
        if self.term:
            self.term.wake()

    def drain(self) -> None:
        while True:
            try:
                fn = self.q.get_nowait()
            except queue.Empty:
                return
            self.guarded(fn)
            self.dirty = True

    def guarded(self, fn, *args) -> None:
        try:
            fn(*args)
        except Exception as e:
            if self.strict:
                raise
            self.note(f"{type(e).__name__.upper()}: {str(e).upper()}", True)

    def later(self, delay: float, name: str, fn) -> None:
        if self.sync:
            fn()
            return
        self.timers[name] = (self.now() + delay, fn)

    def tick(self) -> None:
        t = self.now()
        for name, (due, fn) in list(self.timers.items()):
            if t >= due:
                del self.timers[name]
                self.guarded(fn)
                self.dirty = True
        if self.count and self.pending is None and t - self.count_at >= COUNT_S:
            c, self.count = self.count, ""
            if len(c) == 1:  # a lone digit nothing followed: the screen key
                self.guarded(self.fkey, int(c))
            self.dirty = True
        if self.notice_text and t >= self.notice_until:
            self.notice_text = ""
            self.dirty = True
        if t >= self.next_poke and self.id:
            self.next_poke = t + float(self.reg.get("quote_interval_s") or 60)
            self.poke()
        sec = int(time.time())
        if sec != self.last_sec:
            self.last_sec = sec
            self.dirty = True
        if self.flashes:  # one scrolled out of view is never drawn, so never ends by itself
            self.flashes = {k: until for k, until in self.flashes.items() if until > t}
            self.dirty = True

    # ── data flow ────────────────────────────────────────────────────────────────────────────────
    def start(self, registry: dict | None = None) -> None:
        self.reg = registry or self.api.registry()
        if self.reg.get("settings_error"):
            self.note(self.reg["settings_error"], True)
        self.api.subscribe(lambda t, ev: self.post(lambda: self.on_event(t, ev)))
        self.sync_alerts()
        self.sync_builds()
        self.go("PORT")

    def registry_later(self) -> None:
        self.bg(self.api.registry, lambda r: setattr(self, "reg", r), lambda _e: None)

    def here(self) -> str:
        return f"{self.id} {self.param}" if self.param else (self.id or "")

    def go(self, sid: str, param: str | None = None, via_back: bool = False) -> None:
        """app.js route(): a screen, or a notice saying why not."""
        if self.ask_state:
            self.cancel_ask()
        scr = next((s for s in self.reg["screens"] if s["id"] == sid), None)
        if scr and not scr.get("param"):
            param = None
        bad = (
            f"UNKNOWN SCREEN {sid}"
            if not scr
            else f"{sid} — SOON"
            if scr["status"] != "live"
            else f"{sid}: {scr.get('param_name') or 'TICKER'} REQUIRED"
            if scr.get("param") and not param
            else None
        )
        if bad:
            self.note(bad, not scr or scr["status"] != "soon")
            return
        key = C.key_of(sid, param)
        if key == self.key:
            return
        if self.key and not via_back:
            self.back = C.push_back(self.back, self.key)
        self.id, self.param, self.key = sid, param, key
        self.payload = self.live = self.cold = None
        self.max = self.focus = self.field = None
        self.mode = "normal" if self.mode == "insert" else self.mode
        self.scroll = 0
        self.running.clear()
        self.cross.clear()
        self.sel.clear()
        self.pscroll.clear()
        self.load()

    def load(self) -> None:
        key = self.key

        def done(res):
            if key != self.key:
                return
            self.live = res.get("live")
            self.running = set((self.live or {}).get("running") or [])
            if res.get("cold"):
                self.cold = res.get("reason") or (
                    "NO DATA YET · COMPUTING — the first run fetches prices and history; this view fills in by itself."
                )
                self.payload = None
                return
            self.cold = None
            self.show(res)

        def fail(e):
            if key != self.key:
                return
            if isinstance(e, ApiError) and e.status == 404 and self.param:
                self.note(why(e), True)
                self.leave()
                return
            self.note(f"LOAD {self.here()} FAILED: {why(e)}", True)

        self.bg(lambda: self.api.screen(self.id, self.param), done, fail)

    def leave(self) -> None:
        """Back to where the user came from after a route the server refused (no back-stack entry for it)."""
        to, self.back = C.pop_back(self.back, self.key)
        self.key = None
        self.go(*C.split_key(to), via_back=True)

    def go_back(self) -> None:
        to, self.back = C.pop_back(self.back, self.key)
        if to != self.key:
            self.go(*C.split_key(to), via_back=True)

    def sync_builds(self) -> None:
        touched: set = set()
        self.build_touched.append(touched)

        def done(r):
            snap = builds_from_jobs(r.get("jobs"))
            out = {s: b for s, b in snap.items() if s not in touched}
            out.update({s: self.builds[s] for s in touched if s in self.builds})
            self.builds = out
            self.build_touched.remove(touched)

        self.bg(self.api.jobs, done, lambda _e: self.build_touched.remove(touched))

    def sync_alerts(self) -> None:
        def done(v):
            act = (v or {}).get("active") or []
            self.alerts = {"active": len(act), "down": any(e.get("down") for e in act)}

        self.bg(self.api.alerts, done, lambda _e: None)

    def poke(self) -> None:
        sid, param = self.id, self.param

        def done(r):
            if r:
                self.live = r.get("live")
                self.running.update(j.get("tier") for j in r.get("jobs") or [])

        self.bg(lambda: self.api.ensure(sid, param), done, lambda _e: None)

    def show(self, payload: dict) -> None:
        prev = self.payload if (self.payload or {}).get("screen") == payload.get("screen") else None
        self.payload = payload
        self.gen += 1
        self.memo.clear()
        for p in payload.get("panels") or []:  # a preview checked against the old trades: check it again
            st = self.forms.get(self.form_key(p["id"]))
            if p.get("type") == "paste" and st and st.get("preview"):
                self.preview_soon(p, 0)
        if (
            self.ask_state
            and payload.get("screen") == "TRADES"
            and not T.stands(self.ask_state, self.key, self.trade_ids())
        ):
            self.cancel_ask("THE TRADES CHANGED — NOTHING DONE, ASK AGAIN")
        until = self.now() + FLASH_S
        for k in changed(prev, payload):
            self.flashes[k] = until

    def on_event(self, kind: str, ev: dict) -> None:
        self.dirty = True
        if kind == "conn":
            was, self.conn = self.conn, ev.get("ok")
            if ev.get("ok") and not was:
                if self.id:
                    self.load()
                self.sync_alerts()
                self.sync_builds()
            return
        if kind == "alerts":
            self.alerts = {"active": ev.get("active") or 0, "down": bool(ev.get("down"))}
            new = ev.get("new") or []
            if new:
                msg = (
                    f"ALERT {new[0].get('id')} · {new[0].get('msg')}"
                    if len(new) == 1
                    else f"{len(new)} NEW ALERTS · {new[0].get('msg')} · ALRT"
                )
                self.note(msg, any(e.get("down") for e in new))
            return
        if kind == "job" and ev.get("screen") == "ALRT" and ev.get("tier") == "watch":
            if ev.get("state") == "failed":
                self.alert_err = ev.get("error") or "failed"
            if ev.get("state") == "done":
                self.alert_err = None
        if kind == "job" and ev.get("tier") == "heavy":
            self.builds = track_build(self.builds, ev)
            if ev.get("force"):
                for t in self.build_touched:
                    t.add(ev.get("screen"))
        if kind == "screen" and (ev.get("key") or ev.get("id")) == self.key:
            self.load()
            return
        if kind == "job" and ev.get("screen") == self.key:
            if ev.get("state") in ("queued", "running"):
                self.running.add(ev.get("tier"))
            else:
                self.running.discard(ev.get("tier"))
            if ev.get("state") == "failed":
                self.live = {
                    **(self.live or {}),
                    "error": {"tier": ev.get("tier"), "error": ev.get("error"), "at": ev.get("finished")},
                }
            if ev.get("state") == "done" and ((self.live or {}).get("error") or {}).get("tier") == ev.get("tier"):
                self.live = {**self.live, "error": None}

    def note(self, msg, err: bool = False) -> None:
        self.notice_text, self.notice_err, self.notice_until = str(msg), err, self.now() + NOTICE_S
        self.dirty = True

    # ── what panels read ─────────────────────────────────────────────────────────────────────────
    def hit(self, x0, y0, x1, y1, act) -> None:
        self.hits.append((x0, y0, x1, y1, act))

    def panels(self) -> list:
        return (self.payload or {}).get("panels") or []

    def panel(self, pid) -> dict | None:
        return next((p for p in self.panels() if p["id"] == pid), None)

    def table_state(self, p: dict) -> dict:
        return self.tables.setdefault(
            p["id"],
            {
                "sort": list(p.get("sort") or [p.get("key"), "asc"]),
                "cursor": None,
                "show_closed": False,
                "rows": [],
                "hidden": set(),
                "reveal": False,
            },
        )

    def follow_key(self, p: dict):
        return (self.tables.get(p["follows"]) or {}).get("cursor") if p.get("follows") else None

    def flash_bg(self, key: str):
        until = self.flashes.get(key)
        if until is None:
            return None
        left = (until - self.now()) / FLASH_S
        if left <= 0:
            del self.flashes[key]
            return None
        return blend(FLASH, min(1.0, left))

    def is_max(self, p: dict) -> bool:
        return self.max is not None and str(p.get("n")) == str(self.max)

    def spark_of(self, values, w: int) -> tuple:
        key = ("spark", id(values), w)
        if key not in self.memo:
            self.memo[key] = spark(values, w)
        return self.memo[key]

    def today(self) -> date:
        return date.today()

    def form_key(self, pid) -> str:
        return f"{self.key}|{pid}"

    def form_state(self, p: dict) -> dict:
        return self.forms.setdefault(self.form_key(p["id"]), self.blank_form(p))

    @staticmethod
    def blank_form(p: dict) -> dict:
        if p.get("type") == "paste":
            return {"text": "", "file": None, "preview": None, "caret": 0, "top": 0}
        action = next((f.get("options", ["buy"])[0] for f in p.get("fields") or [] if f.get("kind") == "choice"), "buy")
        return {
            "values": {
                "ticker": "",
                "action": action,
                "shares": "",
                "pps": "",
                "total": "",
                "date": "",
                "fee": "",
                "keep": None,
            },
            "edit": None,
            "caret": 0,
            "sug": {"items": [], "i": -1},
        }

    def rows_of(self, p: dict) -> list:
        return next((q.get("rows") or [] for q in self.panels() if q.get("edit") == p["id"]), [])

    def your_file(self) -> str:
        pl = self.payload or {}
        if pl.get("error"):
            n = pl.get("lines") or 0
            return f"MY FILE'S {n} LINE{'' if n == 1 else 'S'}"
        n = len(next((q.get("rows") or [] for q in self.panels() if q.get("type") == "table" and q.get("remove")), []))
        return f"MY {n} TRADE{'' if n == 1 else 'S'}"

    def trade_ids(self) -> list:
        return [
            r.get("id")
            for r in next(
                (q.get("rows") or [] for q in self.panels() if q.get("type") == "table" and q.get("remove")), []
            )
        ]

    # ── drawing ──────────────────────────────────────────────────────────────────────────────────
    def draw(self) -> None:
        w, h = self.size
        c = Canvas(w, h)
        self.hits, self.caret, self.floats = [], None, []
        self.draw_cmdbar(c)
        top = 1
        if not self.zen:
            self.draw_strip(c, 1)
            top = 2
        bottom = h - (0 if self.zen else 1)
        self.grid_top, self.grid_h = top, max(1, bottom - top)
        self.draw_grid(c, top, self.grid_h)
        if not self.zen:
            self.draw_fkeys(c, h - 1)
        for x, y, lines in self.floats:
            wide = min(max((P.seg_w(ln) for ln in lines), default=0), w - x)
            view = c.view(x, y, wide, len(lines))
            P.paint_lines(view, lines, self, "_float")
        self.draw_palette(c)
        self.draw_overlay(c)
        self.painter.paint(c, self.caret if self.mode in ("command", "search", "insert") and not self.overlay else None)
        self.canvas = c
        self.dirty = False

    def draw_cmdbar(self, c: Canvas) -> None:
        width = min(BAR_W, max(12, c.w // 3))
        c.fill(1, 0, width, 1, style(CMD, BAR_BG))
        typing = self.mode in ("command", "search")
        if typing:
            lead = ":" if self.mode == "command" else "/"
            shown = self.bar if self.mode == "search" or self.bar.upper().startswith("IMPORT") else self.bar.upper()
            off = max(0, self.bar_caret - (width - 3))
            c.put(1, 0, lead + shown[off : off + width - 1], style(CMD, BAR_BG))
            self.caret = (2 + self.bar_caret - off, 0)
        else:
            text = {"insert": "-- INSERT --", "visual": "-- VISUAL --"}.get(self.mode, ":")
            c.put(1, 0, f"{text} {self.count}{_chord_name(self.pending)}", style(DIM, BAR_BG))
        x = width + 3
        pl = self.payload
        if c.w >= P.NARROW and self.id:
            x = c.put(x, 0, self.id, style(AMBER))
            x = c.put(x + 1, 0, clip((pl or {}).get("title") or (self.param or ""), 24), style(DIM)) + 2
        note_w = min(tw(self.notice_text), 60) if self.notice_text else 0
        room = c.w - x - 1 - (note_w + 2 if note_w else 0)
        items = self.status_items()
        hidden = C.fit_hidden([{"p": it[0], "w": tw(it[1])} for it in items], max(0, room), 2)
        shown = [it for i, it in enumerate(items) if i not in hidden]
        used = sum(tw(it[1]) for it in shown) + 2 * max(0, len(shown) - 1)
        sx = c.w - 1 - used
        for _p, text, fg, bold, act in shown:
            nx = c.put(sx, 0, text, style(fg, BG, bold))
            if act:
                self.hit(sx, 0, nx, 1, act)
            sx = nx + 2
        if self.notice_text:
            c.put(x, 0, clip(self.notice_text, max(0, c.w - 1 - used - x - 2)), style(DN if self.notice_err else AMBER))

    def status_items(self) -> list:
        meta, live = (self.payload or {}).get("meta") or {}, self.live or {}
        out = []
        a = self.alerts
        if a["active"]:
            out.append(
                (
                    PRIO["alert"],
                    f"▲ {a['active']} ALERT{'' if a['active'] == 1 else 'S'}",
                    DN if a["down"] else AMBER,
                    True,
                    ("run", "ALERTS"),
                )
            )
        if self.alert_err and self.id != "ALRT":
            out.append((PRIO["err"], "ERR ALRT", DN, False, None))
        pulse = int(time.time()) % 2 == 0
        amber_pulse = AMBER if pulse else blend(AMBER, 0.35)
        for s, b in self.builds.items():
            if b["run"] is not None:
                out.append((PRIO["build"], f"{s} BUILD {b['run']}", amber_pulse, False, None))
            else:
                out.append((PRIO["build"], f"ERR {s} · {b['err'][:60]}", DN, False, None))
        if self.reg.get("settings_error"):
            out.append((PRIO["err"], "SETTINGS ERR", DN, False, None))
        for t, at in (meta.get("tiers") or {}).items():
            out.append((PRIO["tier"], f"{t[0].upper()} {fmt_stamp(at)}", DIM, False, None))
        for text in meta_badges(meta):
            out.append((PRIO["meta"], text, DN, False, None))
        if live.get("code_changed"):
            out.append((PRIO["code"], "CODE CHANGED · RECOMPUTING", AMBER, False, None))
        if live.get("error"):
            out.append(
                (PRIO["err"], f"ERR {self.id or ''} {(live['error'].get('tier') or '').upper()}", DN, False, ("err",))
            )
        if not self.conn:
            out.append((PRIO["live"], "● OFFLINE", DN, False, None))
        elif self.running:
            out.append(
                (
                    PRIO["live"],
                    f"● UPDATING {'+'.join(sorted(map(str, self.running))).upper()}",
                    amber_pulse,
                    False,
                    None,
                )
            )
        else:
            out.append((PRIO["live"], "● LIVE", AMBER, False, None))
        out.append((PRIO["clock"], fmt_clock(datetime.now()), FG, False, None))
        return out

    def draw_strip(self, c: Canvas, y: int) -> None:
        c.fill(0, y, c.w, 1, style(FG, STRIP_BG))
        pl = self.payload
        base = lambda fg=FG, bold=False: style(fg, STRIP_BG, bold)
        if not pl:
            x = c.put(1, y, self.here(), base(AMBER, True))
            c.put(x + 2, y, "NO STORED DATA YET" if self.cold else "", base(DIM))
            return
        x = c.put(1, y, pl.get("screen"), base(AMBER, True))
        x = c.put(x + 2, y, pl.get("title"), base(FG, True))
        hint_x = c.w - tw(HINT) - 1
        ctx = (pl.get("context") or {}).get("text") or ""
        x = c.put(x + 2, y, clip(ctx, max(0, hint_x - x - 4 if c.w >= P.NARROW else c.w - x - 3)), base(DIM)) + 2
        for a in pl.get("actions") or []:
            nx = c.put(x, y, f" {a.get('label')} ", style(BG, AMBER, True))
            self.hit(x, y, nx, y + 1, ("run", a.get("run")))
            x = nx + 1
        if c.w >= P.NARROW and hint_x > x:
            c.put(hint_x, y, HINT, base(DIM))

    def draw_fkeys(self, c: Canvas, y: int) -> None:
        keys = sorted((s for s in self.reg["screens"] if s.get("fkey")), key=lambda s: s["fkey"])
        x = 1

        def key(num, label, on, soon, act):
            nonlocal x
            bg = FG if on else "#5a3a10" if soon else AMBER
            x0 = x
            x = c.put(x, y, f" {num} ", style(FG, BG, True))
            x = c.put(x, y, f" {label} ", style(BG, bg))
            self.hit(x0, y, x, y + 1, act)
            x += 1

        key("F1", "HELP", self.overlay is not None and self.overlay.get("help"), False, ("run", "HELP"))
        for s in keys:
            key(s["fkey"], s["id"], s["id"] == self.id, s["status"] != "live", ("fkey", s["id"]))
        hint = ": command · Esc back"
        if c.w - tw(hint) - 1 > x:
            c.put(c.w - tw(hint) - 1, y, hint, style(DIM))

    def draw_grid(self, c: Canvas, top: int, h: int) -> None:
        w = c.w
        self.row_ys, self.plots, self.rects = {}, {}, {}
        if self.cold is not None:
            for i, t in enumerate(wrap(self.cold, w - 4)):
                c.put(2, top + 1 + i, t, style(DIM))
            return
        if not self.payload:
            return
        outer, self.hits = self.hits, []
        panels = self.panels()
        maxed = next((p for p in panels if self.is_max(p)), None)
        if maxed:
            g = Canvas(w, h)
            self.draw_one(g, maxed, 0, 0, w, h, None, True)
            self.scroll = 0
            self.rects[maxed["id"]] = (0, 0, w, h)
        else:
            placed, total = P.layout(panels, w, h, self)
            g = Canvas(w, max(total, h))
            for p, x, y, pw, ph, lines in placed:
                self.rects[p["id"]] = (x, y, pw, ph)
                self.draw_one(g, p, x, y, pw, ph, lines, False)
                if x + pw < w:
                    g.fill(x + pw, y, 1, ph, style(RULE), "│")
            self.reveal(total, h)
        for i in range(min(h, c.h - top)):
            c.ch[top + i][:] = g.ch[self.scroll + i]
            c.st[top + i][:] = g.st[self.scroll + i]
        dy = top - self.scroll
        self.hits = outer + [
            (x0, y0 + dy, x1, y1 + dy, a) for x0, y0, x1, y1, a in self.hits if top <= y0 + dy < top + h
        ]
        self.row_ys = {pid: [(y + dy, k) for y, k in ys] for pid, ys in self.row_ys.items()}
        if self.caret:
            self.caret = (self.caret[0], self.caret[1] + dy)
        self.floats = [(x, y + dy, ls) for x, y, ls in self.floats]

    def draw_one(self, g: Canvas, p: dict, x: int, y: int, w: int, h: int, lines, windowed: bool) -> None:
        try:
            if windowed:
                lines = P.safe_lines(p, w, self)
            P.draw_panel(g.view(x, y, w, h), p, lines, self, windowed)
        except Exception as e:  # one panel that cannot draw never takes the screen with it
            if self.strict:
                raise
            g.fill(x, y, w, h)
            g.put(x + 1, y, clip(P.failed_text(p, e), w - 2), style(DN))

    def reveal(self, total: int, h: int) -> None:
        for pid, st in self.tables.items():
            if st.get("reveal"):
                st["reveal"] = False
                y = next((y for y, k in self.row_ys.get(pid, []) if js_str(k) == js_str(st["cursor"])), None)
                if y is not None:
                    self.scroll = y if y < self.scroll else y - h + 1 if y >= self.scroll + h else self.scroll
        if self.reveal_panel in self.rects:
            _x, y, _w, ph = self.rects[self.reveal_panel]
            self.scroll = y if y < self.scroll or ph >= h else y + ph - h if y + ph > self.scroll + h else self.scroll
        self.reveal_panel = None
        self.scroll = max(0, min(self.scroll, max(0, total - h)))

    def draw_palette(self, c: Canvas) -> None:
        items = self.ac["items"]
        if not items or self.mode != "command":
            return
        label = min(44, max(19, max(tw(it["label"]) for it in items)))
        width = min(c.w - 2, label + 60)
        y = 1
        for i, it in enumerate(items):
            on = i == self.ac["i"]
            bg = AMBER if on else "#111111"
            fg = BG if on else DIM if it.get("dim") else FG
            c.fill(1, y, width, 1, style(fg, bg))
            x = c.put(2, y, pad(it["label"], label), style(fg, bg))
            c.put(
                x + 1,
                y,
                clip(it.get("desc") or "", width - label - 3),
                style(BG if on else DIM if it.get("dim") else TEXT, bg),
            )
            self.hit(1, y, 1 + width, y + 1, ("ac", i))
            y += 1
        c.fill(1, y, width, 1, style(DIM, "#111111"))
        c.put(2, y, clip("↑↓ ^N ^P select · Tab complete · Enter go · Esc close", width - 2), style(DIM, "#111111"))

    def draw_overlay(self, c: Canvas) -> None:
        ov = self.overlay
        if not ov:
            return
        w, h = c.w, c.h
        if ov.get("ask"):
            bw = max(40, int(w * 0.56))
            bx = (w - bw) // 2
            inner = bw - 4
            lines = ov["render"](inner)
            by, bh = self.grid_top + 1, min(h - self.grid_top - 2, len(lines) + 3)
        else:
            bx, by = int(w * 0.12), int(h * 0.08)
            bw, bh = w - 2 * bx, h - 2 * by
            inner = bw - 4
            lines = ov["render"](inner)
        bs = style(AMBER, OVER_BG)
        self.hit(bx, by, bx + bw, by + bh, ("overlay",))
        c.fill(bx, by, bw, bh, style(FG, OVER_BG))
        c.fill(bx, by, bw, 1, style(BG, AMBER))
        c.put(bx + 1, by, clip(ov["title"], bw - 14), style(BG, AMBER, True))
        ctx = "ANSWER BELOW" if ov.get("ask") else "j k scroll · Esc close"
        c.put(bx + bw - tw(ctx) - 1, by, ctx, style(BG, AMBER))
        for y in range(by + 1, by + bh):
            c.put(bx, y, "│", bs)
            c.put(bx + bw - 1, y, "│", bs)
        c.put(bx, by + bh - 1, "└" + "─" * (bw - 2) + "┘", bs)
        room = bh - 2
        ov["top"] = max(0, min(ov.get("top", 0), max(0, len(lines) - room)))
        ov["room"], ov["n"] = room, len(lines)
        view = c.view(bx + 2, by + 1, inner, room)
        for y, ln in enumerate(lines[ov["top"] : ov["top"] + room]):
            x = 0
            for text, fg, bg, bold, act in ln:
                nx = view.put(x, y, text, (fg, bg or OVER_BG, bold))
                if act:
                    self.hit(view.ox + x, view.oy + y, view.ox + nx, view.oy + y + 1, act)
                x = nx

    # ── input ────────────────────────────────────────────────────────────────────────────────────
    def on_input(self, e) -> None:
        self.dirty = True
        self.guarded(self.dispatch, e)

    def dispatch(self, e) -> None:
        if isinstance(e, tuple):
            if e[0] == "paste":
                self.on_paste(e[1])
            elif e[0] == "mouse":
                self.on_mouse(*e[1:])
            return
        if self.ask_state:
            self.ask_key(e)
        elif self.overlay:
            self.overlay_key(e)
        elif self.mode == "command":
            self.command_key(e)
        elif self.mode == "search":
            self.search_key(e)
        elif self.mode == "insert":
            self.insert_key(e)
        elif self.mode == "visual":
            self.visual_key(e)
        else:
            self.normal_key(OPTION.get(e, e))

    def take(self, default: int | None = 1) -> int | None:
        n = int(self.count) if self.count else default
        self.count = ""
        return n

    def normal_key(self, k: str) -> None:
        if self.pending is not None:
            first, self.pending = self.pending, None
            self.chord(first, k)
            return
        if len(k) == 1 and k.isdigit() and (k != "0" or self.count):
            self.count += k
            self.count_at = self.now()
            return
        if k in CHORD_FIRST:
            self.pending = k
            return
        chart = self.focused_chart()
        n = self.take(None)
        step = n or 1
        page = max(1, self.grid_h - 2)
        if k in ("j", "down"):
            self.cursor(step)
        elif k in ("k", "up"):
            self.cursor(-step)
        elif k in ("ctrl-d",):
            self.cursor(max(1, page // 2) * step)
        elif k in ("ctrl-u",):
            self.cursor(-max(1, page // 2) * step)
        elif k in ("ctrl-f", "pgdn"):
            self.cursor(page * step)
        elif k in ("ctrl-b", "pgup"):
            self.cursor(-page * step)
        elif k in ("G", "end"):
            self.cursor_to(n - 1 if n else 10**9)
        elif k == "home":
            self.cursor_to(0)
        elif k in ("H", "M", "L"):
            self.cursor_view(k)
        elif k == "ctrl-e":
            self.scroll += step
        elif k == "ctrl-y":
            self.scroll = max(0, self.scroll - step)
        elif k in ("h", "left"):
            if chart:
                self.chart_step(chart["id"], -step)
            else:
                self.focus_toward(-1, 0)
        elif k in ("l", "right"):
            if chart:
                self.chart_step(chart["id"], step)
            else:
                self.focus_toward(1, 0)
        elif chart and k in ("w", "b"):
            self.chart_step(chart["id"], (10 if k == "w" else -10) * step)
        elif chart and k in ("0", "^", "$"):
            self.chart_to(chart["id"], 0 if k != "$" else 10**9)
        elif chart and k == "v":
            self.visual_start(chart["id"])
        elif k == "-":
            if chart:
                self.zoom_out(chart["id"])
        elif k == "tab":
            self.focus_panel(1)
        elif k == "shift-tab":
            self.focus_panel(-1)
        elif k in ("<", "shift-left"):
            self.sort_step(-1)
        elif k in (">", "shift-right"):
            self.sort_step(1)
        elif k == "s":
            self.sort_flip()
        elif k == "shift-up":
            self.sort_dir("asc")
        elif k == "shift-down":
            self.sort_dir("desc")
        elif k == "enter":
            self.drill()
        elif k in ("i", "a"):
            self.step_in()
        elif k == "o":
            form = next((p for p in self.panels() if p.get("type") == "form"), None)
            if form:
                self.focus_field(form["id"])
        elif k == "del":
            if not self.typed:
                self.remove()
        elif k == "u":
            self.run("UNDO")
        elif k == "ctrl-r":
            self.run("UNDO")
        elif k == ":":
            self.open_bar()
        elif k == "/":
            self.search_start()
        elif k == "n":
            self.search_next(1)
        elif k == "N":
            self.search_next(-1)
        elif k in ("?", "f1"):
            self.show_help()
        elif k == "esc":
            self.escape()
        elif k == "q":
            if self.max:
                self.max = None
            else:
                self.note("TYPE :q TO QUIT")
        elif k == "ctrl-c":
            self.note("TYPE :q TO QUIT")
        elif k == "ctrl-l":
            self.painter.invalidate()
        elif k == "ctrl-o":
            self.go_back()
        elif k == "ctrl-z":
            if self.term and os.name != "nt":
                self.term.suspend()
                self.painter.invalidate()
        elif k == "m":
            self.mode_cycle()
        elif k in ("[", "]"):
            self.range_cycle(1 if k == "]" else -1)
        elif k in ("{", "}"):
            self.horizon_cycle(1 if k == "}" else -1)
        elif k in MODE_KEYS:
            self.set_mode(MODE_KEYS[k])
        elif k in ("p", "alt-p"):
            self.pick_cycle()
        elif k.startswith("alt-") and k[4:].isdigit() and k[4:] != "0":
            self.maximize(int(k[4:]))

    def chord(self, first: str, k: str) -> None:
        n = self.take(None)
        if k == "esc":
            return
        if first == "g":
            if k == "g":
                self.cursor_to((n or 1) - 1)
            elif k == "t":
                self.screen_step(1, n)
            elif k == "T":
                self.screen_step(-1, None)
            elif k == "e":
                self.show_error()
        elif first == "z":
            if k in ("z", "t", "b"):
                self.scroll_cursor(k)
            elif k == "a":
                self.toggle_closed()
            elif k.isdigit() and k != "0":
                self.maximize(int(k))
        elif first == "d":
            if k == "d":
                self.remove(ask_why=True)
        elif first == "Z":
            if k in ("Z", "Q"):
                self.quit = True
        elif first == "ctrl-w":
            if k in ("h", "left"):
                self.focus_toward(-1, 0)
            elif k in ("l", "right"):
                self.focus_toward(1, 0)
            elif k in ("j", "down"):
                self.focus_toward(0, 1)
            elif k in ("k", "up"):
                self.focus_toward(0, -1)
            elif k in ("w", "ctrl-w"):
                self.focus_panel(1)
            elif k == "W":
                self.focus_panel(-1)
            elif k in ("o", "ctrl-o", "m", "|", "_"):
                p = self.panel(self.focus) or self.focused_table()
                if p:
                    self.maximize(p.get("n"))
            elif k in ("q", "c", "="):
                self.max = None
            elif k.isdigit() and k != "0":
                self.maximize(int(k))

    def overlay_key(self, k: str) -> None:
        ov = self.overlay
        room = max(1, ov.get("room", 10))
        if k in ("esc", "q", "enter", "ctrl-c") or (k in ("?", "f1") and ov.get("help")):
            self.close_overlay()
            return
        if k in ("?", "f1"):
            self.show_help()
            return
        if self.pending == "g":
            self.pending = None
            if k == "g":
                ov["top"] = 0
            return
        moves = {
            "j": 1,
            "down": 1,
            "k": -1,
            "up": -1,
            "ctrl-d": room // 2,
            "ctrl-u": -(room // 2),
            "ctrl-f": room,
            "pgdn": room,
            "ctrl-b": -room,
            "pgup": -room,
            "G": 10**9,
            "end": 10**9,
            "home": -(10**9),
        }
        if k == "g":
            self.pending = "g"
        elif k in moves:
            ov["top"] = ov.get("top", 0) + moves[k]

    def ask_key(self, k: str) -> None:
        """A question is answered only by Enter, no sooner than ASK_S after it opened; Esc closes it; any other key closes it
        unanswered too — and says so."""
        if k == "enter":
            if self.now() - self.ask_state["at"] >= ASK_S:
                self.confirm()
            return
        if k == "esc":
            self.close_overlay()
            return
        self.cancel_ask()

    # ── the command bar ──────────────────────────────────────────────────────────────────────────
    def open_bar(self, text: str = "") -> None:
        self.mode = "command"
        self.bar, self.bar_caret = text, len(text)
        self.ac["moved"] = False
        self.typed = True
        self.update_ac()

    def close_bar(self) -> None:
        self.mode = "normal"
        self.bar, self.bar_caret = "", 0
        self.ac = {"items": [], "i": 0, "moved": False}

    def command_key(self, k: str) -> None:
        if k in ("esc", "ctrl-c"):
            self.close_bar()
        elif k == "enter":
            self.exec_bar()
        elif k == "tab":
            if self.bar.upper().startswith("IMPORT "):
                self.complete_path()
            elif self.ac["items"]:
                self.ac_accept()
        elif k in ("down", "ctrl-n", "up", "ctrl-p", "alt-up", "alt-down"):
            d = 1 if k in ("down", "ctrl-n") else -1
            if self.ac["items"] and self.hi < 0 and not k.startswith("alt"):
                self.ac["i"] = (self.ac["i"] + d) % len(self.ac["items"])
                self.ac["moved"] = True
            else:  # recalled: ↑↓ walk the history on, past the list it opened
                self.history_step(-d)
        elif k == "backspace" and not self.bar:
            self.close_bar()
        else:
            out = edit_line(self.bar, self.bar_caret, k)
            if out:
                self.bar, self.bar_caret = out
                self.ac["moved"] = False
                self.hi = -1
                self.typed = True
                self.update_ac()

    def history_step(self, d: int) -> None:
        if not self.history:
            return
        self.hi = max(-1, min(len(self.history) - 1, self.hi + d))
        self.bar = "" if self.hi < 0 else self.history[self.hi]
        self.bar_caret = len(self.bar)
        self.update_ac()

    def ctx(self) -> dict:
        return {
            "screens": self.reg["screens"],
            "params": self.reg.get("params") or {},
            "targets": self.reg.get("targets") or [],
            "cashflow": self.reg.get("cashflow_url"),
        }

    def candidates(self) -> list:
        pos = next((p for p in self.panels() if p.get("type") == "table" and p.get("drives")), None)
        names = (
            {r.get(pos["key"]): r.get("name") or "" for r in pos.get("rows") or [] if not r.get("_closed")}
            if pos
            else {}
        )
        screens = self.reg["screens"]
        opt_live = any(s["id"] == "OPT" and s["status"] == "live" for s in screens)
        return [
            *[
                {
                    "label": s["id"],
                    "desc": s["title"] + ("" if s["status"] == "live" else " · soon"),
                    "run": s["id"],
                    "dim": s["status"] != "live",
                }
                for s in screens
                if not s.get("param")
            ],
            *C.verb_items(self.ctx()),
            *[
                {
                    "label": f"BUILD {s['id']}",
                    "desc": f"rerun the {s['title']} build now ({s['build']})",
                    "run": f"BUILD {s['id']}",
                }
                for s in screens
                if s.get("build") and s["status"] == "live"
            ],
            *(
                [
                    {"label": f"TARGET {n}", "desc": "optimizer target portfolio", "run": f"TARGET {n}"}
                    for n in self.reg.get("targets") or []
                ]
                if opt_live
                else []
            ),
            *[
                {"label": t, "desc": f"{names.get(t) or 'security'} · SEC", "run": t}
                for t in (self.reg.get("params") or {}).get("SEC", [])
            ],
            *[
                {"label": f"{s['id']} {q}", "desc": str(s.get("title") or s["id"]).lower(), "run": f"{s['id']} {q}"}
                for s in screens
                if s.get("param") and s["id"] != "SEC" and s["status"] == "live"
                for q in (self.reg.get("params") or {}).get(s["id"], [])
            ],
        ]

    def update_ac(self) -> None:
        q = self.bar.strip()
        if not q or q.upper().startswith("IMPORT "):
            self.ac = {"items": [], "i": 0, "moved": False}
            return
        watch = q[6:].strip() if q.upper().startswith("WATCH ") and q[6:].strip() else None
        if watch:
            self.lookup_soon(watch)
            items = C.watch_items(self.lookup_state["items"]) if C.fold(self.lookup_state["q"]) == C.fold(watch) else []
        else:
            items = C.rank(q, self.candidates(), lambda c: [c["label"], c["desc"]])
        self.ac["items"] = items
        self.ac["i"] = min(self.ac["i"], max(0, len(items) - 1))

    def lookup_soon(self, q: str) -> None:
        if C.fold(q) == C.fold(self.lookup_state["q"]):
            return

        def ask():
            def done(items):
                self.lookup_state = {"q": q, "items": items}
                if (
                    self.mode == "command"
                    and self.bar.strip().upper().startswith("WATCH ")
                    and C.fold(self.bar.strip()[6:]) == C.fold(q)
                ):
                    self.update_ac()

            self.bg(lambda: self.api.lookup(q), done, lambda _e: None)

        self.later(LOOKUP_S, "lookup", ask)

    def ac_accept(self) -> None:
        items = self.ac["items"]
        if items:
            self.bar = items[min(self.ac["i"], len(items) - 1)]["run"]
            self.bar_caret = len(self.bar)
            self.ac["moved"] = False
            self.update_ac()

    def exec_bar(self) -> None:
        items = self.ac["items"]
        it = items[self.ac["i"]] if items and self.ac["i"] < len(items) else None
        text = C.enter_text(self.bar, self.ctx(), it, self.ac["moved"])
        if it:
            self.pick(it, text)
        else:
            self.run(text)

    def pick(self, item: dict, text: str | None = None) -> None:
        text = item["run"] if text is None else text
        if C.fills_bar(self.bar, item, text):
            self.bar, self.bar_caret = item["run"], len(item["run"])
            self.ac["moved"] = False
            self.update_ac()
            return
        self.run(text)

    def complete_path(self) -> None:
        typed = self.bar[7:]
        hits = sorted(glob.glob(glob.escape(os.path.expanduser(typed)) + "*"))
        if not hits:
            self.note(f"NO FILE {typed}", True)
            return
        common = os.path.commonprefix(hits)
        if len(hits) == 1 and os.path.isdir(common):
            common = os.path.join(common, "")
        self.bar = f"IMPORT {common}"
        self.bar_caret = len(self.bar)
        if len(hits) > 1:
            self.note(" · ".join(os.path.basename(h) for h in hits[:8]))

    def run(self, text: str) -> None:
        """app.js run(): one command, whichever key or click it came from."""
        raw = str(text).strip()
        a = C.parse(raw, self.ctx())
        if raw:
            self.history = [raw, *[h for h in self.history if h != raw]][:50]
        self.hi = -1
        self.close_bar()
        self.field = None
        fail = lambda e: self.note(why(e), True)
        kind = a["type"]
        if kind == "screen":
            self.go(a["id"], a.get("param"))
        elif kind == "help":
            self.show_help(a.get("screen"), a.get("param"))
        elif kind == "fullscreen":
            self.zen = not self.zen
        elif kind == "goto":
            webbrowser.open(a["url"])
        elif kind == "quit":
            self.quit = True
        elif kind == "import":
            self.import_file(raw.split(None, 1)[1] if len(raw.split(None, 1)) > 1 else "")
        elif kind == "refresh":
            sid = a.get("screen") or self.id
            param = None if a.get("screen") else self.param
            what = f"{sid} {param}" if param else sid

            def done(r):
                jobs = r.get("jobs") or []
                self.running.update(j.get("tier") for j in jobs)
                self.note(
                    f"REFRESH {what} {'+'.join(str(j.get('tier')).upper() for j in jobs)}"
                    if jobs
                    else f"{what}: NOTHING TO REFRESH YET"
                )

            self.bg(lambda: self.api.refresh(sid, a.get("tier"), param), done, fail)
        elif kind == "build":
            sid = a.get("screen") or self.id
            req = build_request(sid, self.reg["screens"])
            if not req["ok"]:
                self.note(req["text"], True)
            else:
                self.bg(lambda: self.api.build(sid), lambda _r: self.note(req["text"]), fail)
        elif kind == "toggle-closed":
            self.toggle_closed()
        elif kind == "target":
            show = lambda r: self.note(f"TARGET {r.get('target')}")
            self.bg(self.api.prefs if not a.get("name") else (lambda: self.api.set_target(a["name"])), show, fail)
        elif kind == "alert":
            self.bg(
                lambda: self.api.add_alert(a["text"]),
                lambda r: self.note(f"ALERT {r.get('id')} · {r.get('text')}"),
                fail,
            )
        elif kind == "unalert":
            self.bg(lambda: self.api.del_alert(a["id"]), lambda _r: self.note(f"REMOVED {a['id']}"), fail)
        elif kind == "ack":
            body = {"all": True} if a.get("all") else {"id": a["id"]}
            self.bg(lambda: self.api.ack(body), lambda r: self.note(f"ACKED {r.get('acked')}"), fail)
        elif kind == "watch":
            self.do_watch(a["query"])
        elif kind == "trade":
            self.do_trade(a["values"])
        elif kind == "fresh":
            self.start_fresh()
        elif kind == "undo":
            self.do_undo()
        elif kind == "unwatch":

            def gone(_r):
                self.note(f"UNWATCHED {a['ticker']}")
                self.registry_later()

            self.bg(lambda: self.api.unwatch(a["ticker"]), gone, fail)
        elif kind == "soon":
            self.note(f"{a['id']} — SOON")
        elif kind == "error":
            self.note(a["msg"], True)

    def do_watch(self, query: str) -> None:
        def done(rows):
            r = C.resolve_watch(query, rows)
            if r["kind"] == "none":
                self.note(f"NO MATCH: {query}", True)
            elif r["kind"] == "several":
                self.lookup_state = {"q": query, "items": rows}
                self.open_bar(f"WATCH {query}")
                self.note(f"{len(rows)} MATCHES — ↑↓ PICK · ENTER")
            else:

                def added(_r):
                    self.note(f"WATCHING {r['ticker']} · {r['name']}")
                    self.registry_later()

                self.bg(lambda: self.api.watch(r["ticker"]), added)

        self.bg(lambda: self.api.lookup(query), done)

    # ── movement ─────────────────────────────────────────────────────────────────────────────────
    def focused_table(self) -> dict | None:
        tables = [p for p in self.panels() if p.get("type") == "table"]
        return next((p for p in tables if p["id"] == self.focus), tables[0] if tables else None)

    def focused_chart(self) -> dict | None:
        p = self.panel(self.focus)
        return p if p and p.get("type") == "chart" and p["id"] in self.plots else None

    def set_cursor(self, pid: str, key) -> None:
        self.focus, self.typed = pid, False
        st = self.tables[pid]
        st["cursor"], st["reveal"] = key, True

    def row_keys(self, p: dict) -> list:
        return [r.get(p["key"]) for r in self.table_state(p)["rows"]]

    def cursor(self, d: int) -> None:
        p = self.focused_table()
        if not p:
            self.scroll = max(0, self.scroll + d)  # no table here: j k scroll the page
            return
        keys = self.row_keys(p)
        if not keys:
            return
        cur = [js_str(k) for k in keys]
        at = cur.index(js_str(self.table_state(p)["cursor"])) if js_str(self.table_state(p)["cursor"]) in cur else -1
        self.set_cursor(p["id"], keys[max(0, min(len(keys) - 1, at + d))])

    def cursor_to(self, i: int) -> None:
        p = self.focused_table()
        keys = self.row_keys(p) if p else []
        if keys:
            self.set_cursor(p["id"], keys[max(0, min(len(keys) - 1, i))])

    def cursor_view(self, which: str) -> None:
        p = self.focused_table()
        if not p:
            return
        top, bottom = self.grid_top, self.grid_top + self.grid_h
        seen = [k for y, k in self.row_ys.get(p["id"], []) if top <= y < bottom]
        if seen:
            self.set_cursor(p["id"], seen[{"H": 0, "M": len(seen) // 2, "L": len(seen) - 1}[which]])

    def scroll_cursor(self, how: str) -> None:
        p = self.focused_table()
        if not p:
            return
        st = self.table_state(p)
        y = next((y for y, k in self.row_ys.get(p["id"], []) if js_str(k) == js_str(st["cursor"])), None)
        if y is None:
            return
        at = y - self.grid_top + self.scroll  # its row in the page
        h = self.grid_h
        self.scroll = max(0, at - (h // 2 if how == "z" else 0 if how == "t" else h - 1))

    def screen_step(self, d: int, n: int | None) -> None:
        keys = sorted((s for s in self.reg["screens"] if s.get("fkey")), key=lambda s: s["fkey"])
        if not keys:
            return
        if n:
            self.fkey(n)
            return
        i = next((i for i, s in enumerate(keys) if s["id"] == self.id), -1)
        self.run(keys[(i + d) % len(keys)]["id"])

    def fkey(self, n: int) -> None:
        s = next((x for x in self.reg["screens"] if x.get("fkey") == n), None)
        if s:
            self.run(s["id"])

    def focus_panel(self, d: int) -> None:
        ids = [p["id"] for p in self.panels()]
        if ids:
            i = ids.index(self.focus) if self.focus in ids else -1
            self.focus = ids[(i + d) % len(ids)]
            self.reveal_panel = self.focus

    def focus_toward(self, dx: int, dy: int) -> None:
        """The nearest panel that way, one overlapping this one's span first (vim's Ctrl-W h j k l)."""
        here = self.rects.get(self.focus) or self.rects.get((self.focused_table() or {}).get("id"))
        if not here:
            return
        x, y, w, h = here
        best, score = None, None
        for pid, (qx, qy, qw, qh) in self.rects.items():
            if dx:
                gap = (qx - (x + w)) if dx > 0 else (x - (qx + qw))
                overlap = min(y + h, qy + qh) - max(y, qy)
            else:
                gap = (qy - (y + h)) if dy > 0 else (y - (qy + qh))
                overlap = min(x + w, qx + qw) - max(x, qx)
            if gap < -1 or (qx, qy) == (x, y):
                continue
            s = (overlap <= 0, gap, -overlap)
            if score is None or s < score:
                best, score = pid, s
        if best:
            self.focus = best
            self.reveal_panel = best

    def maximize(self, n) -> None:
        self.max = None if str(self.max) == str(n) else n
        if self.max is not None:
            p = next((q for q in self.panels() if str(q.get("n")) == str(n)), None)
            if p:
                self.focus = p["id"]
            else:
                self.max = None

    def sort_step(self, d: int) -> None:
        p = self.focused_table()
        if p:
            st = self.table_state(p)
            cols = P.pick_cols(p.get("cols") or [], self.pick_of(p)) if p.get("picks") else p.get("cols") or []
            st["sort"] = [C.next_sort_col(cols, st["sort"][0], d, st.get("hidden") or set()), "desc"]

    def sort_flip(self) -> None:
        p = self.focused_table()
        if p:
            st = self.table_state(p)
            st["sort"] = [st["sort"][0], "asc" if st["sort"][1] == "desc" else "desc"]

    def sort_dir(self, direction: str) -> None:
        p = self.focused_table()
        if p:
            st = self.table_state(p)
            st["sort"] = [st["sort"][0], direction]

    def toggle_closed(self) -> None:
        t = next((p for p in self.panels() if any(r.get("_closed") for r in p.get("rows") or [])), None)
        if t:
            st = self.table_state(t)
            st["show_closed"] = not st["show_closed"]

    def drill(self) -> None:
        fp = self.panel(self.focus)
        if fp and fp.get("type") in ("form", "paste"):
            self.focus_field(fp["id"])
            return
        if fp and fp.get("type") == "banner" and fp.get("run"):
            self.run(fp["run"])
            return
        p = self.focused_table()
        k = self.tables.get(p["id"], {}).get("cursor") if p else None
        if k is None:
            return
        k = js_str(k)
        if p.get("edit"):
            self.edit_row(p, k)
        elif p.get("enter"):
            self.run(p["enter"].replace("{key}", k))
        elif p.get("drives"):
            self.run(k)

    def escape(self) -> None:
        if self.overlay:
            self.close_overlay()
            return
        if self.focus in self.cross:
            self.cross.pop(self.focus)
            return
        if self.max:
            self.max = None
            return
        if self.param:
            self.go_back()

    # ── search ───────────────────────────────────────────────────────────────────────────────────
    def search_start(self) -> None:
        p = self.focused_table()
        if not p:
            self.note("NO TABLE TO SEARCH HERE")
            return
        st = self.table_state(p)
        self.search = {"pid": p["id"], "from": st["cursor"]}
        self.mode = "search"
        self.bar, self.bar_caret = "", 0

    def search_key(self, k: str) -> None:
        pid = self.search["pid"]
        if k in ("esc", "ctrl-c"):
            self.tables[pid]["cursor"] = self.search["from"]
            self.tables[pid]["reveal"] = True
            self.close_bar()
            self.search = None
        elif k == "enter":
            self.last_search = self.bar
            self.close_bar()
            self.search = None
        elif k == "backspace" and not self.bar:
            self.tables[pid]["cursor"] = self.search["from"]
            self.close_bar()
            self.search = None
        else:
            out = edit_line(self.bar, self.bar_caret, k)
            if out:
                self.bar, self.bar_caret = out
                hit = self.find_row(self.panel(pid), self.bar, self.search["from"], 1, True)
                if hit is not None:
                    self.set_cursor(pid, hit)

    def find_row(self, p, q: str, start, d: int, inclusive: bool = False):
        """The next row holding `q` in any cell, wrapping; None when none does."""
        if not p or not q:
            return None
        st = self.table_state(p)
        rows = st["rows"]
        keys = [js_str(r.get(p["key"])) for r in rows]
        at = keys.index(js_str(start)) if js_str(start) in keys else 0
        needle = C.fold(q)
        cols = p.get("cols") or []
        for step in range(0 if inclusive else 1, len(rows) + 1):
            r = rows[(at + d * step) % len(rows)]
            if any(needle in C.fold(fmt(r.get(c["k"]), P.cell_fmt(r, c))[0]) for c in cols if c.get("fmt") != "spark"):
                return r.get(p["key"])
        return None

    def search_next(self, d: int) -> None:
        p = self.focused_table()
        if not p or not self.last_search:
            return
        hit = self.find_row(p, self.last_search, self.table_state(p)["cursor"], d)
        if hit is None:
            self.note(f"NOT FOUND: {self.last_search}", True)
        else:
            self.set_cursor(p["id"], hit)

    # ── charts ───────────────────────────────────────────────────────────────────────────────────
    def chart_target(self, need: str | None = None) -> dict | None:
        p = self.panel(self.focus)
        if p and p.get("type") == "chart" and (not need or p.get(need)):
            return p
        return next((q for q in self.panels() if q.get("type") == "chart" and (not need or q.get(need))), None)

    def chart_step(self, pid: str, cells: int) -> None:
        plot = self.plots.get(pid)
        if not plot:
            return
        m, r = plot["model"], plot["raster"]
        n = len(m["x"])
        each = max(1, round(n / max(1, r.pw)))  # a key moves a cell, however many points are in one
        at = self.cross.get(pid)
        self.chart_to(pid, (m["rest"] if at is None else at) + cells * each)

    def chart_to(self, pid: str, i: int) -> None:
        plot = self.plots.get(pid)
        if not plot:
            return
        n = len(plot["model"]["x"])
        self.cross[pid] = max(0, min(n - 1, i))
        if self.mode == "visual" and pid in self.sel:
            self.sel[pid] = (self.sel[pid][0], self.cross[pid])

    def visual_start(self, pid: str) -> None:
        plot = self.plots.get(pid)
        if not plot:
            return
        at = self.cross.get(pid)
        at = plot["model"]["rest"] if at is None else at
        self.cross[pid], self.sel[pid] = at, (at, at)
        self.mode = "visual"

    def visual_key(self, k: str) -> None:
        pid = next(iter(self.sel), None)
        if pid is None:
            self.mode = "normal"
            return
        if len(k) == 1 and k.isdigit() and (k != "0" or self.count):
            self.count += k
            self.count_at = self.now()
            return
        step = self.take(1)
        if k in ("esc", "ctrl-c", "v"):
            self.sel.clear()
            self.mode = "normal"
        elif k == "enter":
            a, b = self.sel.pop(pid)
            self.mode = "normal"
            self.zoom(pid, a, b)
        elif k in ("h", "left"):
            self.chart_step(pid, -step)
        elif k in ("l", "right"):
            self.chart_step(pid, step)
        elif k in ("w", "b"):
            self.chart_step(pid, (10 if k == "w" else -10) * step)
        elif k in ("0", "^", "$"):
            self.chart_to(pid, 0 if k != "$" else 10**9)

    def zoom(self, pid: str, a: int, b: int) -> None:
        plot = self.plots.get(pid)
        if not plot:
            return
        m = plot["model"]
        a, b = sorted((a, b))
        if b - a < 1:
            self.note("SELECT A PERIOD: h l, THEN ENTER")
            return
        back = (m["range"].get("prev") or m["default"]) if m["dragged"] else m["range"]
        self.ranges[pid] = {"from": m["x"][a], "to": m["x"][b], "prev": back, "key": m["key"]}
        self.cross.pop(pid, None)

    def zoom_out(self, pid: str) -> None:
        plot = self.plots.get(pid)
        if plot and plot["model"]["dragged"]:
            m = plot["model"]
            self.ranges[pid] = m["range"].get("prev") or m["default"]
            self.cross.pop(pid, None)

    def set_range(self, pid: str, r: str) -> None:
        self.ranges[pid] = r
        self.cross.pop(pid, None)

    def range_cycle(self, d: int) -> None:
        p = self.chart_target("ranges")
        if not p:
            self.note("NO CHART WITH RANGES HERE")
            return
        names = p["ranges"]
        cur = self.ranges.get(p["id"])
        cur = (p.get("range") or "ALL") if not isinstance(cur, str) else cur
        i = names.index(cur) if cur in names else 0
        self.set_range(p["id"], names[(i + d) % len(names)])

    def horizon_cycle(self, d: int) -> None:
        p = self.chart_target("horizons")
        if not p:
            self.note("NO CHART WITH A HORIZON HERE")
            return
        names = p["horizons"]
        cur = self.horizons.get(p["id"]) if self.horizons.get(p["id"]) in names else p.get("horizon")
        i = names.index(cur) if cur in names else 0
        self.horizons[p["id"]] = names[(i + d) % len(names)]
        self.cross.pop(p["id"], None)

    def modes_here(self) -> list:
        out = []
        for p in self.panels():
            if p.get("type") == "chart":
                m = P.chart_model(p, self)
                if m and m["modes"]:
                    out.append((p, m))
        return out

    def set_mode(self, mode: str) -> None:
        ids = [p["id"] for p, m in self.modes_here() if mode in m["modes"]]
        if not ids:
            self.note(f"NO CHART WITH {mode} HERE")
        for pid in ids:
            self.modes[pid] = mode
            self.cross.pop(pid, None)

    def pick_of(self, p: dict):
        return self.table_state(p).get("pick") or p.get("pick")

    def set_pick(self, pid: str, k: str) -> None:
        p = self.panel(pid)
        st = self.table_state(p)
        st["sort"] = [P.pick_sort(p.get("cols") or [], st["sort"][0], self.pick_of(p), k), st["sort"][1]]
        st["pick"] = k

    def pick_cycle(self) -> None:
        """The focused table's period (POSITIONS: DAY 1W 1M YTD), else the first table's that has periods."""
        tables = [p for p in self.panels() if p.get("picks")]
        p = next((q for q in tables if q["id"] == self.focus), tables[0] if tables else None)
        if p is None:
            self.note("NO PERIODS HERE")
            return
        self.set_pick(p["id"], p["picks"][(p["picks"].index(self.pick_of(p)) + 1) % len(p["picks"])])

    def mode_cycle(self) -> None:
        here = self.modes_here()
        if not here:
            self.note("NO CHART WITH VIEWS HERE")
            return
        p = self.panel(self.focus)
        _q, m = next(((q, m) for q, m in here if p and q["id"] == p["id"]), here[0])
        self.set_mode(m["modes"][(m["modes"].index(m["mode"]) + 1) % len(m["modes"])])

    # ── TRADES: forms ────────────────────────────────────────────────────────────────────────────
    def form_order(self, p: dict) -> list:
        """What Tab walks through, in the order it is shown."""
        pid = p["id"]
        if p.get("type") == "paste":
            st = self.form_state(p)
            res = st.get("preview") or {}
            return [
                "text",
                "@preview",
                "@file",
                *(["@add"] if res.get("ok") else []),
                *(["@replace"] if res.get("ok") and st.get("file") else []),
            ]
        fields = p.get("fields") or []
        order = [f["k"] for f in fields]
        return [*[k for k in order], "@save", "@clear"] if pid else []

    def focus_field(self, pid: str, name: str | None = None) -> None:
        p = self.panel(pid)
        if not p:
            return
        st = self.form_state(p)
        name = name or self.form_order(p)[0]
        self.focus, self.field, self.mode = pid, (pid, name), "insert"
        self.reveal_panel = pid
        if p.get("type") == "paste":
            st["caret"] = min(st.get("caret", 0), len(st.get("text") or ""))
        else:
            st["caret"] = len(str(st["values"].get(name) or ""))

    def step_in(self) -> None:
        p = self.panel(self.focus)
        if not (p and p.get("type") in ("form", "paste")):
            p = next((q for q in self.panels() if q.get("type") == "form"), None)
        if p:
            self.focus_field(p["id"])

    def leave_field(self) -> None:
        self.field, self.mode = None, "normal"

    def insert_key(self, k: str) -> None:
        pid, name = self.field
        p = self.panel(pid)
        if not p:
            self.leave_field()
            return
        st = self.form_state(p)
        if k == "f1":
            self.show_help()
            return
        if k in ("esc", "ctrl-c"):
            if (st.get("sug") or {}).get("items"):  # the first Esc closes the list, the next leaves the field
                st["sug"] = {"items": [], "i": -1}
            else:
                self.leave_field()
            return
        if k in ("tab", "shift-tab"):
            self.field_step(p, st, 1 if k == "tab" else -1)
            return
        if name.startswith("@"):
            if k in ("enter", " "):
                self.button(p, name)
            elif k in ("left", "h", "up", "k"):
                self.field_step(p, st, -1)
            elif k in ("right", "l", "down", "j"):
                self.field_step(p, st, 1)
            return
        if p.get("type") == "paste":
            self.paste_key(p, st, k)
        else:
            self.form_key_press(p, st, name, k)

    def field_step(self, p: dict, st: dict, d: int) -> None:
        order = self.form_order(p)
        name = self.field[1]
        i = order.index(name) if name in order else 0
        if st.get("sug"):
            st["sug"] = {"items": [], "i": -1}
        self.focus_field(p["id"], order[(i + d) % len(order)])

    def form_key_press(self, p: dict, st: dict, name: str, k: str) -> None:
        f = next((x for x in p.get("fields") or [] if x["k"] == name), {})
        sug = st["sug"]
        if f.get("kind") == "ticker" and sug["items"] and k in ("down", "up", "ctrl-n", "ctrl-p"):
            n = len(sug["items"])  # -1 = none highlighted (Enter then saves)
            sug["i"] += 1 if k in ("down", "ctrl-n") else -1
            sug["i"] = -1 if sug["i"] >= n else n - 1 if sug["i"] < -1 else sug["i"]
            return
        if k in ("down", "ctrl-n", "up", "ctrl-p"):
            self.field_step(p, st, 1 if k in ("down", "ctrl-n") else -1)
            return
        if k == "enter":
            if f.get("kind") == "ticker" and sug["i"] >= 0:
                self.pick_suggestion(p, sug["i"])
            else:
                st["sug"] = {"items": [], "i": -1}
                self.save_form(p)
            return
        if f.get("kind") == "choice":
            opts = f.get("options") or []
            if k in ("left", "right", " ", "h", "l", "j", "k") and opts:
                cur = st["values"].get(name)
                i = opts.index(cur) if cur in opts else 0
                st["values"] = T.typed(
                    st["values"], name, opts[(i + (-1 if k in ("left", "h", "k") else 1)) % len(opts)]
                )
            return
        text = str(st["values"].get(name) or "")
        out = edit_line(text, st.get("caret", 0), k)
        if out and out[0] != text:
            st["values"] = T.typed(st["values"], name, out[0])
            if f.get("kind") == "ticker":
                self.suggest(p, st, out[0])
        if out:
            st["caret"] = out[1]

    def suggest(self, p: dict, st: dict, q: str) -> None:
        st["sug"] = {"items": T.rank_suggestions(q, p.get("book"), []), "i": -1}
        if not q.strip():
            return

        def ask():
            def done(found):
                if str(st["values"].get("ticker")) != q or self.field != (p["id"], "ticker"):
                    return
                old = st["sug"]
                kept = (
                    old["items"][old["i"]]["ticker"] if 0 <= old["i"] < len(old["items"]) else None
                )  # a row picked meanwhile stays
                items = T.rank_suggestions(q, p.get("book"), found)
                st["sug"] = {"items": items, "i": next((i for i, s in enumerate(items) if s["ticker"] == kept), -1)}

            self.bg(lambda: self.api.lookup(q), done, lambda _e: None)

        self.later(LOOKUP_S, "suggest", ask)

    def pick_suggestion(self, p: dict, i: int) -> None:
        st = self.form_state(p)
        items = st["sug"]["items"]
        if 0 <= i < len(items):
            st["values"] = T.typed(st["values"], "ticker", items[i]["ticker"])
            st["sug"] = {"items": [], "i": -1}
            self.focus_field(
                p["id"], next((f["k"] for f in p.get("fields") or [] if f.get("kind") == "choice"), "ticker")
            )

    def button(self, p: dict, name: str) -> None:
        if name == "@save":
            self.save_form(p)
        elif name == "@clear":
            self.forms[self.form_key(p["id"])] = self.blank_form(p)
            self.focus_field(p["id"], "ticker")
        elif name == "@preview":
            self.preview_soon(p, 0)
        elif name == "@file":
            self.leave_field()
            self.open_bar("IMPORT ")
        elif name == "@add":
            self.import_form(p, "append")
        elif name == "@replace":
            self.import_form(p, "replace")

    def paste_key(self, p: dict, st: dict, k: str) -> None:
        text, caret = st.get("text") or "", st.get("caret", 0)
        if k == "ctrl-g":  # the browser's Ctrl+Enter: a terminal cannot tell that from Enter
            self.preview_soon(p, 0)
            return
        if k == "enter":
            k = "\n"
        if k == "\n":
            out = (text[:caret] + "\n" + text[caret:], caret + 1)
        elif k in ("up", "down", "ctrl-p", "ctrl-n"):
            out = (text, _caret_rows(text, caret, -1 if k in ("up", "ctrl-p") else 1))
        elif k in ("home", "ctrl-a"):
            out = (text, text.rfind("\n", 0, caret) + 1)
        elif k in ("end", "ctrl-e"):
            end = text.find("\n", caret)
            out = (text, len(text) if end < 0 else end)
        else:
            out = edit_line(text, caret, k)
        if not out:
            return
        if out[0] != text:
            st["text"] = out[0]
            self.preview_soon(p, PREVIEW_S)
        st["caret"] = out[1]

    def on_paste(self, text: str) -> None:
        """A bracketed paste: whole, never as keys (a newline in it must not run or save anything)."""
        if self.ask_state:
            self.cancel_ask()
            return
        if self.mode == "insert":
            pid, name = self.field
            p = self.panel(pid)
            st = self.form_state(p)
            if p.get("type") == "paste" and name == "text":
                clean = text.replace("\r\n", "\n").replace("\r", "\n")
                caret = st.get("caret", 0)
                st["text"] = (st.get("text") or "")[:caret] + clean + (st.get("text") or "")[caret:]
                st["caret"] = caret + len(clean)
                self.preview_soon(p, 0)
            elif not name.startswith("@"):
                flat = " ".join(text.split())
                cur = str(st["values"].get(name) or "")
                caret = st.get("caret", 0)
                st["values"] = T.typed(st["values"], name, cur[:caret] + flat + cur[caret:])
                st["caret"] = caret + len(flat)
        elif self.mode in ("command", "search"):
            flat = " ".join(text.split())
            self.bar = self.bar[: self.bar_caret] + flat + self.bar[self.bar_caret :]
            self.bar_caret += len(flat)
            if self.mode == "command":
                self.update_ac()

    def preview_soon(self, p: dict, delay: float) -> None:
        key = self.form_key(p["id"])

        def go():
            st = self.forms.get(key)
            if st is None:
                return
            text = st.get("text") or ""
            if not text.strip():
                st["preview"] = None
                return
            st["asked"] = st.get("asked", 0) + 1
            n = st["asked"]

            def done(res):
                if st.get("asked") == n:  # a newer preview is on its way otherwise
                    st["preview"] = {**res, "text": text}  # ADD sends exactly this text

            def fail(e):
                if st.get("asked") == n:
                    st["preview"] = {"error": why(e), "rows": [], "text": text}

            self.bg(lambda: self.api.preview_trades(text, "append"), done, fail)

        if delay <= 0:
            go()
        else:
            self.later(delay, "preview", go)

    def import_file(self, path: str) -> None:
        p = next((q for q in self.panels() if q.get("type") == "paste"), None)
        if not p:
            self.note("IMPORT: OPEN TRADES FIRST", True)
            return
        if not path:
            self.open_bar("IMPORT ")
            return
        file = Path(os.path.expanduser(path))
        try:
            if file.stat().st_size > MAX_IMPORT:
                self.note(f"{file.name}: OVER {MAX_IMPORT // 1024 // 1024} MB — NOT A TRADES FILE", True)
                return
            data = file.read_bytes()
        except OSError as e:
            self.note(f"CANNOT READ {file.name}: {(e.strerror or '').upper()}", True)
            return
        st = self.form_state(p)
        st.update(text=T.decode_bytes(data), file=file.name, caret=0, top=0)
        self.focus = p["id"]
        self.preview_soon(p, 0)

    def trade_write(self, call, done, etag=None) -> None:
        """A write to your trades, made on the version the user saw — `etag`: the one a question was asked on; else this
        screen's, or (from another screen) the file's now. A 409 (changed meanwhile) reloads TRADES; the user saves again."""

        def work():
            tag = (
                etag or ((self.payload or {}).get("etag") if self.id == "TRADES" else None) or self.api.trades()["etag"]
            )
            return call(tag)

        def then(res):
            if self.id == "TRADES" and self.payload:
                self.payload["etag"] = res.get("etag")
            done(res)
            self.registry_later()  # SEC's ticker list follows the book

        def fail(e):
            self.note(why(e), True)
            if isinstance(e, ApiError) and e.status == 409 and self.id == "TRADES":
                self.load()

        self.bg(work, then, fail)

    @staticmethod
    def saved_text(res: dict) -> str:
        return " · ".join([f"SAVED · {res.get('text')}", *(res.get("warnings") or [])])

    def save_form(self, p: dict) -> None:
        st = self.form_state(p)
        r = T.trade_from(st["values"], self.today())
        if "error" in r:
            self.note(r["error"], True)
            return
        edit = st.get("edit")

        def call(etag):
            return self.api.edit_trade(edit["id"], etag, r["body"]) if edit else self.api.add_trade(etag, r["body"])

        def done(out):
            self.note(self.saved_text(out), bool(out.get("warnings")))
            blank = self.blank_form(p)
            blank["values"]["action"] = st["values"]["action"]
            self.forms[self.form_key(p["id"])] = blank
            if self.mode == "insert" and self.field and self.field[0] == p["id"]:
                self.focus_field(p["id"], "ticker")  # the next trade

        self.trade_write(call, done)

    def edit_row(self, p: dict, key: str) -> None:
        row = next((r for r in p.get("rows") or [] if js_str(r.get(p["key"])) == key), None)
        target = self.panel(p.get("edit"))
        if not row or not target:
            return
        blank = self.blank_form(target)
        self.forms[self.form_key(target["id"])] = {
            **blank,
            "values": {**blank["values"], **T.edit_values(row)},
            "edit": {
                "id": row["id"],
                "label": f"{fmt_date(row['date'])} {row['tkr']} {row['action']} {T.fmt_qty(row['shares'])}",
            },
        }
        self.focus_field(target["id"], "ticker")
        self.note("EDITING · SAVE REPLACES IT · CANCEL EDIT KEEPS IT")

    def remove(self, ask_why: bool = False) -> None:
        p = T.removable(self.panels(), self.focus)
        k = self.tables.get(p["id"], {}).get("cursor") if p else None
        if k is None:
            if ask_why:
                self.note("NOTHING TO DELETE HERE — ON TRADES, j k TO A ROW FIRST")
            return
        row = next((r for r in p.get("rows") or [] if js_str(r.get(p["key"])) == js_str(k)), None)
        if not row:
            return
        etag = (self.payload or {}).get("etag")
        self.ask(
            "DELETE THIS TRADE?",
            f"{T.row_line(row)} — the file as it is now is kept in input/backups/ and UNDO brings it back.",
            "DELETE",
            lambda: self.trade_write(
                lambda tag: self.api.del_trade(row["id"], tag),
                lambda res: self.note(f"DELETED · {res.get('text')} · UNDO BRINGS IT BACK"),
                etag,
            ),
            [row["id"]],
        )

    @staticmethod
    def trade_rows(now: dict) -> list:
        return [
            {
                "id": r.get("id"),
                "date": r.get("date"),
                "tkr": r.get("ticker"),
                "action": str(r.get("action")).upper(),
                "shares": r.get("shares"),
                "total": r.get("price"),
            }
            for r in now.get("rows") or []
        ]

    def import_form(self, p: dict, mode: str) -> None:
        st = self.form_state(p)
        text = (st.get("preview") or {}).get("text")
        if text is None or text != st.get("text"):  # ADD sends exactly what was previewed: the box changed since
            self.preview_soon(p, 0)
            self.note("THE BOX CHANGED SINCE ITS PREVIEW — PREVIEWED AGAIN: CHECK IT, THEN ADD")
            return

        def go(etag=None):
            def done(res):
                left = res.get("left") or ""
                st.update(text=left, preview=None, caret=0, top=0)
                if not left:
                    st["file"] = None
                many = lambda k, w: f"{k} {w}{'' if k == 1 else 'S'}"
                self.note(
                    f"{'REPLACED YOUR TRADES WITH' if mode == 'replace' else 'ADDED'} {many(res.get('added') or 0, 'TRADE')}"
                    + (f" · {many(res['skipped'], 'LINE')} LEFT IN THE BOX TO FIX" if res.get("skipped") else "")
                )
                if self.field and self.field[0] == p["id"]:
                    self.focus_field(p["id"], "text")

            self.trade_write(lambda tag: self.api.import_trades(tag, text, mode), done, etag)

        if mode != "replace":
            go()
            return
        key = self.key

        def both():
            return self.api.preview_trades(text, "replace"), self.api.trades()

        def asked(pair):
            pre, now = pair
            if self.key != key:  # the screen changed while it asked the server: no question
                return
            if not pre.get("ok"):
                self.note(pre.get("error") or "NOTHING TO ADD — EVERY ROW HAS AN ERROR", True)
                return
            self.ask(
                "REPLACE YOUR TRADES?",
                T.replace_text(self.trade_rows(now), now.get("error"), now.get("lines"), pre, st.get("file")),
                "REPLACE",
                lambda: go(now.get("etag")),
                [r.get("id") for r in now.get("rows") or []],
            )

        self.bg(both, asked)

    def do_trade(self, values: dict) -> None:
        r = T.trade_from(values, self.today())
        if "error" in r:
            self.note(r["error"], True)
            return
        self.note(T.describe(r["trade"], r["fee"]))  # what will be stored, then the save
        self.trade_write(
            lambda tag: self.api.add_trade(tag, r["body"]),
            lambda res: self.note(self.saved_text(res), bool(res.get("warnings"))),
        )

    def start_fresh(self) -> None:
        key = self.key

        def asked(now):
            if self.key != key:
                return
            if not now.get("error") and not now.get("rows"):
                self.note("NOTHING TO CLEAR — input/portfolio.csv HOLDS NO TRADES")
                return

            def done(res):
                self.note(
                    f"STARTED FRESH · THE {res.get('removed')} {'LINES' if now.get('error') else 'TRADES'} ARE IN input/backups/ · UNDO BRINGS THEM BACK"
                )
                if self.id != "TRADES":
                    self.run("TRADES")

            self.ask(
                "START FRESH?",
                T.fresh_text(self.trade_rows(now), now.get("error"), now.get("lines")),
                "START FRESH",
                lambda: self.trade_write(lambda tag: self.api.reset_trades(tag), done, now.get("etag")),
                [r.get("id") for r in now.get("rows") or []],
            )

        self.bg(self.api.trades, asked)

    def do_undo(self) -> None:
        key = self.key

        def asked(now):
            if self.key != key:
                return
            u = now.get("undo")
            if not u:
                self.note("NOTHING TO UNDO")
                return
            if not u.get("ready"):
                self.note(
                    "THE FILE CHANGED SINCE THE LAST CHANGE HERE — UNDO WOULD LOSE THAT; THE FILE BEFORE IT IS IN input/backups/",
                    True,
                )
                return
            redo = u["what"].startswith("UNDO: ")
            self.ask(
                "REDO THE CHANGE UNDO TOOK BACK?" if redo else "UNDO THE LAST CHANGE?",
                T.undo_text(u),
                "REDO" if redo else "UNDO",
                lambda: self.trade_write(
                    lambda tag: self.api.undo_trades(tag), lambda res: self.note(res.get("text")), now.get("etag")
                ),
                [],
            )

        self.bg(self.api.trades, asked)

    # ── overlays ─────────────────────────────────────────────────────────────────────────────────
    def open_overlay(self, title: str, render, **flags) -> None:
        self.overlay = {"title": title, "render": render, "top": 0, **flags}
        self.ask_state = None

    def close_overlay(self) -> None:
        self.overlay = self.ask_state = None
        self.pending = None

    def ask(self, title: str, text: str, yes: str, fn, rows=()) -> None:
        def render(w):
            return [
                *[line(seg(t, FG)) for t in wrap(text, w)],
                line(),
                line(
                    seg(f" {yes} ↵ ", BG, AMBER, True, ("ask", True)),
                    seg(" "),
                    seg(" KEEP · Esc ", FG, "#222222", False, ("ask", False)),
                ),
            ]

        self.open_overlay(title, render, ask=True)
        self.ask_state = {"fn": fn, "rows": list(rows), "route": self.key, "at": self.now()}

    def cancel_ask(self, reason: str = "NOTHING CHANGED — THE QUESTION WAS CLOSED") -> None:
        if self.ask_state:
            self.close_overlay()
            self.note(reason)

    def confirm(self) -> None:
        fn = self.ask_state["fn"] if self.ask_state else None
        if fn:
            self.close_overlay()
            fn()

    def show_help(self, sid: str | None = None, param: str | None = None) -> None:
        if not sid or (sid == self.id and (not param or param == self.param)):
            self.open_help(self.here(), (self.payload or {}).get("help"))
            return
        scr = next((s for s in self.reg["screens"] if s["id"] == sid), None)
        if scr and scr.get("param") and not param:
            self.open_help(sid, [], f"HELP {sid} <{scr.get('param_name') or 'TICKER'}> — its definitions come with one")
            return
        title = f"{sid} {param}" if param else sid
        self.bg(
            lambda: self.api.screen(sid, param),
            lambda p: self.open_help(
                title, p.get("help"), f"{sid}: NO STORED DATA YET — no definitions to show" if p.get("cold") else None
            ),
        )

    def open_help(self, title: str, defs, note: str | None = None) -> None:
        top = max([1, *(s.get("fkey") or 0 for s in self.reg["screens"])])
        rows_cmd = C.help_rows(self.ctx())

        def render(w):
            out = []
            if note:
                out += [line(seg(t, DIM)) for t in wrap(note, w)]
            for h in defs or []:
                out.append(line(seg(h.get("h"), AMBER)))
                out += [line(seg("  " + t, TEXT)) for t in wrap(h.get("body"), w - 2)]

            def table(rows):
                lw = min(40, w // 2)
                for a, b in rows:
                    left, right = wrap(a, lw), wrap(b, max(8, w - lw - 2))
                    for i in range(max(len(left), len(right))):
                        out.append(
                            line(
                                seg(pad(left[i] if i < len(left) else "", lw) + "  ", AMBER),
                                seg(right[i] if i < len(right) else "", FG),
                            )
                        )

            out += [line(), line(seg("COMMANDS", AMBER))]
            table([(r["cmd"], r["desc"]) for r in rows_cmd])
            out += [line(), line(seg("KEYS", AMBER))]
            table(key_rows(top))
            return out

        self.open_overlay(f"HELP {title}", render, help=True)

    def show_error(self) -> None:
        err = (self.live or {}).get("error")
        if not err:
            self.note("NO ERROR ON THIS SCREEN")
            return
        title = f"ERROR {self.here()} {(err.get('tier') or '').upper()}"

        def opened(trace):
            def render(w):
                out = [line(seg(t, DN)) for t in wrap(err.get("error") or "", w)]
                out.append(line())
                for ln in str(trace).splitlines():
                    out += [line(seg(t, TEXT)) for t in wrap(ln, w)]
                return out

            self.open_overlay(title, render)

        trace = err.get("trace")
        if trace:
            opened(trace)
        else:
            key = self.key
            self.bg(
                self.api.jobs,
                lambda r: opened(
                    next(
                        (
                            j.get("trace") or ""
                            for j in r.get("jobs") or []
                            if j.get("screen") == key and j.get("state") == "failed"
                        ),
                        "",
                    )
                ),
                lambda _e: opened(""),
            )

    # ── the mouse ────────────────────────────────────────────────────────────────────────────────
    def hit_at(self, x: int, y: int):
        for x0, y0, x1, y1, act in reversed(self.hits):
            if x0 <= x < x1 and y0 <= y < y1:
                return act
        return None

    def on_mouse(self, kind: str, b: int, x: int, y: int) -> None:
        if kind == "wheel":
            if self.overlay:
                self.overlay["top"] = self.overlay.get("top", 0) + 3 * b
            elif self.max and (self.panel(self.focus) or {}).get("type") == "table":
                self.cursor(3 * b)
            elif self.max:
                self.pscroll[self.focus] = max(0, self.pscroll.get(self.focus, 0) + 3 * b)
            else:
                self.scroll = max(0, self.scroll + 3 * b)
            return
        if kind == "drag":
            if self.drag:
                self.drag_to(x)
            return
        if kind == "release":
            if self.drag:
                self.drag_end()
            return
        if b != 0:
            return
        act = self.hit_at(x, y)
        if self.ask_state and not (act and act[0] in ("ask", "overlay")):  # a press elsewhere: no answer
            self.cancel_ask()
            return
        if self.overlay and not act and not self.ask_state:
            self.close_overlay()
            return
        if act:
            self.do_act(act, x)

    def drag_to(self, x: int) -> None:
        pid = self.drag["pid"]
        plot = self.plots.get(pid)
        if plot:
            i = plot["raster"].index_at(x - plot["x"])
            self.sel[pid] = (self.drag["at"], i)

    def drag_end(self) -> None:
        pid, self.drag = self.drag["pid"], None
        a, b = self.sel.pop(pid, (0, 0))
        if abs(b - a) >= 1:
            self.zoom(pid, a, b)

    def do_act(self, act: tuple, x: int) -> None:
        kind = act[0]
        if kind == "overlay":
            return
        if act == ("run", "HELP") and self.overlay:  # HELP's key again closes it
            self.close_overlay()
        elif kind == "row":
            self.leave_field_if_typing()
            self.set_cursor(act[1], act[2])
        elif kind == "sort":
            st = self.tables[act[1]]
            st["sort"] = (
                [act[2], "asc" if st["sort"][1] == "desc" else "desc"] if st["sort"][0] == act[2] else [act[2], "desc"]
            )
        elif kind == "closed":
            st = self.tables[act[1]]
            st["show_closed"] = not st["show_closed"]
        elif kind == "range":
            self.set_range(act[1], act[2])
        elif kind == "mode":
            self.modes[act[1]] = act[2]
        elif kind == "pick":
            self.set_pick(act[1], act[2])
        elif kind == "hz":
            self.horizons[act[1]] = act[2]
        elif kind == "head":
            self.focus = act[1]
        elif kind == "run":
            self.run(act[1])
        elif kind == "fkey":
            self.bar_click(act[1])
        elif kind == "field":
            self.focus_field(act[1], act[2])
        elif kind == "btn":
            p = self.panel(act[1])
            if p:
                self.button(p, act[2])
        elif kind == "sug":
            p = self.panel(act[1])
            if p:
                self.pick_suggestion(p, act[2])
        elif kind == "ask":
            if act[1]:
                if self.ask_state and self.now() - self.ask_state["at"] >= ASK_S:  # a double-click is no answer
                    self.confirm()
            else:
                self.cancel_ask()
        elif kind == "ac":
            self.pick(self.ac["items"][act[1]])
        elif kind == "err":
            self.show_error()
        elif kind == "plot":
            self.plot_press(act[1], x)

    def leave_field_if_typing(self) -> None:
        if self.mode == "insert":
            self.leave_field()

    def plot_press(self, pid: str, x: int) -> None:
        self.focus = pid
        plot = self.plots.get(pid)
        if not plot:
            return
        t = self.now()
        i = plot["raster"].index_at(x - plot["x"])
        if self.last_press[1] == pid and t - self.last_press[0] < 0.4:  # a double-click goes back to the range before
            self.zoom_out(pid)
            self.last_press = (0.0, None)
            return
        self.last_press = (t, pid)
        self.cross[pid] = i
        self.drag = {"pid": pid, "at": i}

    def bar_click(self, sid: str) -> None:
        """A click on the key bar: its open screen's key closes it — an overlay or a maximized panel first, else back; never
        past home (tabbar.js barClick)."""
        if sid != self.id:
            self.run(sid)
        elif self.overlay or self.max:
            self.escape()
        elif sid != "PORT":
            self.go_back()

    # ── the loop ─────────────────────────────────────────────────────────────────────────────────
    def check_size(self) -> None:
        size = self.term.size()
        if size != self.size:
            self.size = size
            self.memo.clear()
            self.painter.invalidate()
            self.dirty = True

    def wait_s(self, keys: Keys) -> float:
        if keys.waiting:
            return ESC_WAIT_S
        t, wait = self.now(), 0.25
        if self.flashes:
            wait = 0.08
        if self.count and self.pending is None:
            wait = min(wait, max(0.0, self.count_at + COUNT_S - t))
        for due, _fn in self.timers.values():
            wait = min(wait, max(0.0, due - t))
        return wait

    def loop(self) -> None:
        keys = Keys()
        while not self.quit:
            self.check_size()
            data = self.term.read(self.wait_s(keys))
            if data is None:  # the terminal went away
                return
            events = keys.feed(data) if data else keys.timeout() if keys.waiting else []
            for e in events:
                self.on_input(e)
                if self.quit:
                    return
            self.drain()
            self.tick()
            if self.dirty:
                self.guarded(self.draw)
                self.dirty = False


def _chord_name(p: str | None) -> str:
    return {"ctrl-w": "^W", None: ""}.get(p, p or "")


def _caret_rows(text: str, caret: int, d: int) -> int:
    """The caret a line up or down, in the same column as far as that line reaches."""
    start = text.rfind("\n", 0, caret) + 1
    col = caret - start
    if d < 0:
        if start == 0:
            return caret
        prev = text.rfind("\n", 0, start - 1) + 1
        return min(prev + col, start - 1)
    end = text.find("\n", caret)
    if end < 0:
        return caret
    after = text.find("\n", end + 1)
    return min(end + 1 + col, len(text) if after < 0 else after)


# ── build.js, status.js, diff.js ─────────────────────────────────────────────────────────────────
def track_build(builds: dict, ev: dict) -> dict:
    if ev.get("tier") != "heavy" or not ev.get("force"):
        return builds  # an unforced heavy job is a cheap reload
    out = dict(builds)
    s = ev.get("screen")
    if ev.get("state") == "failed":
        out[s] = {"run": None, "err": str(ev.get("error") or "failed").removeprefix("BuildFailed: ")}
    elif ev.get("state") == "done":
        out.pop(s, None)
    else:
        out[s] = {"run": ev.get("progress") or ("QUEUED" if ev.get("state") == "queued" else "STARTING"), "err": None}
    return out


def builds_from_jobs(jobs) -> dict:
    out: dict = {}
    for j in sorted(jobs or [], key=lambda j: j.get("id") or 0):
        out = track_build(out, j)
    return out


def build_request(sid: str | None, screens: list) -> dict:
    s = next((x for x in screens if x["id"] == sid), None)
    if s and s.get("build"):
        return {"ok": True, "text": f"{sid} BUILD STARTED · {s['build']}"}
    builds = [x["id"] for x in screens if x.get("build")]
    if not sid:
        return {"ok": False, "text": f"BUILD <{'|'.join(builds) or 'SCREEN'}>"}
    return {
        "ok": False,
        "text": f"{sid} HAS NOTHING TO BUILD" + (" — " + " · ".join(f"BUILD {b}" for b in builds) if builds else ""),
    }


def meta_badges(meta: dict) -> list:
    stale = list((meta.get("stale") or {}).items())
    never = [t for t, ts in stale if ts is None]
    old = [t for t, ts in stale if ts is not None]
    out = []
    if never:
        out.append(f"NO PRICE {' '.join(never)}")
    if old:
        out.append(f"STALE {len(old)} {' '.join(old)}")
    warn = meta.get("warn")
    if isinstance(warn, list):
        out += [x for x in warn if isinstance(x, str) and x]
    elif isinstance(warn, dict):
        by: dict = {}
        src = warn["ccy"] if isinstance(warn.get("ccy"), dict) else warn
        for t, c in src.items():
            if isinstance(c, str) and c:
                by.setdefault(c.upper(), []).append(t)
        out += [f"CCY {c}: {' '.join(sorted(by[c]))}" for c in sorted(by)]
    return out


def cell_values(payload: dict | None) -> dict:
    m = {}
    for p in (payload or {}).get("panels") or []:
        kind, pid = p.get("type"), p.get("id")
        if kind in ("kpi", "bars"):
            for it in p.get("items") or []:
                m[f"{pid}|{it.get('k') or it.get('label')}|v"] = it.get("v")
        elif kind == "ledger":
            for ln in p.get("lines") or []:
                if not ln.get("sep"):
                    m[f"{pid}|{ln.get('label')}|v"] = ln.get("v")
        elif kind == "heatmap":
            labels = p.get("labels") or []
            cells = p.get("cells") or []
            for i, r in enumerate(labels):
                for j, c in enumerate(labels):
                    m[f"{pid}|{r}|{c}"] = cells[i][j] if i < len(cells) and j < len(cells[i]) else None
        elif kind == "table":
            cols = [c for c in p.get("cols") or [] if c.get("fmt") != "spark"]
            for r in p.get("rows") or []:
                for c in cols:
                    m[f"{pid}|{js_str(r.get(p.get('key')))}|{c['k']}"] = r.get(c["k"])
            total = p.get("total")
            if total:
                for c in cols:
                    if c["k"] in total:
                        m[f"{pid}|__total__|{c['k']}"] = total[c["k"]]
    return m


def changed(prev: dict | None, next_payload: dict | None) -> set:
    """Cells whose value changed between two payloads: what flashes."""
    if not prev or not next_payload:
        return set()

    def same(x, y):
        if isinstance(x, (int, float)) and isinstance(y, (int, float)):
            return abs(x - y) <= 1e-9 * max(1, abs(x), abs(y))
        return x == y

    before = cell_values(prev)
    return {k for k, v in cell_values(next_payload).items() if k in before and not same(before[k], v)}
