"""Watchlist store: idempotent add, remove, corrupt or hand-mangled files, path None."""
from datetime import date

from monitor.alerts import watchlist as W


def test_add_is_idempotent_and_remove(tmp_path):
    f = tmp_path / "w.json"
    assert W.load(f) == []
    W.add(f, "rhm.de", "Rheinmetall", today=date(2026, 10, 3))
    assert W.add(f, "RHM.DE", "Rheinmetall") == [{"ticker": "RHM.DE", "name": "Rheinmetall", "added": "2026-10-03"}]
    W.add(f, "AAPL", "Apple")
    assert W.tickers(f) == ["RHM.DE", "AAPL"]
    assert [w["ticker"] for w in W.remove(f, "rhm.de")] == ["AAPL"]
    assert W.tickers(f) == ["AAPL"]
    assert W.tickers(f) == [w["ticker"] for w in W.remove(f, "NOPE")]          # unknown: unchanged


def test_corrupt_or_mangled_file_reads_as_empty_or_cleaned(tmp_path):
    f = tmp_path / "w.json"
    f.write_text("{not json")
    assert W.load(f) == []
    f.write_text('{"ticker": "AAPL"}')
    assert W.load(f) == []
    f.write_text('[{"ticker": "aapl"}, {"ticker": ""}, 7, {"name": "x"}, {"ticker": "AAPL", "name": "dup"}]')
    assert W.load(f) == [{"ticker": "AAPL", "name": "AAPL", "added": ""}]
    W.add(f, "NVDA", "NVIDIA")                      # a corrupt file is replaced by a clean one
    assert W.tickers(f) == ["AAPL", "NVDA"]


def test_path_none_is_an_empty_unsaved_watchlist():
    assert W.load(None) == []
    assert W.tickers(None) == []
    assert W.add(None, "AAPL", "Apple")[0]["ticker"] == "AAPL"
    assert W.load(None) == []
