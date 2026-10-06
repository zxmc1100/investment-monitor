"""Yahoo Finance access — the only module that turns the tickers in your CSV into network calls
for prices, caps and quotes. Pure of caching; see monitor.data.buffer for that."""

import math
import re
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


# EUR exchanges as Yahoo names them, and the ones an ISIN's country lists on first (others: Frankfurt, Xetra)
_EUR = {"MIL": ".MI", "GER": ".DE", "FRA": ".F", "PAR": ".PA", "AMS": ".AS", "MCE": ".MC", "BRU": ".BR",
        "VIE": ".VI", "LIS": ".LS", "HEL": ".HE", "ISE": ".IR", "STU": ".SG", "DUS": ".DU", "MUN": ".MU",
        "BER": ".BE", "HAM": ".HM"}
_HOME = {"IT": ("MIL",), "DE": ("GER", "FRA"), "FR": ("PAR",), "NL": ("AMS",), "ES": ("MCE",), "BE": ("BRU",),
         "AT": ("VIE",), "PT": ("LIS",), "FI": ("HEL",), "IE": ("AMS", "GER", "MIL"), "LU": ("GER", "AMS", "MIL")}


def eur_listing(name: str, isin: str) -> str | None:
    """A EUR-quoted Yahoo listing of the company or fund called `name` (a broker export's name for `isin`):
    Yahoo's search, its listings on EUR exchanges only, the ISIN country's own exchange first (IT → Milan),
    else Frankfurt / Xetra. None when it finds none — Yahoo searches by ISIN return the home (often USD)
    line only. Network."""
    if not (name or "").strip():
        return None
    quotes = yf.Search(name, max_results=20, news_count=0).quotes or []
    found = [(q.get("symbol"), q.get("exchange")) for q in quotes if q.get("exchange") in _EUR and q.get("symbol")]
    order = list(_HOME.get(str(isin)[:2].upper(), ())) + ["FRA", "GER"]
    for ex in order:
        for sym, at in found:
            if at == ex:
                return sym
    return found[0][0] if found else None


_US = {"NMS", "NGM", "NCM", "NAS", "NYQ", "NYS", "ASE", "PCX", "BTS"}


_FILLER = {"the", "inc", "corp", "corporation", "company", "co", "ltd", "limited", "plc", "holdings", "group"}


def _words(name) -> list[str]:
    """A company name's first two words that tell it apart — "The Coca-Cola Company" → [coca, cola]."""
    words = "".join(c if c.isalnum() else " " for c in str(name or "").lower()).split()
    return [w for w in words if w not in _FILLER][:2]


def _same_company(a, b) -> bool:
    wa, wb = _words(a), _words(b)
    n = min(len(wa), len(wb))
    return n > 0 and wa[:n] == wb[:n]


def us_listing(name: str) -> str | None:
    """The US line (a stock on a US exchange) of the company called `name` — a Frankfurt line's company → its
    New York or Nasdaq listing — by Yahoo's search: the first US stock whose name starts with the same words
    ("Western Express" is not "Western Tower"); else None. Network."""
    if not _words(name):
        return None
    plain = " ".join(re.sub(r"\([^)]*\)", " ", str(name)).split())       # "Acme Inc. (Acme Labs)": no hits
    for q in yf.Search(plain or name, max_results=10, news_count=0).quotes or []:
        if (q.get("exchange") in _US and q.get("quoteType") == "EQUITY" and q.get("symbol")
                and _same_company(name, q.get("longname") or q.get("shortname"))):
            return q["symbol"]
    return None


def dividend_pair(symbol: str) -> list[str] | None:
    """[ex, pay] of the latest dividend a line's Yahoo profile names (exDividendDate, dividendDate) — None when
    either is missing or the pay date is the one before (earlier than the ex date). Network."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            info = yf.Ticker(symbol).info or {}
    except Exception:
        return None
    day = lambda v: datetime.fromtimestamp(v).date().isoformat() if isinstance(v, (int, float)) else None  # noqa: E731
    ex, pay = day(info.get("exDividendDate")), day(info.get("dividendDate"))
    return [ex, pay] if ex and pay and pay >= ex else None


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


# a fund's sector keys as Yahoo's funds_data names them → the book's GICS names
_FUND_SECTORS = {"technology": "Information Technology", "financial_services": "Financials",
                 "healthcare": "Healthcare", "industrials": "Industrials", "consumer_cyclical": "Consumer Discretionary",
                 "communication_services": "Communication Services", "consumer_defensive": "Consumer Staples",
                 "energy": "Energy", "basic_materials": "Materials", "realestate": "Real Estate",
                 "utilities": "Utilities"}
_METAL = re.compile(r"\b(physical|etc)\b.*\b(gold|silver|platinum|palladium|precious metals?)\b|"
                    r"\b(gold|silver|platinum|palladium|precious metals?)\b.*\b(physical|etc)\b", re.IGNORECASE)


def _fund_sectors(tk) -> dict[str, float] | None:
    """A fund's own sector weights (Yahoo funds_data), in the book's names, summing to 1; None without them."""
    try:
        raw = tk.funds_data.sector_weightings or {}
    except Exception:
        return None
    w = {_FUND_SECTORS[k]: float(v) for k, v in raw.items() if k in _FUND_SECTORS and float(v or 0) > 0}
    tot = sum(w.values())
    return {k: round(v / tot, 4) for k, v in w.items()} if tot > 0 else None


def fetch_info(ticker: str) -> dict | None:
    """Who a ticker in your CSV is, from Yahoo's quote profile (mapped through TICKER_MAP):
    {name, sector, country, kind, sectors} — sector in Yahoo's naming ("Technology"), country spelled out.
    kind: FUND (its domicile is not where its money is: no country; `sectors` its own sector weights, when
    Yahoo has them), CRYPTO and COMMODITY (a physical-metal ETC) — buckets of their own, as sector and
    country — or EQUITY. None on failure or no profile."""
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tk = yf.Ticker(TICKER_MAP.get(ticker, ticker))
            info = tk.info
    except Exception:
        return None
    if not isinstance(info, dict):
        return None
    name = str(info.get("longName") or info.get("shortName") or "").strip()
    if not name:
        return None
    if info.get("quoteType") == "CRYPTOCURRENCY":
        return {"name": name, "sector": "Crypto", "country": "Crypto", "kind": "CRYPTO", "sectors": None}
    if _METAL.search(name):
        return {"name": name, "sector": "Commodities", "country": "Commodities", "kind": "COMMODITY", "sectors": None}
    if info.get("quoteType") in FUND_TYPES:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            sectors = _fund_sectors(tk)
        return {"name": name, "sector": None, "country": None, "kind": "FUND", "sectors": sectors}
    return {"name": name, "sector": info.get("sector") or None, "country": info.get("country") or None,
            "kind": "EQUITY", "sectors": None}


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
    the official close (one Milan line once: 6.41 vs 6.378), which skewed DAY %. None = no usable
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
