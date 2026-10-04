"""Universe lookup over the 11-name fixture universe (tests/fixtures/universe)."""
import json

import pandas as pd

from monitor.universe import lookup


def tickers(rows):
    return [r["ticker"] for r in rows]


def test_exact_ticker_and_isin_win():
    assert lookup.search("rhm.de") == [{"ticker": "RHM.DE", "name": "Rheinmetall", "sector": "Industrials",
                                        "country": "DE"}]
    assert tickers(lookup.search("US0378331005")) == ["AAPL"]


def test_name_search_ranks_by_turnover_and_keeps_no_meta_names():
    # the ADR has no universe_meta row: still searchable, ranked after the known turnover
    assert tickers(lookup.search("rheinmetall")) == ["RHM.DE", "RNMBY"]
    assert tickers(lookup.search("free")) == ["FNTN.DE"]


def test_accents_and_case_fold():
    assert tickers(lookup.search("societe gen")) == ["GLE.PA"]
    assert tickers(lookup.search("SOCIÉTÉ")) == ["GLE.PA"]


def test_ticker_prefix_matches():
    assert tickers(lookup.search("rhm")) == ["RHM.DE"]


def test_no_match_blank_and_limit():
    assert lookup.search("zzzz") == [] and lookup.search("  ") == []
    assert len(lookup.search("a", n=2)) == 2


def test_delisted_demoted_and_unmapped_are_excluded():
    assert lookup.search("deadco") == [] and lookup.search("DEMO") == []
    assert lookup.search("no ticker") == []
    assert not lookup.is_tradeable("DEAD") and not lookup.is_tradeable("DEMO")


def test_liquid_ranks_live_names_with_turnover():
    assert lookup.liquid(3) == ["NVDA", "AAPL", "RHM.DE"]
    assert "DEAD" not in lookup.liquid() and "RNMBY" not in lookup.liquid()


def test_is_tradeable_and_info():
    assert lookup.is_tradeable("rhm.de") and lookup.is_tradeable("FNTN.DE")
    assert not lookup.is_tradeable("NVD.F") and not lookup.is_tradeable("")
    assert lookup.info("BAS.DE") == {"ticker": "BAS.DE", "name": "BASF", "sector": "—", "country": "DE"}


def test_missing_files_mean_an_empty_universe(tmp_path):
    assert lookup.search("apple", root=tmp_path) == []
    assert lookup.liquid(root=tmp_path) == [] and not lookup.is_tradeable("AAPL", root=tmp_path)


def test_build_without_meta_marks_everything_live():
    uni = pd.DataFrame({"isin": ["A1", "B2"], "name": ["Alpha", "Beta"], "country": ["DE", "US"]})
    df = lookup.build(uni, {"A1": "ALP.DE", "B2": "beta"}, None, {})
    assert df["ticker"].tolist() == ["ALP.DE", "BETA"] and df["live"].all()
    assert df["sector"].tolist() == ["—", "—"] and df["med_turnover"].isna().all()


def test_a_query_that_folds_to_nothing_matches_nothing():
    assert lookup.search("€") == [] and lookup.search("東京") == [] and lookup.search("€ ") == []


def _write_universe(root, rows):
    pd.DataFrame([r[:3] for r in rows], columns=["isin", "name", "country"]).to_csv(root / "tr_universe.csv", index=False)
    (root / "tr_ticker_map.json").write_text(json.dumps({r[0]: r[3] for r in rows}), encoding="utf-8")


def test_refreshed_universe_is_picked_up_without_restart(tmp_path):
    import os
    _write_universe(tmp_path, [("A1", "Alpha", "DE", "ALP.DE")])
    assert tickers(lookup.search("alpha", root=tmp_path)) == ["ALP.DE"]
    _write_universe(tmp_path, [("A1", "Alpha", "DE", "ALP.DE"), ("B2", "Alphabet", "US", "GOOG")])
    st = (tmp_path / "tr_universe.csv").stat()
    os.utime(tmp_path / "tr_universe.csv", ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    assert tickers(lookup.search("alphabet", root=tmp_path)) == ["GOOG"]


def test_exact_ticker_beats_a_name_prefix_of_another(tmp_path):
    _write_universe(tmp_path, [("A1", "RHM Holdings", "US", "RHMH"), ("B2", "Rheinmetall", "DE", "RHM")])
    assert tickers(lookup.search("rhm", root=tmp_path)) == ["RHM"]


# ── fresh clone: the tracked universe_meta.csv stands in for the gitignored TR files (fix 7) ──────

def _meta_only(tmp_path):
    tmp_path.mkdir(parents=True, exist_ok=True)
    from pathlib import Path
    fix = Path(__file__).resolve().parent.parent / "fixtures" / "universe"
    for f in ("universe_meta.csv", "sector_map.json"):
        (tmp_path / f).write_text((fix / f).read_text(encoding="utf-8"), encoding="utf-8")
    return tmp_path


def test_without_tr_files_the_tracked_meta_is_the_universe(tmp_path):
    root = _meta_only(tmp_path)
    assert lookup.info("RHM.DE", root=root) == {"ticker": "RHM.DE", "name": "Rheinmetall", "sector": "Industrials",
                                                 "country": "Germany"}
    assert tickers(lookup.search("apple", root=root)) == ["AAPL"]
    assert lookup.search("deadco", root=root) == [] and not lookup.is_tradeable("DEMO", root=root)
    assert lookup.liquid(2, root=root) == ["NVDA", "AAPL"]


def test_identify_works_on_the_meta_fallback(tmp_path, monkeypatch):
    from monitor import config
    from monitor.screens.identity import identify
    monkeypatch.setattr(config, "UNIVERSE_DIR", _meta_only(tmp_path / "u"))
    got = identify(["AAPL"], buffer_dir=tmp_path)["AAPL"]
    assert got == {"name": "Apple", "sector": "Information Technology", "country": "United States"}


def test_by_isin_is_an_exact_match_of_a_live_name_or_none():
    assert lookup.by_isin("DE0007030009") == "RHM.DE" and lookup.by_isin(" de0007030009 ") == "RHM.DE"
    assert lookup.by_isin("US0000000001") is None                 # Deadco: delisted
    assert lookup.by_isin("DE0007030") is None and lookup.by_isin("") is None
