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

import base64
import json
import logging
import os
import re
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Callable

from monitor.data.files import write_bytes_durable
from monitor.portfolio import trades as T
from monitor.portfolio.ledger import (COLUMNS, DIVIDEND_COLUMNS, CSVError, load_interest, load_paid_dividends,
                                      parse_portfolio_text, read_table)

KEEP = 20              # the ring of ordinary backups (pinned copies are never pruned)
FIELDS = ("date", "ticker", "action", "shares", "price", "pps")
FRESH, REPLACE = "before-start-fresh", "before-replace"
BLOCKED = "CANNOT WRITE {} — OPEN IN EXCEL OR ANOTHER PROGRAM? CLOSE IT AND SAVE AGAIN"
_RING = re.compile(r"\d+\.csv")       # the ring's names after "<stem>-"
_PLAIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\.csv")      # a backup's name in the undo record
log = logging.getLogger("monitor.trades")


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
    def _numbered(self, prefix: str) -> list[tuple[int, Path]]:
        """(number, path) of `<prefix>-<digits>.csv`, by number — never by the name's text, so 10000 follows 9999."""
        pat = re.compile(rf"{re.escape(prefix)}-(\d+)\.csv")
        found = [(int(m.group(1)), p) for p in self.backups.glob(f"{prefix}-*.csv") if (m := pat.fullmatch(p.name))]
        return sorted(found)

    def _ring(self) -> list[tuple[int, Path]]:
        return self._numbered(self.csv.stem)

    def _next(self, prefix: str, width: int) -> Path:
        have = self._numbered(prefix)
        return self.backups / f"{prefix}-{(have[-1][0] if have else 0) + 1:0{width}d}.csv"

    def _copy_of(self, data: bytes) -> Path | None:
        """A file in backups/ holding exactly `data` — pinned ones first."""
        same = [p for p in self.backups.glob("*.csv") if p.stat().st_size == len(data) and p.read_bytes() == data]
        return min(same, key=lambda p: (bool(_RING.fullmatch(p.name.removeprefix(f"{self.csv.stem}-"))), p.name),
                   default=None)

    def _merge_side(self, name: str, columns: tuple[str, ...], have: list[dict], new: list[dict], key, cells,
                    mode: str) -> int:
        """An export's rows that are not trades (dividends.csv, interest.csv): appended once — a row whose key is
        already there is not added again — or, with REPLACE, the export's alone. The file before is kept as
        <name>-<time>.csv in input/backups. Returns how many rows went in."""
        seen, add = ({key(r) for r in have} if mode == "append" else set()), []
        for r in new:
            if key(r) not in seen:
                seen.add(key(r))
                add.append(r)
        path = self.csv.with_name(name)
        if not add and (mode == "append" or not path.exists()):
            return 0
        if path.exists():
            self.backups.mkdir(parents=True, exist_ok=True)
            write_bytes_durable(self.backups / f"{path.stem}-{datetime.now():%Y%m%d-%H%M%S}.csv", path.read_bytes())
        rows = sorted((have if mode == "append" else []) + add, key=key)
        text = ",".join(columns) + "\n" + "".join(",".join(cells(r)) + "\n" for r in rows)
        write_bytes_durable(path, text.encode("utf-8"))
        return len(add)

    def _keep_backup(self, data: bytes, pin: str | None) -> dict:
        """Keep the file about to be replaced, and say where for the undo record. Never two copies of the same bytes
        (an existing one is reused — a ring copy that must now be pinned is renamed, not copied); never a copy of a
        file without trades (empty, a header alone): its bytes go into the undo record itself. The first copy ever
        is portfolio-original.csv; then a pinned before-start-fresh-N / before-replace-N, or the ring's next."""
        if not _data_lines(data.decode("utf-8", "replace")):
            return {"kind": "inline", "b64": base64.b64encode(data).decode("ascii")}
        self.backups.mkdir(parents=True, exist_ok=True)
        found = self._copy_of(data)
        original = self.backups / f"{self.csv.stem}-original.csv"
        ring = found is not None and found.parent == self.backups and bool(_RING.fullmatch(
            found.name.removeprefix(f"{self.csv.stem}-")))
        if found is None and not original.exists():
            write_bytes_durable(original, data)
            return {"kind": "file", "name": original.name}
        if pin and (found is None or ring):
            target = self._next(pin, 4)
            if found is None:
                write_bytes_durable(target, data)
            else:
                os.replace(found, target)                    # promoted: the one copy, now pinned
            return {"kind": "file", "name": target.name}
        if found is None:
            found = self._next(self.csv.stem, 7)
            write_bytes_durable(found, data)
        return {"kind": "file", "name": found.name}

    def _rotate(self, protect: str | None) -> None:
        """After a write went in: the ring down to its newest `keep` — but never the copy the undo record names.
        Pinned copies are never pruned (none duplicates another)."""
        ring = [p for _, p in self._ring()]
        for p in ring[:max(0, len(ring) - self.keep)]:
            if p.name != protect:
                p.unlink(missing_ok=True)

    def _last(self) -> dict | None:
        try:
            rec = json.loads(self.journal.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        ok = isinstance(rec, dict) and {"what", "after", "before", "at"} <= rec.keys() and isinstance(rec["before"], dict)
        return rec if ok else None

    def _bytes(self) -> bytes | None:
        try:
            return self.csv.read_bytes()
        except FileNotFoundError:
            return None

    def _commit(self, data: bytes | None, blob: bytes | None, what: str, pin: str | None = None) -> str:
        """Replace the file by `blob` (None: remove it) after keeping `data` (None: there was no file). The same
        bytes again: nothing is written and the undo record stays. Right before the replace the file is read once
        more — changed since `data` was read: Conflict, nothing written. After it: the undo record, then the ring
        rotated; a failure there is logged, never the write's failure. Returns the new etag."""
        if blob == data:
            return T.etag(data)
        try:
            before = self._keep_backup(data, pin) if data is not None else {"kind": "absent"}
        except OSError:
            raise Blocked(f"CANNOT WRITE {self.csv.parent.name}/backups/ — IS THE FOLDER READ-ONLY OR THE DISK FULL? "
                          "NOTHING CHANGED") from None

        def unchanged() -> None:
            if self._bytes() != data:
                raise Conflict()
        try:
            if blob is None:
                unchanged()
                self._remove()
            else:
                write_bytes_durable(self.csv, blob, check=unchanged)
        except OSError:
            raise Blocked(BLOCKED.format(self.csv.name)) from None
        after = T.etag(blob)
        record = {"what": what, "at": datetime.now().isoformat(timespec="seconds"), "before": before, "after": after}
        try:
            write_bytes_durable(self.journal, json.dumps(record, ensure_ascii=False).encode("utf-8"))
        except OSError:
            log.warning("the undo record could not be written — UNDO is off until the next change", exc_info=True)
        try:
            self._rotate(before.get("name"))
        except OSError:
            log.warning("an old backup could not be rotated out of %s", self.backups, exc_info=True)
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
        kept until you delete it, as before-replace-N.csv). Bad rows are never written — {"added", "skipped", "lines": the skipped
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
            if not good and not (bulk["dividends"] or bulk["interest"]):
                raise Invalid("NOTHING TO ADD — EVERY ROW HAS AN ERROR")
            out = etag
            if good:
                new = T.merge(base, [r["trade"] for r in good], mode)
                n = len(f["rows"]) if not f["error"] else f["lines"]
                what = (f"ADD {len(good)} PASTED TRADE{'S' * (len(good) != 1)}" if mode == "append"
                        else f"REPLACE {n} TRADE{'S' * (n != 1)} WITH {len(good)}")
                out = self._write({**f, "rows": base}, new, what, None if mode == "append" else REPLACE)
            # a broker export also carries dividends and interest: into their own files beside the trades
            # (a row you wrote with a pay date alone — announced — has no amounts: blank cells, kept as written)
            divs = self._merge_side("dividends.csv", DIVIDEND_COLUMNS, load_paid_dividends(self.csv.with_name("dividends.csv")),
                                    bulk["dividends"], lambda d: (d["pay"], d["ticker"], -1.0 if d["net"] is None
                                                                  else round(d["net"], 2)),
                                    lambda d: [d["pay"], d["ticker"], *("" if d[k] is None else f"{d[k]:{f}}" for k, f in
                                               (("shares", "g"), ("gross", ".2f"), ("tax", ".2f"), ("net", ".2f")))], mode)
            paid = self._merge_side("interest.csv", ("Date", "Amount"), load_interest(self.csv.with_name("interest.csv")),
                                    bulk["interest"], lambda i: (i["date"], round(i["eur"], 2)),
                                    lambda i: [i["date"], f"{i['eur']:.2f}"], mode)
        skipped = [r["line"] for r in bulk["rows"] if not (r["trade"] and not r["error"])]
        return {"etag": out, "mode": mode, "added": len(good), "skipped": len(skipped), "lines": skipped,
                "header": bulk["header"], "left": T.left_text(bulk, skipped), "dividends": divs, "interest": paid}

    def reset(self, etag: str) -> dict:
        """Start fresh: the header alone (your extra columns kept); the file before is kept until you delete it (as
        before-start-fresh-N.csv, unless a copy of it is already there). `removed` counts its rows — its data lines
        when it cannot be read."""
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
            before = rec["before"]
            name = before.get("name") if before.get("kind") == "file" else None
            if rec["after"] != T.etag(f["data"]):
                where = f"; THE FILE BEFORE IT IS input/backups/{name}" if name else ""
                raise Invalid(f"THE FILE CHANGED SINCE THE LAST CHANGE HERE — UNDO WOULD LOSE THAT{where}")
            blob = self._before_bytes(before)
            what = rec["what"].removeprefix("UNDO: ") if rec["what"].startswith("UNDO: ") else f"UNDO: {rec['what']}"
            out = self._commit(f["data"], blob, what)
            return {"etag": out, "text": f"UNDID: {rec['what']}"}

    def _before_bytes(self, before: dict) -> bytes | None:
        """The file an undo record says was there before (None: no file). A damaged record — a name that is not a
        plain file in backups/ — or a missing copy: Invalid."""
        kind = before.get("kind")
        if kind == "absent":
            return None
        if kind == "inline":
            try:
                return base64.b64decode(before.get("b64", ""), validate=True)
            except ValueError:
                raise Invalid("THE UNDO RECORD IS DAMAGED — NOTHING TO UNDO") from None
        name = before.get("name")
        if kind != "file" or not isinstance(name, str) or not _PLAIN.fullmatch(name):
            raise Invalid("THE UNDO RECORD IS DAMAGED — NOTHING TO UNDO")
        try:
            return (self.backups / name).read_bytes()
        except OSError:
            raise Invalid(f"THE BACKUP input/backups/{name} IS GONE — NOTHING TO UNDO") from None


def _mode(mode: str) -> str:
    if mode not in ("append", "replace"):
        raise Invalid("MODE MUST BE APPEND OR REPLACE")
    return mode
