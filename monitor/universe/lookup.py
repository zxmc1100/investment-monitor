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
import logging
import unicodedata
from pathlib import Path

import pandas as pd

from monitor import config

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


def is_tradeable(ticker: str, root: Path | None = None) -> bool:
    """True if `ticker` is the Yahoo home ticker of a live Trade Republic ISIN."""
    return info(ticker, root) is not None
