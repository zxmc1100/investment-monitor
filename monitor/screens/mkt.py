"""MKT — Market Overview: how markets are doing (indices, FX, rates, commodities,
crypto), what is moving among the most liquid Trade Republic stocks, my names and sectors in that
context, and what is coming (earnings, ex-dividend dates).

quote tier: board quotes (forced each minute), my names' quotes from the shared buffer (the alert
loop refreshes them), movers (their own 15-minute cache), live position values.
daily tier: 1-year closes for the board and the sector ETFs, my names' real closes (52W range),
upcoming events (24 h cache).
Public: the board, the movers (liquid stocks only — a watched small cap never appears) and MY
SECTORS. MY NAMES and EVENTS are private.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd

from monitor import config
from monitor.alerts import watchlist
from monitor.data.buffer import age_s, cached_events, cached_movers, cached_price_history, cached_quotes
from monitor.data.instruments import BOARD, BOARD_TICKERS, FX_PAIRS, SECTOR_PROXIES, YIELDS
from monitor.portfolio import snapshot
from monitor.portfolio.meta import exposure_breakdown
from monitor.screens.base import Ctx, Screen
from monitor.screens.common import PRIV, PUB, num, rebased, session_day
from monitor.screens.identity import identify
from monitor.universe import lookup

DEPS = ("monitor.screens.mkt", "monitor.screens.common", "monitor.screens.base", "monitor.alerts.watchlist",
        "monitor.universe.lookup", "monitor.portfolio.snapshot", "monitor.portfolio.ledger",
        "monitor.portfolio.meta", "monitor.data.buffer", "monitor.data.market", "monitor.data.yahoo",
        "monitor.data.instruments", "monitor.config", "monitor.screens.identity")
MON = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
TOP, SPIKES, HORIZON_D, NAME_W = 15, 10, 30, 20
NO_UNIVERSE = "NO TR UNIVERSE — movers rank its most liquid stocks: data/universe/ has no universe files"
# MY NAMES reads the shared buffer unforced; the alert check refreshes every name each minute while a
# tab is open, so a buffered quote older than three checks is a failing fetch: last-good + STALE.
NAMES_STALE_S = 3 * config.ALERT_INTERVAL_S
ROUND_THE_CLOCK = {"BTC-EUR", "ETH-EUR"}                  # quote on weekends: not evidence of an open session
SHORT = {"Information Technology": "Info Tech", "Communication Services": "Comm Svcs",   # MY SECTORS is 3/12 wide
         "Consumer Discretionary": "Cons Discr", "Consumer Staples": "Cons Staples"}
# the movers' SECTOR column carries Yahoo's names: same short labels, so GAINERS / LOSERS / VOLUME SPIKES fit
SHORT_YAHOO = {"Technology": "Tech", "Financial Services": "Financials", "Consumer Cyclical": "Cons Cyclical",
               "Consumer Defensive": "Cons Defensive", "Basic Materials": "Materials"}

HELP = [
    {"h": "BOARD", "vis": PUB, "body": "Last price and day change from Yahoo's live quote (previous close = "
     "the official close of the prior session); 1W and YTD against daily closes; 1Y is the last year "
     "rebased to 100. Yahoo quotes can lag the exchange by up to 15 minutes. US 10Y is the yield in "
     "percent, its changes in basis points (bp)."},
    {"h": "GAINERS · LOSERS · VOLUME SPIKES", "vis": PUB, "body": "Ranked among the most liquid Trade "
     "Republic stocks (median daily turnover), refreshed at most every 15 minutes from daily bars on "
     "each stock's home exchange — DAY % is the latest bar (its date is in the panel header) and can "
     "lag intraday. VOL× = the latest bar's volume over the median of the 20 bars before it."},
    {"h": "MY SECTORS", "vis": PUB, "body": "Your sector weights (ETFs looked through) next to one "
     "proxy ETF per sector, in the ETF's listing currency, from daily closes: XLK US Tech, EXV1 "
     "STOXX Europe 600 Banks (Financials), XLC, XLY, XLV, XLI, XLP, XLE, XLB, XLU, XLRE (US SPDR "
     "sector funds). Proxies are approximations of what you hold. MY DAY = your names in that sector, "
     "value-weighted."},
    {"h": "MY NAMES", "vis": PRIV, "body": "● held · ★ watched (WATCH <name>, UNWATCH <ticker>). "
     "Watched names quote on their home listing in its currency. 52W POS = where the last price sits "
     "in its 52-week range (0 % low, 100 % high). Enter opens the security."},
    {"h": "EVENTS", "vis": PRIV, "body": "Earnings and ex-dividend dates in the next 30 days from "
     "Yahoo's calendar (refreshed daily); EX-DIV shows the last paid dividend per share (gross, listing "
     "currency — your book's dividend cash is net of tax). The next "
     "business day is highlighted and fires the EVENT alert."},
]


# ── compute ──────────────────────────────────────────────────────────────────

def _book(ctx: Ctx) -> dict | None:
    try:
        return snapshot.load_book(ctx.portfolio_csv)
    except (FileNotFoundError, ValueError):         # no ledger yet, or it cannot be read: the market still shows
        return None


def compute(tier: str, ctx: Ctx) -> dict:
    book = _book(ctx)
    held = sorted(book["holdings"]) if book else []
    watched = watchlist.load(ctx.watchlist)
    names = sorted(set(held) | {w["ticker"] for w in watched})
    if tier == "quote":
        wnames = {w["ticker"]: w["name"] for w in watched}
        mine, mine_stale, _ = (cached_quotes(names, force=False, buffer_dir=ctx.buffer_dir)
                               if names else ({}, {}, None))
        # MY NAMES reads the buffer the alert loop keeps fresh. A forced compute — every automatic
        # quote run is forced (Engine.ensure_fresh), as is REFRESH MKT — also refetches the names
        # whose buffered quote is older than NAMES_STALE_S (the alert loop was not running), in one
        # batched fetch with the board; the refetch's verdict replaces the first read's
        old = [t for t in names if ctx.force and age_s((mine.get(t) or {}).get("ts")) > NAMES_STALE_S]
        both, both_stale, _ = cached_quotes(BOARD_TICKERS + old, force=ctx.force, buffer_dir=ctx.buffer_dir)
        board = {t: both[t] for t in BOARD_TICKERS}
        board_stale = {t: both_stale[t] for t in BOARD_TICKERS if t in both_stale}
        mine = {**mine, **{t: both[t] for t in old}}
        mine_stale = {**{t: v for t, v in mine_stale.items() if t not in old},
                      **{t: both_stale[t] for t in old if t in both_stale}}
        liquid = lookup.liquid(config.LIQUID_N)
        movers, movers_at, movers_stale = cached_movers(liquid, ttl_min=config.MOVERS_TTL_MIN,
                                                        buffer_dir=ctx.buffer_dir)
        name_moves = cached_movers(names, ttl_min=config.MOVERS_TTL_MIN, buffer_dir=ctx.buffer_dir)[0] if names else {}
        positions = snapshot.quote_tier(book, force=False, buffer_dir=ctx.buffer_dir)["positions"] if held else []
        stale = {**board_stale, **snapshot.old_bars(mine), **mine_stale,
                 **{t: q.get("ts") for t, q in mine.items() if q and age_s(q.get("ts")) > NAMES_STALE_S},
                 **{t: None for t in names if mine.get(t) is None}}
        return {"board": board, "mine": mine, "stale": stale, "liquid_n": len(liquid),
                "movers": movers, "movers_at": movers_at, "movers_stale": movers_stale,
                "info": lookup.infos(set(movers) | {w["ticker"] for w in watched}),
                "name_moves": name_moves, "positions": positions, "held": held, "watched": watched,
                "ident": identify(held, buffer_dir=ctx.buffer_dir, need=("name", "sector"), names=wnames),
                # watched-only names: your [names] first, then the watch name (never a Yahoo ask)
                "wident": identify(set(wnames) - set(held), buffer_dir=ctx.buffer_dir, need=("name",),
                                   names=wnames)}
    if tier == "daily":
        proxies = sorted({t for t, _ in SECTOR_PROXIES.values()})
        hist = cached_price_history(BOARD_TICKERS + proxies, period="1y", force=ctx.force, buffer_dir=ctx.buffer_dir)
        names_hist = (cached_price_history(names, period="1y", force=ctx.force, buffer_dir=ctx.buffer_dir,
                                           adjusted=False) if names else pd.DataFrame())
        events = cached_events(names, buffer_dir=ctx.buffer_dir) if names else {}
        return {"hist": hist, "names_hist": names_hist, "events": events}
    raise ValueError(f"MKT has no {tier!r} tier")


# ── helpers ──────────────────────────────────────────────────────────────────

def _col(hist: pd.DataFrame, t: str) -> pd.Series:
    return hist[t].dropna() if hist is not None and t in hist.columns else pd.Series(dtype=float, index=pd.DatetimeIndex([]))


def _close_before(s: pd.Series, when: pd.Timestamp) -> float | None:
    past = s[s.index <= when]
    return float(past.iloc[-1]) if len(past) else None


def _chg(now, ref, bp: bool = False) -> float | None:
    if now is None or not ref:
        return None
    return num((now - ref) * 100) if bp else num((now / ref - 1) * 100)


def _ddmon(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day:02d} {MON[d.month - 1]} {d.year % 100:02d}"           # DD MON YY, as every screen


def _clip(s: str) -> str:
    return s if len(s) <= NAME_W else s[: NAME_W - 1] + "…"


def _day(q: dict | None) -> float | None:
    q = q or {}
    return _chg(q.get("price"), q.get("prev_close"))


# ── panels ───────────────────────────────────────────────────────────────────

BOARD_COLS = [{"k": "n", "label": "#", "fmt": "int", "vis": PUB, "align": "r", "lo": True},
              {"k": "name", "label": "NAME", "fmt": "text", "vis": PUB},
              {"k": "lvl", "label": "LAST", "fmt": "num:2", "vis": PUB, "align": "r"},
              {"k": "day", "label": "DAY", "fmt": "pct+:2", "vis": PUB, "align": "r"},
              {"k": "w1", "label": "1W", "fmt": "pct+", "vis": PUB, "align": "r", "lo": True},
              {"k": "ytd", "label": "YTD", "fmt": "pct+", "vis": PUB, "align": "r"},
              {"k": "spark", "label": "1Y", "fmt": "spark", "vis": PUB, "lo": True}]   # lo: give way in a narrow panel


def _board(pid: str, title: str, n: int, span: int, rows_def, q: dict, d: dict) -> dict:
    today = pd.Timestamp(date.today())
    rows, quotes = [], {}
    for i, (t, name) in enumerate(rows_def, 1):
        qt = q["board"].get(t) or {}
        quotes[t] = qt
        s = _col(d["hist"], t)
        last = qt.get("price")
        if last is None and len(s):                       # 0.0 is a price; only a missing quote falls back
            last = float(s.iloc[-1])
        bp = t in YIELDS
        prev_year = s[s.index.year < today.year]
        row = {"n": i, "name": name, "lvl": num(last),
               "day": _chg(last, qt.get("prev_close"), bp),
               "w1": _chg(last, _close_before(s, today - pd.Timedelta(days=7)), bp),
               "ytd": _chg(last, float(prev_year.iloc[-1]) if len(prev_year) else None, bp),
               "spark": rebased(s)}
        if bp:
            row["_fmt"] = {"lvl": "num:3", "day": "bp+", "w1": "bp+", "ytd": "bp+"}
        elif t in FX_PAIRS:
            row["_fmt"] = {"lvl": "num:4"}
        if t in q["stale"] or not qt or not len(s):
            row["_stale"] = True
        rows.append(row)
    day = session_day({t: v for t, v in quotes.items() if t not in ROUND_THE_CLOCK})
    ctx = f"DAY = {day.strftime('%a').upper()} {day.day:02d} {MON[day.month - 1]}" if day else "LIVE"
    return {"id": pid, "n": n, "title": title, "type": "table", "span": span, "vis": PUB,
            "key": "n", "sort": ["n", "asc"], "cols": BOARD_COLS, "rows": rows,
            "context": {"text": ctx, "vis": PUB}}


MOVER_COLS = [{"k": "tkr", "label": "TKR", "fmt": "tkr", "vis": PUB},
              {"k": "name", "label": "NAME", "fmt": "text", "vis": PUB},
              {"k": "sector", "label": "SECTOR", "fmt": "text", "vis": PUB},
              {"k": "day", "label": "DAY %", "fmt": "pct+:2", "vis": PUB, "align": "r"},
              {"k": "d5", "label": "5D %", "fmt": "pct+", "vis": PUB, "align": "r"},
              {"k": "volx", "label": "VOL×", "fmt": "mult", "vis": PUB, "align": "r"}]


def _short_sector(label: str) -> str:
    return SHORT_YAHOO.get(label) or SHORT.get(label, label)


def _mover_rows(q: dict) -> tuple[list[dict], str | None]:
    """Rows on a current bar (a line whose last bar is > 4 days behind the newest is dead, not moving)."""
    movers = q["movers"]
    newest = max((r["bar"] for r in movers.values()), default=None)
    if newest is None:
        return [], None
    cutoff = (date.fromisoformat(newest) - timedelta(days=4)).isoformat()
    rows = []
    for t, r in sorted(movers.items()):
        if r["bar"] < cutoff:
            continue
        info = q["info"].get(t, {})
        rows.append({"tkr": t, "name": _clip(info.get("name", t)), "sector": _short_sector(info.get("sector", "—")),
                     "day": num(r["day"]), "d5": num(r.get("d5")), "volx": num(r.get("volx"))})
    return rows, newest


def _movers_ctx(q: dict, newest: str | None) -> str:
    if newest is None:
        return "MOVERS UNAVAILABLE — RETRY WITHIN 15 MIN" if q["liquid_n"] else NO_UNIVERSE
    try:
        at = datetime.fromisoformat(q["movers_at"]).strftime("%H:%M")
    except (TypeError, ValueError):                   # missing / unparsable stamp: say nothing of the time
        at = ""
    head = f"LIQUID {q['liquid_n']} · BAR {_ddmon(newest)}"
    if q["movers_stale"]:
        return f"{head} · STALE, LAST GOOD {at}" if at else f"{head} · STALE"
    return f"{head} · {at}" if at else head


def _movers(q: dict) -> list[dict]:
    rows, newest = _mover_rows(q)
    ctx = {"text": _movers_ctx(q, newest), "vis": PUB}
    by_day = sorted((r for r in rows if r["day"] is not None), key=lambda r: (-r["day"], r["tkr"]))
    spikes = sorted((r for r in rows if r["volx"] is not None), key=lambda r: (-r["volx"], r["tkr"]))
    base = {"type": "table", "span": 4, "vis": PUB, "key": "tkr", "cols": MOVER_COLS,
            "context": ctx, "enter": "SEC {key}"}
    if not q["liquid_n"]:
        base["empty"] = NO_UNIVERSE                 # the body says it too, not just the header
    return [{**base, "id": "gainers", "n": 4, "title": "GAINERS", "sort": ["day", "desc"], "rows": by_day[:TOP]},
            {**base, "id": "losers", "n": 5, "title": "LOSERS", "sort": ["day", "asc"], "rows": by_day[::-1][:TOP]},
            {**base, "id": "spikes", "n": 6, "title": "VOLUME SPIKES", "sort": ["volx", "desc"], "rows": spikes[:SPIKES]}]


def _next_event(events: list[dict]) -> str | None:
    if not events:
        return None
    e = min(events, key=lambda e: e["date"])
    return f"{'EARN' if e['kind'] == 'EARNINGS' else 'EX-DIV'} {_ddmon(e['date'])}"


def _pos52(last, s: pd.Series) -> float | None:
    if last is None or not len(s):
        return None
    yr = s[s.index >= s.index[-1] - pd.Timedelta(days=365)]
    lo, hi = min(float(yr.min()), last), max(float(yr.max()), last)
    return num((last - lo) / (hi - lo) * 100) if hi > lo else None


def _my_names(q: dict, d: dict) -> dict:
    watched = {w["ticker"]: w["name"] for w in q["watched"]}
    rows = []
    for t in sorted(set(q["held"]) | set(watched)):
        qt = q["mine"].get(t)
        last = (qt or {}).get("price")
        mv = q["name_moves"].get(t, {})
        ident = (q.get("ident") or {}).get(t) or (q.get("wident") or {}).get(t)
        name = ident["name"] if ident else watched.get(t) or q["info"].get(t, {}).get("name", t)
        row = {"tkr": t, "mark": ("H" if t in q["held"] else "") + ("W" if t in watched else ""),
               "name": _clip(name), "last": num(last), "day": _day(qt), "d5": num(mv.get("d5")),
               "volx": num(mv.get("volx")), "pos52": _pos52(last, _col(d["names_hist"], t)),
               "next": _next_event(d["events"].get(t, []))}
        if qt is None or t in q["stale"]:
            row["_stale"] = True
        rows.append(row)
    cols = [{"k": "mark", "label": "", "fmt": "mark", "vis": PRIV},
            {"k": "tkr", "label": "TKR", "fmt": "tkr", "vis": PRIV},
            {"k": "name", "label": "NAME", "fmt": "text", "vis": PRIV},
            {"k": "last", "label": "LAST", "fmt": "num:2", "vis": PRIV, "align": "r"},
            {"k": "day", "label": "DAY %", "fmt": "pct+:2", "vis": PRIV, "align": "r"},
            {"k": "d5", "label": "5D %", "fmt": "pct+", "vis": PRIV, "align": "r"},
            {"k": "volx", "label": "VOL×", "fmt": "mult", "vis": PRIV, "align": "r"},
            {"k": "pos52", "label": "52W POS", "fmt": "pct", "vis": PRIV, "align": "r"},
            {"k": "next", "label": "NEXT EVENT", "fmt": "text", "vis": PRIV}]
    return {"id": "names", "n": 7, "title": "MY NAMES", "type": "table", "span": 6, "vis": PRIV,
            "key": "tkr", "sort": ["mark", "asc"], "cols": cols, "rows": rows, "enter": "SEC {key}",
            "context": {"text": f"● {len(q['held'])} HELD · ★ {len(watched)} WATCHED", "vis": PRIV}}


def _etf(s: pd.Series, days: int | None) -> float | None:
    if len(s) < 2:
        return None
    last = float(s.iloc[-1])
    if days is None:                                  # day: the last two closes
        return _chg(last, float(s.iloc[-2]))
    return _chg(last, _close_before(s, s.index[-1] - pd.Timedelta(days=days)))


def _ytd(s: pd.Series) -> float | None:
    prev = s[s.index.year < s.index[-1].year] if len(s) else s
    return _chg(float(s.iloc[-1]), float(prev.iloc[-1])) if len(prev) else None


def _my_sectors(q: dict, d: dict) -> dict:
    pos = q["positions"]
    total = sum(p["position_value"] for p in pos)
    weights = {p["ticker"]: p["position_value"] / total for p in pos} if total > 0 else {}
    days = {t: _day(q["mine"].get(t)) for t in weights}
    rows = []
    labels = {t: r["sector"] for t, r in (q.get("ident") or {}).items()}
    for r in exposure_breakdown(weights, "sector", labels) if weights else []:
        if r["w"] <= 0:
            continue
        etf = SECTOR_PROXIES.get(r["label"], (None, None))[0]
        s = _col(d["hist"], etf)
        known = [(w, days[t]) for t, w, _ in r["parts"] if days.get(t) is not None]
        wsum = sum(w for w, _ in known)
        rows.append({"sector": SHORT.get(r["label"], r["label"]), "wt": num(r["w"] * 100), "etf": etf or "—",
                     "eday": _etf(s, None), "e1m": _etf(s, 30), "eytd": _ytd(s) if len(s) else None,
                     "myday": num(sum(w * v for w, v in known) / wsum) if wsum else None})
    cols = [{"k": "sector", "label": "SECTOR", "fmt": "text", "vis": PUB},
            {"k": "wt", "label": "WT", "fmt": "pct", "vis": PUB, "align": "r"},
            {"k": "etf", "label": "ETF", "fmt": "tkr", "vis": PUB, "lo": True},
            {"k": "eday", "label": "DAY", "fmt": "pct+", "vis": PUB, "align": "r"},
            {"k": "e1m", "label": "1M", "fmt": "pct+", "vis": PUB, "align": "r", "lo": True},
            {"k": "eytd", "label": "YTD", "fmt": "pct+", "vis": PUB, "align": "r"},
            {"k": "myday", "label": "MY DAY", "fmt": "pct+", "vis": PUB, "align": "r"}]
    return {"id": "sectors", "n": 8, "title": "MY SECTORS", "type": "table", "span": 3, "vis": PUB,
            "key": "sector", "sort": ["wt", "desc"], "cols": cols, "rows": rows,
            "context": {"text": "ETF = LAST CLOSE, LISTING CCY", "vis": PUB}}


def _events(q: dict, d: dict) -> dict:
    today = date.today()
    nbd = (pd.Timestamp(today) + pd.offsets.BDay(1)).date()
    end = (today + timedelta(days=HORIZON_D)).isoformat()
    rows = []
    for t, evs in sorted(d["events"].items()):
        for e in evs:
            if not today.isoformat() <= e["date"] <= end:
                continue
            day = date.fromisoformat(e["date"])
            what = "EARNINGS" if e["kind"] == "EARNINGS" else (
                f"EX-DIV {e['amount']:.2f}" if num(e.get("amount")) else "EX-DIV")
            when = ("TODAY" if day == today else "TOMORROW" if day == today + timedelta(days=1)
                    else day.strftime("%a").upper() if day == nbd else f"IN {(day - today).days}D")
            row = {"i": f"{e['date']}|{t}|{e['kind']}", "date": e["date"], "tkr": t, "event": what, "when": when}
            if day <= nbd:
                row["_hot"] = True
            rows.append(row)
    cols = [{"k": "date", "label": "DATE", "fmt": "date", "vis": PRIV},
            {"k": "tkr", "label": "TKR", "fmt": "tkr", "vis": PRIV},
            {"k": "event", "label": "EVENT", "fmt": "text", "vis": PRIV},
            {"k": "when", "label": "IN", "fmt": "text", "vis": PRIV}]
    return {"id": "events", "n": 9, "title": "EVENTS", "type": "table", "span": 3, "vis": PRIV,
            "key": "i", "sort": ["date", "asc"], "cols": cols, "rows": rows,
            "context": {"text": f"NEXT {HORIZON_D} DAYS · HELD + WATCHED", "vis": PRIV}}


def assemble(parts: dict, meta: dict) -> dict:
    q, d = parts["quote"], parts["daily"]
    spans = {"indices": 6, "fx": 3, "cmdty": 3}
    board = [_board(pid, title, n, spans[pid], rows, q, d) for n, (pid, title, rows) in enumerate(BOARD, 1)]
    return {"screen": "MKT", "title": "Market Overview",
            "context": {"text": f"MARKETS · MOVERS AMONG THE {q['liquid_n']} MOST LIQUID TR STOCKS", "vis": PUB},
            "meta": {**meta, "stale": q["stale"], "movers_at": q["movers_at"]},
            "help": HELP,
            "panels": [*board, *_movers(q), _my_names(q, d), _my_sectors(q, d), _events(q, d)]}


SCREEN = Screen(id="MKT", title="Market Overview", fkey=4, status="live", public=True,
                tiers=("quote", "daily"), deps=DEPS, compute=compute, assemble=assemble, uses_inputs=True)
