"""On-disk data buffer for the terminal.

Caches the expensive yfinance inputs (5y price history, market caps) with a TTL
and keeps a last-good copy of live prices, so a screen refresh is cheap and a
failed fetch degrades to the last-good value (with a staleness flag) instead of
silently substituting cost basis.

Buffer lives under local/buffer/ (gitignored). Pure helpers: a fetch function is
injectable so the cache logic is testable without the network.
"""

from monitor import config
import hashlib
import json
import math
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

from monitor.data import market as M
from monitor.data import yahoo as Y

BUFFER_DIR = config.BUFFER_DIR


def _fresh(path: Path, ttl_hours: float) -> bool:
    """Written less than ttl_hours ago. A TTL of 0 is never fresh — even when a coarse clock (Windows,
    Python 3.11) reads a moment before the file's own stamp, making its age negative."""
    return ttl_hours > 0 and path.exists() and (time.time() - path.stat().st_mtime) < ttl_hours * 3600


def _dir(buffer_dir: Path | None) -> Path:
    d = buffer_dir or BUFFER_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def cached_price_history(tickers, period="5y", ttl_hours=12, force=False,
                         buffer_dir: Path | None = None, _fetch=None, adjusted: bool = True) -> pd.DataFrame:
    """5y price history with a TTL pickle cache. Reuse within TTL unless `force`.

    adjusted=False caches the real (split-, not dividend-adjusted) closes under their own key;
    the default key is unchanged, so existing adjusted caches stay valid.

    Yahoo throttles bursts with empty frames, so a fresh answer is MERGED with the cache: it wins,
    and a requested line it leaves out (missing or all-NaN) is taken from the cached frame — but
    only if Yahoo last priced that line less than `config.HISTORY_FILL_DAYS` ago (tracked per line
    in a `.seen.json` sidecar; a cache without one counts from its file time). A line gone for
    longer is dropped, so a delisted name is never carried forever. The merged frame is written
    (the TTL restarts); an answer that prices nothing and fills nothing is not cached."""
    _fetch = _fetch or Y.fetch_price_history
    path = _hist_path(tickers, period, adjusted, _dir(buffer_dir))
    if not force and _fresh(path, ttl_hours):
        try:
            return pd.read_pickle(path)
        except Exception:
            pass
    df = _fetch(tickers, period=period, adjusted=adjusted)
    df, seen = _fill_from_cache(df if isinstance(df, pd.DataFrame) else pd.DataFrame(), path, tickers)
    if _priced(df) == 0:
        return df                                      # never cache emptiness: ask again next time
    try:
        df.to_pickle(path)
        _write_json_atomic(path.with_suffix(".seen.json"), seen)
    except Exception:
        pass
    return df


def _hist_path(tickers, period: str, adjusted: bool, d: Path) -> Path:
    key = f"{'_'.join(sorted(tickers))}|{period}" + ("" if adjusted else "|raw")
    return d / f"hist_{hashlib.md5(key.encode()).hexdigest()[:12]}.pkl"


def history_seen(tickers, period="5y", adjusted: bool = True, buffer_dir: Path | None = None) -> dict[str, float]:
    """{ticker: epoch Yahoo last priced that line} for the frame cached_price_history returns for the same
    arguments — a line filled from the cache keeps the time it was really priced. A cache without its
    sidecar counts from its file time; no cache, {}. Read-only."""
    path = _hist_path(tickers, period, adjusted, buffer_dir or BUFFER_DIR)
    if not path.exists():
        return {}
    side = path.with_suffix(".seen.json")
    if side.exists():
        return {t: v for t, v in _read_json(side).items() if isinstance(v, (int, float))}
    try:
        cols = _priced_cols(pd.read_pickle(path))
    except Exception:
        return {}
    born = path.stat().st_mtime
    return {t: born for t in tickers if t in cols}


def _priced(df) -> int:
    """How many columns of a history frame carry at least one price."""
    return int(df.notna().any().sum()) if isinstance(df, pd.DataFrame) and len(df.columns) else 0


def _priced_cols(df: pd.DataFrame) -> set:
    return set(df.columns[df.notna().any()]) if len(df.columns) else set()


def _fill_from_cache(df: pd.DataFrame, path: Path, tickers) -> tuple[pd.DataFrame, dict]:
    """(merged frame, {ticker: epoch Yahoo last priced it}) — see cached_price_history."""
    now = time.time()
    try:
        old, born = pd.read_pickle(path), path.stat().st_mtime
    except Exception:                                  # no cache yet, or unreadable
        old, born = None, now
    got = _priced_cols(df)
    seen = {t: now for t in tickers if t in got}
    if not isinstance(old, pd.DataFrame):
        return df, seen
    last, window, cached = _read_json(path.with_suffix(".seen.json")), config.HISTORY_FILL_DAYS * 86400, _priced_cols(old)
    for t in tickers:
        at = last.get(t, born)
        if t not in got and t in cached and isinstance(at, (int, float)) and now - at < window:
            seen[t] = at                               # filled: keeps the time it was really priced
    fill = [t for t in tickers if t in seen and t not in got]
    if fill:
        idx = df.index.union(old.index) if len(df.index) else old.index
        df = df.reindex(idx)
        for t in fill:
            df[t] = old[t].reindex(idx)
    return df, seen


def _fetch_ohlc_yf(ticker: str, period: str = "max", retries: int = 2) -> pd.DataFrame:
    """OHLC download, robust to two live Yahoo quirks: it throttles rapid successive
    calls (empty 'possibly delisted' frame), and for some index tickers (`^GSPC`) it
    rejects `period=max`/`start=` while accepting a long fixed period. So try a ladder
    of period specs, each with a couple of backed-off retries; an empty result is a
    transient failure, never returned as if it were real data."""
    import yfinance as yf
    ladder = [period] + [p for p in ("30y", "15y", "10y") if p != period]
    last = pd.DataFrame()
    for spec in ladder:
        for attempt in range(retries):
            raw = yf.download(ticker, period=spec, auto_adjust=True, progress=False)
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = raw.columns.get_level_values(0)
            if raw.index.tz is not None:
                raw.index = raw.index.tz_localize(None)
            cols = [c for c in ("Close", "High", "Low") if c in raw.columns]
            last = raw[cols].dropna(how="all") if cols else pd.DataFrame()
            if not last.empty:
                return last
            time.sleep(1.2 * (attempt + 1))               # back off, let the throttle clear
    return last


def cached_ohlc(ticker: str, period: str = "max", ttl_hours: float = 12, force=False,
                buffer_dir: Path | None = None, _fetch=None) -> pd.DataFrame:
    """OHLC (Close/High/Low) with a TTL pickle cache — cached_price_history stores Close
    only. A failed/empty fetch degrades to the last-good pickle (stale beats dead) and
    only raises when nothing was ever cached. An empty result is NEVER cached (that would
    poison the buffer and serve emptiness for the whole TTL)."""
    _fetch = _fetch or _fetch_ohlc_yf
    d = _dir(buffer_dir)
    path = d / f"ohlc_{ticker.replace('^', 'i').replace('.', '_')}.pkl"
    if not force and _fresh(path, ttl_hours):
        try:
            cached = pd.read_pickle(path)
            if not cached.empty:
                return cached
        except Exception:
            pass
    try:
        df = _fetch(ticker, period)
    except Exception:
        df = pd.DataFrame()
    if df.empty:                                          # transient failure
        if path.exists():
            try:
                stale = pd.read_pickle(path)
                if not stale.empty:
                    return stale                          # last-good fallback
            except Exception:
                pass
        raise RuntimeError(f"OHLC fetch for {ticker!r} returned no data and no cache exists")
    try:
        df.to_pickle(path)
    except Exception:
        pass
    return df


def cached_market_caps(tickers, ttl_hours=24, force=False,
                       buffer_dir: Path | None = None, _fetch=None) -> dict[str, float]:
    """Market caps with a long TTL JSON cache. A failed/partial fetch keeps the
    last-good value per ticker (new values win on merge)."""
    _fetch = _fetch or Y.fetch_market_caps
    d = _dir(buffer_dir)
    path = d / "market_caps.json"
    cached: dict[str, float] = {}
    if path.exists():
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            cached = {}
    if not force and _fresh(path, ttl_hours) and set(tickers) <= set(cached):
        return {t: cached[t] for t in tickers if t in cached}
    fresh = _fetch(tickers)                       # dict, possibly partial/empty
    merged = {**cached, **fresh}                  # new wins, keep last-good for missing
    path.write_text(json.dumps(merged), encoding="utf-8")
    return {t: merged[t] for t in tickers if t in merged}


def _write_json_atomic(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, allow_nan=False), encoding="utf-8")
    tmp.replace(path)


def cached_dividends(tickers, ttl_hours: float = 24, force: bool = False,
                     buffer_dir: Path | None = None, _fetch=None) -> dict[str, list[list]]:
    """Per-share dividends {ticker: [[ex_date_iso, amount], ...]} with a long-TTL JSON cache.
    Every ticker asked for is remembered, so a fresh cache answers without the network even for
    names that pay nothing. A failed/partial fetch keeps the last-good list per ticker."""
    _fetch = _fetch or Y.fetch_dividends
    path = _dir(buffer_dir) / "dividends.json"
    tickers = sorted(set(tickers))
    try:
        cached = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        cached = {}
    data, asked = cached.get("data", {}), set(cached.get("asked", []))
    if force or not _fresh(path, ttl_hours) or not set(tickers) <= asked:
        try:
            fresh = _fetch(tickers) or {}
        except Exception:
            fresh = {}
        data = {**data, **{t: [[str(i.date()), float(v)] for i, v in s.items() if math.isfinite(float(v))] for t, s in fresh.items()}}
        asked |= set(tickers)
        _write_json_atomic(path, {"asked": sorted(asked), "data": data})
    return {t: data[t] for t in tickers if t in data}


def cached_quotes(tickers, *, force: bool = False, fresh_s: float = 0, buffer_dir: Path | None = None, _fetch=None):
    """Live quotes (price + previous close), buffered with a last-good fallback.

    Non-force with every ticker buffered → no network. Force (or a cold/missing ticker)
    → one batched fetch; a ticker whose fetch fails keeps its last-good quote and is
    reported in `stale`; a ticker never seen stays None. A fetch exception degrades
    every ticker to last-good. Never substitutes average cost. With `fresh_s` > 0 a force skips
    the tickers whose buffered quote was fetched successfully less than `fresh_s` seconds ago
    (served from the buffer, not stale) — the alert job must not re-ask what PORT just fetched.

    Returns (quotes {t: {"price","prev_close","date","ts"} | None}, stale {t: ts}, as_of).
    """
    tickers = list(tickers)
    if not tickers:
        return {}, {}, None
    _fetch = _fetch or Y.fetch_quotes
    path = _dir(buffer_dir) / "quotes.json"
    try:
        buf: dict[str, dict] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        buf = {}
    if not force and all(t in buf for t in tickers):
        quotes = {t: buf[t] for t in tickers}
        return quotes, {}, max(q["ts"] for q in quotes.values())
    due = [t for t in tickers if not (t in buf and age_s(buf[t].get("ts")) < fresh_s)]
    try:
        fresh = (_fetch(due) or {}) if due else {}
    except Exception:
        fresh = {}
    now = datetime.now().isoformat(timespec="seconds")
    quotes, stale = {}, {}
    for t in tickers:
        if t not in due:
            quotes[t] = buf[t]
            continue
        q = fresh.get(t)
        if q:
            buf[t] = {**q, "ts": now}
            quotes[t] = buf[t]
        elif t in buf:
            quotes[t] = buf[t]
            stale[t] = buf[t]["ts"]
        else:
            quotes[t] = None
    _write_json_atomic(path, buf)
    served = [q["ts"] for q in quotes.values() if q]
    return quotes, stale, (max(served) if served else None)


def never_quoted(tickers, buffer_dir: Path | None = None) -> set[str]:
    """Tickers the quote buffer holds no quote for — Yahoo never priced them (a listing it lacks).
    Read-only: no fetch, no write. Empty when there is no quote buffer yet: a cold start says
    nothing about a ticker."""
    path = (buffer_dir or BUFFER_DIR) / "quotes.json"
    if not path.exists():
        return set()
    buf = _read_json(path)
    return {t for t in tickers if not buf.get(t)}


def age_s(ts, now: datetime | None = None) -> float:
    """Seconds since a buffer timestamp (ISO, local time) — infinite when missing or unreadable."""
    try:
        return ((now or datetime.now()) - datetime.fromisoformat(ts)).total_seconds()
    except (TypeError, ValueError):
        return math.inf


def _read_json(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except Exception:
        raw = {}
    return raw if isinstance(raw, dict) else {}


def cached_movers(tickers, ttl_min: float = 15, buffer_dir: Path | None = None, _fetch=None):
    """Movers rows {ticker: {day, d5, volx, bar}} for one ticker set (one JSON file per set),
    fetched at most every `ttl_min` — also after a failure, so a dead Yahoo is not hammered each
    minute. A successful fetch replaces the rows (a name Yahoo stopped quoting drops out); a failed
    or empty one keeps the last-good rows.

    Returns (rows, as_of, stale): as_of = when the rows were fetched (None = never); stale = the
    latest attempt brought nothing, so the rows (if any) are last-good."""
    _fetch = _fetch or M.fetch_movers
    tickers = sorted(set(tickers))
    if not tickers:
        return {}, None, False
    h = hashlib.md5("|".join(tickers).encode()).hexdigest()[:12]
    path = _dir(buffer_dir) / f"movers_{h}.json"
    buf = _read_json(path)
    rows, at, tried = buf.get("rows") or {}, buf.get("at"), buf.get("tried")
    now = datetime.now()
    if age_s(tried, now) >= ttl_min * 60:             # missing or unreadable `tried` = expired
        try:
            fresh = _fetch(tickers) or {}
        except Exception:
            fresh = {}
        tried = now.isoformat(timespec="seconds")
        if fresh:
            rows, at = fresh, tried
        _write_json_atomic(path, {"rows": rows, "at": at, "tried": tried})
        for old in path.parent.glob("movers_*.json"):  # sets nobody asks for any more
            try:
                if old != path and now.timestamp() - old.stat().st_mtime > 7 * 86400:
                    old.unlink()
            except OSError:
                pass
    return rows, at, at != tried


def cached_eur_listing(isin: str, name: str, buffer_dir: Path | None = None, _search=None) -> str | None:
    """yahoo.eur_listing, kept in isins.json: a listing once found is kept; none found is asked again after a
    day (a search that failed too)."""
    _search = _search or Y.eur_listing
    path = _dir(buffer_dir) / "isins.json"
    buf = _read_json(path)
    hit = buf.get(isin) if isinstance(buf.get(isin), dict) else {}
    if hit.get("ticker") or age_s(hit.get("at"), datetime.now()) < 24 * 3600:
        return hit.get("ticker")
    try:
        ticker = _search(name, isin)
    except Exception:
        ticker = None
    _write_json_atomic(path, {**buf, isin: {"ticker": ticker, "at": datetime.now().isoformat(timespec="seconds")}})
    return ticker


def cached_events(tickers, ttl_hours: float = 24, buffer_dir: Path | None = None, _fetch=None,
                  today: date | None = None) -> dict[str, list[dict]]:
    """Upcoming events {ticker: [{date, kind, amount}]}, each ticker re-asked once per `ttl_hours`
    (a ticker whose calendar failed is re-asked after an hour, keeping its last-good events
    meanwhile). Events dated before today are never returned."""
    _fetch = _fetch or M.fetch_events
    tickers = sorted(set(tickers))
    path = _dir(buffer_dir) / "events.json"
    buf = _read_json(path)
    asked, data = buf.get("asked") or {}, buf.get("data") or {}
    now = datetime.now()
    due = [t for t in tickers
           if age_s(asked.get(t), now) >= ttl_hours * 3600]       # unreadable `asked` = expired
    if due:
        try:
            fresh = _fetch(due, today=today or now.date()) or {}
        except Exception:
            fresh = {}
        retry = (now - timedelta(hours=max(ttl_hours - 1, 0))).isoformat(timespec="seconds")
        data = {**data, **fresh}
        asked = {**asked, **{t: now.isoformat(timespec="seconds") if t in fresh else retry for t in due}}
        _write_json_atomic(path, {"asked": asked, "data": data})
    iso = (today or now.date()).isoformat()
    return {t: [e for e in data[t] if e.get("date", "") >= iso] for t in tickers if t in data}


def cached_info(tickers, *, buffer_dir: Path | None = None, _fetch=None, ttl_days: float = 30,
                fail_ttl_days: float = 1, max_asks: int | None = None) -> dict[str, dict | None]:
    """Yahoo identity {ticker: {name, sector, country} | None} (yahoo.fetch_info), one ticker at a
    time: a name is re-asked after `ttl_days`, a failed ask after `fail_ttl_days` — meanwhile the
    last-good answer (if any) is served. At most `max_asks` asks per call (sorted order): the rest stay
    due for the next call. Only asked for names no built-in map or TR universe knows."""
    _fetch = _fetch or Y.fetch_info
    tickers = sorted(set(tickers))
    if not tickers:
        return {}
    path = _dir(buffer_dir) / "info.json"
    buf = _read_json(path)
    data, asked = buf.get("data") or {}, buf.get("asked") or {}
    now = datetime.now()

    def due(t) -> bool:
        rec = asked.get(t)
        at, ok = rec if isinstance(rec, list) and len(rec) == 2 else (None, False)   # unreadable = due
        return age_s(at, now) >= (ttl_days if ok else fail_ttl_days) * 86400

    todo = [t for t in tickers if due(t)][:max_asks]
    for t in todo:
        try:
            got = _fetch(t)
        except Exception:
            got = None
        if got:
            data[t] = got
        asked[t] = [now.isoformat(timespec="seconds"), bool(got)]
    if todo:
        _write_json_atomic(path, {"data": data, "asked": asked})
    return {t: data.get(t) for t in tickers}
