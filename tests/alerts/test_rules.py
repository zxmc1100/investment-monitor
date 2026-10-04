"""Alert rules: grammar, descriptions and the no-repeat state machine (pure)."""
import copy
from datetime import datetime

import pytest

from monitor.alerts.rules import KINDS, USAGE, describe, evaluate, parse_rule, rule_text

NOW = datetime(2026, 10, 1, 15, 0)          # a Thursday


@pytest.mark.parametrize("text,want", [
    ("NVD.F < 180", ("LEVEL", "NVD.F", "<", 180.0)),
    ("alert rhm.de >2000.5", ("LEVEL", "RHM.DE", ">", 2000.5)),
    ("^GDAXI < 20000", ("LEVEL", "^GDAXI", "<", 20000.0)),
    ("NVD.F MOVE 5", ("MOVE", "NVD.F", ">=", 5.0)),
    ("MOVE * 5", ("MOVE", "*", ">=", 5.0)),
    ("* dd 20%", ("DD", "*", ">=", 20.0)),
    ("PORT DAY -2", ("PORT_DAY", None, "<=", -2.0)),
    ("port day 2", ("PORT_DAY", None, "<=", -2.0)),
    ("PORT DRIFT 10", ("PORT_DRIFT", None, ">=", 10.0)),
    ("PORT WEIGHT 30", ("PORT_WEIGHT", None, ">=", 30.0)),
    ("event", ("EVENT", "*", None, None)),
    ("STALE", ("STALE", "*", None, None)),
])
def test_parse_good(text, want):
    r = parse_rule(text)
    assert (r["kind"], r["ticker"], r["op"], r["value"]) == want


@pytest.mark.parametrize("text", ["", "NVD.F", "NVD.F < -5", "NVD.F < abc", "NVD.F MOVE 0", "NVD.F DD 100",
                                  "PORT DAY 0", "PORT WEIGHT 150", "PORT FOO 3", "* < 10", "hello world"])
def test_parse_bad_raises_usage(text):
    with pytest.raises(ValueError, match="ALERT <TKR>"):
        parse_rule(text)
    assert "PORT DRIFT" in USAGE


def test_describe():
    assert describe({"kind": "LEVEL", "ticker": "NVD.F", "op": "<", "value": 180.0}) == "NVD.F < 180"
    assert describe({"kind": "MOVE", "ticker": "*", "op": ">=", "value": 5.0}) == "HELD+WATCHED MOVE ±5%"
    assert describe({"kind": "PORT_DAY", "ticker": None, "op": "<=", "value": -2.0}) == "PORT DAY ≤ -2%"


@pytest.mark.parametrize("text,want", [
    ("NVD.F < 180,5", ("LEVEL", "NVD.F", "<", 180.5, "NVD.F < 180.5")),
    ("NVD.F > .5", ("LEVEL", "NVD.F", ">", 0.5, "NVD.F > 0.5")),
    ("NVD.F <= 180", ("LEVEL", "NVD.F", "<", 180.0, "NVD.F < 180")),
    ("nvd.f >=2000,25", ("LEVEL", "NVD.F", ">", 2000.25, "NVD.F > 2000.25")),
    ("NVD.F MOVE 2,5", ("MOVE", "NVD.F", ">=", 2.5, "NVD.F MOVE ±2.5%")),
    ("PORT DAY -.5", ("PORT_DAY", None, "<=", -0.5, "PORT DAY ≤ -0.5%")),
])
def test_parse_decimal_comma_leading_dot_and_inclusive_ops(text, want):
    r = parse_rule(text)
    assert (r["kind"], r["ticker"], r["op"], r["value"]) == want[:4]
    assert describe(r) == want[4]                        # the confirmation shows the normalized rule
    assert parse_rule(rule_text(r)) == r                 # and it round-trips


@pytest.mark.parametrize("text", ["ASML.AS > 1,700", "NVD.F < 12,500", "* MOVE 1,000"])
def test_thousands_or_decimal_comma_is_refused_not_guessed(text):
    """'1,700' could be 1700 (English) or 1.7 (Italian/German): refuse it, never guess."""
    with pytest.raises(ValueError, match="AMBIGUOUS"):
        parse_rule(text)
    assert parse_rule("ISP.MI < 0,125")["value"] == 0.125          # a leading 0 is never thousands


def test_describe_all_eight_kinds():
    cases = [({"kind": "LEVEL", "ticker": "NVD.F", "op": ">", "value": 200.5}, "NVD.F > 200.5"),
             ({"kind": "MOVE", "ticker": "NVD.F", "op": ">=", "value": 2.5}, "NVD.F MOVE ±2.5%"),
             ({"kind": "DD", "ticker": "*", "op": ">=", "value": 20.0}, "HELD+WATCHED 20% OFF 52W HIGH"),
             ({"kind": "DD", "ticker": "RHM.DE", "op": ">=", "value": 15.0}, "RHM.DE 15% OFF 52W HIGH"),
             ({"kind": "PORT_DAY", "ticker": None, "op": "<=", "value": -2.0}, "PORT DAY ≤ -2%"),
             ({"kind": "PORT_DRIFT", "ticker": None, "op": ">=", "value": 10.0}, "PORT DRIFT ≥ 10pp"),
             ({"kind": "PORT_WEIGHT", "ticker": None, "op": ">=", "value": 30.0}, "PORT WEIGHT ≥ 30%"),
             ({"kind": "EVENT", "ticker": "*", "op": None, "value": None}, "EARNINGS / EX-DIV NEXT BUSINESS DAY"),
             ({"kind": "STALE", "ticker": "*", "op": None, "value": None}, "HELD QUOTE STALE")]
    assert {c[0]["kind"] for c in cases} == set(KINDS)
    for r, want in cases:
        assert describe(r) == want


def rule(rid, text):
    return {"id": rid, **parse_rule(text)}


def q(price, prev=None, d="2026-10-01", stale=False, lapsed=False):
    return {"price": price, "prev_close": prev, "date": d, "stale": stale, "lapsed": lapsed}


def snap(**kw):
    base = {"quotes": {}, "high52": {}, "held": [], "watched": [], "events": {}, "port": None, "drift": None}
    return {**base, **kw}


def run(rules, steps, state=None):
    """Evaluate a sequence of snapshots; return the fired subjects per step and the final state."""
    state, fired = state or {}, []
    for s, now in steps:
        state, ev = evaluate(rules, state, s, now)
        fired.append([e["subject"] for e in ev])
    return fired, state


def test_level_fires_once_holds_then_rearms_after_the_band():
    r = [rule("A1", "NVD.F < 180")]
    prices = [185, 179, 175, 180.5, 180.95, 181, 179]       # 180.5 sits inside the 0.5 % band; re-arms from 180.9 (180 x 1.005) up
    fired, _ = run(r, [(snap(quotes={"NVD.F": q(p)}), NOW) for p in prices])
    assert fired == [[], ["NVD.F"], [], [], [], [], ["NVD.F"]]


def test_level_event_shape_and_down_flag():
    _, ev = evaluate([rule("A1", "NVD.F < 180")], {}, snap(quotes={"NVD.F": q(172.4)}), NOW)
    assert ev == [{"rule": "A1", "subject": "NVD.F", "ts": "2026-10-01T15:00:00", "value": 172.4,
                   "msg": "NVD.F 172.40 < 180", "down": True}]
    _, ev = evaluate([rule("A2", "NVD.F > 100")], {}, snap(quotes={"NVD.F": q(172.4)}), NOW)
    assert ev[0]["down"] is False


def test_move_once_per_trading_day_for_held_and_watched():
    r = [rule("A4", "MOVE * 5")]
    s1 = snap(held=["NVD.F"], watched=["RHM.DE"],
              quotes={"NVD.F": q(106, 100), "RHM.DE": q(1900, 2000)})
    s2 = snap(held=["NVD.F"], watched=["RHM.DE"],                       # back inside, then out again
              quotes={"NVD.F": q(101, 100), "RHM.DE": q(1990, 2000)})
    s3 = snap(held=["NVD.F"], watched=["RHM.DE"], quotes={"NVD.F": q(107, 100), "RHM.DE": q(1880, 2000)})
    s4 = snap(held=["NVD.F"], watched=["RHM.DE"],
              quotes={"NVD.F": q(113, 107, "2026-10-02"), "RHM.DE": q(1880, 1880, "2026-10-02")})
    fired, _ = run(r, [(s1, NOW), (s2, NOW), (s3, NOW), (s4, NOW)])
    assert fired == [["NVD.F", "RHM.DE"], [], [], ["NVD.F"]]
    _, ev = evaluate(r, {}, s1, NOW)
    assert [e["down"] for e in ev] == [False, True] and ev[1]["msg"] == "RHM.DE -5.0% TODAY"


def test_dd_uses_the_52w_high_or_the_live_price_and_rearms_after_the_band():
    r = [rule("A5", "RHM.DE DD 20")]
    steps = [(snap(quotes={"RHM.DE": q(p)}, high52={"RHM.DE": 2000.0}), NOW) for p in (1700, 1590, 1500, 1595, 1615)]
    fired, _ = run(r, steps)
    assert fired == [[], ["RHM.DE"], [], [], []]            # 1615 is 19.25 % off: re-armed, not fired
    fired, _ = run(r, steps + [(snap(quotes={"RHM.DE": q(1550)}, high52={"RHM.DE": 2000.0}), NOW)])
    assert fired[-1] == ["RHM.DE"]


def test_port_day_drift_and_weight():
    r = [rule("A1", "PORT DAY -2"), rule("A2", "PORT DRIFT 10"), rule("A3", "PORT WEIGHT 30")]
    s1 = snap(port={"day_pct": -2.4, "weights": {"NVD.F": 31.0, "AMZ.F": 12.0}, "day": "2026-10-01"},
              drift={"value": 11.2, "target": "HRP"})
    s2 = snap(port={"day_pct": -2.1, "weights": {"NVD.F": 30.5}, "day": "2026-10-01"}, drift={"value": 10.5, "target": "HRP"})
    s3 = snap(port={"day_pct": -1.9, "weights": {"NVD.F": 29.0}, "day": "2026-10-01"}, drift={"value": 9.0, "target": "HRP"})
    s4 = snap(port={"day_pct": -2.2, "weights": {"NVD.F": 30.0}, "day": "2026-10-01"}, drift={"value": 10.0, "target": "HRP"})
    fired, _ = run(r, [(s, NOW) for s in (s1, s2, s3, s4)])
    assert fired == [["PORT", "PORT", "NVD.F"], [], [], ["PORT", "PORT", "NVD.F"]]
    _, ev = evaluate(r, {}, s1, NOW)
    assert [e["msg"] for e in ev] == ["PORTFOLIO -2.40% TODAY", "DRIFT 11.2pp FROM HRP", "NVD.F 31.0% OF THE BOOK"]


def test_port_day_rearms_on_a_new_trading_day():
    r = [rule("A1", "PORT DAY -2")]
    day1 = snap(port={"day_pct": -3.0, "weights": {}, "day": "2026-10-01"})
    day2 = snap(port={"day_pct": -2.5, "weights": {}, "day": "2026-10-02"})   # terminal was closed overnight
    fired, _ = run(r, [(day1, NOW), (day2, NOW)])
    assert fired == [["PORT"], ["PORT"]]


def test_port_rules_without_data_do_nothing():
    r = [rule("A1", "PORT DAY -2"), rule("A2", "PORT DRIFT 10"), rule("A3", "PORT WEIGHT 30")]
    assert evaluate(r, {}, snap(), NOW) == ({"A1": {}, "A2": {}, "A3": {}}, [])


@pytest.mark.parametrize("now,event_day,fires", [
    (datetime(2026, 10, 1, 15), "2026-10-02", True),        # Thursday → Friday
    (datetime(2026, 10, 2, 15), "2026-10-05", True),        # Friday → Monday
    (datetime(2026, 10, 3, 15), "2026-10-05", True),        # Saturday → Monday
    (datetime(2026, 10, 1, 15), "2026-10-05", False),       # not tomorrow
    (datetime(2026, 10, 2, 15), "2026-10-03", False),       # Friday: Saturday is not a business day
])
def test_event_fires_for_the_next_business_day(now, event_day, fires):
    s = snap(held=["NVD.F"], events={"NVD.F": [{"date": event_day, "kind": "EARNINGS", "amount": None}]})
    _, ev = evaluate([rule("A5", "EVENT")], {}, s, now)
    assert bool(ev) is fires


def test_event_fires_once_per_event_date_and_prunes_past_dates():
    r = [rule("A5", "EVENT")]
    ev1 = {"RHM.DE": [{"date": "2026-10-05", "kind": "EX-DIV", "amount": 8.1}]}
    s = snap(watched=["RHM.DE"], events=ev1)
    fri, sat = datetime(2026, 10, 2, 9), datetime(2026, 10, 3, 9)
    fired, state = run(r, [(s, fri), (s, sat)])
    assert fired == [["RHM.DE|EX-DIV|2026-10-05"], []]
    _, ev = evaluate(r, {}, s, fri)
    assert ev[0]["msg"] == "RHM.DE EX-DIV 8.1 05 OCT — NEXT BUSINESS DAY"
    state, _ = evaluate(r, state, snap(), datetime(2026, 10, 6, 9))
    assert state == {"A5": {}}


def test_stale_per_held_ticker_rearms_when_fresh():
    r = [rule("A6", "STALE")]
    steps = [snap(held=["NVD.F", "AMZ.F"], quotes={"NVD.F": q(1, stale=True, lapsed=True), "AMZ.F": None}),
             snap(held=["NVD.F", "AMZ.F"], quotes={"NVD.F": q(1, stale=True, lapsed=True), "AMZ.F": q(2)}),
             snap(held=["NVD.F", "AMZ.F"], quotes={"NVD.F": q(1), "AMZ.F": q(2)}),
             snap(held=["NVD.F", "AMZ.F"], quotes={"NVD.F": q(1, stale=True, lapsed=True), "AMZ.F": q(2)})]
    fired, _ = run(r, [(s, NOW) for s in steps])
    assert fired == [["AMZ.F", "NVD.F"], [], [], ["NVD.F"]]


def test_stale_ignores_a_quote_that_failed_only_this_minute():
    """`stale` (this minute's fetch failed) gates the price rules; only `lapsed` fires STALE."""
    r = [rule("A6", "STALE"), rule("A4", "MOVE * 5")]
    s = snap(held=["NVD.F"], quotes={"NVD.F": q(110, 100, stale=True)})
    assert evaluate(r, {}, s, NOW)[1] == []


def test_delisted_or_stale_watched_ticker_never_fires_price_rules():
    r = [rule("A1", "DEAD.DE < 5"), rule("A2", "DEAD.DE MOVE 5"), rule("A3", "DEAD.DE DD 10")]
    for quote in (None, q(1.0, 2.0, "2026-06-30", stale=True)):
        s = snap(watched=["DEAD.DE"], quotes={"DEAD.DE": quote}, high52={"DEAD.DE": 50.0})
        assert evaluate(r, {}, s, NOW)[1] == []


def test_persisted_state_means_no_flood_after_a_restart():
    r = [rule("A1", "NVD.F < 180"), rule("A4", "MOVE * 5"), rule("A5", "EVENT")]
    s = snap(held=["NVD.F"], quotes={"NVD.F": q(170, 180)},
             events={"NVD.F": [{"date": "2026-10-02", "kind": "EARNINGS", "amount": None}]})
    state, ev = evaluate(r, {}, s, NOW)
    assert len(ev) == 3
    reloaded = copy.deepcopy(state)                          # what alerts.json holds after a restart
    assert evaluate(r, reloaded, s, datetime(2026, 10, 1, 18))[1] == []


def test_evaluate_does_not_mutate_its_inputs():
    r = [rule("A1", "NVD.F < 180")]
    state = {"A1": {"NVD.F": {"armed": True, "day": None, "at": None}}}
    s = snap(quotes={"NVD.F": q(170)})
    before = copy.deepcopy((r, state, s))
    evaluate(r, state, s, NOW)
    assert (r, state, s) == before


def _lv(text, prices, high=None):
    kw = {"high52": {"X": high}} if high else {}
    return run([rule("A1", text)], [(snap(quotes={"X": q(p)}, **kw), NOW) for p in prices])[0]


def test_rearm_band_known_answers():
    assert _lv("X < 180", [179, 180.85, 179]) == [["X"], [], []]
    assert _lv("X < 180", [179, 180.9, 179]) == [["X"], [], ["X"]]
    assert _lv("X > 2000", [2001, 1991, 2001]) == [["X"], [], []]
    assert _lv("X > 2000", [2001, 1990, 2001]) == [["X"], [], ["X"]]
    assert _lv("X DD 20", [1580, 1608, 1580], 2000.0) == [["X"], [], []]
    assert _lv("X DD 20", [1580, 1611, 1580], 2000.0) == [["X"], [], ["X"]]


def test_string_exdiv_amount_does_not_stop_other_alerts():
    r = [rule("A1", "NVD.F < 180"), rule("A5", "EVENT")]
    s = snap(held=["NVD.F"], quotes={"NVD.F": q(170)},
             events={"NVD.F": [{"date": "2026-10-02", "kind": "EX-DIV", "amount": "n/a"}]})
    _, ev = evaluate(r, {}, s, NOW)
    assert len(ev) == 2 and ev[1]["msg"] == "NVD.F EX-DIV 02 OCT — NEXT BUSINESS DAY"


def test_port_weight_rearms_when_position_sold_and_bought_back():
    r = [rule("A3", "PORT WEIGHT 30")]
    p = lambda w: snap(port={"day_pct": 0, "weights": w, "day": "2026-10-01"})
    fired, _ = run(r, [(p({"X.F": 31.0}), NOW), (p({}), NOW), (p({"X.F": 32.0}), NOW)])
    assert fired == [["X.F"], [], ["X.F"]]


def test_no_date_means_no_weekend_refire():
    sat = datetime(2026, 10, 3, 12)
    r = [rule("A4", "MOVE * 5")]
    s = snap(held=["X"], quotes={"X": q(106, 100, d=None)})
    assert evaluate(r, {}, s, NOW)[1] == [] and evaluate(r, {}, s, sat)[1] == []
    r = [rule("A1", "PORT DAY -2")]
    s = snap(port={"day_pct": -3.0, "weights": {}, "day": None})
    fired, _ = run(r, [(s, NOW), (s, sat)])
    assert fired == [["PORT"], []]


def test_unicode_minus_parses():
    assert parse_rule("PORT DAY \u22122")["value"] == -2.0


def test_move_and_port_day_name_a_prior_session():
    """'TODAY' only for today's session; a weekend or pre-open check names the session it fired on."""
    sat, mon = datetime(2026, 10, 3, 12), datetime(2026, 10, 5, 8)
    s = snap(held=["X"], quotes={"X": q(106, 100, d="2026-10-02")})
    assert evaluate([rule("A4", "MOVE * 5")], {}, s, sat)[1][0]["msg"] == "X +6.0% FRI 02 OCT"
    assert evaluate([rule("A4", "MOVE * 5")], {}, s, datetime(2026, 10, 2, 15))[1][0]["msg"] == "X +6.0% TODAY"
    s = snap(port={"day_pct": -2.4, "weights": {}, "day": "2026-10-02"})
    assert evaluate([rule("A1", "PORT DAY -2")], {}, s, mon)[1][0]["msg"] == "PORTFOLIO -2.40% FRI 02 OCT"


def test_dd_is_measured_from_the_higher_of_52w_high_and_price():
    r = [rule("A5", "RHM.DE DD 1")]
    # a new high today (price above the stale 52W high) is no drawdown, however small the threshold
    fired, _ = run(r, [(snap(quotes={"RHM.DE": q(2100)}, high52={"RHM.DE": 2000.0}), NOW)])
    assert fired == [[]]
    _, ev = evaluate(r, {}, snap(quotes={"RHM.DE": q(1950)}, high52={"RHM.DE": 2000.0}), NOW)
    assert ev[0]["value"] == 2.5 and "2,000.00" in ev[0]["msg"]


def test_new_state_does_not_alias_unobserved_subjects_of_the_old_state():
    r = [rule("A1", "MOVE * 5")]
    old = {"A1": {"GONE": {"armed": False, "day": "2026-09-30", "at": "t"}}}
    new, _ = evaluate(r, old, snap(), NOW)
    assert new == old and new["A1"]["GONE"] is not old["A1"]["GONE"]
    new["A1"]["GONE"]["armed"] = True
    assert old["A1"]["GONE"]["armed"] is False
