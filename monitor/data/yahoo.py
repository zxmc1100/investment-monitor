"""Yahoo Finance access — the only module that turns the tickers in your CSV into network calls
for prices, caps and quotes. Pure of caching; see monitor.data.buffer for that."""

import math
import warnings
from datetime import datetime

import numpy as np
import pandas as pd
import yfinance as yf

from monitor.data.instruments import TICKER_CURRENCY, TICKER_MAP


def fetch_price_history(tr_tickers: list[str], start: str | None = None,
                        period: str = "5y", adjusted: bool = True) -> pd.DataFrame:
    """
    Download Close for the given tickers as written in your CSV (mapped to their yfinance price
    tickers). Returns a DataFrame indexed by date with one column per CSV ticker (renamed
    back from the yfinance ticker).

    adjusted=True: dividend-adjusted closes (total return — for vol, beta, covariance).
    adjusted=False: the real close, split- but not dividend-adjusted — what a price shown to
    the user must be (an adjusted close lowers old prices by every later dividend).
    """
    yf_map = {tk: TICKER_MAP.get(tk, tk) for tk in tr_tickers}
    yf_tickers = list(dict.fromkeys(yf_map.values()))   # unique, order-preserving

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        kw = {"start": start} if start else {"period": period}
        raw = yf.download(yf_tickers, auto_adjust=adjusted, progress=False, **kw)
    close = raw["Close"] if "Close" in raw else raw
    if isinstance(close, pd.Series):
        close = close.to_frame(name=yf_tickers[0])
    if close.index.tz is not None:
        close.index = close.index.tz_localize(None)

    # Map yfinance columns back to the CSV tickers (the first one wins on collisions)
    cols = {}
    for tk in tr_tickers:
        yft = yf_map[tk]
        if yft in close.columns and tk not in cols:
            cols[tk] = close[yft]
    return pd.DataFrame(cols).sort_index()


def fetch_dividends(tr_tickers: list[str]) -> dict[str, pd.Series]:
    """Per-share cash dividends by ex-date for the tickers in your CSV (mapped through TICKER_MAP), in the
    listing's currency — EUR for every line in the book. A ticker whose lookup fails or that
    paid nothing is left out, so callers keep their last-good data."""
    out: dict[str, pd.Series] = {}
    for tk in tr_tickers:
        try:
            d = yf.Ticker(TICKER_MAP.get(tk, tk)).dividends
        except Exception:
            continue
        if d is None or len(d) == 0:
            continue
        d = d.astype(float).copy()
        d = d[np.isfinite(d) & (d > 0)]          # a NaN/inf/zero amount must never reach the JSON cache
        if len(d) == 0:
            continue
        if d.index.tz is not None:
            d.index = d.index.tz_localize(None)
        out[tk] = d.sort_index()
    return out


def fetch_market_caps(tr_tickers: list[str]) -> dict[str, float]:
    """Single-stock market cap per ticker (Frankfurt listings carry caps too).

    Deliberately ignores ETF ``totalAssets``: an ETF's AUM (tens of billions) is
    dwarfed by trillion-euro single-stock caps, so including it crushes a
    diversified ETF to ~0% in the Black-Litterman prior. ETFs return no cap here
    and the caller falls back to position value instead.
    """
    out = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for tk in tr_tickers:
            yft = TICKER_MAP.get(tk, tk)
            try:
                info = yf.Ticker(yft).info
                if info.get("quoteType") in ("ETF", "MUTUALFUND", "INDEX"):
                    continue                      # fund — caller uses position value
                cap = info.get("marketCap")
                if cap and cap > 0:
                    out[tk] = float(cap)
            except Exception:
                pass
    return out


FUND_TYPES = ("ETF", "MUTUALFUND", "INDEX")


def fetch_info(ticker: str) -> dict | None:
    """Who a ticker in your CSV is, from Yahoo's quote profile (mapped through TICKER_MAP):
    {name, sector, country} — sector in Yahoo's naming ("Technology"), country spelled out. A fund
    gets its name only: its domicile is not where its money is. None on failure or no profile."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            info = yf.Ticker(TICKER_MAP.get(ticker, ticker)).info
    except Exception:
        return None
    if not isinstance(info, dict):
        return None
    name = str(info.get("longName") or info.get("shortName") or "").strip()
    if not name:
        return None
    fund = info.get("quoteType") in FUND_TYPES
    sector, country = (info.get("sector") or None, info.get("country") or None) if not fund else (None, None)
    return {"name": name, "sector": sector, "country": country}


def _close_frame(raw: pd.DataFrame, tickers: list[str]) -> pd.DataFrame:
    """Close prices as one column per Yahoo ticker, tz-naive, whatever yfinance's shape."""
    if raw is None or raw.empty:
        return pd.DataFrame()
    if isinstance(raw.columns, pd.MultiIndex):
        close = raw["Close"] if "Close" in raw.columns.get_level_values(0) else pd.DataFrame()
    else:
        close = raw[["Close"]].rename(columns={"Close": tickers[0]}) if "Close" in raw else raw
    if isinstance(close, pd.Series):
        close = close.to_frame(name=tickers[0])
    if close.index.tz is not None:
        close.index = close.index.tz_localize(None)
    return close


def _session_date(when) -> str | None:
    """Local date of Yahoo's regularMarketTime: an epoch int (yfinance < 1.5), a pandas Timestamp
    (tz-aware from 1.5 on; naive read as UTC) or a datetime. None when absent or unreadable."""
    if when is None or when == "":
        return None
    try:
        epoch = float(when) if isinstance(when, (int, float)) else pd.Timestamp(when).timestamp()
        return datetime.fromtimestamp(epoch).date().isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _live_quote(yft: str) -> dict | None:
    """Live last price + previous close for one Yahoo ticker, in its native currency (`ccy`, Yahoo's
    quote currency — absent when Yahoo does not say).

    Yahoo's quote metadata is what a broker app shows during and after the session; the
    batch daily download lags it — today's row carries NaN closes until Yahoo finalizes the
    session, so a daily-only quote silently serves the previous day. None on any failure.
    """
    try:
        tk = yf.Ticker(yft)
        last, prev = tk.fast_info.last_price, tk.fast_info.previous_close
        when = (tk.history_metadata or {}).get("regularMarketTime")
    except Exception:
        return None
    try:
        ccy = getattr(tk.fast_info, "currency", None)
    except Exception:
        ccy = None
    if last is None or not math.isfinite(last) or last <= 0:
        return None
    ok_prev = prev is not None and math.isfinite(prev) and prev > 0
    out = {"price": float(last), "prev_close": float(prev) if ok_prev else None, "date": _session_date(when)}
    if isinstance(ccy, str) and ccy:
        out["ccy"] = ccy
    return out


def fetch_quotes(tr_tickers: list[str]) -> dict[str, dict | None]:
    """Last price and previous close in EUR per ticker in your CSV.

    Returns {ticker: {"price", "prev_close", "date"[, "ccy"]} | None}; `ccy` = the currency the price is
    in when known: EUR once converted (a TICKER_CURRENCY line), else Yahoo's quote currency — prices are
    taken as euros, so a non-EUR `ccy` is a line the terminal warns about. The live quote (`_live_quote`,
    one call per ticker) supplies the price; ONE batched daily download of real (unadjusted)
    closes supplies FX rates, the fallback for any ticker whose live quote fails (its own last
    valid bar), and the previous close: the official close of the last session before the live
    quote's date. Yahoo's live "previous close" is only the fallback — for Milan lines it is not
    the official close (Intesa 2026-10-02: 6.41 vs 6.378), which skewed DAY %. None = no usable
    price — callers keep their last-good value and flag it stale, never substitute cost.
    """
    if not tr_tickers:
        return {}
    yf_map = {tk: TICKER_MAP.get(tk, tk) for tk in tr_tickers}
    yf_tickers = list(dict.fromkeys(yf_map.values()))
    ccys = sorted({TICKER_CURRENCY[y] for y in yf_tickers if TICKER_CURRENCY.get(y, "EUR") != "EUR"})
    fx_tickers = [f"{c}EUR=X" for c in ccys]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        raw = yf.download(yf_tickers + fx_tickers, period="7d", interval="1d",
                          auto_adjust=False, progress=False)
    close = _close_frame(raw, yf_tickers + fx_tickers)

    def _valid(col: str) -> pd.Series:
        if col not in close.columns:
            return pd.Series(dtype=float)
        s = close[col].dropna()
        return s[s > 0]

    fx = {c: float(_valid(f"{c}EUR=X").iloc[-1]) for c in ccys if not _valid(f"{c}EUR=X").empty}
    live = {yft: _live_quote(yft) for yft in yf_tickers}
    out: dict[str, dict | None] = {}
    for tk, yft in yf_map.items():
        s = _valid(yft)
        ccy = TICKER_CURRENCY.get(yft, "EUR")
        rate = 1.0 if ccy == "EUR" else fx.get(ccy)
        if rate is None:
            out[tk] = None
            continue
        daily = None if s.empty else {
            "price": round(float(s.iloc[-1]) * rate, 4),
            "prev_close": round(float(s.iloc[-2]) * rate, 4) if len(s) > 1 else None,
            "date": str(s.index[-1].date())}
        q = live.get(yft)
        if q is None:
            out[tk] = daily and {**daily, **({"ccy": "EUR"} if ccy != "EUR" else {})}
            continue
        before = s[s.index < pd.Timestamp(q["date"])] if q["date"] else s.iloc[:0]
        if len(before):                     # the official close of the session before the live one
            prev = round(float(before.iloc[-1]) * rate, 4)
        else:
            prev = round(q["prev_close"] * rate, 4) if q["prev_close"] is not None else None
        if prev is None and daily:          # borrow: a newer live session's previous close is daily's last
            newer = q["date"] and q["date"] > daily["date"]
            prev = daily["price"] if newer else daily["prev_close"]
        out[tk] = {"price": round(q["price"] * rate, 4), "prev_close": prev,
                   "date": q["date"] or (daily or {}).get("date")}
        if (held_in := "EUR" if ccy != "EUR" else q.get("ccy")) is not None:
            out[tk]["ccy"] = held_in
    return out
