"""ALRT payload: active alerts with Enter → ACK, rule states, the log; the service keeps it current."""
import json

from monitor.alerts import store as astore
from monitor.alerts.rules import parse_rule
from monitor.screens import SCREENS, alrt
from monitor.screens.base import Ctx
from monitor.server.engine import Engine
from monitor.server.redact import public_view
from monitor.server.store import Store
from tests.server.helpers import Recorder

META = {"computed_at": "2026-10-01T15:00:00", "tiers": {}, "code_version": "x", "prefs": {}}


def ev(rule, subject, ts, down=False):
    return {"rule": rule, "subject": subject, "ts": ts, "value": 1.5, "msg": f"{subject} moved", "down": down}


def data():
    d = astore.defaults()
    for t in ("A < 5", "B < 5", "C < 5", "D < 5"):                 # A7 … A10
        d, _ = astore.add_rule(d, parse_rule(t))
    d, _ = astore.record(d, {"A4": {"NVD.F": {"armed": False, "day": "2026-10-01", "at": "2026-10-01T15:00:00"}}},
                         [ev("A4", "NVD.F", "2026-10-01T15:00:00", down=True), ev("A1", "PORT", "2026-10-01T15:01:00")])
    d, _ = astore.ack(d, "E2")
    return d


def panel(p, pid):
    return next(q for q in p["panels"] if q["id"] == pid)


def test_panels_active_rules_log():
    p = alrt.assemble({"quote": data()}, dict(META))
    assert [q["id"] for q in p["panels"]] == ["active", "rules", "log"]
    act = panel(p, "active")
    assert act["enter"] == "ACK {key}" and [r["id"] for r in act["rows"]] == ["E1"]
    assert act["rows"][0] == {"id": "E1", "ts": "2026-10-01T15:00:00", "time": "01 OCT 15:00",
                              "rule": "A4 · HELD+WATCHED MOVE ±5%", "value": 1.5, "msg": "NVD.F moved",
                              "ack": "ACTIVE", "_dn": True}
    rules = panel(p, "rules")
    assert rules["sort"] == ["no", "asc"] and [r["id"] for r in rules["rows"]][-2:] == ["A9", "A10"]
    a4 = next(r for r in rules["rows"] if r["id"] == "A4")
    assert (a4["state"], a4["last"], a4["_hot"]) == ("FIRED", "01 OCT 15:00", True)
    assert next(r for r in rules["rows"] if r["id"] == "A1")["state"] == "ARMED"
    assert [(r["id"], r["ack"]) for r in panel(p, "log")["rows"]] == [("E1", "ACTIVE"), ("E2", "ACK")]
    json.dumps(p, allow_nan=False)


def test_private_and_never_exported():
    assert alrt.SCREEN.public is False and alrt.SCREEN.fkey == 5 and SCREENS["ALRT"] is alrt.SCREEN
    assert public_view(alrt.assemble({"quote": data()}, dict(META)))["panels"] == []


def test_compute_reads_the_rules_file(tmp_path):
    f = tmp_path / "alerts.json"
    assert alrt.compute("quote", Ctx(alerts=f)) == astore.defaults()
    astore.save(f, data())
    assert alrt.compute("quote", Ctx(alerts=f))["seq"] == {"rule": 10, "entry": 2}


def test_the_alert_service_keeps_alrt_current(tmp_path):
    from monitor.server.alerting import AlertService
    eng = Engine({"ALRT": alrt.SCREEN}, Store(tmp_path / "store"), Recorder(), ctx=Ctx(alerts=tmp_path / "a.json"))
    svc = AlertService(eng, tmp_path / "a.json")
    eng.tasks[("ALRT", "watch")] = lambda force: None
    svc.add("^GDAXI < 20000")
    assert [r["id"] for r in panel(eng.payload("ALRT"), "rules")["rows"]][-1] == "A7"
    astore.save(tmp_path / "a.json", data())
    svc.ack("ALL")
    assert panel(eng.payload("ALRT"), "active")["rows"] == []
    assert eng.broker.events[-1]["type"] == "screen" and eng.broker.events[-1]["id"] == "ALRT"
    eng.runner.wait_idle(5)


def hand_edited(path):
    """alerts.json after a hand edit: an entry of a removed rule, and a None, a garbage and an int ts."""
    d = data()
    junk = [{"id": "E9", "rule": "A99", "subject": "X", "ts": "2026-10-01T09:00:00", "value": 1.0,
             "msg": "X moved", "down": False, "ack": False},
            {"id": "E8", "rule": "A1", "subject": "PORT", "ts": None, "value": None, "msg": "no ts", "down": True,
             "ack": False},
            {"id": "E7", "rule": "A1", "subject": "PORT", "ts": "yesterday 3pm", "value": -2.4, "msg": "garbage ts",
             "down": True, "ack": False},
            {"id": "E6", "rule": "A4", "subject": "NVD.F", "ts": 1759300000, "value": 5.1, "msg": "int ts",
             "down": False, "ack": False}]
    path.write_text(json.dumps({**d, "log": junk + d["log"], "seq": {**d["seq"], "entry": 9}}), encoding="utf-8")


def test_alrt_assembles_from_degraded_inputs(tmp_path):
    p = alrt.assemble({"quote": alrt.compute("quote", Ctx(alerts=None))}, dict(META))   # no rules file at all
    assert [r["id"] for r in panel(p, "rules")["rows"]] == ["A1", "A2", "A3", "A4", "A5", "A6"]
    json.dumps(p, allow_nan=False)
    f = tmp_path / "alerts.json"
    hand_edited(f)
    p = alrt.assemble({"quote": alrt.compute("quote", Ctx(alerts=f))}, dict(META))
    json.dumps(p, allow_nan=False)
    rows = {r["id"]: r for r in panel(p, "log")["rows"]}
    assert rows["E9"]["time"] == "01 OCT 09:00" and rows["E9"]["rule"] == "A99"      # its rule was removed: just the id
    assert all(rows[i]["time"] is None and rows[i]["ts"] is None for i in ("E8", "E7", "E6"))
    assert [r["id"] for r in panel(p, "active")["rows"]] == ["E9", "E8", "E7", "E6", "E1"]


def test_when_never_raises():
    assert alrt._when("2026-10-01T09:05:00") == "01 OCT 09:05"
    assert alrt._when("yesterday 3pm") is None and alrt._when(1759300000) is None and alrt._when(None) is None


def test_service_edits_succeed_with_a_hand_edited_log_on_disk(tmp_path):
    from monitor.server.alerting import AlertService
    f = tmp_path / "a.json"
    hand_edited(f)
    eng = Engine({"ALRT": alrt.SCREEN}, Store(tmp_path / "store"), Recorder(), ctx=Ctx(alerts=f))
    svc = AlertService(eng, f)
    eng.tasks[("ALRT", "watch")] = lambda force: None
    assert svc.add("^GDAXI < 20000")["id"] == "A100"        # above A99, which the log still names
    assert [r["id"] for r in panel(eng.payload("ALRT"), "rules")["rows"]][-1] == "A100"
    assert svc.ack("ALL") == 5
    assert panel(eng.payload("ALRT"), "active")["rows"] == []
    eng.runner.wait_idle(5)


def test_a_failed_publish_after_a_saved_edit_is_logged_not_raised(tmp_path, caplog):
    from monitor.server.alerting import AlertService
    f = tmp_path / "a.json"
    eng = Engine({"ALRT": alrt.SCREEN}, Store(tmp_path / "store"), Recorder(), ctx=Ctx(alerts=f))
    svc = AlertService(eng, f)
    eng.tasks[("ALRT", "watch")] = lambda force: None

    def boom(*a, **k):
        raise RuntimeError("assemble broke")
    eng.put_and_assemble = boom
    assert svc.add("^GDAXI < 20000")["id"] == "A7"                   # the rule WAS added: no error
    assert astore.load(f)["rules"][-1]["id"] == "A7"
    assert "assemble broke" in caplog.text
    eng.runner.wait_idle(5)


def test_entry_without_rule_text_has_no_dangling_separator():
    assert alrt._entry({"id": "E1", "rule": "A4", "msg": "m"})["rule"] == "A4"
    assert alrt._entry({"id": "E1", "rule": "A4", "text": "", "msg": "m"})["rule"] == "A4"
    assert alrt._entry({"id": "E1", "rule": "A4", "text": "STALE", "msg": "m"})["rule"] == "A4 · STALE"
