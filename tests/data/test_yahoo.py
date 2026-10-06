"""fetch_quotes: last price + previous close per TR ticker from one batched download."""
import numpy as np
import pandas as pd
import pytest

import monitor.data.yahoo as Y
from tests import fakes_yf


def _frame(cols: dict[str, list[float]], end="2026-06-30") -> pd.DataFrame:
    n = len(next(iter(cols.values())))
    idx = pd.bdate_range(end=end, periods=n)
    df = pd.DataFrame({("Close", t): v for t, v in cols.items()}, index=idx)
    df.columns = pd.MultiIndex.from_tuples(df.columns)
    return df


@pytest.fixture
def fake_download(monkeypatch):
    calls = []

    def install(frame, live=None):
        def dl(tickers, **kw):
            calls.append((list(tickers) if not isinstance(tickers, str) else [tickers], kw))
            return frame
        monkeypatch.setattr(Y.yf, "download", dl)
        live = live or {}
        monkeypatch.setattr(Y, "_live_quote", lambda yft: live.get(yft))   # no network in tests
        return calls
    return install


def test_fetch_quotes_maps_tr_ticker_and_reads_last_two_closes(fake_download, monkeypatch):
    monkeypatch.setitem(Y.TICKER_MAP, "EUNL.F", "IWDA.AS")
    calls = fake_download(_frame({"IWDA.AS": [100.0, 101.0, 102.5], "NVD.F": [150.0, 155.0, 160.0]}))
    q = Y.fetch_quotes(["EUNL.F", "NVD.F"])
    assert q["EUNL.F"] == {"price": 102.5, "prev_close": 101.0, "date": "2026-06-30"}
    assert q["NVD.F"]["price"] == 160.0 and q["NVD.F"]["prev_close"] == 155.0
    assert len(calls) == 1                                   # one batched request


def test_fetch_quotes_uses_last_valid_bar_per_ticker(fake_download):
    fake_download(_frame({"AAA.F": [10.0, 11.0, np.nan], "BBB.F": [5.0, 6.0, 7.0]}))
    q = Y.fetch_quotes(["AAA.F", "BBB.F"])
    assert q["AAA.F"] == {"price": 11.0, "prev_close": 10.0, "date": "2026-06-29"}
    assert q["BBB.F"]["date"] == "2026-06-30"


def test_fetch_quotes_missing_or_nonpositive_is_none(fake_download):
    fake_download(_frame({"AAA.F": [0.0, -1.0, np.nan]}))
    q = Y.fetch_quotes(["AAA.F", "ZZZ.F"])
    assert q == {"AAA.F": None, "ZZZ.F": None}


def test_fetch_quotes_single_bar_has_no_prev_close(fake_download):
    fake_download(_frame({"AAA.F": [12.0]}))
    assert Y.fetch_quotes(["AAA.F"])["AAA.F"]["prev_close"] is None


def test_fetch_quotes_empty_input_makes_no_request(fake_download):
    calls = fake_download(_frame({"AAA.F": [1.0]}))
    assert Y.fetch_quotes([]) == {}
    assert calls == []


def test_fetch_quotes_converts_non_eur_with_fx(fake_download, monkeypatch):
    monkeypatch.setitem(Y.TICKER_CURRENCY, "TSCO.L", "GBP")
    fake_download(_frame({"TSCO.L": [3.0, 4.0], "GBPEUR=X": [1.2, 1.25]}))
    q = Y.fetch_quotes(["TSCO.L"])
    assert q["TSCO.L"]["price"] == pytest.approx(5.0)        # 4.0 * 1.25
    assert q["TSCO.L"]["prev_close"] == pytest.approx(3.75)  # 3.0 * 1.25 (today's rate)


def test_live_quote_overrides_unfinalized_daily_bar(fake_download):
    # Yahoo's batch download carries today's row with NaN closes until it finalizes the session
    fake_download(_frame({"NVD.F": [203.35, 205.45, np.nan]}, end="2026-10-02"),
                  live={"NVD.F": {"price": 207.85, "prev_close": 205.45, "date": "2026-10-02"}})
    assert Y.fetch_quotes(["NVD.F"])["NVD.F"] == {"price": 207.85, "prev_close": 205.45, "date": "2026-10-02"}


def test_live_failure_falls_back_to_daily_bar(fake_download):
    fake_download(_frame({"AAA.F": [10.0, 11.0], "BBB.F": [5.0, 6.0]}),
                  live={"BBB.F": {"price": 6.5, "prev_close": 6.0, "date": "2026-06-30"}})
    q = Y.fetch_quotes(["AAA.F", "BBB.F"])
    assert q["AAA.F"] == {"price": 11.0, "prev_close": 10.0, "date": "2026-06-30"}
    assert q["BBB.F"]["price"] == 6.5


def test_live_quote_without_prev_close_or_date_borrows_daily(fake_download):
    fake_download(_frame({"AAA.F": [10.0, 11.0]}),                       # last daily bar 2026-06-30
                  live={"AAA.F": {"price": 11.5, "prev_close": None, "date": None}})
    assert Y.fetch_quotes(["AAA.F"])["AAA.F"] == {"price": 11.5, "prev_close": 10.0, "date": "2026-06-30"}


def test_newer_live_session_takes_last_daily_close_as_prev(fake_download):
    fake_download(_frame({"AAA.F": [10.0, 11.0]}),
                  live={"AAA.F": {"price": 11.5, "prev_close": None, "date": "2026-07-01"}})
    assert Y.fetch_quotes(["AAA.F"])["AAA.F"] == {"price": 11.5, "prev_close": 11.0, "date": "2026-07-01"}


def test_live_quote_converted_with_fx(fake_download, monkeypatch):
    monkeypatch.setitem(Y.TICKER_CURRENCY, "TSCO.L", "GBP")
    fake_download(_frame({"TSCO.L": [3.0, 4.0], "GBPEUR=X": [1.2, 1.25]}),
                  live={"TSCO.L": {"price": 4.4, "prev_close": 4.0, "date": "2026-07-01"}})
    q = Y.fetch_quotes(["TSCO.L"])["TSCO.L"]
    assert q["price"] == pytest.approx(5.5) and q["prev_close"] == pytest.approx(5.0)


def test_live_quote_reads_fast_info_and_market_time(monkeypatch):
    import datetime as dt
    from types import SimpleNamespace
    when = int(dt.datetime(2026, 10, 2, 21, 59).timestamp())

    class T:
        def __init__(self, t):
            self.fast_info = SimpleNamespace(last_price=207.85, previous_close=205.45)
            self.history_metadata = {"regularMarketTime": when}
    monkeypatch.setattr(Y.yf, "Ticker", T)
    assert Y._live_quote("NVD.F") == {"price": 207.85, "prev_close": 205.45, "date": "2026-10-02"}


@pytest.mark.parametrize("when", [
    "epoch", "timestamp_tz", "timestamp_naive", "datetime_tz"])
def test_live_quote_market_time_in_any_shape_yfinance_returns(monkeypatch, when):
    """yfinance ≥ 1.5 returns regularMarketTime as a tz-aware pandas Timestamp, older versions an epoch
    int: both (and a datetime) give the session date — a fresh install must never lose every quote."""
    import datetime as dt
    from types import SimpleNamespace
    import pandas as pd
    t = pd.Timestamp("2026-10-02 17:30", tz="Europe/Berlin")
    value = {"epoch": int(t.timestamp()), "timestamp_tz": t, "timestamp_naive": t.tz_convert(None),
             "datetime_tz": t.to_pydatetime()}[when]

    class T:
        def __init__(self, x):
            self.fast_info = SimpleNamespace(last_price=10.0, previous_close=9.5)
            self.history_metadata = {"regularMarketTime": value}
    monkeypatch.setattr(Y.yf, "Ticker", T)
    assert Y._live_quote("SAP.DE") == {"price": 10.0, "prev_close": 9.5, "date": "2026-10-02"}


def test_live_quote_bad_price_or_error_is_none(monkeypatch):
    from types import SimpleNamespace

    class Zero:
        def __init__(self, t):
            self.fast_info = SimpleNamespace(last_price=0.0, previous_close=1.0)
            self.history_metadata = {}

    class Boom:
        def __init__(self, t):
            raise ConnectionError("yahoo down")
    monkeypatch.setattr(Y.yf, "Ticker", Zero)
    assert Y._live_quote("X") is None
    monkeypatch.setattr(Y.yf, "Ticker", Boom)
    assert Y._live_quote("X") is None


def test_fetch_dividends_maps_tickers_and_skips_empty(monkeypatch):
    fakes_yf.install(monkeypatch)
    monkeypatch.setattr(fakes_yf, "DIVIDENDS", {"IWDA.AS": pd.Series([0.3], index=pd.to_datetime(["2025-03-03"]))})
    monkeypatch.setitem(Y.TICKER_MAP, "EUNL.F", "IWDA.AS")
    out = Y.fetch_dividends(["EUNL.F", "AAA.F"])
    assert list(out) == ["EUNL.F"] and float(out["EUNL.F"].iloc[0]) == 0.3


def test_fetch_dividends_drops_non_finite_and_non_positive(monkeypatch):
    fakes_yf.install(monkeypatch)
    idx = pd.to_datetime(["2025-03-03", "2025-06-02", "2025-09-01", "2025-12-01"])
    monkeypatch.setattr(fakes_yf, "DIVIDENDS", {"AAA.F": pd.Series([0.5, float("nan"), float("inf"), 0.0], index=idx),
                                                "BBB.F": pd.Series([float("nan")], index=idx[:1])})
    out = Y.fetch_dividends(["AAA.F", "BBB.F"])
    assert list(out) == ["AAA.F"] and list(out["AAA.F"]) == [0.5]


@pytest.mark.parametrize("adjusted", [True, False])
def test_fetch_price_history_passes_the_adjustment_flag(fake_download, adjusted):
    calls = fake_download(_frame({"AAA.F": [10.0, 11.0]}))
    h = Y.fetch_price_history(["AAA.F"], adjusted=adjusted)
    assert calls[0][1]["auto_adjust"] is adjusted and list(h["AAA.F"]) == [10.0, 11.0]


def test_prev_close_is_the_daily_close_before_the_live_session(fake_download):
    # Milan lines: Yahoo's live "previous close" is not the official close (Intesa 2026-10-02:
    # live prev 6.41, official Thursday close 6.378) — the daily series is authoritative.
    fake_download(_frame({"ISP.MI": [6.645, 6.378, 6.328]}, end="2026-10-02"),
                  live={"ISP.MI": {"price": 6.328, "prev_close": 6.41, "date": "2026-10-02"}})
    q = Y.fetch_quotes(["ISP.MI"])["ISP.MI"]
    assert q["prev_close"] == 6.378 and q["price"] == 6.328


def test_live_prev_close_is_the_fallback_without_an_earlier_daily_bar(fake_download):
    fake_download(_frame({"AAA.F": [10.0]}, end="2026-06-30"),
                  live={"AAA.F": {"price": 10.5, "prev_close": 9.9, "date": "2026-06-30"}})
    assert Y.fetch_quotes(["AAA.F"])["AAA.F"]["prev_close"] == 9.9


def test_quote_batch_uses_real_closes(fake_download):
    calls = fake_download(_frame({"AAA.F": [10.0, 11.0]}))
    Y.fetch_quotes(["AAA.F"])
    assert calls[0][1]["auto_adjust"] is False


# ── fetch_info: who a ticker is (name / sector / country) for holdings no map knows ───────────
from monitor.data.yahoo import fetch_info as real_fetch_info   # bound before conftest stubs it


def _info_ticker(monkeypatch, infos):
    seen = []

    class T:
        def __init__(self, t):
            seen.append(t)
            self.t = t

        @property
        def info(self):
            v = infos[self.t]
            if isinstance(v, Exception):
                raise v
            return {k: x for k, x in v.items() if k != "_sectors"}

        @property
        def funds_data(self):                    # a fund's own sector weights (Yahoo's keys), when the test gives them
            v = infos[self.t]
            if not isinstance(v, dict) or "_sectors" not in v:
                raise RuntimeError("no fund data")
            from types import SimpleNamespace
            return SimpleNamespace(sector_weightings=v["_sectors"])
    monkeypatch.setattr(Y.yf, "Ticker", T)
    return seen


def test_fetch_info_reads_name_sector_country_through_the_ticker_map(monkeypatch):
    monkeypatch.setitem(Y.TICKER_MAP, "ZZZ.F", "ZZZ.DE")
    seen = _info_ticker(monkeypatch, {"ZZZ.DE": {"quoteType": "EQUITY", "shortName": "ZED AG NA O.N.",
                                                 "longName": "Zed AG", "sector": "Technology",
                                                 "country": "Germany"}})
    assert real_fetch_info("ZZZ.F") == {"name": "Zed AG", "sector": "Technology", "country": "Germany",
                                        "kind": "EQUITY", "sectors": None}
    assert seen == ["ZZZ.DE"]


def test_fetch_info_an_etf_has_a_name_but_no_sector_or_country(monkeypatch):
    _info_ticker(monkeypatch, {"VWCE.DE": {"quoteType": "ETF", "shortName": "Vanguard FTSE All-World",
                                           "country": "Ireland"}})
    assert real_fetch_info("VWCE.DE") == {"name": "Vanguard FTSE All-World", "sector": None, "country": None,
                                          "kind": "FUND", "sectors": None}


def test_fetch_info_a_fund_brings_its_own_sector_weights_in_the_books_names(monkeypatch):
    _info_ticker(monkeypatch, {"CSPX.AS": {"quoteType": "ETF", "longName": "iShares Core S&P 500 UCITS ETF",
                                           "_sectors": {"technology": 0.3, "financial_services": 0.1,
                                                        "realestate": 0.0}}})
    got = real_fetch_info("CSPX.AS")
    assert got["kind"] == "FUND" and got["sectors"] == {"Information Technology": 0.75, "Financials": 0.25}


def test_fetch_info_crypto_and_a_metal_etc_have_buckets_of_their_own(monkeypatch):
    _info_ticker(monkeypatch, {"BTC-EUR": {"quoteType": "CRYPTOCURRENCY", "shortName": "Bitcoin EUR"},
                               "PPFB.DE": {"quoteType": "EQUITY", "longName": "iShares Physical Gold ETC"},
                               "XAD5.DE": {"quoteType": "EQUITY", "longName": "Xtrackers Physical Silver EUR Hedged ETC"}})
    assert real_fetch_info("BTC-EUR") == {"name": "Bitcoin EUR", "sector": "Crypto", "country": "Crypto",
                                          "kind": "CRYPTO", "sectors": None}
    for t in ("PPFB.DE", "XAD5.DE"):
        got = real_fetch_info(t)
        assert (got["sector"], got["country"], got["kind"]) == ("Commodities", "Commodities", "COMMODITY"), t


def test_fetch_info_failure_or_empty_is_none(monkeypatch):
    _info_ticker(monkeypatch, {"A.F": RuntimeError("429"), "B.F": {}, "C.F": {"quoteType": "NONE"}})
    assert real_fetch_info("A.F") is None and real_fetch_info("B.F") is None and real_fetch_info("C.F") is None


def test_a_quote_carries_the_currency_its_price_is_in(fake_download, monkeypatch):
    """Prices are taken as euros. A line Yahoo quotes in another currency (a US listing held directly) is
    reported with its currency so the terminal can warn (CCY USD: AAPL); a currency the instrument map
    converts (TICKER_CURRENCY) is in euros once converted."""
    fake_download(_frame({"AAPL": [200.0, 201.0], "XUSD": [10.0, 11.0], "USDEUR=X": [0.9, 0.9]}),
                  live={"AAPL": {"price": 202.0, "prev_close": 201.0, "date": "2026-06-30", "ccy": "USD"},
                        "XUSD": {"price": 12.0, "prev_close": 11.0, "date": "2026-06-30", "ccy": "USD"}})
    monkeypatch.setitem(Y.TICKER_CURRENCY, "XUSD", "USD")
    q = Y.fetch_quotes(["AAPL", "XUSD", "AAA.F"])
    assert q["AAPL"]["ccy"] == "USD" and q["AAPL"]["price"] == 202.0         # never converted: flagged
    assert q["XUSD"]["ccy"] == "EUR" and q["XUSD"]["price"] == pytest.approx(12.0 * 0.9)
    assert q["AAA.F"] is None or q["AAA.F"].get("ccy") is None               # no live quote: unknown


def test_the_live_quote_reads_yahoos_currency(monkeypatch):
    fakes_yf.install(monkeypatch)
    monkeypatch.setitem(fakes_yf.CURRENCIES, "AAPL", "USD")
    assert Y._live_quote("AAPL")["ccy"] == "USD" and Y._live_quote("SAP.DE")["ccy"] == "EUR"
