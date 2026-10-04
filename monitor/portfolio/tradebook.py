"""input/portfolio.csv as TRADES edits it: read it with stable row ids and its version (etag = sha1 of its bytes),
and write it — one trade added, edited or deleted, an import appended or swapped in, or a fresh start.

Every write names the version it was made on and is refused (Conflict) when the file changed meanwhile — an
edit in Excel, another tab. The WHOLE resulting file is checked (no sale beyond the shares held then, read back
through the ledger) before anything is touched; then the old file is copied to input/backups/
(portfolio-YYYYMMDD-HHMMSS.csv, the newest 20 kept) and the new one replaces it atomically, always in the
canonical form (monitor.portfolio.trades: `,`, decimal point, ISO dates, LF) — the backup keeps yours as it was.
"""
from __future__ import annotations

import re
import shutil
import threading
from datetime import date, datetime
from pathlib import Path
from typing import Callable

from monitor.data.files import write_text_atomic
from monitor.portfolio import trades as T
from monitor.portfolio.ledger import CSVError, parse_portfolio_text

KEEP = 20
FIELDS = ("date", "ticker", "action", "shares", "price", "pps")
_BACKUP = re.compile(r"-(\d{8}-\d{6})(?:-(\d+))?\.csv$")


class Conflict(Exception):
    """The file changed since the version the write was made on."""


class Invalid(ValueError):
    """The write would leave a file that is wrong (one line; `errors` lists every problem)."""

    def __init__(self, msg: str, errors: list[str] | None = None):
        super().__init__(msg)
        self.errors = errors or [msg]


class Missing(KeyError):
    """No row with that id (any more)."""

    def __str__(self) -> str:
        return "NO SUCH TRADE — IT WAS CHANGED OR DELETED; RELOADED"


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
        self._lock = threading.Lock()

    # ── reading ────────────────────────────────────────────────────────────────────────────────
    def _load(self) -> tuple[bytes | None, list[dict], str | None]:
        """(the file's bytes | None, its rows, why it cannot be read | None)."""
        try:
            data = self.csv.read_bytes()
        except FileNotFoundError:
            return None, [], None
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            return data, [], f'{self.csv.name}: not UTF-8 text — in Excel use Save As → "CSV UTF-8"'
        try:
            return data, parse_portfolio_text(text, self.csv.name)["transactions"], None
        except CSVError as e:
            return data, [], str(e)

    def read(self) -> dict:
        """{"etag", "rows": [{id, date, ticker, action, shares, price, pps}] in file order, "error" | None,
        "exists", "example": the file is still examples/portfolio.example.csv}."""
        data, rows, error = self._load()
        example = data is not None and self.example is not None and T.same_trades(self.csv, self.example)
        return {"etag": T.etag(data), "rows": [{"id": i, **r} for i, r in zip(T.ids(rows), rows)],
                "error": error, "exists": data is not None, "example": example}

    # ── writing ────────────────────────────────────────────────────────────────────────────────
    def _current(self, etag: str, *, readable: bool = True) -> tuple[bytes | None, list[dict]]:
        data, rows, error = self._load()
        if T.etag(data) != etag:
            raise Conflict()
        if readable and error:
            raise Invalid(f"YOUR FILE HAS AN ERROR — {error}: FIX IT, OR IMPORT A FILE WITH REPLACE, OR START FRESH")
        return data, rows

    def _backup(self, data: bytes) -> None:
        """Copy the old file to backups/<stem>-YYYYMMDD-HHMMSS[-n].csv (n counts writes within one second, never
        reusing a pruned name), then drop all but the newest `keep`."""
        self.backups.mkdir(parents=True, exist_ok=True)

        def order(p: Path) -> tuple[str, int]:
            m = _BACKUP.search(p.name)
            return m.group(1), int(m.group(2) or 1)
        mine = [p for p in self.backups.glob(f"{self.csv.stem}-*.csv") if _BACKUP.search(p.name)]
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        n = 1 + max((k for s, k in map(order, mine) if s == stamp), default=0)
        target = self.backups / (f"{self.csv.stem}-{stamp}.csv" if n == 1 else f"{self.csv.stem}-{stamp}-{n}.csv")
        target.write_bytes(data)
        for old in sorted([*mine, target], key=order)[:-self.keep]:
            old.unlink(missing_ok=True)

    def _write(self, data: bytes | None, old: list[dict], new: list[dict]) -> str:
        """Check `new` as a whole, back up `data`, write `new` canonically; returns the new etag."""
        if problems := T.new_problems(old, new):
            raise Invalid(problems[0], problems)
        text = T.to_csv(new)
        parse_portfolio_text(text, self.csv.name)            # the file the ledger will read: never written unread
        if data is not None:
            self._backup(data)
        write_text_atomic(self.csv, text, newline="\n")
        return T.etag(text.encode("utf-8"))

    def _make(self, d: dict) -> dict:
        try:
            return T.make_trade(d, self.today())
        except T.TradeError as e:
            raise Invalid(str(e)) from None

    def _quote_warnings(self, trade: dict, rows: list[dict]) -> list[str]:
        """One quote for a ticker new to the file: none, or not in euros, is a warning."""
        t = trade["ticker"]
        if self.quote is None or any(r["ticker"] == t for r in rows):
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
        _, peek, _ = self._load()
        warnings = self._quote_warnings(trade, peek)          # outside the lock: it may ask Yahoo
        with self._lock:
            data, rows = self._current(etag)
            if T.duplicate(rows, trade):
                warnings.append("SAME AS A TRADE ALREADY IN YOUR FILE")
            new, _ = T.place(rows, trade)
            return self._result(self._write(data, rows, new), new, trade, self._fee(d), warnings)

    def _index(self, rows: list[dict], rid: str) -> int:
        try:
            return T.ids(rows).index(rid)
        except ValueError:
            raise Missing(rid) from None

    def update(self, etag: str, rid: str, d: dict) -> dict:
        """Replace row `rid`: in place when its date is unchanged, else moved to where its new date belongs."""
        trade = self._make(d)
        _, peek, _ = self._load()
        warnings = self._quote_warnings(trade, peek)
        with self._lock:
            data, rows = self._current(etag)
            i = self._index(rows, rid)
            others = rows[:i] + rows[i + 1:]
            if T.duplicate(others, trade):
                warnings.append("SAME AS A TRADE ALREADY IN YOUR FILE")
            if rows[i]["date"] == trade["date"]:
                new = [*rows[:i], trade, *rows[i + 1:]]
            else:
                new, _ = T.place(others, trade)
            return self._result(self._write(data, rows, new), new, trade, self._fee(d), warnings)

    def delete(self, etag: str, rid: str) -> dict:
        with self._lock:
            data, rows = self._current(etag)
            i = self._index(rows, rid)
            gone = rows[i]
            return {"etag": self._write(data, rows, rows[:i] + rows[i + 1:]), "id": rid,
                    "trade": {k: gone[k] for k in FIELDS}, "text": T.describe(gone)}

    def preview(self, text: str, mode: str = "append") -> dict:
        """parse_bulk + review against the file as it is now: nothing is written. {"rows", "error", "delimiter",
        "decimal", "header", "ok": rows that would be added, "bad": rows that would not}."""
        mode = _mode(mode)
        _, rows, error = self._load()
        bulk = T.parse_bulk(text, self.today(), isin=self.isin)
        if error and mode == "append" and not bulk["error"]:
            bulk["error"] = f"YOUR FILE HAS AN ERROR — {error}: FIX IT, OR IMPORT WITH REPLACE, OR START FRESH"
        if not bulk["error"]:
            T.review(rows, bulk, mode)
        good = sum(1 for r in bulk["rows"] if r["trade"] and not r["error"])
        return {**bulk, "mode": mode, "ok": good, "bad": len(bulk["rows"]) - good}

    def import_text(self, etag: str, text: str, mode: str = "append") -> dict:
        """The good rows of a paste or file: appended (each by its date) or replacing every trade. Bad rows are
        never written — {"added", "skipped", "lines": the skipped rows' line numbers, "header": one was read}."""
        mode = _mode(mode)
        with self._lock:
            data, rows = self._current(etag, readable=(mode == "append"))
            bulk = T.parse_bulk(text, self.today(), isin=self.isin)
            if bulk["error"]:
                raise Invalid(bulk["error"])
            T.review(rows if mode == "append" else [], bulk, mode)
            good = [r for r in bulk["rows"] if r["trade"] and not r["error"]]
            if not good:
                raise Invalid("NOTHING TO ADD — EVERY ROW HAS AN ERROR")
            base = rows if mode == "append" else []
            new = T.merge(base, [r["trade"] for r in good], mode)
            out = self._write(data, base, new)
        skipped = [r["line"] for r in bulk["rows"] if not (r["trade"] and not r["error"])]
        return {"etag": out, "mode": mode, "added": len(good), "skipped": len(skipped), "lines": skipped,
                "header": bulk["header"]}

    def reset(self, etag: str) -> dict:
        """Start fresh: the header alone (the old file in input/backups/)."""
        with self._lock:
            data, rows = self._current(etag, readable=False)
            return {"etag": self._write(data, [], []), "removed": len(rows)}


def _mode(mode: str) -> str:
    if mode not in ("append", "replace"):
        raise Invalid("MODE MUST BE APPEND OR REPLACE")
    return mode
