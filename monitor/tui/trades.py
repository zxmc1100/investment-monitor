"""TRADES in the terminal: web/app/trades.js and ask.js. The arithmetic and the words are the server's own
(monitor.portfolio.trades) rather than a copy; what is left is what the form and the questions show. Pure."""

from __future__ import annotations

from datetime import date

from monitor.portfolio import trades as book
from monitor.tui.cmd import fold
from monitor.tui.fmt import fmt_date

_eur = book._eur
fmt_qty, fmt_money, describe = book.fmt_qty, book.fmt_money, book.describe


def given(v) -> bool:
    return v is not None and str(v).strip() != ""


def trade_from(v: dict, today: date) -> dict:
    """The form's (or a command's) values -> {trade, fee, body} | {error}. `keep`: an edit's stored price per share, kept
    while the price is untouched. `body` is what the API gets — and goes through the server's make_trade to get there, so
    what this shows is what will be stored."""
    try:
        total = book.number(v.get("total"), "TOTAL") if given(v.get("total")) else None
        pps = book.number(v.get("pps"), "PRICE PER SHARE") if given(v.get("pps")) else None
        fee = book.number(v.get("fee"), "FEE") if given(v.get("fee")) else None
        t = book.make_trade(v, today)
        keep = v["keep"] if total is not None and pps is None and (v.get("keep") or 0) > 0 else None
        if keep is not None:
            t["pps"] = keep
        body = {
            "ticker": t["ticker"],
            "action": t["action"],
            "shares": t["shares"],
            **({"total": total} if total is not None else {"pps": pps}),
            **({"pps": keep, "keep_pps": True} if keep is not None else {}),
            **({"fee": fee} if fee is not None and total is None else {}),
            "date": t["date"],
        }
        return {"trade": t, "fee": None if total is not None else fee, "body": body}
    except book.TradeError as e:
        return {"error": str(e)}


def edit_values(r: dict) -> dict:
    """A TRANSACTIONS row -> the form's values for editing it (the stored total; its price per share kept)."""
    return {
        "ticker": r["tkr"],
        "action": str(r["action"]).lower(),
        "shares": fmt_qty(r["shares"]),
        "pps": "",
        "total": fmt_money(r["total"]),
        "date": r["date"],
        "fee": "",
        "keep": r.get("pps"),
    }


def duplicate_of(rows: list, t: dict, except_id=None):
    return next(
        (
            r
            for r in rows or []
            if r.get("id") != except_id
            and r.get("date") == t["date"]
            and r.get("tkr") == t["ticker"]
            and str(r.get("action")).lower() == t["action"]
            and r.get("shares") == t["shares"]
            and r.get("total") == t["price"]
        ),
        None,
    )


def typed(values: dict, k: str, value: str) -> dict:
    """The ADD form after `value` was typed into `k`: the price typed last is the source — the other empties — and
    touching shares or a price ends an edit's kept price per share."""
    v = {**values, k: value}
    if k in ("pps", "total") and given(value):
        v["total" if k == "pps" else "pps"] = ""
    if k in ("shares", "pps", "total"):
        v["keep"] = None
    return v


def form_status(values: dict, today: date, rows: list, editing=None) -> dict:
    """The line under the ADD form: {text, cls: "" | dim (still needed) | err | warn (a twin in your file), derived}."""
    if not any(given(values.get(k)) for k in ("ticker", "shares", "pps", "total")):
        return {"text": "", "cls": "", "derived": {}}
    need = [
        w
        for w, miss in (
            ("TICKER", not given(values.get("ticker"))),
            ("SHARES", not given(values.get("shares"))),
            ("€ / SHARE OR TOTAL", not given(values.get("pps")) and not given(values.get("total"))),
        )
        if miss
    ]
    if need:
        return {"text": f"STILL NEEDED: {' · '.join(need)}", "cls": "dim", "derived": {}}
    r = trade_from(values, today)
    if "error" in r:
        return {"text": r["error"], "cls": "err", "derived": {}}
    derived = (
        {"pps": fmt_money(r["trade"]["pps"])}
        if given(values.get("total"))
        else {"total": fmt_money(r["trade"]["price"])}
    )
    twin = duplicate_of(rows, r["trade"], editing)
    return {
        "text": describe(r["trade"], r["fee"]) + (" · SAME AS A TRADE ALREADY IN YOUR FILE" if twin else ""),
        "cls": "warn" if twin else "",
        "derived": derived,
    }


SEP = {",": "COMMA", ";": "SEMICOLON", "\t": "TAB"}


def _many(n: int, one: str) -> str:
    return f"{n} {one}{'' if n == 1 else 'S'}"


def paste_summary(res: dict) -> str:
    if res.get("error"):
        return res["error"]
    rows = res.get("rows") or []
    warn = sum(1 for r in rows if not r.get("error") and r.get("warnings"))
    return " · ".join(
        x
        for x in [
            _many(len(rows), "ROW"),
            f"{res.get('ok') or 0} TO ADD",
            f"{res['bad']} WITH AN ERROR" if res.get("bad") else "",
            _many(warn, "WARNING") if warn else "",
            f"{SEP.get(res.get('delimiter'), res.get('delimiter'))} BETWEEN FIELDS",
            "DECIMAL COMMA" if res.get("decimal") == "comma" else "DECIMAL POINT",
            "HEADER READ" if res.get("header") else "NO HEADER",
            *(res.get("notes") or []),
        ]
        if x
    )


# Euro-area exchanges Yahoo suffixes: a ticker without one of these is quoted in another currency.
EUR = (
    ".DE",
    ".F",
    ".BE",
    ".DU",
    ".HM",
    ".HA",
    ".MU",
    ".SG",
    ".TG",
    ".AS",
    ".PA",
    ".MI",
    ".MC",
    ".BR",
    ".LS",
    ".VI",
    ".HE",
    ".IR",
    ".AT",
)


def is_eur(t) -> bool:
    return str(t).upper().endswith(EUR)


def rank_suggestions(q: str, held: list, found: list, n: int = 8) -> list:
    """The ticker field's suggestions: the holdings that match, then the universe's — EUR listings first, the rest marked."""
    f = fold(q)
    if not f:
        return []
    mine = [
        {"ticker": b["ticker"], "name": b.get("name"), "note": "IN YOUR BOOK"}
        for b in held or []
        if fold(b["ticker"]).startswith(f) or f in fold(b.get("name"))
    ]
    have = {m["ticker"] for m in mine}
    rest = sorted((r for r in found or [] if r["ticker"] not in have), key=lambda r: not is_eur(r["ticker"]))
    return (
        mine
        + [
            {
                "ticker": r["ticker"],
                "name": r.get("name"),
                "note": " · ".join(
                    x
                    for x in (r.get("country"), "" if is_eur(r["ticker"]) else "NOT A EUR LISTING — PRICED AS €")
                    if x and x != "—"
                ),
            }
            for r in rest
        ]
    )[:n]


def decode_bytes(data: bytes) -> str:
    """An imported file's bytes as text: UTF-8 (a BOM dropped), else Windows-1252 — what an older Excel saves."""
    try:
        return data.decode("utf-8").removeprefix("\ufeff")
    except UnicodeDecodeError:
        return data.decode("cp1252", "replace")


# ── ask.js ───────────────────────────────────────────────────────────────────────────────────────
KEPT = "The file as it is now is kept in input/backups/ until you delete it, and UNDO brings it back."


def removable(panels: list, focus):
    """The table a delete may act on: the FOCUSED one that allows it — never the first table as a fallback."""
    return next((p for p in panels or [] if p["id"] == focus and p["type"] == "table" and p.get("remove")), None)


def stands(q: dict, route: str, rows: list) -> bool:
    if not q or q["route"] != route:
        return False
    have = {str(r) for r in rows or []}
    return all(str(r) in have for r in q.get("rows") or [])


def _lower_many(n: int, one: str) -> str:
    return f"{n} {one}{'' if n == 1 else 's'}"


def _span(rows: list) -> str:
    d = sorted(r["date"] for r in rows or [] if r.get("date"))
    return f"{fmt_date(d[0])} to {fmt_date(d[-1])}" if d else ""


def row_line(r: dict) -> str:
    return f"{fmt_date(r['date'])} · {r['action']} {fmt_qty(r['shares'])} {r['tkr']} · {_eur(r['total'])}"


def fresh_text(rows: list, error, lines) -> str:
    what = (
        f"{_lower_many(lines or 0, 'line')} it cannot read ({error})"
        if error
        else f"all {_lower_many(len(rows), 'trade')}{', ' + _span(rows) if rows else ''}"
    )
    return f"Empties input/portfolio.csv — {what}. {KEPT}"


def replace_text(rows: list, error, lines, pre: dict, file) -> str:
    good = f"{pre['ok']} good row{'' if pre['ok'] == 1 else 's'} of {file or 'the box'}"
    if not error and not rows:
        return f"The {good} become your trades (you have none yet)."
    yours = (
        f"The {_lower_many(lines or 0, 'line')} of your file (it cannot be read)"
        if error
        else f"Your {_lower_many(len(rows), 'trade')}{' (' + _span(rows) + ')' if rows else ''}"
    )
    return f"{yours} give way to the {good}{' (' + str(pre['bad']) + ' with an error left out)' if pre.get('bad') else ''}. {KEPT}"


def undo_text(u: dict) -> str:
    at = f" ({str(u['at'])[11:19]})" if u.get("at") else ""
    if u["what"].startswith("UNDO: "):
        return f"Brings back what UNDO took back: {u['what'][6:]}{at}. UNDO again takes it back."
    return f"Puts input/portfolio.csv back as it was before: {u['what']}{at}. UNDO again brings that change back."
