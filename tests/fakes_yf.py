"""Deterministic stand-ins for yfinance.download / yfinance.Ticker. No network.

Each ticker gets a seeded geometric random walk from START to "today" (freeze time with
time_machine for reproducible values). install(monkeypatch) patches the yfinance module
itself, so every `import yfinance as yf` caller sees the fakes.
"""
from datetime import datetime
from types import SimpleNamespace

import numpy as np
import pandas as pd
import yfinance

DIVIDENDS: dict[str, pd.Series] = {}      # yf ticker -> per-share dividends by ex-date (tests set it)
CALENDARS: dict[str, dict] = {}           # yf ticker -> yfinance-style calendar dict (tests set it)
CURRENCIES: dict[str, str] = {}           # yf ticker -> its quote currency (tests set it; default EUR)
START = "2024-12-02"
_PERIOD_DAYS = {"5d": 7, "7d": 10, "1mo": 31, "1y": 366, "5y": 1830}


def series(ticker: str, end=None) -> pd.Series:
    # stdlib datetime.now (not pd.Timestamp.today) so time_machine freezing is guaranteed
    end = pd.Timestamp(end if end is not None else datetime.now()).normalize()
    idx = pd.bdate_range(START, end)
    seed = sum(map(ord, ticker))
    rng = np.random.default_rng(seed)
    base = 20.0 + seed % 180
    path = base * np.exp(np.cumsum(rng.normal(0.0004, 0.012, len(idx))))
    return pd.Series(path, index=idx).round(4)


def volume(ticker: str, end=None) -> pd.Series:
    """Seeded daily share volume on the same business days as series()."""
    s = series(ticker, end)
    rng = np.random.default_rng(sum(map(ord, ticker)) + 7)
    return pd.Series((1e5 * (0.5 + rng.random(len(s)))).round(), index=s.index)


def download(tickers, start=None, period=None, interval="1d", auto_adjust=True,
             progress=False, **kw) -> pd.DataFrame:
    tickers = [tickers] if isinstance(tickers, str) else list(tickers)
    cols = {}
    for t in tickers:
        s, v = series(t), volume(t)
        if start is not None:
            s = s[s.index >= pd.Timestamp(start)]
        elif period in _PERIOD_DAYS:
            s = s[s.index >= s.index[-1] - pd.Timedelta(days=_PERIOD_DAYS[period])]
        cols[("Close", t)] = s
        cols[("High", t)] = s * 1.01
        cols[("Low", t)] = s * 0.99
        cols[("Volume", t)] = v.reindex(s.index)
    df = pd.DataFrame(cols)
    df.columns = pd.MultiIndex.from_tuples(df.columns)
    return df


class Ticker:
    def __init__(self, ticker: str):
        self.ticker = ticker

    @property
    def fast_info(self):
        s = series(self.ticker)
        return SimpleNamespace(last_price=float(s.iloc[-1]), previous_close=float(s.iloc[-2]),
                               currency=CURRENCIES.get(self.ticker, "EUR"))

    @property
    def history_metadata(self) -> dict:
        last = series(self.ticker).index[-1]
        return {"regularMarketTime": int((last + pd.Timedelta(hours=17, minutes=30)).timestamp())}

    def history(self, period="5d", start=None, **kw) -> pd.DataFrame:
        s = series(self.ticker)
        if start is not None:
            s = s[s.index >= pd.Timestamp(start)]
        elif period in _PERIOD_DAYS:
            s = s[s.index >= s.index[-1] - pd.Timedelta(days=_PERIOD_DAYS[period])]
        return pd.DataFrame({"Close": s})

    @property
    def dividends(self) -> pd.Series:
        return DIVIDENDS.get(self.ticker, pd.Series(dtype=float))

    @property
    def calendar(self) -> dict:
        return CALENDARS.get(self.ticker, {})

    @property
    def info(self) -> dict:
        return {"quoteType": "EQUITY", "marketCap": 1e9 * (1 + sum(map(ord, self.ticker)) % 50)}


def install(monkeypatch) -> None:
    monkeypatch.setattr(yfinance, "download", download)
    monkeypatch.setattr(yfinance, "Ticker", Ticker)
