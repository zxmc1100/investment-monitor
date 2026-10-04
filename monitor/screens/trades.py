"""TRADES — enter and manage your trades inside the terminal: ADD one (a form), PASTE / IMPORT many (a box and a
CSV file picker, previewed row by row), and TRANSACTIONS — every trade, newest first: Enter edits, Del deletes.
While input/portfolio.csv is still the example a banner offers START FRESH. Private: never exported.

One inline tier (local, no network — names come from the maps, the universe and what PORT already asked Yahoo):
it reads the file through monitor.portfolio.tradebook, whose API routes (monitor.server.app, /api/trades) do the
writing; a write recomputes this screen at once and every portfolio screen after it (Engine.inputs_changed).
"""
from __future__ import annotations

from monitor import config
from monitor.portfolio.tradebook import TradeBook
from monitor.screens.base import Ctx, Screen
from monitor.screens.common import PRIV, num
from monitor.screens.identity import identify

DEPS = ("monitor.screens.trades", "monitor.screens.base", "monitor.screens.common", "monitor.screens.identity",
        "monitor.portfolio.tradebook", "monitor.portfolio.trades", "monitor.portfolio.ledger", "monitor.config")
EXAMPLE = config.EXAMPLES_DIR / "portfolio.example.csv"

HELP = [
    {"h": "ADD", "vis": PRIV, "body": "Ticker (type a name or a ticker: your holdings and the Trade Republic "
     "universe are suggested — pick a EUR listing such as SAP.DE), action, shares, and EITHER the price per share "
     "OR the total you paid (received, for a sale) with fees included: fill one and the other follows. No fee is "
     "ever added for you — FEE is only for a fee not already in the price (added to a buy, taken off a sale). "
     "Date as YYYY-MM-DD, DD.MM.YYYY or DD/MM/YYYY; empty is today. The line under the form is exactly what "
     "will be stored; Enter saves. Tab, then Enter, takes you into the form; Esc leaves it."},
    {"h": "EDIT / DELETE", "vis": PRIV, "body": "↑↓ picks a trade in TRANSACTIONS. Enter loads it into the "
     "form: SAVE replaces it (in place, or moved to its new date). Del or Backspace deletes it after you "
     "confirm. A change that would leave a sale of more shares than you held then is refused."},
    {"h": "PASTE / IMPORT", "vis": PRIV, "body": "Paste rows (from a spreadsheet, a note) or pick your "
     "broker's CSV. A header is optional; its names may be English, German or Italian (date / datum / data, "
     "ticker / symbol or ISIN, action / type / typ, shares / quantity / anzahl, price per share / price / kurs / "
     "prezzo, total / amount / betrag / importo, fee / gebühr / commissione). Tab, ; or , between fields; a "
     "decimal comma where ; separates them. Without a header: Date,Ticker,Action,Shares,Total[,PricePerShare] "
     "or Date,Ticker,Action,Shares,@PricePerShare. A minus sign (money out) is ignored — the action says the "
     "direction. The preview shows each row as it will be stored, or why not; ADD adds the good rows (each "
     "by its date); a file can instead REPLACE every trade. Bad rows are never written: they stay in the box "
     "to fix."},
    {"h": "COMMANDS", "vis": PRIV, "body": "From any screen: BUY SAP.DE 4 @ 240 (price per share) · "
     "BUY SAP.DE 4 = 961 (total paid, fees in it) · SELL ALV.DE 2 @ 410 2026-03-02 (a date last) · "
     "BONUS IWDA.AS 0.15 = 15 · FEE 1 at the end adds a fee not in the price · START FRESH empties the file "
     "(asks first)."},
    {"h": "THE FILE", "vis": PRIV, "body": "Your trades are input/portfolio.csv. Every change rewrites it in "
     "one form — commas, a decimal point, YYYY-MM-DD — and first copies the previous file to input/backups/ "
     "(the newest 20 are kept). You may still edit it in Excel; the terminal picks the change up. A save made "
     "on a file that changed meanwhile is refused: the view reloads, save again."},
]

FIELDS = [
    {"k": "ticker", "label": "TICKER", "kind": "ticker", "placeholder": "name or ticker, e.g. SAP.DE"},
    {"k": "action", "label": "ACTION", "kind": "choice", "options": ["buy", "sell", "bonus"]},
    {"k": "shares", "label": "SHARES", "kind": "number", "placeholder": "4"},
    {"k": "pps", "label": "€ / SHARE", "kind": "number", "pair": "total", "placeholder": "price per share"},
    {"k": "total", "label": "TOTAL €", "kind": "number", "pair": "pps", "placeholder": "fees included"},
    {"k": "date", "label": "DATE", "kind": "date", "placeholder": "today"},
    {"k": "fee", "label": "FEE €", "kind": "number", "placeholder": "only if not already in the price"},
]

COLS = [
    {"k": "date", "label": "DATE", "fmt": "date", "vis": PRIV},
    {"k": "tkr", "label": "TKR", "fmt": "tkr", "vis": PRIV},
    {"k": "name", "label": "NAME", "fmt": "text", "vis": PRIV},
    {"k": "action", "label": "ACTION", "fmt": "side", "vis": PRIV},
    {"k": "shares", "label": "SHARES", "fmt": "qty", "vis": PRIV, "align": "r"},
    {"k": "pps", "label": "€/SHARE", "fmt": "px", "vis": PRIV, "align": "r"},
    {"k": "total", "label": "TOTAL €", "fmt": "eur", "vis": PRIV, "align": "r"},
]


def compute(tier: str, ctx: Ctx) -> dict:
    """The file as TradeBook reads it, plus a display name per ticker — offline (max_asks=0)."""
    if tier != "quote":
        raise ValueError(f"TRADES has no {tier!r} tier")
    state = TradeBook(ctx.portfolio_csv, example=EXAMPLE).read()
    who = identify({r["ticker"] for r in state["rows"]}, buffer_dir=ctx.buffer_dir, need=("name",), max_asks=0)
    return {**state, "names": {t: w["name"] for t, w in who.items()}}


def _row(i: int, r: dict, names: dict) -> dict:
    return {"id": r["id"], "when": f"{r['date']}#{i:06d}", "date": r["date"], "tkr": r["ticker"],
            "name": names.get(r["ticker"], r["ticker"]), "action": r["action"].upper(),
            "shares": num(r["shares"]), "pps": num(r["pps"]), "total": num(r["price"])}


def assemble(parts: dict, meta: dict) -> dict:
    q = parts["quote"]
    names, rows = q["names"], [_row(i, r, q["names"]) for i, r in enumerate(q["rows"])]
    book = [{"ticker": t, "name": names.get(t, t)} for t in dict.fromkeys(r["ticker"] for r in reversed(q["rows"]))]
    top = []
    if q["error"]:
        top.append({"id": "problem", "n": 4, "title": "YOUR FILE HAS AN ERROR", "type": "banner", "span": 12,
                    "tone": "dn", "vis": PRIV, "text": q["error"], "run": "START FRESH", "button": "START FRESH",
                    "context": {"text": "FIX IT IN THE FILE · OR IMPORT A FILE WITH REPLACE · OR START FRESH"}})
    elif q["example"]:
        top.append({"id": "fresh", "n": 4, "title": "EXAMPLE PORTFOLIO", "type": "banner", "span": 12,
                    "vis": PRIV, "text": "EXAMPLE PORTFOLIO — START FRESH clears it", "run": "START FRESH",
                    "button": "START FRESH",
                    "context": {"text": "INVENTED TRADES FROM examples/ · A BACKUP IS KEPT"}})
    n = len(rows)
    return {"screen": "TRADES", "title": "Trades",
            "context": {"text": f"{n} TRADE{'' if n == 1 else 'S'} · input/portfolio.csv · BACKUPS IN input/backups/"},
            "meta": meta, "help": HELP, "etag": q["etag"], "error": q["error"],
            "panels": [
                *top,
                {"id": "add", "n": 1, "title": "ADD", "type": "form", "span": 5, "form": "trade", "vis": PRIV,
                 "fields": FIELDS, "book": book,
                 "context": {"text": "ENTER SAVES · ESC LEAVES · TAB NEXT FIELD"}},
                {"id": "paste", "n": 2, "title": "PASTE / IMPORT", "type": "paste", "span": 7, "vis": PRIV,
                 "context": {"text": "MANY ROWS AT ONCE · YOUR BROKER'S CSV"}},
                {"id": "txns", "n": 3, "title": "TRANSACTIONS", "type": "table", "span": 12, "vis": PRIV,
                 "key": "id", "sort": ["when", "desc"], "cols": COLS, "rows": rows, "edit": "add", "remove": True,
                 "empty": "NO TRADES YET — ADD ONE ABOVE, PASTE MANY, OR IMPORT YOUR BROKER'S CSV",
                 "context": {"text": "NEWEST FIRST · ENTER EDITS · DEL DELETES" if n else "NO TRADES YET"}},
            ]}


SCREEN = Screen(id="TRADES", title="Trades", fkey=6, status="live", public=False, tiers=("quote",), deps=DEPS,
                compute=compute, assemble=assemble, uses_inputs=True, inline=True)
