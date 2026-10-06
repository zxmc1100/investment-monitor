"""Your dividends — what you were paid, what is due and what is coming — each with its ex date (who gets it: the
shares held after every trade dated strictly before it), its pay date (when the money arrives) and its net
(after tax).

Sources, best first:
  dividends.csv   what your broker paid (TRADES' import of a Trade Republic export): exact pay date and net
  Yahoo           per-share amounts by ex date, for every line; its calendar: the next ex date (and, for some
                  lines, the pay date)

A Yahoo dividend that a paid one matches (same ticker, paid from 3 days before to 90 days after its ex date)
takes the paid one's pay date, gross, tax and net — a payment written with a date still ahead (announced, copied
from the broker's app) is due until then. One not matched is estimated: shares x per-share, less the dividend
tax; its pay date is the ex date plus the gap this line's past dividends took from ex date to pay (learned from
the matched ones), else the gap your other lines' took, else unknown — then the ex date stands in, as before pay
dates were known.

Status: PAID (paid, or its pay date has come), DUE (ex date passed, pay date still ahead), UPCOMING (ex date
ahead). Only PAID is cash (`cash`): ROI, XIRR, YTD and ACCOUNTING's dividends count it on its pay date."""
from __future__ import annotations

from datetime import date, timedelta
from statistics import median

from monitor.portfolio.ledger import DUST, _signed_shares

MATCH_BEFORE, MATCH_AFTER = 3, 90          # days around an ex date a paid dividend may land and still be it


def _held(trades: list[dict], before: str) -> float:
    return sum(_signed_shares(t) for t in trades if t["date"] < before)


def _plus(day: str, days: float) -> str:
    return (date.fromisoformat(day) + timedelta(days=round(days))).isoformat()


def combine(transactions: list[dict], per_share: dict[str, list], paid: list[dict], calendar: dict[str, dict],
            *, today: date | None = None, tax: float) -> list[dict]:
    """Every dividend record: {ticker, ex, pay, pay_est, shares, per_share, gross, tax, net, status, source, date}.
    per_share: {ticker: [[ex_iso, amount], ...]} (Yahoo); paid: [{pay, ticker, shares, gross, tax, net}] (broker);
    calendar: {ticker: {ex, pay, amount}} (Yahoo's next ex date); tax: the rate an estimate is taxed at.
    `date` is the day it is cash: the pay date, or the ex date when no pay date is known."""
    iso = (today or date.today()).isoformat()
    out = []
    by_ticker: dict[str, list[dict]] = {}
    for p in sorted(paid, key=lambda p: p["pay"]):
        by_ticker.setdefault(p["ticker"], []).append(p)

    # each line's dividends you were entitled to, and the paid one each became
    lines = {}
    for tk in sorted(set(per_share) | set(by_ticker) | set(calendar)):
        trades = [t for t in transactions if t["ticker"] == tk]
        left = list(by_ticker.get(tk, []))
        entitled = [(ex, float(a)) for ex, a in sorted(per_share.get(tk, [])) if ex <= iso and float(a) > 0]
        entitled = [(ex, a, h) for ex, a in entitled if (h := _held(trades, ex)) > DUST]
        matched = {}
        for ex, _, _ in entitled:
            lo, hi = _plus(ex, -MATCH_BEFORE), _plus(ex, MATCH_AFTER)
            near = [p for p in left if lo <= p["pay"] <= hi]                 # a real payment before an announcement
            hit = min(near, key=lambda p: (p["net"] is None, p["pay"])) if near else None
            if hit is not None:
                left.remove(hit)
                matched[ex] = hit
        gaps = [(date.fromisoformat(p["pay"]) - date.fromisoformat(ex)).days for ex, p in matched.items()]
        lines[tk] = (trades, left, entitled, matched, gaps)
    # a line paid before: its own gap from ex date to pay; one never paid: the gap your other lines' took
    every = [g for *_, gaps in lines.values() for g in gaps]
    usual = median(every) if every else None

    for tk, (trades, left, entitled, matched, gaps) in lines.items():
        gap = median(gaps) if gaps else usual
        cal = calendar.get(tk) or {}

        for ex, amount, held in entitled:
            status = lambda pay: "DUE" if pay and pay > iso else "PAID"     # noqa: E731 — dated ahead: still due
            if (p := matched.get(ex)) is not None and p["net"] is not None:
                out.append({"ticker": tk, "ex": ex, "pay": p["pay"], "pay_est": False, "shares": p["shares"] or held,
                            "per_share": amount, "gross": p["gross"], "tax": p["tax"], "net": p["net"],
                            "status": status(p["pay"]), "source": "BROKER"})
                continue
            if p is not None:                                  # announced: the day known, the amount Yahoo's
                gross = held * amount
                out.append({"ticker": tk, "ex": ex, "pay": p["pay"], "pay_est": False, "shares": held,
                            "per_share": amount, "gross": gross, "tax": gross * tax, "net": gross * (1 - tax),
                            "status": status(p["pay"]), "source": "ANNOUNCED"})
                continue
            pay, est = (cal["pay"], False) if cal.get("ex") == ex and cal.get("pay") else (
                (_plus(ex, gap), True) if gap is not None else (None, False))
            gross = held * amount
            out.append({"ticker": tk, "ex": ex, "pay": pay, "pay_est": est, "shares": held, "per_share": amount,
                        "gross": gross, "tax": gross * tax, "net": gross * (1 - tax),
                        "status": "DUE" if pay and pay > iso else "PAID", "source": "YAHOO"})
        announced = [p for p in left if p["net"] is None]      # a day without an amount: the next one's, if any
        for p in left:                                     # paid, but Yahoo never listed it
            if p["net"] is None:
                continue
            out.append({"ticker": tk, "ex": None, "pay": p["pay"], "pay_est": False, "shares": p["shares"],
                        "per_share": p["gross"] / p["shares"] if p["shares"] and p["gross"] else None,
                        "gross": p["gross"], "tax": p["tax"], "net": p["net"],
                        "status": "DUE" if p["pay"] > iso else "PAID", "source": "BROKER"})
        # the next one, from the calendar
        if cal.get("ex") and cal["ex"] > iso and (now := _held(trades, _plus(iso, 1))) > DUST:
            amount = cal.get("amount") or (entitled[-1][1] if entitled else None)
            if amount:
                told = next((p["pay"] for p in announced if p["pay"] >= cal["ex"]), None)
                pay, est = (told or cal["pay"], False) if told or cal.get("pay") else (
                    (_plus(cal["ex"], gap), True) if gap is not None else (None, False))
                gross = now * amount
                out.append({"ticker": tk, "ex": cal["ex"], "pay": pay, "pay_est": est, "shares": now,
                            "per_share": amount, "gross": gross, "tax": gross * tax, "net": gross * (1 - tax),
                            "status": "UPCOMING", "source": "CALENDAR"})
    for r in out:
        r["date"] = r["pay"] or r["ex"]
    return sorted(out, key=lambda r: (r["date"], r["ticker"]))


def cash(records: list[dict]) -> list[dict]:
    """The dividends that are cash: PAID ones, on their pay date (the ex date when none is known) —
    [{date, ticker, shares, per_share, gross, eur}] as ROI, XIRR, YTD and ACCOUNTING read them."""
    return [{"date": r["date"], "ticker": r["ticker"], "shares": r["shares"], "per_share": r["per_share"],
             "gross": r["gross"], "eur": r["net"]} for r in records if r["status"] == "PAID"]
