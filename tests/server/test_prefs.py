import pytest

from monitor.server import prefs


def test_defaults_when_missing(tmp_path):
    assert prefs.load(tmp_path / "nope.json") == {"target": "HRP"}
    assert prefs.load(None) == {"target": "HRP"}


def test_corrupt_or_unknown_prefs_fall_back_to_defaults(tmp_path):
    f = tmp_path / "p.json"
    f.write_text("{not json", encoding="utf-8")
    assert prefs.load(f) == {"target": "HRP"}
    f.write_text('{"target": "YOLO"}', encoding="utf-8")
    assert prefs.load(f) == {"target": "HRP"}


def test_save_load_roundtrip(tmp_path):
    f = tmp_path / "sub" / "p.json"
    prefs.save(f, {"target": "RP"})
    assert prefs.load(f) == {"target": "RP"}


def test_with_target_uppercases_and_validates():
    assert prefs.with_target({"target": "HRP"}, "blsame") == {"target": "BLSAME"}
    with pytest.raises(ValueError, match="unknown target"):
        prefs.with_target({"target": "HRP"}, "nope")


def test_save_none_path_is_a_noop():
    prefs.save(None, {"target": "RP"})
