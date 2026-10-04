"""Portfolio parser and P&L calculator for Trade Republic holdings."""

import csv
import logging
import math
from collections import deque
from datetime import date
from pathlib import Path

from monitor import config

log = logging.getLogger(__name__)

DUST = 0.001          # a holding below this many shares counts as fully exited
# Actions that add shares. `bonus` = Trade Republic Saveback/bonus shares: received, not paid for —
# a FIFO lot at its booked `Price` (so P&L % matches the app), but never money invested.
ADDS = ("buy", "bonus")


def _lot_cost(lots) -> float:
    return sum(n * c for n, c in lots)


def parse_portfolio(csv_path: str | Path) -> dict:
    """
    Parse trade history CSV into current holdings and realized P&L, the way Trade Republic
    computes them: FIFO lots (a sale consumes the earliest-bought shares first), each costed
    at the buy's exact EUR amount (`Price`, fees included) — never the rounded
    `PricePerShare`. Sales realize their exact `Price`. Rows apply in file order (the CSV is
    chronological). A `bonus` row (see ADDS) builds holdings and lots exactly like a buy.

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

    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ticker   = row["Ticker"].strip()
            action   = row["Action"].strip().lower()
            shares   = float(row["Shares"])
            price    = float(row["Price"])          # total EUR (exact)
            pps      = float(row["PricePerShare"])  # EUR per share (rounded; display only)
            date     = row["Date"].strip()

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


def _euro_amount(text: str) -> float:
    """'3.95' or German '3,95' (quote it in the CSV, or the comma splits the field). A value with
    both separators ('1.234,56') is ambiguous and rejected rather than guessed."""
    text = text.strip()
    if "," in text:
        if "." in text:
            raise ValueError("ambiguous decimal separators")
        text = text.replace(",", ".")
    eur = float(text)
    if not math.isfinite(eur):
        raise ValueError("not a finite amount")
    return eur


def load_interest(path: str | Path) -> list[dict]:
    """Interest Trade Republic paid on uninvested cash: `interest.csv` with columns Date,Amount (EUR
    received; a decimal comma works when quoted: "3,95"). Reported in ACCOUNTING only — never part
    of ROI, XIRR or YTD. Tolerates an Excel BOM and spaces in the header. A missing file means no
    interest; a malformed row — bad date or amount, or the wrong number of fields (an unquoted
    3,95) — is skipped with a warning, never misread. Returns [{date, eur}] sorted by date."""
    path = Path(path)
    if not path.exists():
        return []
    out = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames:
            reader.fieldnames = [(name or "").strip() for name in reader.fieldnames]
        for n, row in enumerate(reader, start=2):
            try:
                if None in row or None in row.values():
                    raise ValueError("wrong number of fields (quote a decimal comma: \"3,95\")")
                d = date.fromisoformat((row.get("Date") or "").strip()).isoformat()
                eur = _euro_amount(row.get("Amount") or "")
            except (TypeError, ValueError) as e:
                log.warning("interest file %s line %d skipped (%s): %r", path.name, n, e, row)
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
