"""Trade Republic's own transaction export (app → Profile → Transactions → Export CSV), read by TRADES' import.

One row per booking: `category` / `type` say what it is, `symbol` the ISIN, `amount` the cash moved (negative
out), `fee` and `tax` separate (negative). Read as:

  TRADING BUY / SELL            a buy (amount + fee + tax — a transaction tax such as Italy's: what it cost)
                                or a sell (amount − fee: what came in; the tax on a gain is not a cost of
                                the position — said in a warning). A sell's shares are negative: read as is.
                                An order's fills booked one after the other (a fractional part, then the
                                whole) are one trade.
  CASH BENEFITS_SAVEBACK        with the buy it pays for (same day, same amount): that buy is a BONUS
  CASH STOCKPERK                the same, for a gift of shares
  DELIVERY FREE_RECEIPT         shares received free: a BONUS worth shares x price
  CASH DIVIDEND                 a dividend: its pay date, shares, gross (net + tax), tax and net
  CASH INTEREST_PAYMENT         interest on cash
  anything else                 deposits, withdrawals, card payments, transfers: not the portfolio — skipped,
                                counted in a note

The ISIN becomes a ticker through `isin(code, name, alias)` — `name` the export's ("Infineon"), `alias` the
security as its description spells it ("INFINEON TECH.AG NA O.N."); one it cannot place is an error on that
row alone."""
from __future__ import annotations

import re
from collections import Counter
from typing import Callable

NAME = "TRADE REPUBLIC"
# the export's own column names; a header holding these is the export
COLUMNS = ("datetime", "date", "account_type", "category", "type", "asset_class", "name", "symbol", "shares", "price",
           "amount", "fee", "tax", "currency", "transaction_id")
_PAIRED = ("BENEFITS_SAVEBACK", "STOCKPERK")        # cash that pays for a buy of free shares
_ISIN = re.compile(r"[A-Z]{2}[A-Z0-9]{9}\d")


class ExportError(ValueError):
    pass


_DESC = re.compile(r"(?:trade|execution)\s+[A-Z]{2}[A-Z0-9]{9}\d\s+(.+?),\s*quantity", re.IGNORECASE)


def _alias(description: str) -> str:
    """The security as the description spells it: "Buy trade DE0006231004 INFINEON TECH.AG NA O.N., quantity: 3"
    → "INFINEON TECH.AG NA O.N."; "" when it holds none."""
    m = _DESC.search(description or "")
    return m.group(1).strip() if m else ""


def _merge_fills(rows: list[dict]) -> list[dict]:
    """One order's fills booked as rows one after the other (a fractional part without a fee, the whole part with
    it): one trade — shares and totals added, the price per share their weighted mean."""
    out = []
    for r in rows:
        prev = out[-1] if out else None
        t, p = r["trade"], prev["trade"] if prev else None
        if (t and p and not r["error"] and not prev["error"] and r["line"] == prev["end"] + 1
                and (t["date"], t["ticker"], t["action"]) == (p["date"], p["ticker"], p["action"])):
            shares = p["shares"] + t["shares"]
            prev["trade"] = {**p, "shares": round(shares, 10), "price": _r(p["price"] + t["price"]),
                             "pps": round((p["pps"] * p["shares"] + t["pps"] * t["shares"]) / shares, 6)}
            prev.update(end=r["end"], text=f"{prev['text']}\n{r['text']}", warnings=prev["warnings"] + r["warnings"])
        else:
            out.append(r)
    return out


def is_export(head: list[str]) -> bool:
    names = {c.strip().strip('"').lower() for c in head}
    return set(COLUMNS) <= names


def _num(v: str) -> float:
    v = (v or "").strip()
    if not v:
        return 0.0
    try:
        return float(v)
    except ValueError:
        raise ExportError(f"'{v}' IS NOT A NUMBER") from None


def _r(x: float, nd: int = 2) -> float:
    return round(x + 0.0, nd)


def read(records: list[tuple[int, int, list[str]]], head: list[str], lines: list[str],
         isin: Callable[[str], str | None] | None) -> dict:
    """records: (first line, last line, cells) after the header. Returns parse_bulk's {"rows", "notes"} plus
    "dividends" [{pay, ticker, shares, gross, tax, net}] and "interest" [{date, eur}]."""
    at = {c.strip().strip('"').lower(): i for i, c in enumerate(head)}
    recs = []
    for start, end, cells in records:
        get = {k: (cells[i].strip() if i < len(cells) else "") for k, i in at.items()}
        recs.append((start, end, get))

    # Saveback / stock perk: the cash credit names no ISIN — it pays for the buy of the same amount that day
    credits = Counter((g["date"], _r(abs(_num(g["amount"])))) for _, _, g in recs
                      if g["category"] == "CASH" and g["type"] in _PAIRED)
    rows, dividends, interest, skipped = [], [], [], Counter()

    def ticker(g) -> str:
        code = g["symbol"].upper()
        if not _ISIN.fullmatch(code):
            raise ExportError(f"NO ISIN IN '{g['symbol']}'")
        t = isin(code, g["name"], _alias(g["description"])) if isin else None
        if not t:
            raise ExportError(f"ISIN {code} ({g['name'] or '?'}) NOT FOUND — ADD IT UNDER [isins] IN settings.toml, "
                              f'e.g. "{code}" = "TICKER"')
        return t

    for start, end, g in recs:
        cat, typ = g["category"].upper(), g["type"].upper()
        rec = {"line": start, "end": end, "text": "\n".join(lines[start - 1:end]), "trade": None, "error": None,
               "warnings": []}
        try:
            if cat == "TRADING" and typ in ("BUY", "SELL"):
                shares, pps = abs(_num(g["shares"])), abs(_num(g["price"]))
                amount, fee, tax = abs(_num(g["amount"])), abs(_num(g["fee"])), abs(_num(g["tax"]))
                if shares <= 0:
                    raise ExportError("NO SHARES")
                action, total = "buy", amount + fee + tax     # tax on a buy: a transaction tax (Italy's FTT)
                if typ == "SELL":
                    action, total = "sell", amount - fee
                    if tax:
                        rec["warnings"].append(f"TAX {tax:.2f} ON THE GAIN NOT IN THE TOTAL — IT IS NOT A COST OF THE POSITION")
                elif credits[(g["date"], _r(amount))] > 0:     # paid by a Saveback / stock perk: received free
                    credits[(g["date"], _r(amount))] -= 1
                    action, total = "bonus", amount
                rec["trade"] = {"date": g["date"], "ticker": ticker(g), "action": action, "shares": shares,
                                "price": _r(total), "pps": pps or _r(total / shares, 6)}
                rows.append(rec)
            elif cat == "DELIVERY" and typ == "FREE_RECEIPT":
                shares, pps = abs(_num(g["shares"])), abs(_num(g["price"]))
                rec["trade"] = {"date": g["date"], "ticker": ticker(g), "action": "bonus", "shares": shares,
                                "price": _r(shares * pps), "pps": pps}
                rows.append(rec)
            elif cat == "CASH" and typ == "DIVIDEND":
                net, tax = abs(_num(g["amount"])), abs(_num(g["tax"]))
                dividends.append({"pay": g["date"], "ticker": ticker(g), "shares": abs(_num(g["shares"])),
                                  "gross": _r(net + tax), "tax": _r(tax), "net": _r(net)})
            elif cat == "CASH" and typ == "INTEREST_PAYMENT":
                interest.append({"date": g["date"], "eur": _r(_num(g["amount"]))})
            elif cat == "CASH" and typ in _PAIRED:
                pass                                            # read with its buy
            else:
                skipped[f"{cat} {typ}".strip()] += 1
        except ExportError as e:
            rec["error"] = str(e)
            rows.append(rec)

    cash = sum(n for k, n in skipped.items() if k.startswith("CASH"))
    other = {k: n for k, n in skipped.items() if not k.startswith("CASH")}
    notes = [f"{NAME} EXPORT", f"{len(dividends)} DIVIDEND{'S' * (len(dividends) != 1)} → dividends.csv",
             f"{len(interest)} INTEREST → interest.csv"]
    if cash:
        notes.append(f"{cash} CASH MOVE{'S' * (cash != 1)} SKIPPED (DEPOSITS, WITHDRAWALS, CARD, TRANSFERS)")
    if other:
        notes.append("NOT READ: " + ", ".join(f"{n} {k}" for k, n in sorted(other.items())))
    return {"rows": _merge_fills(rows), "notes": notes, "dividends": dividends, "interest": interest}
