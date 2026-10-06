"""Trade Republic universe lookup: every stock TR offers, keyed by its Yahoo home ticker — search by
ticker / ISIN / name, the most liquid live names, tradeability.

Sources under config.UNIVERSE_DIR: universe_meta.csv (ticker, name, country, isin, med_turnover,
exit_reason) and sector_map.json (ticker → sector), both tracked — on a fresh clone universe_meta.csv
alone is the universe. Optional and gitignored: tr_universe.csv (isin,name,country) and
tr_ticker_map.json (ISIN → home ticker); when both exist they list the names and the meta adds turnover
and exits. With no files at all the universe is EMPTY, never an error.
The table is built once per directory and rebuilt when a source file's mtime changes (a refreshed file
is picked up without a restart); every query is pure over it.
"""
from __future__ import annotations

import json
import re
import logging
import unicodedata
from pathlib import Path

import pandas as pd

from monitor import config
from monitor.data import buffer

log = logging.getLogger(__name__)
COLS = ["isin", "ticker", "name", "country", "sector", "med_turnover", "live", "fold"]
_CACHE: dict[str, tuple[tuple, pd.DataFrame]] = {}
_SOURCES = ("tr_universe.csv", "tr_ticker_map.json", "universe_meta.csv", "sector_map.json")


def fold(s) -> str:
    """Case- and accent-insensitive form: 'Société Générale' -> 'SOCIETE GENERALE'."""
    return unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().upper()


def build(universe: pd.DataFrame, ticker_map: dict, meta: pd.DataFrame | None, sectors: dict) -> pd.DataFrame:
    """One row per TR ISIN that has a Yahoo ticker. A meta row with an exit_reason (delisted,
    demoted) marks the name not live; an ISIN without meta stays live with unknown turnover."""
    df = universe[["isin", "name", "country"]].copy()
    df["ticker"] = df["isin"].map(ticker_map).fillna("").astype(str).str.strip().str.upper()
    df = df[df["ticker"] != ""].copy()
    m = meta.drop_duplicates("isin").set_index("isin") if meta is not None and len(meta) else None
    if m is not None:
        df["med_turnover"] = pd.to_numeric(df["isin"].map(m["med_turnover"]), errors="coerce")
        reason = df["isin"].map(m["exit_reason"]).fillna("").astype(str).str.strip()
        meta_sector = df["isin"].map(m["sector"]).where(lambda s: s.notna() & (s != "Unknown"))
    else:
        df["med_turnover"] = float("nan")
        reason = pd.Series("", index=df.index)
        meta_sector = pd.Series(None, index=df.index, dtype=object)
    df["live"] = reason.eq("")
    df["sector"] = df["ticker"].map(sectors).fillna(meta_sector).fillna("—")
    df["fold"] = df["name"].map(fold)
    return df.drop_duplicates("ticker").reset_index(drop=True)[COLS]


def _load(root: Path) -> pd.DataFrame:
    try:
        meta = pd.read_csv(root / "universe_meta.csv", dtype={"isin": str, "exit_reason": str, "sector": str})
    except (OSError, ValueError):
        meta = None
    try:
        uni = pd.read_csv(root / "tr_universe.csv", dtype=str)
        tmap = json.loads((root / "tr_ticker_map.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        if meta is None or not {"isin", "ticker", "name", "country"} <= set(meta.columns):
            log.warning("TR universe unavailable under %s (%s): lookup is empty", root, e)
            return pd.DataFrame(columns=COLS)
        log.info("TR universe files absent under %s: using universe_meta.csv", root)
        m = meta.dropna(subset=["isin", "ticker", "name"]).drop_duplicates("isin")
        uni = m[["isin", "name", "country"]].astype(str)
        tmap = dict(zip(m["isin"], m["ticker"].astype(str)))
    try:
        sectors = json.loads((root / "sector_map.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        sectors = {}
    return build(uni, tmap, meta, sectors)


def load_universe(root: Path | None = None) -> pd.DataFrame:
    """The lookup table (cached in-process): isin, ticker, name, country, sector ('—' unknown),
    med_turnover (NaN unknown), live."""
    root = Path(root or config.UNIVERSE_DIR)
    key = str(root.resolve())
    stamp = tuple((root / f).stat().st_mtime_ns if (root / f).exists() else None for f in _SOURCES)
    if key not in _CACHE or _CACHE[key][0] != stamp:
        _CACHE[key] = (stamp, _load(root))
    return _CACHE[key][1]


def _out(r) -> dict:
    return {"ticker": r.ticker, "name": r.name, "sector": r.sector, "country": r.country}


def search(q: str, n: int = 8, root: Path | None = None) -> list[dict]:
    """Exact ticker → exact ISIN → name contains / ticker starts with (accents and case folded),
    the last ranked by med_turnover (unknown last). Live names only."""
    df = load_universe(root)
    live = df[df["live"]]
    q = str(q or "").strip()
    if not q or live.empty:
        return []
    up = q.upper()
    hit = live[live["ticker"] == up]
    if hit.empty:
        hit = live[live["isin"] == up]
    if hit.empty:
        f = fold(q)
        if not f.strip():                     # "€", CJK: nothing survives folding — it is no name
            return []
        hit = live[live["fold"].str.contains(f, regex=False) | live["ticker"].str.startswith(up)]
        hit = hit.sort_values("med_turnover", ascending=False, na_position="last", kind="stable")
    return [_out(r) for r in hit.head(n).itertuples()]


def liquid(n: int = 500, root: Path | None = None) -> list[str]:
    """The n live tickers with the highest median turnover (names without turnover never count)."""
    df = load_universe(root)
    live = df[df["live"] & df["med_turnover"].notna()]
    return live.sort_values("med_turnover", ascending=False, kind="stable")["ticker"].head(n).tolist()


def info(ticker: str, root: Path | None = None) -> dict | None:
    """{ticker, name, sector, country} for a live universe ticker, else None."""
    df = load_universe(root)
    hit = df[(df["ticker"] == str(ticker or "").strip().upper()) & df["live"]]
    return _out(next(hit.itertuples())) if len(hit) else None


def infos(tickers, root: Path | None = None) -> dict[str, dict]:
    """{ticker: {name, sector}} for the live universe tickers among `tickers` (one pass)."""
    df = load_universe(root)
    hit = df[df["live"] & df["ticker"].isin(set(tickers))]
    return {r.ticker: {"name": r.name, "sector": r.sector} for r in hit.itertuples()}


def by_isin(code: str, root: Path | None = None) -> str | None:
    """The Yahoo home ticker of the live name with exactly this ISIN, else None (a paste or a broker's CSV
    names ISINs — TRADES resolves them here)."""
    df = load_universe(root)
    hit = df[(df["isin"] == str(code or "").strip().upper()) & df["live"]]
    return str(hit["ticker"].iloc[0]) if len(hit) else None


# words that name a company's form or a fund's share class, not the company: "Siemens" is "Siemens AG"
_FORM = frozenset("""inc incorporated corp corporation co company companies ag se sa spa nv plc ltd limited group
holding holdings the class adr ads reg registered shares share aktie aktien namens inhaber vz ord ordinary ucits
etf acc accumulating dist distributing usd eur gbp chf of and de
aktiengesellschaft gmbh kgaa societa per azioni societe anonyme naamloze vennootschap oyj asa ab publ""".split())


_BRACKETS = re.compile(r"\([^)]*\)")
# a broker description's currency of the nominal value: "COCA-COLA CO. DL-,25" (dollar), "ASML HOLDING EO -,09"
_CURRENCY = frozenset("dl eo ls hd sf jy ck nk sk cl".split())


def name_key(name: str) -> tuple[str, ...]:
    """The words that tell a company apart, in order: folded; anything in brackets ("(Google)", "(ADR)"), numbers,
    single letters, company-form words and a description's currency codes dropped."""
    words = "".join(c if c.isalnum() else " " for c in _BRACKETS.sub(" ", fold(name).lower())).split()
    return tuple(dict.fromkeys(w for w in words if len(w) > 1 and not w.isdigit() and w not in _FORM
                               and w not in _CURRENCY))


def _same(a: tuple[str, ...], b: tuple[str, ...], loose: bool) -> bool:
    """One name's words within the other's. A one-word name matches only that same one word ("ASML" is
    "ASML Holding N.V.", "Bank" is not "Bank of America"). loose — a broker's abbreviation: every word of the
    shorter name (two at least) and some word of the other begin one another ("TECH." and "Technologies")."""
    if not a or not b:
        return False
    small, big = sorted((a, b), key=len)
    if loose:
        return len(small) >= 2 and all(any(w.startswith(x) or x.startswith(w) for w in big if min(len(w), len(x)) >= 3)
                                       for x in small)
    return (set(small) <= set(big)) and (len(small) > 1 or len(big) == 1)


def match_name(name: str, held: dict[str, str], loose: bool = False) -> str | None:
    """The one held ticker whose name is the same company as `name` (a broker export's), else None. Never a
    guess: two held lines that both match give None (see _same for what matches)."""
    key = name_key(name)
    hits = [t for t, other in held.items() if _same(key, name_key(other), loose)]
    return hits[0] if len(hits) == 1 else None


_EUR_SUFFIX = (".DE", ".F", ".MI", ".PA", ".AS", ".MC", ".BR", ".VI", ".LS", ".HE", ".IR", ".SG", ".DU", ".MU",
               ".BE", ".HM")


def resolve_isin(code: str, name: str = "", held: dict[str, str] | None = None, *, alias: str = "",
                 overrides: dict[str, str] | None = None, buffer_dir: Path | None = None, _search=None,
                 root: Path | None = None) -> str | None:
    """A broker export's ISIN (its name for it, and `alias`, the name as its description spells it) → the
    ticker the terminal books it under: your settings.toml [isins]; else the held line of the same company
    (`held` {ticker: name}, by name, then by the alias's word prefixes — an import never splits a holding you
    keep under its Frankfurt or Milan ticker); else the universe's line when EUR-quoted; else a EUR listing
    from Yahoo's search (kept in the buffer); else None."""
    code = str(code or "").strip().upper()
    if t := {k.strip().upper(): v for k, v in (overrides or {}).items()}.get(code):
        return t
    if held and (t := match_name(name, held) or match_name(alias, held, loose=True)):
        return t
    if (t := by_isin(code, root)) and t.upper().endswith(_EUR_SUFFIX):
        return t
    return buffer.cached_eur_listing(code, name, buffer_dir=buffer_dir, _search=_search)


def is_tradeable(ticker: str, root: Path | None = None) -> bool:
    """True if `ticker` is the Yahoo home ticker of a live Trade Republic ISIN."""
    return info(ticker, root) is not None
