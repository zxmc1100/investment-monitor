"""input/portfolio.csv as TRADES edits it: read it with stable row ids and its version (etag = sha1 of its bytes),
and write it — one trade added, edited or deleted, an import appended or swapped in, a fresh start, or an UNDO.

Every write names the version it was made on and is refused (Conflict) when the file changed meanwhile — an
edit in Excel, another tab. The WHOLE resulting file is checked first: no sale beyond the shares held then, and
read back through the ledger it must give exactly the trades meant. Your own extra columns (Notes, Broker …) are
carried along after the six. Then the old file is kept in input/backups/ and the new one replaces it — fsynced,
retried while another program (Excel on Windows) holds it, always in the canonical form (monitor.portfolio.trades).

input/backups/ holds
  * portfolio-original.csv — the file before the very first TRADES write, kept once, never rotated;
  * before-start-fresh-NNNN.csv / before-replace-NNNN.csv — the file before each START FRESH / REPLACE, the
    newest 5 of each, never in the ring;
  * portfolio-NNNNNNN.csv — a ring of the newest 20 before ordinary edits, numbered (never dated: a wrong clock
    cannot make the new one look old), a copy equal to the newest one skipped, rotated only after the new file
    is in place;
  * undo.json — what the last write did and which backup holds the file before it: UNDO puts that back (and is
    a write like any other, so it can be undone too).
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Callable

from monitor.data.files import write_bytes_durable
from monitor.portfolio import trades as T
from monitor.portfolio.ledger import COLUMNS, CSVError, parse_portfolio_text, read_table

KEEP = 20              # the ring of ordinary backups
KEEP_PINNED = 5        # before-start-fresh / before-replace, each
FIELDS = ("date", "ticker", "action", "shares", "price", "pps")
FRESH, REPLACE = "before-start-fresh", "before-replace"
BLOCKED = "CANNOT WRITE {} — OPEN IN EXCEL OR ANOTHER PROGRAM? CLOSE IT AND SAVE AGAIN"


class Conflict(Exception):
    """The file changed since the version the write was made on."""


class Invalid(ValueError):
    """The write would leave a file that is wrong (one line; `errors` lists every problem)."""

    def __init__(self, msg: str, errors: list[str] | None = None):
        super().__init__(msg)
        self.errors = errors or [msg]


class Blocked(Exception):
    """The file cannot be written now — on Windows, Excel holds a CSV it has open."""


class Missing(KeyError):
    """No row with that id (any more)."""

    def __str__(self) -> str:
        return "NO SUCH TRADE — IT WAS CHANGED OR DELETED; RELOADED"


def _data_lines(text: str) -> int:
    """Rows below the header that hold something (T.has_trades' notion) — what an unreadable file holds."""
    return max(0, len([ln for ln in text.splitlines() if ln.strip(" \t\ufeff,;\"")]) - 1)


class TradeBook:
    """`quote(ticker) -> {price[, ccy]} | None`: one quote for a ticker new to your file (warns, never blocks);
    `isin(code) -> ticker | None` resolves ISINs in a paste; `today()` dates a trade without one."""

    def __init__(self, csv: Path, *, example: Path | None = None, keep: int = KEEP,
                 quote: Callable[[str], dict | None] | None = None,
                 isin: Callable[[str], str | None] | None = None,
                 today: Callable[[], date] = date.today):
        self.csv = Path(csv)
        self.example, self.keep, self.quote, self.isin, self.today = example, keep, quote, isin, today
        self.backups = self.csv.parent / "backups"
        self.journal = self.backups / "undo.json"
        self._lock = threading.Lock()

    # ── reading ────────────────────────────────────────────────────────────────────────────────
    def _load(self) -> dict:
        """{data: the bytes | None, rows (each with its "extra" columns), extra: those columns' names, error |
        None, lines: data rows — counted from the text when it cannot be read}."""
        try:
            data = self.csv.read_bytes()
        except FileNotFoundError:
            return {"data": None, "rows": [], "extra": [], "error": None, "lines": 0}
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            return {"data": data, "rows": [], "extra": [], "lines": _data_lines(data.decode("utf-8", "replace")),
                    "error": f'{self.csv.name}: not UTF-8 text — in Excel use Save As → "CSV UTF-8"'}
        try:
            header, raw, book = read_table(text, self.csv.name)
        except CSVError as e:
            return {"data": data, "rows": [], "extra": [], "error": str(e), "lines": _data_lines(text)}
        extra = [h for h in header if h and h not in COLUMNS]
        rows = [{**t, "extra": {c: r.get(c, "") for c in extra if r.get(c)}} for t, r in zip(book["transactions"], raw)]
        return {"data": data, "rows": rows, "extra": extra, "error": None, "lines": len(rows)}

    def read(self) -> dict:
        """{"etag", "rows": [{id, date, ticker, action, shares, price, pps, extra}] in file order, "error" | None,
        "lines": its data rows (counted even when it cannot be read), "exists", "example": the file is still
        examples/portfolio.example.csv, "undo": {what, at, ready} | None — ready: the file is still as that write
        left it}."""
        f = self._load()
        example = f["data"] is not None and self.example is not None and T.same_trades(self.csv, self.example)
        rec = self._last()
        undo = {"what": rec["what"], "at": rec["at"], "ready": rec["after"] == T.etag(f["data"])} if rec else None
        return {"etag": T.etag(f["data"]), "rows": [{"id": i, **r} for i, r in zip(T.ids(f["rows"]), f["rows"])],
                "error": f["error"], "lines": f["lines"], "exists": f["data"] is not None, "example": example,
                "undo": undo}

    # ── the backups and the undo record ────────────────────────────────────────────────────────
    def _numbered(self, prefix: str, digits: int) -> list[tuple[int, Path]]:
        pat = re.compile(rf"{re.escape(prefix)}-(\d{{{digits}}})\.csv")
        found = [(int(m.group(1)), p) for p in self.backups.glob(f"{prefix}-*.csv") if (m := pat.fullmatch(p.name))]
        return sorted(found)

    def _ring(self) -> list[tuple[int, Path]]:
        return self._numbered(self.csv.stem, 7)

    def _keep_backup(self, data: bytes, pin: str | None) -> str:
        """Copy the file about to be replaced: the original once, then a pinned copy (START FRESH / REPLACE) or the
        ring's next — unless the ring's newest already holds these very bytes. Returns the copy's name."""
        self.backups.mkdir(parents=True, exist_ok=True)
        original = self.backups / f"{self.csv.stem}-original.csv"
        if not original.exists():
            write_bytes_durable(original, data)
        if pin:
            have = self._numbered(pin, 4)
            target = self.backups / f"{pin}-{(have[-1][0] if have else 0) + 1:04d}.csv"
        else:
            ring = self._ring()
            if ring and ring[-1][1].read_bytes() == data:
                return ring[-1][1].name
            target = self.backups / f"{self.csv.stem}-{(ring[-1][0] if ring else 0) + 1:07d}.csv"
        write_bytes_durable(target, data)
        return target.name

    def _rotate(self) -> None:
        """After a write went in: the ring down to `keep`, each pinned kind down to KEEP_PINNED."""
        for found, keep in ((self._ring(), self.keep), (self._numbered(FRESH, 4), KEEP_PINNED),
                            (self._numbered(REPLACE, 4), KEEP_PINNED)):
            for _, p in found[:-keep]:
                p.unlink(missing_ok=True)

    def _last(self) -> dict | None:
        try:
            rec = json.loads(self.journal.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return rec if isinstance(rec, dict) and {"what", "after", "backup", "at"} <= rec.keys() else None

    def _commit(self, data: bytes | None, blob: bytes | None, what: str, pin: str | None = None) -> str:
        """Replace the file by `blob` (None: remove it) after keeping `data` (None: there was no file); then rotate
        and record the undo. Returns the new etag. Blocked when the file cannot be written."""
        try:
            kept = self._keep_backup(data, pin) if data is not None else None
            if blob is None:
                self._remove()
            else:
                write_bytes_durable(self.csv, blob)
        except OSError:
            raise Blocked(BLOCKED.format(self.csv.name)) from None
        self._rotate()
        after = T.etag(blob)
        self.backups.mkdir(parents=True, exist_ok=True)
        record = {"what": what, "at": datetime.now().isoformat(timespec="seconds"), "backup": kept, "after": after}
        write_bytes_durable(self.journal, json.dumps(record, ensure_ascii=False).encode("utf-8"))
        return after

    def _remove(self, tries: int = 5) -> None:
        for attempt in range(tries):
            try:
                self.csv.unlink(missing_ok=True)
                return
            except PermissionError:
                if attempt == tries - 1:
                    raise
                time.sleep(0.1)

    # ── writing ────────────────────────────────────────────────────────────────────────────────
    def _current(self, etag: str, *, readable: bool = True) -> dict:
        f = self._load()
        if T.etag(f["data"]) != etag:
            raise Conflict()
        if readable and f["error"]:
            raise Invalid(f"YOUR FILE HAS AN ERROR — {f['error']}: FIX IT, OR IMPORT A FILE WITH REPLACE, OR UNDO")
        return f

    def _write(self, f: dict, new: list[dict], what: str, pin: str | None = None) -> str:
        """Check `new` as a whole against the file `f` it replaces, write it canonically (your extra columns after
        the six); returns the new etag."""
        if problems := T.new_problems(f["rows"], new):
            raise Invalid(problems[0], problems)
        text = T.to_csv(new, f["extra"])
        try:
            back = parse_portfolio_text(text, self.csv.name)["transactions"]
        except CSVError as e:
            raise Invalid(f"THE FILE WOULD NOT READ BACK: {e} — NOTHING CHANGED") from None
        meant = [{**{k: r[k] for k in FIELDS}, "ticker": r["ticker"].upper()} for r in new]
        if back != meant:
            raise Invalid("THE FILE WOULD NOT READ BACK AS WRITTEN — NOTHING CHANGED")
        return self._commit(f["data"], text.encode("utf-8"), what, pin)

    def _make(self, d: dict) -> dict:
        try:
            return T.make_trade(d, self.today())
        except T.TradeError as e:
            raise Invalid(str(e)) from None

    def _quote_warnings(self, trade: dict, rows: list[dict]) -> list[str]:
        """One quote for a ticker new to the file: none, or not in euros, is a warning."""
        t = trade["ticker"]
        if self.quote is None or any(r["ticker"].upper() == t for r in rows):
            return []
        try:
            q = self.quote(t)
        except Exception:                    # a network hiccup says nothing about the ticker
            return []
        if not q:
            return [f"NO YAHOO QUOTE FOR {t} — CHECK THE TICKER (SAVED ANYWAY)"]
        ccy = str(q.get("ccy") or "EUR").upper()
        if ccy != "EUR":
            return [f"YAHOO QUOTES {t} IN {ccy} — PRICES ARE TAKEN AS EUROS: USE ITS EUR LISTING (.DE, .F …)"]
        return []

    @staticmethod
    def _result(etag: str, rows: list[dict], trade: dict, fee=None, warnings=()) -> dict:
        at = next(i for i, r in enumerate(rows) if r is trade)
        return {"etag": etag, "id": T.ids(rows)[at], "trade": {k: trade[k] for k in FIELDS},
                "text": T.describe(trade, fee), "warnings": list(warnings)}

    @staticmethod
    def _fee(d: dict):
        try:
            return T.number(d["fee"], "FEE") if T._given(d.get("fee")) and not T._given(d.get("total")) else None
        except T.TradeError:
            return None

    def add(self, etag: str, d: dict) -> dict:
        """One trade, slotted in after the last row dated on or before it."""
        trade = self._make(d)
        warnings = self._quote_warnings(trade, self._load()["rows"])     # outside the lock: it may ask Yahoo
        with self._lock:
            f = self._current(etag)
            if T.duplicate(f["rows"], trade):
                warnings.append("SAME AS A TRADE ALREADY IN YOUR FILE")
            new, _ = T.place(f["rows"], trade)
            fee = self._fee(d)
            out = self._write(f, new, f"ADD {T.describe(trade, fee)}")
            return self._result(out, new, trade, fee, warnings)

    def _index(self, rows: list[dict], rid: str) -> int:
        try:
            return T.ids(rows).index(rid)
        except ValueError:
            raise Missing(rid) from None

    def update(self, etag: str, rid: str, d: dict) -> dict:
        """Replace row `rid` (its extra columns kept): in place when its date is unchanged, else moved to where its
        new date belongs."""
        trade = self._make(d)
        warnings = self._quote_warnings(trade, self._load()["rows"])
        with self._lock:
            f = self._current(etag)
            rows = f["rows"]
            i = self._index(rows, rid)
            trade["extra"] = rows[i].get("extra") or {}
            others = rows[:i] + rows[i + 1:]
            if T.duplicate(others, trade):
                warnings.append("SAME AS A TRADE ALREADY IN YOUR FILE")
            if rows[i]["date"] == trade["date"]:
                new = [*rows[:i], trade, *rows[i + 1:]]
            else:
                new, _ = T.place(others, trade)
            fee = self._fee(d)
            out = self._write(f, new, f"EDIT {T.describe(rows[i])} → {T.describe(trade, fee)}")
            return self._result(out, new, trade, fee, warnings)

    def delete(self, etag: str, rid: str) -> dict:
        with self._lock:
            f = self._current(etag)
            rows = f["rows"]
            i = self._index(rows, rid)
            gone = rows[i]
            out = self._write(f, rows[:i] + rows[i + 1:], f"DELETE {T.describe(gone)}")
            return {"etag": out, "id": rid, "trade": {k: gone[k] for k in FIELDS}, "text": T.describe(gone)}

    def preview(self, text: str, mode: str = "append") -> dict:
        """parse_bulk + review against the file as it is now: nothing is written. {"rows", "error", "notes",
        "delimiter", "decimal", "header", "ok": rows that would be added, "bad": rows that would not}."""
        mode = _mode(mode)
        f = self._load()
        bulk = T.parse_bulk(text, self.today(), isin=self.isin)
        if f["error"] and mode == "append" and not bulk["error"]:
            bulk["error"] = f"YOUR FILE HAS AN ERROR — {f['error']}: FIX IT, OR IMPORT WITH REPLACE, OR UNDO"
        if not bulk["error"]:
            T.review(f["rows"], bulk, mode)
        good = sum(1 for r in bulk["rows"] if r["trade"] and not r["error"])
        return {**bulk, "mode": mode, "ok": good, "bad": len(bulk["rows"]) - good}

    def import_text(self, etag: str, text: str, mode: str = "append") -> dict:
        """The good rows of a paste or file: appended (each by its date) or replacing every trade (the file before
        kept as before-replace-NNNN.csv). Bad rows are never written — {"added", "skipped", "lines": the skipped
        rows' first lines, "header": one was read, "left": the header and the skipped rows, as typed}."""
        mode = _mode(mode)
        with self._lock:
            f = self._current(etag, readable=(mode == "append"))
            bulk = T.parse_bulk(text, self.today(), isin=self.isin)
            if bulk["error"]:
                raise Invalid(bulk["error"])
            base = f["rows"] if mode == "append" else []
            T.review(base, bulk, mode)
            good = [r for r in bulk["rows"] if r["trade"] and not r["error"]]
            if not good:
                raise Invalid("NOTHING TO ADD — EVERY ROW HAS AN ERROR")
            new = T.merge(base, [r["trade"] for r in good], mode)
            n = len(f["rows"]) if not f["error"] else f["lines"]
            what = (f"ADD {len(good)} PASTED TRADE{'S' * (len(good) != 1)}" if mode == "append"
                    else f"REPLACE {n} TRADE{'S' * (n != 1)} WITH {len(good)}")
            out = self._write({**f, "rows": base}, new, what, None if mode == "append" else REPLACE)
        skipped = [r["line"] for r in bulk["rows"] if not (r["trade"] and not r["error"])]
        return {"etag": out, "mode": mode, "added": len(good), "skipped": len(skipped), "lines": skipped,
                "header": bulk["header"], "left": T.left_text(bulk, skipped)}

    def reset(self, etag: str) -> dict:
        """Start fresh: the header alone (your extra columns kept); the file before is pinned as
        before-start-fresh-NNNN.csv. `removed` counts its rows — its data lines when it cannot be read."""
        with self._lock:
            f = self._current(etag, readable=False)
            n = f["lines"]
            what = f"START FRESH ({n} {'TRADE' if not f['error'] else 'LINE'}{'S' * (n != 1)})"
            return {"etag": self._write({**f, "rows": []}, [], what, FRESH), "removed": n}

    def undo(self, etag: str) -> dict:
        """Put back the file as it was before the last write — only while the file is still as that write left it.
        An UNDO is a write too: undoing it brings the change back."""
        with self._lock:
            f = self._current(etag, readable=False)
            rec = self._last()
            if rec is None:
                raise Invalid("NOTHING TO UNDO")
            if rec["after"] != T.etag(f["data"]):
                where = f"; THE FILE BEFORE IT IS input/backups/{rec['backup']}" if rec["backup"] else ""
                raise Invalid(f"THE FILE CHANGED SINCE THE LAST CHANGE HERE — UNDO WOULD LOSE THAT{where}")
            blob = None
            if rec["backup"] is not None:
                src = self.backups / rec["backup"]
                try:
                    blob = src.read_bytes()
                except OSError:
                    raise Invalid(f"THE BACKUP input/backups/{rec['backup']} IS GONE — NOTHING TO UNDO") from None
            what = rec["what"].removeprefix("UNDO: ") if rec["what"].startswith("UNDO: ") else f"UNDO: {rec['what']}"
            out = self._commit(f["data"], blob, what)
            return {"etag": out, "text": f"UNDID: {rec['what']}"}


def _mode(mode: str) -> str:
    if mode not in ("append", "replace"):
        raise Invalid("MODE MUST BE APPEND OR REPLACE")
    return mode
