from monitor.screens.base import Ctx
from monitor.server.engine import Engine
from monitor.server.store import Store
from tests.server.helpers import Recorder, make_screen


def _engine(tmp_path, scr, prefs_path=None):
    rec = Recorder()
    return Engine({scr.id: scr}, Store(tmp_path / "store"), rec, ctx=Ctx(buffer_dir=tmp_path),
                  prefs_path=prefs_path), rec


def test_param_keys_isolate_freshness(tmp_path, monkeypatch):
    scr, calls, _ = make_screen(tmp_path, monkeypatch, sid="P", params=["AAA", "BBB"])
    eng, rec = _engine(tmp_path, scr)
    assert eng.params("P") == ["AAA", "BBB"]
    eng.ensure_fresh("P", "AAA")
    assert eng.runner.wait_idle(5)
    assert eng.payload("P", "AAA")["parts"]["quote"]["param"] == "AAA"
    assert eng.payload("P", "BBB") is None
    assert {c[2] for c in calls} == {"AAA"}
    assert (tmp_path / "store" / "P~AAA.quote.pkl").exists()
    ev = [e for e in rec.events if e["type"] == "screen"][-1]
    assert ev["key"] == "P~AAA" and ev["param"] == "AAA" and ev["id"] == "P"


def test_unparametrized_screen_reports_no_params(tmp_path, monkeypatch):
    scr, _, _ = make_screen(tmp_path, monkeypatch)
    eng, _ = _engine(tmp_path, scr)
    assert eng.params("FAKE") is None


def test_set_target_persists_and_reassembles(tmp_path, monkeypatch):
    import pytest
    scr, _, _ = make_screen(tmp_path, monkeypatch)
    f = tmp_path / "prefs.json"
    eng, _ = _engine(tmp_path, scr, prefs_path=f)
    eng.compute_now("FAKE")
    assert eng.payload("FAKE")["meta"]["prefs"] == {"target": "HRP"}
    assert eng.set_target("rp") == {"target": "RP"}
    assert eng.payload("FAKE")["meta"]["prefs"] == {"target": "RP"}
    assert Engine({scr.id: scr}, Store(tmp_path / "store"), Recorder(), prefs_path=f).prefs == {"target": "RP"}
    with pytest.raises(ValueError):
        eng.set_target("bogus")
