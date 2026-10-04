"""The trades you enter in the terminal (TRADES) — the arithmetic, the file format and the checks. Pure: no I/O
beyond reading a file to compare it (same_trades / has_trades); monitor.portfolio.tradebook writes.

A trade is a row of input/portfolio.csv as monitor.portfolio.ledger reads it: {date (ISO), ticker, action
(buy | sell | bonus), shares, price, pps}. `price` is the TOTAL in EUR — what left or reached your account,
fees included; `pps` (PricePerShare) is display only. Tickers are written in capitals and compared without case.

Fees are never added for you. Give the total you paid (fees in it) and the price per share is total / shares;
give the price per share and the total is shares x price per share exactly — plus a fee on a buy, less it on a
sell, only when you enter one (a bonus has none). A total given (or stored) is kept to the last digit; only a
computed one is rounded, to the cent. A written file is always the canonical form: `,` between fields (csv-quoted
where a value needs it), a decimal point, ISO dates, `\\n` line ends, your own extra columns after the six
(the backup keeps whatever the file was before).

parse_bulk() reads what you paste or import — a header optional, its column names in English, German or
Italian; tab, `;` or `,` between fields — and review() checks it against your file.
"""
from __future__ import annotations

import csv
import hashlib
import io
import math
import re
import unicodedata
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Callable

from monitor.portfolio.ledger import COLUMNS, DUST, CSVError, _date, _number, parse_portfolio

ACTIONS = ("buy", "sell", "bonus")
HEADER = ",".join(COLUMNS)
MISMATCH = 0.01          # price per share x shares vs a given total: flagged beyond 1 %
LIMIT = 1e9              # shares, prices, totals and fees above this are typing slips, never trades


class TradeError(ValueError):
    """One line saying what is wrong with a trade, as the terminal shows it."""


def _loud(msg: str) -> str:
    """Upper case outside quoted values: "SHARES: 'x' IS NOT A NUMBER"."""
    return "".join(part if i % 2 else part.upper() for i, part in enumerate(re.split(r"('[^']*')", msg)))


# ── numbers and dates as you type them ────────────────────────────────────────────────────────────
def _round(x: float, places: int) -> float:
    """Half up on the number as written (3 x 0.335 = 1.005 -> 1.01), the way the terminal's preview rounds."""
    return float(Decimal(repr(float(x))).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def money(x: float) -> float:
    return _round(x, 2)


def per_share(x: float) -> float:
    return _round(x, 4)


def number(value, label: str, decimal_comma: bool = False) -> float:
    """A form field or a command token: a number as is, or text with a decimal point or a decimal comma —
    never a thousands separator (ledger._number: 1,234 / 1.234,56 / 1 234 are refused, never guessed)."""
    if isinstance(value, bool):
        raise TradeError(f"{label} MUST BE A NUMBER")
    if isinstance(value, (int, float)):
        v = float(value)
    else:
        try:
            v = _number(str(value).strip(), decimal_comma)
        except ValueError as e:
            raise TradeError(_loud(f"{label}: {e}")) from None
    if not math.isfinite(v):
        raise TradeError(f"{label} IS NOT A NUMBER")
    if abs(v) > LIMIT:
        raise TradeError(f"{label} IS TOO LARGE (OVER 1,000,000,000)")
    return v


def _given(value) -> bool:
    return value is not None and not (isinstance(value, str) and not value.strip())


def trade_date(text, today: date) -> str:
    """YYYY-MM-DD, DD.MM.YYYY or DD/MM/YYYY (day first) -> ISO; empty = today; never after today."""
    if not _given(text):
        return today.isoformat()
    try:
        iso = _date(str(text).strip())
    except ValueError as e:
        raise TradeError(_loud(f"date: {e}")) from None
    if iso > today.isoformat():
        raise TradeError(f"DATE {iso} IS IN THE FUTURE")
    return iso


def _action(word) -> str:
    a = str(word or "").strip().lower()
    if a not in ACTIONS:
        raise TradeError("ACTION MUST BE BUY, SELL OR BONUS")
    return a


_TICKER = re.compile(r"[A-Z0-9.\-=^]+")


def _ticker(text) -> str:
    """A new trade's ticker: invisible characters (zero-width spaces a copy from a web page brings) dropped,
    capitals, and only what Yahoo tickers are made of — letters, digits, . - = ^."""
    t = "".join(c for c in unicodedata.normalize("NFKC", str(text or "")) if unicodedata.category(c) != "Cf")
    t = t.strip().upper()
    if not t:
        raise TradeError("TICKER MISSING")
    if not _TICKER.fullmatch(t):
        raise TradeError(f"TICKER '{t}': ONLY LETTERS, DIGITS AND . - = ^")
    return t


# ── price per share <-> total ────────────────────────────────────────────────────────────────────
def compute(action: str, shares: float, *, pps: float | None = None, total: float | None = None,
            fee: float | None = None) -> tuple[float, float]:
    """(total, price per share) as stored. A total given wins, kept exactly as given: price per share = total /
    shares (a fee is already in it). Else total = shares x price per share, + fee on a buy, - fee on a sell (a
    bonus has none), rounded to the cent. Prices per share to 4 decimals."""
    action = _action(action)
    if shares is None or not shares > 0:
        raise TradeError("SHARES MUST BE > 0")
    if fee is not None and fee < 0:
        raise TradeError("FEE MUST BE 0 OR MORE")
    if total is not None:
        if not total > 0:
            raise TradeError("TOTAL MUST BE > 0")
        return float(total), per_share(total / shares)
    if pps is None:
        raise TradeError("PRICE PER SHARE OR TOTAL REQUIRED")
    if not pps > 0:
        raise TradeError("PRICE PER SHARE MUST BE > 0")
    gross = shares * pps
    fee = fee or 0.0
    out = money(gross + fee if action == "buy" else gross - fee if action == "sell" else gross)
    if out <= 0:
        raise TradeError(f"THE FEE LEAVES NOTHING: TOTAL €{out:.2f}")
    return out, per_share(pps)


def make_trade(d: dict, today: date) -> dict:
    """A trade from the form, a command or the API: {ticker, action, shares, pps | total, fee?, date?}, numbers
    as numbers or as typed. Both pps and total given: the total wins and pps follows from it — unless
    `keep_pps` (an edit that left the price alone keeps the stored price per share)."""
    ticker, action = _ticker(d.get("ticker")), _action(d.get("action"))
    shares = number(d.get("shares"), "SHARES") if _given(d.get("shares")) else None
    pps = number(d.get("pps"), "PRICE PER SHARE") if _given(d.get("pps")) else None
    total = number(d.get("total"), "TOTAL") if _given(d.get("total")) else None
    fee = number(d.get("fee"), "FEE") if _given(d.get("fee")) else None
    when = trade_date(d.get("date"), today)
    price, unit = compute(action, shares, pps=pps, total=total, fee=fee)
    if d.get("keep_pps") and total is not None and pps is not None and pps > 0:
        unit = per_share(pps)
    return {"date": when, "ticker": ticker, "action": action, "shares": float(shares), "price": price, "pps": unit}


def _eur(x: float) -> str:
    s = fmt_money(abs(x))
    whole, _, frac = s.partition(".")
    return f"€{int(whole):,}.{frac}"


def describe(t: dict, fee: float | None = None) -> str:
    """What will be stored, in one line: BUY 4 SAP.DE · €240.00/sh = €960.00 (+ / − €1.00 fee when one was
    entered)."""
    sign = "+" if t["action"] == "buy" else "−"
    extra = f" {sign} {_eur(fee)} fee" if fee and t["action"] in ("buy", "sell") else ""
    return f"{t['action'].upper()} {fmt_qty(t['shares'])} {t['ticker']} · {_eur(t['pps'])}/sh{extra} = {_eur(t['price'])}"


# ── the file ─────────────────────────────────────────────────────────────────────────────────────
def _plain(x: float) -> str:
    s = format(Decimal(repr(float(x))), "f")
    return s.rstrip("0").rstrip(".") if "." in s else s


def fmt_qty(x: float) -> str:
    """Shares as written: 4, 0.15, 0.0000001 — every digit kept, no exponent."""
    return _plain(x)


def fmt_money(x: float) -> str:
    """A euro amount: at least two decimals, every further one kept (961.00, 33.3333)."""
    whole, _, frac = _plain(x).partition(".")
    return f"{whole}.{frac.ljust(2, '0')}"


def _cells(t: dict) -> list[str]:
    return [t["date"], t["ticker"].upper(), t["action"], fmt_qty(t["shares"]), fmt_money(t["price"]), fmt_money(t["pps"])]


def canonical(t: dict) -> str:
    """A row's six fields as one line — what its id is made of (the ticker in capitals: case never matters)."""
    return ",".join(_cells(t))


def to_csv(rows: list[dict], extra: list[str] | tuple = ()) -> str:
    """The file, canonical: header, one line per trade in the given order, `\\n` line ends, csv-quoted where a
    value holds a `,` or a quote. `extra`: your own further columns, after the six — each row's from its "extra"
    ({column: text}; blank for a new row)."""
    buf = io.StringIO()
    out = csv.writer(buf, lineterminator="\n")
    out.writerow([*COLUMNS, *extra])
    for r in rows:
        mine = r.get("extra") or {}
        out.writerow([*_cells(r), *(mine.get(c, "") for c in extra)])
    return buf.getvalue()


def ids(rows: list[dict]) -> list[str]:
    """A stable id per row: its canonical line and how many identical lines precede it — the same however the
    file was saved, and unchanged by edits to other rows."""
    seen: dict[str, int] = {}
    out = []
    for r in rows:
        line = canonical(r)
        n = seen[line] = seen.get(line, -1) + 1
        out.append(hashlib.sha1(f"{line}#{n}".encode("utf-8")).hexdigest()[:12])
    return out


def etag(data: bytes | None) -> str:
    """The file's version: sha1 of its bytes ("absent" when there is no file)."""
    return "absent" if data is None else hashlib.sha1(data).hexdigest()


# ── checks on the whole resulting file ───────────────────────────────────────────────────────────
def oversold(rows: list[dict]) -> dict[int, float]:
    """{row index: shares held just before it} for every sale of more than was held then (file order, as the
    ledger applies it; a sale within DUST of the holding is fine)."""
    held: dict[str, float] = {}
    out = {}
    for i, r in enumerate(rows):
        t = r["ticker"].upper()
        if r["action"] in ("buy", "bonus"):
            held[t] = held.get(t, 0.0) + r["shares"]
        elif r["action"] == "sell":
            have = held.get(t, 0.0)
            if r["shares"] > have + DUST:
                out[i] = max(have, 0.0)
            held[t] = max(have - r["shares"], 0.0)
    return out


def _short(r: dict, held: float) -> str:
    return f"SELL {fmt_qty(r['shares'])} {r['ticker']} ON {r['date']}: ONLY {fmt_qty(_round(held, 6))} HELD THEN"


def new_problems(old: list[dict], new: list[dict]) -> list[str]:
    """What the change from `old` to `new` breaks: each sale beyond the shares held then that `old` did not
    already have (a problem your file had before is not this change's)."""
    before = {k for i, k in enumerate(ids(old)) if i in oversold(old)}
    keys = ids(new)
    return [_short(new[i], held) for i, held in oversold(new).items() if keys[i] not in before]


def place(rows: list[dict], trade: dict) -> tuple[list[dict], int]:
    """`rows` with `trade` slotted in after the last row dated on or before it (your order otherwise kept)."""
    at = max((i + 1 for i, r in enumerate(rows) if r["date"] <= trade["date"]), default=0)
    return [*rows[:at], trade, *rows[at:]], at


def _key(t: dict) -> tuple:
    """What makes two rows the same trade: date, ticker, action, shares, total — not the display price."""
    return t["date"], t["ticker"].upper(), t["action"], t["shares"], t["price"]


def duplicate(rows: list[dict], trade: dict) -> dict | None:
    """The row of `rows` that is the same trade (date, ticker, action, shares, total — not the display price)."""
    k = _key(trade)
    return next((r for r in rows if _key(r) == k), None)


def same_trades(a: Path, b: Path) -> bool:
    """The same trades, however the file was saved (line endings, a BOM, Excel's `;` and decimal comma)."""
    try:
        return Path(a).read_bytes() == Path(b).read_bytes() or \
            parse_portfolio(a)["transactions"] == parse_portfolio(b)["transactions"]
    except (OSError, ValueError):            # unreadable: not the same (whoever reads it says why)
        return False


_ERRORS: dict[tuple, str | None] = {}


def book_error(path: Path) -> str | None:
    """Why the file cannot be read — the ledger's one line — else None (no file: None). Cached per (path, mtime,
    size): the screens ask on every request."""
    p = Path(path)
    try:
        st = p.stat()
    except OSError:
        return None
    key = (str(p), st.st_mtime_ns, st.st_size)
    if key not in _ERRORS:
        if len(_ERRORS) > 32:
            _ERRORS.clear()
        try:
            parse_portfolio(p)
            _ERRORS[key] = None
        except (CSVError, OSError) as e:
            _ERRORS[key] = str(e)
    return _ERRORS[key]


def has_trades(path: Path) -> bool:
    """The file holds a row below its header (a blank one — Excel's `;;;;;` too — is none). Cheap: no parse."""
    try:
        text = Path(path).read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return False
    lines = [ln for ln in text.splitlines() if ln.strip(" \t\ufeff,;\"")]
    return len(lines) > 1


# ── paste / import ───────────────────────────────────────────────────────────────────────────────
def _fold(s: str) -> str:
    """Accents and case folded, everything but letters and digits dropped, (parentheses) too: 'Kurs (EUR)' -> 'kurs'."""
    s = re.sub(r"\([^)]*\)", "", str(s))
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", s)


_COLS = {
    "date": "date datum data tradedate transactiondate executiondate bookingdate buchungsdatum buchungstag "
            "handelstag ausfuhrungsdatum dataoperazione dataesecuzione datetime dateandtime",
    "ticker": "ticker symbol tkr tickersymbol yahooticker yahoo",
    "isin": "isin",
    "action": "action type side transactiontype transaction typ art transaktion buysell tipo operazione "
              "tipooperazione direction",
    "shares": "shares share quantity qty anzahl units stuck stueck stuckzahl quantita pieces menge nominal "
              "numberofshares",
    "pps": "pricepershare pps kurs price prezzo unitprice shareprice preis prezzounitario sharepriceeur "
           "ausfuhrungskurs executionprice",
    "total": "total amount betrag importo value netamount totalamount gesamt gesamtbetrag summe wert "
             "controvalore totale net netvalue",
    "fee": "fee fees commission commissions gebuhr gebuehr gebuhren gebuehren commissione commissioni "
           "transactionfee orderfee kosten",
}
_FIELD = {w: f for f, words in _COLS.items() for w in words.split()}
# a column named so wins over an earlier one naming the same field loosely: Side (Sell) over Type (Limit)
_STRONG = {"action": 0, "side": 0, "buysell": 0, "direction": 0, "transactiontype": 1, "tipooperazione": 1}
_ACTION_WORDS = {
    "buy": "buy b bought purchase kauf kaufen acquisto acquista compra achat plan pac sparplan savingsplan "
           "sparplanausfuhrung savingsplanexecution piano pianodiaccumulo",
    "sell": "sell s sold sale verkauf verkaufen vendita vendi vente",
    "bonus": "bonus saveback",
}
_ACTION = {w: a for a, words in _ACTION_WORDS.items() for w in words.split()}
_ISIN = re.compile(r"[A-Z]{2}[A-Z0-9]{9}\d")
_STAMP = re.compile(r"(\d{4}-\d{2}-\d{2}|\d{1,2}[./]\d{1,2}[./]\d{4})(?:[T ](.*))?")
_ZONED = re.compile(r"(Z|[+-]\d{2}:?\d{2})$")
_SLASH = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")
_DOTTED = re.compile(r"[+-]?\d{1,3}\.\d{3}")
_CCY = re.compile(r"^\s*(?:€|EUR)\s*|\s*(?:€|EUR)\s*$", re.IGNORECASE)
_BREAK = re.compile(r"\r\n|\r|\n")          # the line breaks the csv reader splits on — not str.splitlines()'
LABEL = {"date": "DATE", "ticker": "TICKER", "isin": "ISIN", "action": "ACTION", "shares": "SHARES",
         "pps": "PRICE PER SHARE", "total": "TOTAL", "fee": "FEE"}
HEADLESS = "WRITE DATE,TICKER,ACTION,SHARES,TOTAL[,PRICEPERSHARE] OR …,SHARES,@PRICEPERSHARE"
OWN = ("date", "ticker", "action", "shares", "price")
OWN_NOTE = "PRICE READ AS THE TOTAL PAID (THIS TERMINAL'S OWN COLUMNS) — NAME IT PRICE PER SHARE IF IT IS ONE"


def _is_date(cell: str) -> bool:
    m = _STAMP.fullmatch(cell.strip())
    if not m:
        return False
    try:
        _date(m.group(1))
        return True
    except ValueError:
        return False


def _bulk_date(cell: str, today: date) -> str:
    """A pasted date: YYYY-MM-DD / DD.MM.YYYY / DD/MM/YYYY, a time after it ignored — but a UTC or offset
    timestamp (2025-01-15T23:30:00Z, a broker's export) is the day it was where you are."""
    c = cell.strip()
    m = _STAMP.fullmatch(c)
    if m and m.group(2) and "-" in m.group(1) and _ZONED.search(m.group(2)):
        try:
            when = datetime.fromisoformat(c.replace(" ", "T", 1).removesuffix("Z") + ("+00:00" if c.endswith("Z") else ""))
            return trade_date(when.astimezone().date().isoformat(), today)
        except ValueError:
            pass
    return trade_date(m.group(1) if m else c, today)


def _mapping(header: list[str]) -> tuple[dict[str, int], dict[str, str], bool, list[str]]:
    """({field: column index}, {field: the header's own name}, whether Price is the total, notes). The first
    column naming a field wins. `Price` is the total in this terminal's own format: a header that also has
    PricePerShare — or exactly its first five, Date,Ticker,Action,Shares,Price, and no further known column (a
    note says so)."""
    folded = [_fold(h) for h in header]
    project = "price" in folded and "pricepershare" in folded
    own = not project and tuple(folded[:5]) == OWN and not any(_FIELD.get(f) for f in folded[5:])
    cols, names, rank = {}, {}, {}
    for i, f in enumerate(folded):
        field = ("total" if f == "price" else "pps" if f == "pricepershare" else None) if project or own else None
        field = field or _FIELD.get(f)
        r = _STRONG.get(f, 9)
        if field and (field not in cols or r < rank[field]):
            cols[field], names[field], rank[field] = i, header[i].strip().upper(), r
    return cols, names, project, [OWN_NOTE] if own else []


def _missing(cols: dict) -> list[str]:
    need = [("date", "DATE"), ("action", "ACTION"), ("shares", "SHARES")]
    out = [label for f, label in need if f not in cols]
    if "ticker" not in cols and "isin" not in cols:
        out.insert(1, "TICKER")
    if "pps" not in cols and "total" not in cols:
        out.append("PRICE")
    return out


def _cell_number(text: str, label: str, comma: bool) -> float | None:
    """A pasted amount: blank -> None; a currency mark around it and a sign (brokers write money out as
    negative) are dropped — the action says the direction."""
    t = _CCY.sub("", text or "").strip()
    if not t:
        return None
    t = t[1:].strip() if t[0] in "+-−" else t
    return abs(number(t, label, comma))


MON = ("JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST", "SEPTEMBER", "OCTOBER",
       "NOVEMBER", "DECEMBER")


def _slash_dates(dates) -> str | None:
    """Why a paste's `/` dates cannot be read, or None. Day first unless one reads only month first (03/15/2025:
    then 01/02/2025 may be 2 January too); and when every one could be either (01/02/2025), one that can only be
    day first (15/02/2025) must say so — nothing is guessed."""
    seen = [m for d in dates if (m := _SLASH.match(d.strip()))]
    for m in seen:
        if int(m.group(1)) <= 12 < int(m.group(2)) <= 31:
            return f"DATES LOOK LIKE MONTH/DAY ({m.group(0)}) — WRITE THEM AS YYYY-MM-DD OR DD.MM.YYYY"
    if seen and not any(int(m.group(1)) > 12 for m in seen):
        m = next((m for m in seen if m.group(1) != m.group(2)), None)
        if m:
            d, mo = int(m.group(1)), int(m.group(2))
            return (f"/ DATES COULD BE MONTH/DAY ({m.group(0)}: {d} {MON[mo - 1]} OR {mo} {MON[d - 1]}?) — WRITE THEM "
                    f"AS YYYY-MM-DD OR DD.MM.YYYY")
    return None


def parse_bulk(text: str, today: date, *, isin: Callable[[str], str | None] | None = None) -> dict:
    """Read pasted rows or an imported CSV (as text). One record per row, the first row a header when no cell
    of it is a date and one names a column (English, German or Italian synonyms, case and accents ignored).
    Fields split by a tab, `;` or `,` (whichever the first row holds: a tab, else the more of `;` and `,`); a
    decimal comma in a `;` file, either style in a tab paste (a comma in any amount makes it the decimal
    mark; 1.000 / 12.500 without one is flagged). Without a header a row is
    Date,Ticker,Action,Shares,Total[,PricePerShare] — or Date,Ticker,Action,Shares,@PricePerShare.
    `isin(code) -> ticker | None` resolves an ISIN. `/` dates are day first, but a paste is refused whole when
    one reads only month first (03/15/2025) or every one could be either (01/02/2025) — never guessed.

    Returns {"rows": [{"line", "end", "text", "trade" | None, "error" | None, "warnings": [...]}], "error" |
    None, "notes": [...], "delimiter", "decimal": "point" | "comma", "header": bool, "head": the header's text}.
    `line` / `end` are the record's first / last line as the csv reader counts them (a quoted value may span
    lines). A bad row says why and never stops the rest."""
    text = (text or "").removeprefix("﻿")
    lines = _BREAK.split(text)
    first = next((ln for ln in lines if ln.strip(" \t,;\"")), None)
    out = {"rows": [], "error": None, "notes": [], "delimiter": ",", "decimal": "point", "header": False, "head": ""}
    if first is None:
        return {**out, "error": "NOTHING TO READ — PASTE ROWS OR PICK A CSV FILE"}
    sep = "\t" if "\t" in first else ";" if first.count(";") > first.count(",") else ","
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=sep)
    records, done = [], 0
    for cells in reader:
        start, done = done + 1, reader.line_num
        cells = [c.strip() for c in cells]
        if any(cells):
            records.append((start, done, cells))
    _, head_end, head = records[0]
    header = not any(_is_date(c) for c in head) and any(_fold(c) in _FIELD or _fold(c) == "pricepershare" for c in head)
    cols, names, project, notes = _mapping(head) if header else ({}, {}, False, [])
    out.update(delimiter=sep, header=header, notes=notes, head="\n".join(lines[:head_end]).strip("\n") if header else "")
    if header and (missing := _missing(cols)):
        return {**out, "error": f"NO {', '.join(missing)} COLUMN — NAME THEM DATE, TICKER, ACTION, SHARES AND PRICE "
                                f"(PER SHARE) OR TOTAL"}
    body = records[1:] if header else records
    at = cols.get("date", 0)
    if why := _slash_dates(cells[at] for _, _, cells in body if at < len(cells)):
        return {**out, "error": why}
    amounts = [cols[f] for f in ("shares", "pps", "total", "fee") if f in cols] if header else [3, 4, 5]
    if sep == ";":
        comma = True
    elif sep == "\t":
        comma = any("," in cells[i] for _, _, cells in body for i in amounts if i < len(cells))
    else:
        comma = False
    rows = []
    for start, end, cells in body:
        rec = {"line": start, "end": end, "text": "\n".join(lines[start - 1:end]), "trade": None, "error": None,
               "warnings": []}
        try:
            if header:
                if len(cells) > len(head) and any(cells[len(head):]):
                    raise TradeError(_too_many(len(cells), len(head), sep))
                rec["trade"], rec["warnings"] = _bulk_row(cells, cols, names, project, comma, today, isin)
            else:
                rec["trade"], rec["warnings"] = _headless_row(cells, sep, comma, today, isin)
        except TradeError as e:
            rec["error"] = str(e)
        if sep == "\t" and not comma and rec["trade"]:
            dotted = [cells[i].lstrip("@") for i in amounts if i < len(cells) and _DOTTED.fullmatch(cells[i].lstrip("@"))]
            if dotted:
                rec["warnings"].append(", ".join(f"'{d}'" for d in dotted) + " READ AS DECIMALS — IF THE DOT SEPARATES "
                                       "THOUSANDS, WRITE THE NUMBERS WITHOUT IT")
        rows.append(rec)
    return {**out, "rows": rows, "decimal": "comma" if comma else "point"}


def left_text(bulk: dict, lines: list[int]) -> str:
    """What stays in the paste box once the good rows went in: the header (when one was read) and the records
    starting on `lines`, each whole and as typed — nothing when every row went in."""
    keep = [r["text"] for r in bulk["rows"] if r["line"] in set(lines)]
    if not keep:
        return ""
    return "".join(f"{t}\n" for t in ([bulk["head"]] if bulk.get("head") else []) + keep)


def _too_many(n: int, expected: int, sep: str) -> str:
    hint = {",": "A DECIMAL COMMA? SEPARATE THE FIELDS WITH ; INSTEAD, OR WRITE 961.00",
            ";": "A ; INSIDE A VALUE?", "\t": "A TAB INSIDE A VALUE?"}[sep]
    return f"{n} FIELDS, EXPECTED {expected} — {hint}"


def _headless_row(cells, sep, comma, today, isin) -> tuple[dict, list[str]]:
    """Date,Ticker,Action,Shares,Total[,PricePerShare] (this project's order: both kept as written) or
    Date,Ticker,Action,Shares,@PricePerShare."""
    if len(cells) < 5:
        raise TradeError(f"{len(cells)} FIELDS — {HEADLESS}")
    at = cells[4].startswith("@")
    if len(cells) > (5 if at else 6):
        raise TradeError(_too_many(len(cells), 5 if at else 6, sep) if not at else f"{len(cells)} FIELDS — {HEADLESS}")
    cols = {"date": 0, "ticker": 1, "action": 2, "shares": 3, **({"pps": 4} if at else {"total": 4, "pps": 5})}
    cells = [*cells[:4], cells[4][1:].strip() if at else cells[4], *cells[5:]]
    return _bulk_row(cells, cols, {}, True, comma, today, isin)


def _bulk_row(cells, cols, names, keep_pps, comma, today, isin) -> tuple[dict, list[str]]:
    def get(f):
        return cells[cols[f]] if f in cols and cols[f] < len(cells) else ""

    def amount(f):
        return _cell_number(get(f), names.get(f, LABEL[f]), comma)

    if not get("date"):
        raise TradeError("DATE MISSING")
    when = _bulk_date(get("date"), today)
    word = get("action")
    action = _ACTION.get(_fold(word))
    if action is None:
        raise TradeError(f"UNKNOWN ACTION '{word.upper()}' — BUY, SELL OR BONUS" if word else "ACTION MISSING")
    tk = get("ticker").upper()
    code = tk or get("isin").upper()
    if code and _ISIN.fullmatch(code):
        hit = isin(code) if isin else None
        if not hit:
            raise TradeError(f"ISIN {code} IS NOT IN THE LOOKUP — WRITE ITS YAHOO TICKER INSTEAD")
        tk = hit
    ticker = _ticker(tk)
    shares, total, pps, fee = amount("shares"), amount("total"), amount("pps"), amount("fee")
    if shares is None:
        raise TradeError("SHARES MISSING")
    warnings = []
    fee_in = False                                   # a fee column next to an amount: where is the fee?
    if total is not None and fee:
        bare = pps is not None and abs(shares * pps - total) <= 0.01
        if action == "bonus":
            warnings.append(f"FEE {_eur(fee)} IGNORED: A BONUS HAS NO FEE")
        elif bare:                                   # amount = shares x price: the fee comes on top (or off)
            total = total + fee if action == "buy" else total - fee
            warnings.append(f"FEE {_eur(fee)} {'ADDED' if action == 'buy' else 'TAKEN OFF'}: THE AMOUNT IS SHARES × PRICE")
        else:
            fee_in = True
            warnings.append(f"FEE {_eur(fee)} TAKEN AS INCLUDED IN THE AMOUNT")
    price, unit = compute(action, shares, pps=pps, total=total, fee=None if total is not None else fee)
    if total is not None and pps is not None and pps > 0:
        sign = 1 if action == "buy" else -1 if action == "sell" else 0
        expect = shares * pps + (sign * fee if fee_in else 0)
        gap = abs(expect - price) / price
        if gap > MISMATCH and abs(expect - price) > 0.01:
            warnings.append(f"{fmt_qty(shares)} × {_eur(pps)} = {_eur(money(expect))} BUT TOTAL {_eur(price)} "
                            f"({gap * 100:.1f} % APART) — A FEE OR A WRONG COLUMN?")
        if keep_pps:
            unit = per_share(pps)
    return {"date": when, "ticker": ticker, "action": action, "shares": shares, "price": price, "pps": unit}, warnings


# ── review against your file, then merge ─────────────────────────────────────────────────────────
def _chronological(new: list[dict]) -> list[dict]:
    """Oldest first, same-day trades in their order — a newest-first list (a broker export) is turned round."""
    if len(new) > 1 and new[0]["date"] > new[-1]["date"] and \
            all(a["date"] >= b["date"] for a, b in zip(new, new[1:])):
        new = new[::-1]
    return sorted(new, key=lambda r: r["date"])


def merge(existing: list[dict], new: list[dict], mode: str) -> list[dict]:
    """The file after an import: `append` slots each new trade in after the last row dated on or before it
    (your order otherwise kept) — as place() would one by one, in one pass; `replace` is the new trades alone.
    Oldest first either way."""
    base = list(existing) if mode == "append" else []
    order = sorted(range(len(base)), key=lambda i: base[i]["date"])
    after: dict[int, list[dict]] = {}                    # existing index -> new rows that follow it (-1: first)
    j, last = 0, -1
    for t in _chronological(new):                        # dates ascending: `last` only ever moves on
        while j < len(order) and base[order[j]]["date"] <= t["date"]:
            last = max(last, order[j])
            j += 1
        after.setdefault(last, []).append(t)
    out = list(after.get(-1, []))
    for i, r in enumerate(base):
        out += [r, *after.get(i, [])]
    return out


def review(existing: list[dict], bulk: dict, mode: str) -> dict:
    """parse_bulk's result checked against your file: a duplicate (of your file — append — or of a line above)
    is a warning; a sale beyond the shares held then is an error on its row — or on the pasted sale that
    leaves a later sale of your file short. Repeated until the rest merges cleanly."""
    base = list(existing) if mode == "append" else []
    rows = bulk["rows"]
    mine = {_key(t) for t in base}
    seen: dict[tuple, int] = {}
    for r in rows:
        t = r["trade"]
        if t is None:
            continue
        k = _key(t)
        if k in mine:
            r["warnings"].append("SAME AS A TRADE ALREADY IN YOUR FILE")
        elif k in seen:
            r["warnings"].append(f"SAME AS LINE {seen[k]}")
        seen.setdefault(k, r["line"])
    before = {k for i, k in enumerate(ids(base)) if i in oversold(base)}
    for _ in range(len(rows) + 1):
        good = [r for r in rows if r["trade"] and not r["error"]]
        mine = {id(r["trade"]): r for r in good}
        merged = merge(base, [r["trade"] for r in good], "append")
        keys = ids(merged)
        bad = [(i, h) for i, h in oversold(merged).items() if keys[i] not in before or id(merged[i]) in mine]
        if not bad:
            break
        i, held = bad[0]
        hit = merged[i]
        if id(hit) in mine:
            mine[id(hit)]["error"] = _short(hit, held)
            continue
        culprit = next((merged[j] for j in range(i - 1, -1, -1) if id(merged[j]) in mine
                        and merged[j]["ticker"] == hit["ticker"] and merged[j]["action"] == "sell"), None)
        culprit = culprit or next((merged[j] for j in range(i - 1, -1, -1) if id(merged[j]) in mine
                                   and merged[j]["ticker"] == hit["ticker"]), None)
        if culprit is None:                      # not the paste's doing after all
            before.add(keys[i])
            continue
        mine[id(culprit)]["error"] = (f"LEAVES YOUR SELL {fmt_qty(hit['shares'])} {hit['ticker']} ON {hit['date']} "
                                      f"SHORT: ONLY {fmt_qty(_round(held, 6))} HELD THEN")
    return bulk
