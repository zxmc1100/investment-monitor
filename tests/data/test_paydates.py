"""Dividend pay dates where free sources have them: Nasdaq's dividend history (Nasdaq-listed US companies),
Yahoo's latest ex/pay pair of the company's US line (NYSE too), found from a Frankfurt line by its company name.
Parsers are pure; the network calls are faked. Companies and tickers are invented."""
from types import SimpleNamespace

from monitor.data import nasdaq
from monitor.data import yahoo as Y
from monitor.data.buffer import cached_pay_dates

US_LISTING, DIVIDEND_PAIR = Y.us_listing, Y.dividend_pair      # the real ones: conftest stubs them per test
US = "United States"


def test_nasdaq_history_reads_ex_and_pay_dates():
    body = {"data": {"dividends": {"rows": [
        {"exOrEffDate": "09/12/2026", "paymentDate": "10/03/2026", "amount": "$0.25"},
        {"exOrEffDate": "06/05/2026", "paymentDate": "06/27/2026", "amount": "$0.25"},
        {"exOrEffDate": "N/A", "paymentDate": "N/A", "amount": "N/A"}]}}}
    assert nasdaq.parse_dividends(body) == [["2026-06-05", "2026-06-27"], ["2026-09-12", "2026-10-03"]]
    assert nasdaq.parse_dividends({"data": {"dividends": {"rows": None}}}) == []
    assert nasdaq.parse_dividends({}) == []


def test_the_us_line_is_found_by_the_company_name(monkeypatch):
    quotes = [{"symbol": "NWD.F", "exchange": "FRA", "quoteType": "EQUITY", "shortname": "NORTHWIND CORP."},
              {"symbol": "NWND", "exchange": "NMS", "quoteType": "EQUITY", "shortname": "Northwind Corporation"},
              {"symbol": "NWDL", "exchange": "NGM", "quoteType": "ETF", "shortname": "Leveraged 2x Long NWND"}]
    monkeypatch.setattr(Y.yf, "Search", lambda q, **kw: SimpleNamespace(quotes=quotes))
    assert US_LISTING("Northwind Corporation") == "NWND"
    assert US_LISTING("Northwind Corporation (Northwind Traders)") == "NWND"     # a bracketed alias is dropped
    monkeypatch.setattr(Y.yf, "Search", lambda q, **kw: SimpleNamespace(quotes=[
        {"symbol": "XYZ", "exchange": "NYQ", "quoteType": "EQUITY", "shortname": "Other Company Inc."}]))
    assert US_LISTING("Northwind Corp") is None                          # a different company: none
    monkeypatch.setattr(Y.yf, "Search", lambda q, **kw: SimpleNamespace(quotes=[
        {"symbol": "WTW", "exchange": "NYQ", "quoteType": "EQUITY", "longname": "Western Tower Corporation"},
        {"symbol": "WXP", "exchange": "NYQ", "quoteType": "EQUITY", "longname": "Western Express Company"}]))
    assert US_LISTING("WESTERN EXPRESS CO. DL -,20") == "WXP"            # two words, not one


def test_yahoos_latest_pair_comes_from_the_us_lines_profile(monkeypatch):
    import datetime as dt
    ts = lambda y, m, d: int(dt.datetime(y, m, d, 12).timestamp())      # noqa: E731
    infos = {"CTSO": {"exDividendDate": ts(2026, 10, 9), "dividendDate": ts(2026, 10, 23)},
             "OLD": {"exDividendDate": ts(2026, 10, 9), "dividendDate": ts(2026, 7, 24)},     # the last one's pay date
             "NONE": {}}
    monkeypatch.setattr(Y.yf, "Ticker", lambda t: SimpleNamespace(info=infos[t]))
    assert DIVIDEND_PAIR("CTSO") == ["2026-10-09", "2026-10-23"]
    assert DIVIDEND_PAIR("OLD") is None and DIVIDEND_PAIR("NONE") is None


def test_pay_dates_are_gathered_per_line_cached_and_asked_a_few_at_a_time(tmp_path):
    asked = []

    def home(name):
        asked.append(name)
        return {"Northwind Corporation": "NWND", "Contoso Inc.": "CTSO"}.get(name)
    history = {"NWND": [["2026-06-05", "2026-06-27"]]}
    pairs = {"NWND": ["2026-09-12", "2026-10-03"], "CTSO": ["2026-10-09", "2026-10-23"]}
    kw = dict(buffer_dir=tmp_path, _home=home, _history=lambda s: history.get(s, []), _pair=lambda s: pairs.get(s))
    lines = {"NWD.F": {"name": "Northwind Corporation", "country": US},
             "CTS.F": {"name": "Contoso Inc.", "country": US},
             "FAB.MI": {"name": "Fabrikam S.p.A.", "country": "Italy"}}
    got = cached_pay_dates(lines, max_asks=1, **kw)
    assert got["CTS.F"] == {"country": US, "pairs": [["2026-10-09", "2026-10-23"]]}
    assert got["NWD.F"]["pairs"] == []                                     # one ask this run: the rest next run
    assert got["FAB.MI"] == {"country": "Italy", "pairs": []} and "Fabrikam S.p.A." not in asked   # not searched
    got = cached_pay_dates(lines, max_asks=1, **kw)
    assert got["NWD.F"]["pairs"] == [["2026-06-05", "2026-06-27"], ["2026-09-12", "2026-10-03"]]
    assert asked.count("Contoso Inc.") == 1                                # kept: not asked again
    assert cached_pay_dates(None, buffer_dir=tmp_path) == got              # what is stored, no network


def test_the_lines_to_search_can_be_named(tmp_path):
    asked = []
    lines = {"TSH.F": {"name": "Tailspin Holding", "country": "Netherlands"},
             "NWD.F": {"name": "Northwind Corporation", "country": US}}
    cached_pay_dates(lines, ask={"TSH.F"}, buffer_dir=tmp_path, _home=lambda n: asked.append(n),
                     _history=lambda s: [], _pair=lambda s: None)
    assert asked == ["Tailspin Holding"]


def test_a_found_pair_is_kept_when_a_later_answer_lacks_it(tmp_path):
    answers = iter([[["2026-06-05", "2026-06-27"]], []])
    kw = dict(buffer_dir=tmp_path, _home=lambda n: "NWND", _history=lambda s: next(answers), _pair=lambda s: None)
    lines = {"NWD.F": {"name": "Northwind Corporation", "country": US}}
    cached_pay_dates(lines, **kw)
    got = cached_pay_dates(lines, ttl_days=0, **kw)                        # asked again: Nasdaq answered nothing
    assert got["NWD.F"]["pairs"] == [["2026-06-05", "2026-06-27"]]
