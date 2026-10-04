"""Screen contract: identity, registry entry, code version tracking."""
import uuid

import pytest

from monitor.screens.base import Screen, code_version


def _dep(tmp_path, monkeypatch, body="X = 1\n"):
    name = f"cv_dep_{uuid.uuid4().hex[:8]}"
    f = tmp_path / f"{name}.py"
    f.write_text(body, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    return name, f


def test_code_version_tracks_dep_source(tmp_path, monkeypatch):
    name, f = _dep(tmp_path, monkeypatch)
    v1 = code_version((name,))
    assert len(v1) == 12 and v1 == code_version((name,))
    f.write_text("X = 2\n", encoding="utf-8")
    assert code_version((name,)) != v1


def test_code_version_unknown_module_raises():
    with pytest.raises(ModuleNotFoundError):
        code_version(("no_such_module_xyz",))


def test_entry_shape():
    s = Screen("OPT", "Optimizer", 2, status="soon", tiers=("daily",))
    assert s.entry() == {"id": "OPT", "title": "Optimizer", "fkey": 2, "status": "soon",
                         "public": False, "tiers": ["daily"], "param": False, "build": None}
    b = Screen("MODEL", "Model", 5, tiers=("heavy",), build_cmd=("-m", "monitor", "build", "model"), build_eta="~10 MIN")
    assert b.entry()["build"] == "~10 MIN"                          # BUILD MODEL's notice


def test_accepts_defaults_to_params_and_can_be_widened():
    plain = Screen("P", "P")
    listed = Screen("S", "S", params=lambda ctx: ["A.F"])
    wide = Screen("W", "W", params=lambda ctx: ["A.F"], accept=lambda ctx, p: p.endswith(".DE"))
    assert plain.accepts(None, "A.F") is False
    assert listed.accepts(None, "A.F") and not listed.accepts(None, "B.F")
    assert wide.accepts(None, "X.DE") and not wide.accepts(None, "A.F")
