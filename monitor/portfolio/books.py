"""Your other portfolios — books: each its own folder input/books/<NAME>/ holding a portfolio.csv in the trades file's
format, with its own dividends.csv, interest.csv and backups/ beside it, exactly as input/ holds yours. BOOK <NAME>
enters a book's trades (monitor.screens.trades); BOOKS shows every book beside yours against the same benchmarks
(monitor.screens.books). A book is created by its first trade, and leaves when its folder is deleted."""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

YOU = "YOU"                                  # your own book, input/portfolio.csv: never another book's name
NAME = re.compile(r"[A-Z][A-Z0-9-]{0,14}")   # a word the command bar reads as a parameter (cmd.js TICKER)


def valid(name: str) -> bool:
    return bool(NAME.fullmatch(name)) and name != YOU


def folder(portfolio_csv: str | Path) -> Path:
    """Where the books live: input/books/, beside your own trades file."""
    return Path(portfolio_csv).parent / "books"


def csv_of(portfolio_csv: str | Path, name: str) -> Path:
    """A book's trades file — YOU: yours."""
    if name == YOU:
        return Path(portfolio_csv)
    if not valid(name):
        raise ValueError(f"not a book name: {name!r}")
    return folder(portfolio_csv) / name / "portfolio.csv"


def names(portfolio_csv: str | Path) -> list[str]:
    """Every book with a trades file, by name."""
    try:
        dirs = list(folder(portfolio_csv).iterdir())
    except OSError:
        return []
    return sorted(d.name for d in dirs if valid(d.name) and (d / "portfolio.csv").is_file())


def fingerprint(portfolio_csv: str | Path) -> str:
    """Hash of every book's name and files (trades, dividends, interest): a book written, added or removed
    changes it — folded into the version of the screens that read the books (Screen.uses_books)."""
    h = hashlib.sha1()
    for n in names(portfolio_csv):
        h.update(n.encode() + b"\0")
        for f in ("portfolio.csv", "interest.csv", "dividends.csv"):
            try:
                h.update((folder(portfolio_csv) / n / f).read_bytes())
            except OSError:
                h.update(b"<absent>")
            h.update(b"\0")
    return h.hexdigest()[:12]
