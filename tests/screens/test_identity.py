"""Who a ticker is — name, sector, country — with no code edit for a new holding:
settings (merged into the maps) → built-in maps → TR universe (fixture) → Yahoo profile (faked,
cached) → the ticker / "Unknown". Sectors and countries come out in the book's own vocabulary."""
import pytest

from monitor.data import buffer as BUF
from monitor.data import yahoo as Y
from monitor.data.instruments import COMPANY_NAMES
from monitor.portfolio import meta as M
from monitor.screens.identity import identify


@pytest.fixture(autouse=True)
def _mapped_line(monkeypatch):
    """A fixture line the maps know (as your settings would): the built-ins carry no one's holdings."""
    monkeypatch.setitem(COMPANY_NAMES, "MAP.F", "Mapped Corp")
    monkeypatch.setitem(M.PORTFOLIO_SECTOR_MAP, "MAP.F", "Information Technology")
    monkeypatch.setitem(M.PORTFOLIO_COUNTRY_MAP, "MAP.F", "United States")


@pytest.fixture
def yahoo(monkeypatch):
    """Scripted Yahoo profiles; records every ask."""
    asked, profiles = [], {}

    def fetch(t):
        asked.append(t)
        return profiles.get(t)
    monkeypatch.setattr(Y, "fetch_info", fetch)
    return asked, profiles


def test_mapped_lines_need_no_lookup(yahoo, tmp_path):
    got = identify(["MAP.F"], buffer_dir=tmp_path)
    assert got == {"MAP.F": {"name": "Mapped Corp", "sector": "Information Technology", "country": "United States"}}
    assert yahoo[0] == []


def test_settings_entries_win_because_they_are_merged_into_the_maps(yahoo, tmp_path, monkeypatch):
    monkeypatch.setitem(COMPANY_NAMES, "RHM.DE", "Rheinmetall (mine)")         # over the TR universe's name
    monkeypatch.setitem(M.PORTFOLIO_SECTOR_MAP, "RHM.DE", "Defence")
    got = identify(["RHM.DE"], buffer_dir=tmp_path)["RHM.DE"]
    assert got["name"] == "Rheinmetall (mine)" and got["sector"] == "Defence" and got["country"] == "Germany"


def test_the_tr_universe_names_a_new_holding_in_the_books_vocabulary(yahoo, tmp_path):
    got = identify(["RHM.DE", "GLE.PA"], buffer_dir=tmp_path)
    assert got["RHM.DE"] == {"name": "Rheinmetall", "sector": "Industrials", "country": "Germany"}
    assert got["GLE.PA"] == {"name": "Société Générale", "sector": "Financials", "country": "France"}
    assert yahoo[0] == []                                   # complete without Yahoo


def test_yahoo_fills_what_the_universe_lacks_and_is_cached(yahoo, tmp_path):
    asked, profiles = yahoo
    profiles["BAS.DE"] = {"name": "BASF SE", "sector": "Basic Materials", "country": "Germany"}
    profiles["ZZZ.DE"] = {"name": "Zed AG", "sector": "Technology", "country": "Germany"}
    got = identify(["BAS.DE", "ZZZ.DE"], buffer_dir=tmp_path)
    assert got["BAS.DE"] == {"name": "BASF", "sector": "Materials", "country": "Germany"}   # universe name kept
    assert got["ZZZ.DE"] == {"name": "Zed AG", "sector": "Information Technology", "country": "Germany"}
    assert sorted(asked) == ["BAS.DE", "ZZZ.DE"]
    identify(["BAS.DE", "ZZZ.DE"], buffer_dir=tmp_path)
    assert len(asked) == 2                                  # cached (local/buffer/info.json, 30 days)
    assert (tmp_path / "info.json").exists()


def test_nothing_anywhere_is_the_ticker_and_unknown(yahoo, tmp_path):
    assert identify(["QQQ.F"], buffer_dir=tmp_path)["QQQ.F"] == {"name": "QQQ.F", "sector": "Unknown",
                                                                 "country": "Unknown"}


def test_only_the_needed_fields_send_a_name_to_yahoo(yahoo, tmp_path):
    asked, _ = yahoo
    assert identify(["BAS.DE"], buffer_dir=tmp_path, need=("name",))["BAS.DE"]["name"] == "BASF"
    assert asked == []                                      # the universe already named it


def test_watchlist_names_come_after_the_maps(yahoo, tmp_path):
    got = identify(["FNTN.DE", "MAP.F"], buffer_dir=tmp_path, names={"FNTN.DE": "freenet AG", "MAP.F": "x"},
                   need=("name",))
    assert got["FNTN.DE"]["name"] == "freenet AG" and got["MAP.F"]["name"] == "Mapped Corp"


def test_a_look_through_etf_is_never_asked(yahoo, tmp_path):
    identify(["IWDA.AS", "EUNL.F"], buffer_dir=tmp_path)
    assert yahoo[0] == []


def test_vocabulary_helpers():
    assert M.sector_name("Technology") == "Information Technology"
    assert M.sector_name("Consumer Cyclical") == "Consumer Discretionary"
    assert M.sector_name("Healthcare") == "Healthcare"
    assert M.sector_name("—") is None and M.sector_name("Unknown") is None and M.sector_name(None) is None
    assert M.country_name("DE") == "Germany" and M.country_name("US") == "United States"
    assert M.country_name("Germany") == "Germany" and M.country_name("") is None
    assert all(M.country_name(c) in M.REGION_OF for c in M.COUNTRY_CODES)


def test_exposure_breakdown_takes_resolved_labels():
    rows = M.exposure_breakdown({"RHM.DE": 0.6, "MAP.F": 0.4}, "sector", labels={"RHM.DE": "Industrials"})
    by = {r["label"]: r["w"] for r in rows}
    assert by["Industrials"] == pytest.approx(0.6) and by["Information Technology"] == pytest.approx(0.4)
    rows = M.exposure_breakdown({"RHM.DE": 1.0}, "country", labels={"RHM.DE": "Germany"})
    assert rows[0]["label"] == "Germany"


def test_yahoo_asks_are_capped_per_run_so_first_paint_stays_fast(yahoo, tmp_path):
    from monitor.screens import identity
    asked, _ = yahoo
    new = [f"N{i}.DE" for i in range(identity.MAX_ASKS + 2)]               # nobody knows any of them
    first = identify(new, buffer_dir=tmp_path)
    assert len(asked) == identity.MAX_ASKS
    assert all(r["name"] == t for t, r in first.items())                     # the rest show the ticker meanwhile
    identify(new, buffer_dir=tmp_path)
    assert sorted(asked) == sorted(new)                                      # the next run asks the rest
    identify(new, buffer_dir=tmp_path)
    assert len(asked) == len(new)                                            # then all cached


def test_cached_info_asks_at_most_max_asks(tmp_path):
    calls = []
    BUF.cached_info(["A.F", "B.F", "C.F"], buffer_dir=tmp_path, _fetch=lambda t: calls.append(t), max_asks=2)
    assert calls == ["A.F", "B.F"]


def test_max_asks_0_reads_what_was_asked_before_and_never_asks(yahoo, tmp_path):
    """A screen that must not touch the network (TRADES) still gets the names an earlier run asked for."""
    asked, profiles = yahoo
    profiles["ZZZ.F"] = {"name": "Zed Corp", "sector": "Industrials", "country": "Germany"}
    assert identify(["ZZZ.F", "YYY.F"], buffer_dir=tmp_path, max_asks=0)["ZZZ.F"]["name"] == "ZZZ.F"
    assert asked == []
    identify(["ZZZ.F"], buffer_dir=tmp_path)
    assert identify(["ZZZ.F"], buffer_dir=tmp_path, max_asks=0)["ZZZ.F"]["name"] == "Zed Corp" and asked == ["ZZZ.F"]
