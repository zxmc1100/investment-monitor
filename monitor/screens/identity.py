"""Who a ticker is — display name, sector, issuer country — for any holding, with no code edit.
Field by field: your settings ([names] / [sectors] / [countries], merged into the
maps) → the built-in maps → the Trade Republic universe → Yahoo's profile (local/buffer/info.json,
30 days; a failure retried after a day) → the ticker / "Unknown". Sectors and countries come out in
the book's vocabulary (GICS sector names, spelled-out countries).

It sits in the screens layer because it joins the data, universe and portfolio layers. Call it
from a compute (it may ask Yahoo for a name nobody else knows), never from an assemble.
"""
from __future__ import annotations

from pathlib import Path

from monitor.data.buffer import cached_info
from monitor.data.instruments import COMPANY_NAMES, TICKER_MAP
from monitor.portfolio.meta import (ETF_SECTOR_WEIGHTS, PORTFOLIO_COUNTRY_MAP, PORTFOLIO_SECTOR_MAP,
                                    country_name, sector_name)
from monitor.universe import lookup

UNKNOWN = "Unknown"
MAX_ASKS = 5       # Yahoo profile asks per run (~0.5 s each): a first paint never waits on a long list
FIELDS = ("name", "sector", "country")


def _fill(rec: dict, name, sector, country) -> None:
    for k, v in (("name", name), ("sector", sector_name(sector)), ("country", country_name(country))):
        if not rec[k] and v:
            rec[k] = str(v).strip()


def identify(tickers, *, buffer_dir: Path | None = None, names: dict[str, str] | None = None,
             need: tuple[str, ...] = FIELDS, max_asks: int = MAX_ASKS) -> dict[str, dict]:
    """{ticker: {"name", "sector", "country"}} — never empty fields. `names`: extra display names
    (your watchlist's) tried right after the built-in map. Yahoo is asked only for a ticker still
    missing one of the `need` fields (a look-through ETF never), and at most `max_asks` times per call —
    the others show the ticker / "Unknown" until a later run (every quote tier) asks them. max_asks=0:
    no network at all, only what earlier asks cached."""
    out = {}
    for t in sorted(set(tickers)):
        rec = {"name": COMPANY_NAMES.get(t) or (names or {}).get(t),
               "sector": PORTFOLIO_SECTOR_MAP.get(t), "country": PORTFOLIO_COUNTRY_MAP.get(t)}
        if not all(rec[k] for k in need):
            for key in dict.fromkeys((t, TICKER_MAP.get(t, t))):
                hit = lookup.info(key)
                if hit:
                    _fill(rec, hit.get("name"), hit.get("sector"), hit.get("country"))
        out[t] = rec
    ask = [t for t, r in out.items() if not all(r[k] for k in need) and t not in ETF_SECTOR_WEIGHTS]
    for t, info in (cached_info(ask, buffer_dir=buffer_dir, max_asks=max_asks) if ask else {}).items():
        if info:
            _fill(out[t], info.get("name"), info.get("sector"), info.get("country"))
    for t, r in out.items():
        r.update(name=r["name"] or t, sector=r["sector"] or UNKNOWN, country=r["country"] or UNKNOWN)
    return out
