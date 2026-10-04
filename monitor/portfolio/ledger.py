"""Portfolio parser and P&L calculator for Trade Republic holdings."""

import csv
import io
import logging
import re
from collections import deque
from datetime import date
from pathlib import Path

from monitor import config

log = logging.getLogger(__name__)

DUST = 0.001          # a holding below this many shares counts as fully exited
# Actions that add shares. `bonus` = Trade Republic Saveback/bonus shares: received, not paid for —
# a FIFO lot at its booked `Price` (so P&L % matches the app), but never money invested.
ADDS = ("buy", "bonus")


COLUMNS = ("Date", "Ticker", "Action", "Shares", "Price", "PricePerShare")


class CSVError(ValueError):
    """What in your CSV cannot be read safely: one line naming the file, the row (as Excel numbers it) and
    the column."""


# ── reading the CSVs you write (or Excel saves) ──────────────────────────────────────────────────────
_DMY = re.compile(r"(\d{1,2})([./])(\d{1,2})\2(\d{4})")       # 15.01.2025, 15/01/2025 (day first)
_NUMBER = re.compile(r"[+-]?(\d+([.,]\d*)?|[.,]\d+)")
_GROUPED = re.compile(r"[+-]?[1-9]\d{0,2}[.,]\d{3}")          # 1.234 / 1,234: thousands, or a decimal?


def _read_csv(path: Path, columns: tuple[str, ...]) -> tuple[list[tuple[int, dict, str | None]], bool]:
    """(rows, decimal_comma) of a CSV as you or Excel wrote it: UTF-8 with or without a BOM; fields split
    by `;` when the header line holds more `;` than `,` (Excel in most of Europe — then a decimal comma is
    the norm), else by `,`. Header names and values are stripped; blank rows (Excel's `;;;;;` too) are
    skipped. rows = [(row number as Excel counts it, {column: text}, problem | None)] — problem: the row
    has more fields than the header (e.g. an unquoted decimal comma in a `,` file). CSVError when the
    file is not UTF-8 or a column is missing."""
    name = path.name
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            text = f.read()
    except UnicodeDecodeError:
        raise CSVError(f'{name}: not UTF-8 text — in Excel use Save As → "CSV UTF-8"') from None
    first = next((line for line in text.splitlines() if line.strip()), "")
    sep = ";" if first.count(";") > first.count(",") else ","
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=sep)
    header, rows = None, []
    for cells in reader:
        cells = [c.strip() for c in cells]
        if not any(cells):
            continue
        if header is None:
            header = cells
            missing = [c for c in columns if c not in header]
            if missing:
                raise CSVError(f"{name} row {reader.line_num}: column {missing[0]} missing — the first row "
                               f"must name the columns {sep.join(columns)}")
            continue
        problem = None
        if len(cells) > len(header) and any(cells[len(header):]):
            problem = (f"{len(cells)} fields, expected {len(header)} — "
                       + ("a decimal comma? separate the fields with ; instead, or write 961.00"
                          if sep == "," else "a ; inside a value?"))
        rows.append((reader.line_num, dict(zip(header, cells + [""] * (len(header) - len(cells)))), problem))
    return rows, sep == ";"


def _number(text: str, decimal_comma: bool) -> float:
    """961.00, or 961,00 (a decimal comma: a `;` file, or quoted in a `,` file). A thousands separator is
    rejected, never guessed: 1.234,56 / 1,234.56 / 1 234 / 1'234, and 1.234 in a decimal-comma file
    (1,234 in a decimal-point one)."""
    if not text:
        raise ValueError("empty")
    if any(ch in text for ch in " '’_\u00a0\u202f") or ("," in text and "." in text) \
            or text.count(",") > 1 or text.count(".") > 1 \
            or (_GROUPED.fullmatch(text) and ("," in text) != decimal_comma):
        raise ValueError(f"{text!r} has a thousands separator — write it without one, "
                         f"e.g. {'1234,56' if decimal_comma else '1234.56'}")
    if not _NUMBER.fullmatch(text):
        raise ValueError(f"{text!r} is not a number")
    return float(text.replace(",", "."))


def _date(text: str) -> str:
    """YYYY-MM-DD, DD.MM.YYYY or DD/MM/YYYY (day first, always) -> YYYY-MM-DD."""
    if not text:
        raise ValueError("empty — write the date as YYYY-MM-DD")
    m = _DMY.fullmatch(text)
    try:
        if m:
            return date(int(m.group(4)), int(m.group(3)), int(m.group(1))).isoformat()
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return date.fromisoformat(text).isoformat()
    except ValueError:
        if m and m.group(2) == "/" and int(m.group(3)) > 12:
            raise ValueError(f"{text!r}: dates with / are read day/month/year — write it as YYYY-MM-DD") from None
    raise ValueError(f"{text!r} is not a date — write it as YYYY-MM-DD (or DD.MM.YYYY)")


def _cell(name: str, n: int, row: dict, column: str, read):
    try:
        return read(row[column])
    except ValueError as e:
        raise CSVError(f"{name} row {n}, column {column}: {e}") from None


def _text(value: str) -> str:
    if not value:
        raise ValueError("empty")
    return value


def _lot_cost(lots) -> float:
    return sum(n * c for n, c in lots)


def parse_portfolio(csv_path: str | Path) -> dict:
    """
    Parse trade history CSV into current holdings and realized P&L, the way Trade Republic
    computes them: FIFO lots (a sale consumes the earliest-bought shares first), each costed
    at the buy's exact EUR amount (`Price`, fees included) — never the rounded
    `PricePerShare`. Sales realize their exact `Price`. Rows apply in file order (the CSV is
    chronological). A `bonus` row (see ADDS) builds holdings and lots exactly like a buy.

    The file may be as Excel saves it (see _read_csv): `,` or `;` between fields, a decimal comma in a
    `;` file, dates YYYY-MM-DD, DD.MM.YYYY or DD/MM/YYYY (day first). Anything that cannot be read
    safely raises CSVError naming the file, row and column — the whole file, never a guess.

    Returns:
      holdings: {ticker: {shares, avg_cost, total_invested, first_buy, last_activity}}
                avg_cost = remaining lots' cost / remaining shares
      realized: {ticker: {pnl_eur, shares_sold, proceeds}}  pnl_eur = proceeds - FIFO cost sold
      transactions: list of all rows
    """
    holdings: dict[str, dict] = {}
    realized: dict[str, dict] = {}
    lots: dict[str, deque] = {}            # ticker -> deque of [shares, EUR cost per share]
    transactions = []

    path = Path(csv_path)
    rows, comma = _read_csv(path, COLUMNS)

    def num(text):
        return _number(text, comma)

    for n, row, problem in rows:
        if problem:
            raise CSVError(f"{path.name} row {n}: {problem}")
        date     = _cell(path.name, n, row, "Date", _date)
        ticker   = _cell(path.name, n, row, "Ticker", _text)
        action   = _cell(path.name, n, row, "Action", _text).lower()
        shares   = _cell(path.name, n, row, "Shares", num)
        price    = _cell(path.name, n, row, "Price", num)           # total EUR (exact)
        pps      = _cell(path.name, n, row, "PricePerShare", num)   # EUR per share (rounded; display only)

        transactions.append({
            "date": date, "ticker": ticker, "action": action,
            "shares": shares, "price": price, "pps": pps,
        })

        if action in ADDS:
            if ticker not in holdings:
                holdings[ticker] = {
                    "shares": 0.0, "avg_cost": 0.0,
                    "total_invested": 0.0, "first_buy": date,
                }
                lots[ticker] = deque()
            h = holdings[ticker]
            if shares > 0:
                lots[ticker].append([shares, price / shares])
            h["shares"] += shares
            h["total_invested"] += price
            h["last_activity"] = date
            h["avg_cost"] = _lot_cost(lots[ticker]) / h["shares"] if h["shares"] > 0 else pps

        elif action == "sell":
            if ticker not in realized:
                realized[ticker] = {"pnl_eur": 0.0, "shares_sold": 0.0, "proceeds": 0.0}
            r = realized[ticker]
            held = lots.get(ticker, deque())
            cost_sold, left = 0.0, shares
            while left > 1e-12 and held:
                lot = held[0]
                take = min(left, lot[0])
                cost_sold += take * lot[1]
                lot[0] -= take
                left -= take
                if lot[0] <= 1e-12:
                    held.popleft()
            if left > 1e-12 and shares > 0:     # sold more than the ledger holds: no basis,
                cost_sold += price * left / shares   # so no P&L on the excess
            r["pnl_eur"]     += price - cost_sold
            r["shares_sold"] += shares
            r["proceeds"]    += price

            if ticker in holdings:
                h = holdings[ticker]
                h["shares"] -= shares
                h["last_activity"] = date
                if h["shares"] <= DUST:
                    del holdings[ticker]        # fully exited
                    lots.pop(ticker, None)
                else:
                    h["avg_cost"] = _lot_cost(lots[ticker]) / h["shares"]

    return {
        "holdings": holdings,
        "realized": realized,
        "transactions": transactions,
    }


def _signed_shares(t: dict) -> float:
    return t["shares"] if t["action"] in ADDS else -t["shares"] if t["action"] == "sell" else 0.0


def dividend_cash(transactions: list[dict], dividends: dict[str, list], today=None) -> list[dict]:
    """Cash dividends you were entitled to: on each ex-date, the shares you held after every trade
    dated strictly before it (a buy ON the ex-date misses it, a sale ON the ex-date keeps it) x the
    per-share amount. `dividends` is cached_dividends' {ticker: [[ex_date_iso, amount], ...]}.
    Ex-dates after `today` (default: now) are not yet cash and are skipped.
    `gross` = shares x per-share; `eur` = what lands in the account, after config.DIVIDEND_TAX.
    Returns [{date, ticker, shares, per_share, gross, eur}] sorted by date, then ticker."""
    horizon = (today or date.today()).isoformat()
    net = 1.0 - config.DIVIDEND_TAX
    out = []
    for tk, rows in dividends.items():
        trades = [t for t in transactions if t["ticker"] == tk]
        for ex, amount in rows:
            if ex > horizon:
                continue
            held = sum(_signed_shares(t) for t in trades if t["date"] < ex)
            if held > DUST and amount > 0:
                gross = held * float(amount)
                out.append({"date": ex, "ticker": tk, "shares": held, "per_share": float(amount),
                            "gross": gross, "eur": gross * net})
    return sorted(out, key=lambda d: (d["date"], d["ticker"]))


def load_interest(path: str | Path) -> list[dict]:
    """Interest Trade Republic paid on uninvested cash: `interest.csv` with columns Date,Amount (EUR
    received). Reported in ACCOUNTING only — never part of ROI, XIRR or YTD. Read like the portfolio
    (_read_csv: a BOM, `;` and a decimal comma, DD.MM.YYYY dates are fine; a decimal comma in a `,` file
    works when quoted: "3,95"). A missing file means no interest; a malformed row — bad date or amount,
    a thousands separator, the wrong number of fields (an unquoted 3,95) — is skipped with a warning
    naming the row and column, never misread; a file that cannot be read at all is one warning and no
    interest. Returns [{date, eur}] sorted by date."""
    path = Path(path)
    if not path.exists():
        return []
    try:
        rows, comma = _read_csv(path, ("Date", "Amount"))
    except CSVError as e:
        log.warning("interest file skipped: %s", e)
        return []
    out = []
    for n, row, problem in rows:
        try:
            if problem:
                raise CSVError(f"{path.name} row {n}: {problem}")
            d = _cell(path.name, n, row, "Date", _date)
            eur = _cell(path.name, n, row, "Amount", lambda text: _number(text, comma))
        except CSVError as e:
            log.warning("interest row skipped: %s", e)
            continue
        out.append({"date": d, "eur": eur})
    return sorted(out, key=lambda r: r["date"])


def compute_portfolio_summary(portfolio: dict, current_prices: dict) -> dict:
    """
    Compute full P&L summary.

    Returns:
      positions: list of position dicts (sorted by value desc)
      totals: {total_invested, current_value, unrealized_pnl, unrealized_pct,
               realized_pnl, total_pnl}
    """
    holdings = portfolio["holdings"]
    realized = portfolio["realized"]

    positions = []
    total_invested   = 0.0
    current_value    = 0.0
    unrealized_pnl   = 0.0

    for ticker, h in holdings.items():
        shares    = h["shares"]
        avg_cost  = h["avg_cost"]
        cur_price = current_prices.get(ticker)

        if cur_price:
            pos_value   = shares * cur_price
            pos_pnl     = (cur_price - avg_cost) * shares
            pos_pnl_pct = (cur_price / avg_cost - 1) * 100 if avg_cost > 0 else 0.0
        else:
            pos_value   = shares * avg_cost  # fallback: cost basis
            pos_pnl     = 0.0
            pos_pnl_pct = 0.0

        positions.append({
            "ticker":       ticker,
            "shares":       round(shares, 6),
            "avg_cost":     round(avg_cost, 4),
            "current_price": cur_price,
            "position_value": round(pos_value, 2),
            "cost_basis":   round(shares * avg_cost, 2),
            "unrealized_pnl": round(pos_pnl, 2),
            "unrealized_pct": round(pos_pnl_pct, 2),
            "first_buy":    h.get("first_buy", ""),
            "last_activity": h.get("last_activity", ""),
        })

        total_invested += shares * avg_cost
        current_value  += pos_value
        unrealized_pnl += pos_pnl

    # Realized P&L across all closed positions
    total_realized = sum(r["pnl_eur"] for r in realized.values())

    positions.sort(key=lambda x: -x["position_value"])

    return {
        "positions": positions,
        "totals": {
            "total_invested":   round(total_invested, 2),
            "current_value":    round(current_value, 2),
            "unrealized_pnl":   round(unrealized_pnl, 2),
            "unrealized_pct":   round((current_value / total_invested - 1) * 100, 2) if total_invested > 0 else 0,
            "realized_pnl":     round(total_realized, 2),
            "total_pnl":        round(unrealized_pnl + total_realized, 2),
        },
        "realized_detail": realized,
    }
