"""The alert job's market snapshot: live quotes for every watched ticker (forced),
52-week highs from the 12 h daily-history cache (only when a DD rule exists), upcoming events from
the 24 h events cache. Portfolio day % and weights come from port_view (PORT's stored positions
marked to this check's quotes); OPT drift is handed in by the caller from the stored OPT payload —
nothing heavy is recomputed here."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from monitor import config
from monitor.data.buffer import age_s, cached_events, cached_price_history, cached_quotes
from monitor.portfolio.snapshot import marked, old_bars

PRICE_KINDS = ("LEVEL", "MOVE", "DD")


def watched_tickers(rules: list[dict], held, watched) -> list[str]:
    """Holdings ∪ watchlist ∪ every ticker a LEVEL / MOVE / DD rule names."""
    named = {r["ticker"] for r in rules if r["kind"] in PRICE_KINDS and r.get("ticker") not in (None, "*")}
    return sorted(set(held) | set(watched) | named)


def _dd_tickers(rules, held, watched) -> list[str]:
    out = set()
    for r in rules:
        if r["kind"] == "DD":
            out |= (set(held) | set(watched)) if r.get("ticker") == "*" else {r["ticker"]}
    return sorted(out)


def gather(rules: list[dict], *, held, watched, port: dict | None, drift: dict | None,
           buffer_dir: Path | None = None, since: datetime | None = None) -> dict:
    """since: when the checks resumed (a tab reopened) — a quote's STALE age counts from no earlier
    than that, so quotes left hours old while no tab was open don't all fire at the first hiccup."""
    held, watched = sorted(held), sorted(watched)
    tickers = watched_tickers(rules, held, watched)
    quotes, stale, _ = cached_quotes(tickers, force=True, fresh_s=45, buffer_dir=buffer_dir) if tickers else ({}, {}, None)
    old, now = old_bars(quotes), datetime.now()
    resumed = (now - since).total_seconds() if since else float("inf")
    # stale: this minute's fetch failed (or the bar is old) — no price rule acts on it. lapsed: no good
    # quote for STALE_ALERT_MIN minutes (stale[t] = the last good fetch) or an old bar — the STALE rule.
    snap_quotes = {t: (None if q is None else {
        **q, "stale": t in stale or t in old,
        "lapsed": t in old or (t in stale and min(age_s(stale[t], now), resumed) >= config.STALE_ALERT_MIN * 60)})
        for t, q in quotes.items()}
    high52 = {}
    dd = _dd_tickers(rules, held, watched)
    if dd:
        hist = cached_price_history(dd, period="1y", buffer_dir=buffer_dir, adjusted=False)
        high52 = {t: float(hist[t].max()) for t in dd if t in hist.columns and hist[t].notna().any()}
    events = {}
    if any(r["kind"] == "EVENT" for r in rules) and (held or watched):
        events = cached_events(sorted(set(held) | set(watched)), buffer_dir=buffer_dir)
    return {"quotes": snap_quotes, "high52": high52, "held": held, "watched": watched,
            "events": events, "port": port, "drift": drift}


def port_view(positions: list[dict] | None, quotes: dict, held) -> dict | None:
    """PORT DAY / PORT WEIGHT inputs {day_pct, weights, day}: PORT's open positions (ticker, shares,
    avg cost — the stored PORT quote part) marked to this check's quotes by PORT's own valuation
    (snapshot.marked), so DAY % equals PORT's for the same quotes whichever screen is open.
    day = the newest date among your held quotes (a watched or rule ticker — BTC on a Saturday —
    never moves it). A quote from an older session adds no move (its value still counts) — Friday's
    drop is not Monday's; with no held quote fetched live this check, DAY % is None (never fire on old
    data). Only positions the ledger still holds count (one sold since PORT ran may still be watched).
    No positions → None: those rules wait."""
    held = {p["ticker"]: p for p in positions or [] if p["ticker"] in quotes and p["ticker"] in set(held)}
    if not held:
        return None
    dates = [q["date"] for t, q in quotes.items() if t in held and q and q.get("date")]
    day = max(dates) if dates else None
    mine = {t: q if not q or q.get("date") == day else {**q, "prev_close": None}
            for t, q in quotes.items() if t in held}
    m = marked({"holdings": held, "realized": {}}, mine)
    total = sum(p["position_value"] for p in m["positions"])
    live = any(q and not q.get("stale") for q in mine.values())
    return {"day_pct": m["day_pct"] if live else None, "day": day,
            "weights": {p["ticker"]: p["position_value"] / total * 100 for p in m["positions"]} if total else {}}
