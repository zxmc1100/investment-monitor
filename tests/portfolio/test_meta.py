"""Allocation look-through: sectors and countries, each position's share, the ETF split by index weights.
Fixture lines (AAA.F …) are mapped here — the built-in maps carry no one's holdings."""
import pytest

from monitor.portfolio import meta as M

LINES = {"AAA.F": ("Information Technology", "United States"), "BBB.F": ("Financials", "Italy"),
         "CCC.F": ("Information Technology", "Taiwan"), "DDD.F": ("Information Technology", "Netherlands")}


@pytest.fixture(autouse=True)
def _fixture_lines(monkeypatch):
    for t, (sector, country) in LINES.items():
        monkeypatch.setitem(M.PORTFOLIO_SECTOR_MAP, t, sector)
        monkeypatch.setitem(M.PORTFOLIO_COUNTRY_MAP, t, country)


def by_label(rows):
    return {r["label"]: r for r in rows}


def test_sector_breakdown_lists_every_sector_and_the_positions_behind_it():
    rows = M.exposure_breakdown({"AAA.F": 0.5, "IWDA.AS": 0.5}, "sector")
    s = by_label(rows)
    assert set(M.ALL_SECTORS) <= set(s)                                   # all 11, zeros included
    it_etf = 0.5 * M.ETF_SECTOR_WEIGHTS["IWDA.AS"]["Information Technology"] / sum(M.ETF_SECTOR_WEIGHTS["IWDA.AS"].values())
    assert s["Information Technology"]["w"] == pytest.approx(0.5 + it_etf)
    assert s["Information Technology"]["parts"] == [("AAA.F", 0.5, False), ("IWDA.AS", pytest.approx(it_etf), True)]
    assert sum(r["w"] for r in rows) == pytest.approx(1.0)
    assert [r["w"] for r in rows] == sorted((r["w"] for r in rows), reverse=True)


def test_country_breakdown_looks_through_the_etf():
    rows = M.exposure_breakdown({"AAA.F": 0.5, "BBB.F": 0.2, "IWDA.AS": 0.3}, "country")
    c = by_label(rows)
    tot = sum(M.ETF_COUNTRY_WEIGHTS["IWDA.AS"].values())
    assert c["United States"]["w"] == pytest.approx(0.5 + 0.3 * M.ETF_COUNTRY_WEIGHTS["IWDA.AS"]["United States"] / tot)
    assert c["Italy"]["parts"][0] == ("BBB.F", 0.2, False)
    assert sum(r["w"] for r in rows) == pytest.approx(1.0)


def test_unknown_lines_are_reported_not_guessed():
    s = by_label(M.exposure_breakdown({"ZZZ.F": 1.0}, "sector"))
    c = by_label(M.exposure_breakdown({"ZZZ.F": 1.0}, "country"))
    assert s["Unknown"]["w"] == pytest.approx(1.0) and c["Unknown"]["w"] == pytest.approx(1.0)


def test_regions_sum_the_countries():
    rows = M.exposure_breakdown({"AAA.F": 0.4, "CCC.F": 0.2, "DDD.F": 0.1, "IWDA.AS": 0.3}, "country")
    reg = by_label(M.region_totals(rows))
    assert reg["Emerging"]["w"] == pytest.approx(0.2)                     # Taiwan
    assert sum(r["w"] for r in reg.values()) == pytest.approx(1.0)
    assert M.REGION_OF["Netherlands"] == "Europe" and M.REGION_OF["Japan"] == "Asia-Pacific"
    assert all(c in M.REGION_OF for c in M.ETF_COUNTRY_WEIGHTS["IWDA.AS"])
    assert all(c in M.REGION_OF for c in M.PORTFOLIO_COUNTRY_MAP.values())


def test_a_mapped_line_lands_in_its_sector():
    s = by_label(M.exposure_breakdown({"BBB.F": 1.0}, "sector"))
    assert s["Financials"]["w"] == pytest.approx(1.0)


# ── any fund looked through: countries by the index its name tracks ────────────────────────────────

@pytest.mark.parametrize("name, top", [
    ("iShares Core S&P 500 UCITS ETF USD (Acc)", "United States"),
    ("Invesco EQQQ Nasdaq-100 UCITS ETF", "United States"),
    ("Amundi Index Solutions - Amundi MSCI Emerging Markets", "China"),
    ("iShares Core MSCI EM IMI UCITS ETF USD (Acc)", "China"),
    ("Franklin FTSE China UCITS ETF", "China"),
    ("Vanguard FTSE All-World UCITS ETF", "United States"),
    ("iShares Core EURO STOXX 50 UCITS ETF", "France"),
    ("Xtrackers MSCI Europe UCITS ETF", "United Kingdom"),
    ("iShares Core DAX UCITS ETF (DE)", "Germany"),
])
def test_an_index_funds_countries_come_from_the_index_it_tracks(name, top):
    w = M.index_countries(name)
    assert sum(w.values()) == pytest.approx(1.0) and max(w, key=w.get) == top
    assert all(c in M.REGION_OF for c in w)


def test_a_fund_whose_index_is_not_known_has_no_countries():
    assert M.index_countries("Some Thematic Robotics ETF") is None


def test_a_looked_through_line_spreads_by_its_weights_and_says_so():
    split = {"CSPX.AS": {"Information Technology": 0.6, "Financials": 0.4}}
    s = by_label(M.exposure_breakdown({"CSPX.AS": 0.5, "BBB.F": 0.5}, "sector", splits=split))
    assert s["Information Technology"]["w"] == pytest.approx(0.3)
    assert s["Financials"]["w"] == pytest.approx(0.2 + 0.5)
    assert ("CSPX.AS", pytest.approx(0.3), True) in s["Information Technology"]["parts"]


def test_crypto_and_commodities_have_regions_of_their_own():
    assert M.REGION_OF["Crypto"] == "Crypto" and M.REGION_OF["Commodities"] == "Commodities"
