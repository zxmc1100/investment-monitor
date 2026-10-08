"""Market fetchers for MKT and the alert loop: daily movers (day %, 5-day %,
volume vs its 20-day median) from ONE batched download, and upcoming earnings / ex-dividend
dates from Yahoo's calendar. Pure of caching; see monitor.data.buffer (cached_movers,
cached_events). Tickers are mapped through TICKER_MAP like every other fetcher."""
from __future__ import annotations

import math
import warnings
from datetime import date, datetime

import pandas as pd
import yfinance as yf

from monitor.data.instruments import TICKER_MAP
from monitor.data.yahoo import fill_missing_sessions


def _field(raw: pd.DataFrame, name: str, tickers: list[str]) -> pd.DataFrame:
    """One OHLCV field as a frame with one column per Yahoo ticker, tz-naive."""
    if raw is None or raw.empty:
        return pd.DataFrame()
    if isinstance(raw.columns, pd.MultiIndex):
        out = raw[name] if name in raw.columns.get_level_values(0) else pd.DataFrame()
    else:
        out = raw[[name]].rename(columns={name: tickers[0]}) if name in raw.columns else pd.DataFrame()
    if isinstance(out, pd.Series):
        out = out.to_frame(name=tickers[0])
    if len(out) and out.index.tz is not None:
        out.index = out.index.tz_localize(None)
    return out


def _finite(x) -> float | None:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def movers_from_frame(close: pd.DataFrame, volume: pd.DataFrame | None) -> dict[str, dict]:
    """Per column: day % from the last two finite closes, 5-day % (6 closes), vol× = the last
    bar's volume / median of the 20 bars before it (None if that median is 0 or missing), and the
    bar date. A column with fewer than two finite closes is left out."""
    out = {}
    for t in close.columns:
        c = close[t].dropna()
        c = c[c > 0]
        if len(c) < 2:
            continue
        last = c.index[-1]
        volx = None
        if volume is not None and t in volume.columns:
            v = volume[t]
            prior = v[v.index < last].dropna().iloc[-20:]
            med = _finite(prior.median()) if len(prior) else None
            now = _finite(v.get(last))
            volx = now / med if med and now is not None else None
        out[t] = {"day": (float(c.iloc[-1]) / float(c.iloc[-2]) - 1) * 100,
                  "d5": (float(c.iloc[-1]) / float(c.iloc[-6]) - 1) * 100 if len(c) >= 6 else None,
                  "volx": volx, "bar": last.date().isoformat()}
    return out


def fetch_movers(tickers: list[str]) -> dict[str, dict]:
    """{ticker: {day, d5, volx, bar}} from one 1-month daily download (Close + Volume)."""
    if not tickers:
        return {}
    yf_map = {t: TICKER_MAP.get(t, t) for t in tickers}
    yf_tickers = list(dict.fromkeys(yf_map.values()))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        raw = yf.download(yf_tickers, period="1mo", interval="1d", auto_adjust=False, progress=False)
    close = fill_missing_sessions(_field(raw, "Close", yf_tickers), adjusted=False)   # a skipped session, rebuilt
    rows = movers_from_frame(close, _field(raw, "Volume", yf_tickers))
    return {t: rows[y] for t, y in yf_map.items() if y in rows}


def _as_date(x) -> date | None:
    try:
        if x is None or pd.isna(x):                   # NaT / NaN: Yahoo left the date blank
            return None
    except (TypeError, ValueError):                   # array-like: not a date
        return None
    if isinstance(x, datetime):
        return x.date()
    if isinstance(x, date):
        return x
    try:
        ts = pd.Timestamp(x)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(ts) else ts.date()            # "" and "NaT" parse to NaT


def events_from_calendar(cal, today: date) -> list[dict]:
    """Upcoming events from a yfinance `calendar` dict: the first earnings date ≥ today, and the
    ex-dividend date only when ≥ today (Yahoo often reports the last, past one)."""
    if not isinstance(cal, dict):
        return []
    out = []
    earn = cal.get("Earnings Date")
    dates = earn if isinstance(earn, (list, tuple)) else [earn]
    upcoming = sorted(d for d in map(_as_date, dates) if d is not None and d >= today)
    if upcoming:
        out.append({"date": upcoming[0].isoformat(), "kind": "EARNINGS", "amount": None})
    ex = _as_date(cal.get("Ex-Dividend Date"))
    if ex is not None and ex >= today:
        pay = _as_date(cal.get("Dividend Date"))             # the pay date, when Yahoo has it (home listings)
        out.append({"date": ex.isoformat(), "kind": "EX-DIV", "amount": None,
                    "pay": pay.isoformat() if pay is not None and pay >= ex else None})
    return out


def fetch_events(tickers: list[str], today: date | None = None) -> dict[str, list[dict]]:
    """{ticker: [{date, kind: EARNINGS|EX-DIV, amount}]} — an EX-DIV carries the last paid
    per-share dividend (listing currency; the calendar has no amount). A ticker whose calendar
    call fails is left out, so the cache keeps its last-good events; an empty calendar → []. A failed
    dividends call only blanks the EX-DIV amount."""
    today = today or date.today()
    out = {}
    for t in tickers:
        try:
            tk = yf.Ticker(TICKER_MAP.get(t, t))
            evs = events_from_calendar(tk.calendar, today)
        except Exception:
            continue
        if any(e["kind"] == "EX-DIV" for e in evs):
            try:
                d = tk.dividends
                last = _finite(d.iloc[-1]) if d is not None and len(d) else None
            except Exception:                         # no amount, but the dated events survive
                last = None
            for e in evs:
                if e["kind"] == "EX-DIV":
                    e["amount"] = last
        out[t] = evs
    return out
