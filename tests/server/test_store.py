import pytest

from monitor.server.store import Store


def test_payload_roundtrip(tmp_path):
    s = Store(tmp_path)
    assert s.get_payload("X") is None
    s.put_payload("X", {"a": 1, "b": None})
    assert s.get_payload("X") == {"a": 1, "b": None}


def test_payload_rejects_nan_and_writes_nothing(tmp_path):
    with pytest.raises(ValueError):
        Store(tmp_path).put_payload("X", {"v": float("nan")})
    assert not (tmp_path / "X.json").exists()


def test_corrupt_files_read_as_cold(tmp_path):
    (tmp_path / "X.json").write_text("{trunc", encoding="utf-8")
    (tmp_path / "X.quote.pkl").write_bytes(b"garbage")
    (tmp_path / "X.quote.json").write_text("nope", encoding="utf-8")
    s = Store(tmp_path)
    assert s.get_payload("X") is None
    assert s.get_part("X", "quote") is None and s.part_info("X", "quote") is None


def test_part_roundtrip_and_info(tmp_path):
    s = Store(tmp_path)
    rec = s.put_part("X", "daily", {"rows": [1, 2]}, "abc")
    assert s.get_part("X", "daily")["data"] == {"rows": [1, 2]}
    assert s.part_info("X", "daily") == {"at": rec["at"], "code": "abc"}


def test_keys_and_drop_handle_dotted_params(tmp_path):
    s = Store(tmp_path)
    for key in ("SEC~NVD.F", "SEC~NVD", "PORT"):
        s.put_part(key, "quote", {}, "c")
        s.put_part(key, "daily", {}, "c")
        s.put_payload(key, {"k": key})
    assert s.keys("SEC~", ("quote", "daily")) == {"SEC~NVD.F", "SEC~NVD"}
    s.drop("SEC~NVD", ("quote", "daily"))
    assert s.keys("SEC~", ("quote", "daily")) == {"SEC~NVD.F"}          # NVD.F is not NVD's file
    assert s.get_payload("SEC~NVD.F") == {"k": "SEC~NVD.F"} and s.part_info("SEC~NVD.F", "daily")
    assert s.get_payload("SEC~NVD") is None and s.get_payload("PORT")
