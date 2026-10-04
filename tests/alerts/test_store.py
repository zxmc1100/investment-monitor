"""Alert store: defaults, round trip, corrupt and hand-edited files, ids, ack, log cap, view."""
import json

from monitor.alerts import store as S
from monitor.alerts.rules import parse_rule


def test_defaults_are_the_six_spec_rules():
    d = S.defaults()
    assert [r["id"] for r in d["rules"]] == ["A1", "A2", "A3", "A4", "A5", "A6"]
    assert [r["kind"] for r in d["rules"]] == ["PORT_DAY", "PORT_DRIFT", "PORT_WEIGHT", "MOVE", "EVENT", "STALE"]
    assert d["rules"][0]["value"] == -2.0 and d["rules"][3]["ticker"] == "*"
    assert d["seq"] == {"rule": 6, "entry": 0} and d["log"] == [] and d["state"] == {}


def test_missing_file_and_round_trip(tmp_path):
    f = tmp_path / "alerts.json"
    assert S.load(f) == S.defaults()
    data, r = S.add_rule(S.defaults(), parse_rule("NVD.F < 180"))
    S.save(f, data)
    assert S.load(f) == data and r["id"] == "A7"
    S.save(None, data)                                  # path None never writes


def test_corrupt_file_falls_back_to_defaults_and_is_kept(tmp_path, caplog):
    f = tmp_path / "alerts.json"
    f.write_text("{oops", encoding="utf-8")
    assert S.load(f) == S.defaults()
    assert "unreadable alerts file" in caplog.text
    assert (tmp_path / "alerts.json.bad").read_text(encoding="utf-8") == "{oops"
    f.write_text('["not", "a", "dict"]', encoding="utf-8")
    assert S.load(f) == S.defaults()


def test_hand_edited_file_keeps_valid_rules_only(tmp_path):
    f = tmp_path / "alerts.json"
    f.write_text(json.dumps({
        "rules": [{"id": "A1", "kind": "LEVEL", "ticker": "NVD.F", "op": "<", "value": 150},
                  {"id": "A2", "kind": "LEVEL", "ticker": "NVD.F", "op": "<", "value": "abc"},
                  {"id": "A3", "kind": "NOPE"}, {"id": "zz", "kind": "EVENT", "ticker": "*"},
                  {"id": "A9", "kind": "MOVE", "ticker": "*", "op": ">=", "value": 4},
                  {"id": "A9", "kind": "STALE", "ticker": "*"}],
        "state": {"A1": {"NVD.F": {"armed": False, "day": None, "at": "x"}, "BAD": {"armed": "yes"}},
                  "A2": {"NVD.F": {"armed": True}}},
        "log": [{"id": "E40", "rule": "A1", "msg": "m", "ack": False}, {"id": "junk"}, "x"],
        "seq": {"rule": 2, "entry": "many"}}), encoding="utf-8")
    d = S.load(f)
    assert [(r["id"], r["value"]) for r in d["rules"]] == [("A1", 150.0), ("A9", 4.0)]
    assert d["state"] == {"A1": {"NVD.F": {"armed": False, "day": None, "at": "x"}}}
    assert [e["id"] for e in d["log"]] == ["E40"]
    assert d["seq"] == {"rule": 9, "entry": 40}            # never below an id already in use


def test_rule_ids_never_reuse_and_remove_acks_its_alerts():
    d, a7 = S.add_rule(S.defaults(), parse_rule("NVD.F < 180"))
    d, _ = S.record(d, {}, [{"rule": "A7", "subject": "NVD.F", "ts": "t", "value": 1.0, "msg": "m", "down": True}])
    d = S.remove_rule(d, "a7")
    assert [r["id"] for r in d["rules"]][-1] == "A6" and S.active(d) == []
    d, a8 = S.add_rule(d, parse_rule("NVD.F > 200"))
    assert (a7["id"], a8["id"]) == ("A7", "A8")
    try:
        S.remove_rule(d, "A99")
        raise AssertionError("expected KeyError")
    except KeyError:
        pass


def ev(rule, subject, down=False):
    return {"rule": rule, "subject": subject, "ts": "2026-10-01T15:00:00", "value": 1.0, "msg": f"{rule} {subject}",
            "down": down}


def test_record_ack_and_cap():
    d, new = S.record(S.defaults(), {"A4": {}}, [ev("A4", "NVD.F"), ev("A4", "AMZ.F", True), ev("A1", "PORT", True)])
    assert [e["id"] for e in new] == ["E1", "E2", "E3"] and new[0]["text"] == "HELD+WATCHED MOVE ±5%"
    assert [e["id"] for e in d["log"]] == ["E1", "E2", "E3"] and d["state"] == {"A4": {}}
    d, n = S.ack(d, "e2")
    assert n == 1 and [e["id"] for e in S.active(d)] == ["E1", "E3"]
    d, n = S.ack(d, "A4")
    assert n == 1 and [e["id"] for e in S.active(d)] == ["E3"]
    d, n = S.ack(d, "ALL")
    assert n == 1 and S.active(d) == [] and S.ack(d, "ALL")[1] == 0
    for _ in range(70):
        d, _ = S.record(d, {}, [ev("A4", "X"), ev("A4", "Y"), ev("A4", "Z")])
    assert len(d["log"]) == S.LOG_CAP and [e["id"] for e in d["log"][:4]] == ["E211", "E212", "E213", "E208"]


def test_view_rule_states_and_last_fired():
    d, _ = S.record(S.defaults(), {"A1": {"PORT": {"armed": False, "day": "2026-10-01", "at": "t"}}},
                    [ev("A1", "PORT", True)])
    v = S.view(d)
    a1 = v["rules"][0]
    assert (a1["text"], a1["state"], a1["last"]) == ("PORT DAY ≤ -2%", "FIRED", "2026-10-01T15:00:00")
    assert v["rules"][1]["state"] == "ARMED" and v["rules"][1]["last"] is None
    assert [e["id"] for e in v["active"]] == ["E1"] and v["log"] == d["log"]


def test_non_finite_numbers_take_the_corrupt_path(tmp_path):
    f = tmp_path / "alerts.json"
    f.write_text('{"rules": [{"id": "A1", "kind": "LEVEL", "ticker": "X", "op": "<", "value": NaN}], "log": []}', encoding="utf-8")
    assert S.load(f) == S.defaults() and (tmp_path / "alerts.json.bad").exists()
    S.save(f, S.load(f))                                   # and saving the result works


def test_unsafe_state_and_log_fields_are_dropped(tmp_path):
    f = tmp_path / "alerts.json"
    f.write_text(json.dumps({
        "rules": [{"id": "A1", "kind": "STALE", "ticker": "*", "op": None, "value": None}],
        "state": {"A1": {"X": {"armed": True, "day": ["l"], "at": 5}}},
        "log": [{"id": "E1", "rule": ["A1"], "msg": "m"}, {"id": "E2", "rule": "A1", "msg": "m", "value": {"a": 1}},
                {"id": "E3", "rule": "A1", "msg": "m", "value": 1.5, "down": True}]}), encoding="utf-8")
    d = S.load(f)
    assert d["state"] == {"A1": {"X": {"armed": True, "day": None, "at": None}}}
    assert [e["id"] for e in d["log"]] == ["E3"]
    S.view(d)
    json.dumps(d, allow_nan=False)


def test_hand_edited_log_timestamps_load_as_none(tmp_path):
    """A log entry keeps its ts only when it is an ISO string; the entry itself is kept."""
    f = tmp_path / "alerts.json"
    f.write_text(json.dumps({
        "rules": [{"id": "A1", "kind": "STALE", "ticker": "*", "op": None, "value": None}],
        "log": [{"id": "E4", "rule": "A1", "msg": "m", "ts": "2026-10-01T09:00:00"},
                {"id": "E3", "rule": "A1", "msg": "m", "ts": "yesterday 3pm"},
                {"id": "E2", "rule": "A1", "msg": "m", "ts": 1759300000},
                {"id": "E1", "rule": "A1", "msg": "m", "ts": None}]}), encoding="utf-8")
    d = S.load(f)
    assert [(e["id"], e["ts"]) for e in d["log"]] == [("E4", "2026-10-01T09:00:00"), ("E3", None), ("E2", None),
                                                      ("E1", None)]
    assert [r["last"] for r in S.view(d)["rules"]] == ["2026-10-01T09:00:00"]


def test_a_new_rule_never_reuses_an_id_still_in_the_log(tmp_path):
    f = tmp_path / "alerts.json"
    f.write_text(json.dumps({
        "rules": [{"id": "A1", "kind": "STALE", "ticker": "*", "op": None, "value": None}],
        "log": [{"id": "E1", "rule": "A7", "msg": "m", "ts": "2026-10-01T09:00:00"}],
        "seq": {"rule": 1, "entry": 1}}), encoding="utf-8")              # a hand edit lowered seq below the logged A7
    d = S.load(f)
    assert d["seq"]["rule"] == 7
    d, r = S.add_rule(d, parse_rule("NVD.F < 180"))
    assert r["id"] == "A8" and S.view(d)["rules"][-1]["last"] is None


def test_remove_rule_purges_its_state():
    d, _ = S.add_rule(S.defaults(), parse_rule("NVD.F < 180"))
    d, _ = S.record(d, {"A7": {"NVD.F": {"armed": False, "day": None, "at": "t"}}}, [])
    assert "A7" in d["state"] and "A7" not in S.remove_rule(d, "A7")["state"]


def test_view_last_skips_entries_without_a_timestamp():
    d, _ = S.record(S.defaults(), {}, [ev("A1", "PORT", True)])
    d = {**d, "log": [{**ev("A1", "PORT"), "id": "E9", "ts": None}, *d["log"]]}      # newest, hand-edited
    assert S.view(d)["rules"][0]["last"] == "2026-10-01T15:00:00"
