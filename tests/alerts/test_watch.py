"""The alert job's snapshot over the yfinance fakes: what is quoted, staleness, highs, events."""
from datetime import date, datetime
from pathlib import Path

import pytest
import time_machine

from monitor.alerts import watch
from monitor.alerts.rules import evaluate, parse_rule
from monitor.portfolio import snapshot
from tests import fakes_yf

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "portfolio_small.csv"


def rules(*texts):
    return [{"id": f"A{i}", **parse_rule(t)} for i, t in enumerate(texts, 1)]


def test_watched_tickers_are_held_watched_and_rule_names():
    r = rules("NVDA < 100", "MOVE * 5", "PORT DAY -2", "RHM.DE DD 20")
    assert watch.watched_tickers(r, ["AAA.F"], ["BAS.DE"]) == ["AAA.F", "BAS.DE", "NVDA", "RHM.DE"]


def test_gather_quotes_flags_old_bars_and_skips_unneeded_fetches(tmp_path, monkeypatch):
    fakes_yf.install(monkeypatch)
    with time_machine.travel("2026-06-30 14:00:00+00:00", tick=False):
        s = watch.gather(rules("MOVE * 5"), held=["AAA.F"], watched=["BAS.DE"], port=None, drift=None,
                         buffer_dir=tmp_path)
    assert set(s["quotes"]) == {"AAA.F", "BAS.DE"} and s["quotes"]["AAA.F"]["stale"] is False
    assert s["high52"] == {} and s["events"] == {}
    with time_machine.travel("2026-07-06 14:00:00+00:00", tick=False):      # the buffered bar is a week old
        monkeypatch.setattr("monitor.alerts.watch.cached_quotes",
                            lambda ts, **k: ({t: {"price": 1.0, "prev_close": 1.0, "date": "2026-06-30"} for t in ts}, {}, None))
        s = watch.gather(rules("MOVE * 5"), held=["AAA.F"], watched=[], port=None, drift=None, buffer_dir=tmp_path)
    assert s["quotes"]["AAA.F"]["stale"] is True


def test_gather_highs_for_dd_rules_and_events_for_event_rules(tmp_path, monkeypatch):
    fakes_yf.install(monkeypatch)
    monkeypatch.setitem(fakes_yf.CALENDARS, "AAA.F", {"Earnings Date": [date(2026, 7, 1)]})
    with time_machine.travel("2026-06-30 14:00:00+00:00", tick=False):
        s = watch.gather(rules("* DD 20", "EVENT"), held=["AAA.F"], watched=[], port={"day_pct": -1.0},
                         drift=None, buffer_dir=tmp_path)
    series = fakes_yf.series("AAA.F", "2026-06-30")
    assert s["high52"]["AAA.F"] == pytest.approx(series[series.index >= "2025-06-29"].max())
    assert s["events"] == {"AAA.F": [{"date": "2026-07-01", "kind": "EARNINGS", "amount": None}]}
    assert s["port"] == {"day_pct": -1.0}


def test_stale_alert_waits_15_minutes_without_a_good_quote(tmp_path, monkeypatch):
    """One check a minute from 14:00. AAA.F answers at minutes 0, 2, 3 and 20 only; NEW.F never
    quoted. A one-minute miss fires nothing; STALE fires once the last good quote is 15 minutes old,
    never again while it stays stale, re-arms on a fresh quote; never-quoted fires at once."""
    up = {0, 2, 3, 20}
    minute = [0]
    monkeypatch.setattr("monitor.data.yahoo.fetch_quotes", lambda ts: {
        t: {"price": 10.0, "prev_close": 10.0, "date": "2026-06-30"} for t in ts if t == "AAA.F" and minute[0] in up})
    r, state, fired, gated = rules("STALE"), {}, [], []
    for minute[0] in range(24):
        with time_machine.travel(datetime(2026, 6, 30, 14, minute[0]), tick=False):
            s = watch.gather(r, held=["AAA.F", "NEW.F"], watched=[], port=None, drift=None, buffer_dir=tmp_path)
            state, ev = evaluate(r, state, s, datetime.now())
        fired.append([e["subject"] for e in ev])
        gated.append(s["quotes"]["AAA.F"]["stale"])
    assert fired == [["NEW.F"]] + [[]] * 17 + [["AAA.F"]] + [[]] * 5
    assert gated[1] is True and gated[2] is False          # a missed minute still gates the price rules


def test_port_view_is_ports_own_day_and_weights_for_the_same_quotes(tmp_path, monkeypatch):
    fakes_yf.install(monkeypatch)
    with time_machine.travel("2026-06-30 14:00:00+00:00", tick=False):
        q = snapshot.quote_tier(snapshot.load_book(FIX), force=True, buffer_dir=tmp_path)
    v = watch.port_view(q["positions"], q["quotes"], held=[p["ticker"] for p in q["positions"]])
    assert q["day_pct"] != 0 and v["day_pct"] == pytest.approx(q["day_pct"], abs=1e-9) and v["day"] == "2026-06-30"
    total = sum(p["position_value"] for p in q["positions"])
    assert v["weights"] == pytest.approx({p["ticker"]: p["position_value"] / total * 100 for p in q["positions"]})


def pos(*tickers):
    return [{"ticker": t, "shares": 10.0, "avg_cost": 50.0, "position_value": 1.0} for t in tickers]


def test_port_view_never_counts_an_older_session_or_a_dead_check():
    fri = {"price": 97.0, "prev_close": 100.0, "date": "2026-10-02", "stale": False}
    mon = {"price": 100.0, "prev_close": 100.0, "date": "2026-10-05", "stale": False}
    v = watch.port_view(pos("A.F", "B.F", "SOLD.F"), {"A.F": fri, "B.F": mon, "W.DE": mon}, held=["A.F", "B.F", "SOLD.F"])
    assert v["day"] == "2026-10-05" and v["day_pct"] == 0.0          # A.F's -3 % is Friday's, not today's
    assert v["weights"] == pytest.approx({"A.F": 970 / 19.7, "B.F": 1000 / 19.7})
    v = watch.port_view(pos("A.F"), {"A.F": {**fri, "date": "2026-10-05"}}, held=["A.F"])
    assert v["day_pct"] == pytest.approx(-3.0)
    dead = watch.port_view(pos("A.F"), {"A.F": {**fri, "date": "2026-10-05", "stale": True}}, held=["A.F"])
    assert dead["day_pct"] is None and dead["weights"] == {"A.F": 100.0}   # nothing live this check: no DAY
    assert watch.port_view([], {"A.F": mon}, held=["A.F"]) is None and watch.port_view(None, {}, held=[]) is None


def test_port_view_day_and_positions_come_from_what_you_hold():
    """A later-dated rule/watched ticker (BTC on a Saturday) never moves PORT's session day, and a
    sold position that is still watched is not counted."""
    fri = {"price": 97.0, "prev_close": 100.0, "date": "2026-10-02", "stale": False}
    sat = {"price": 60000.0, "prev_close": 59000.0, "date": "2026-10-03", "stale": False}
    v = watch.port_view(pos("A.F"), {"A.F": fri, "BTC-EUR": sat}, held=["A.F"])
    assert v["day"] == "2026-10-02" and v["day_pct"] == pytest.approx(-3.0)
    v = watch.port_view(pos("A.F", "SOLD.F"), {"A.F": fri, "SOLD.F": fri}, held=["A.F"])
    assert v["weights"] == {"A.F": 100.0}


def test_stale_counts_from_when_the_check_resumed(tmp_path, monkeypatch):
    """The buffer's last good quote is from 11:00; the terminal reopens at 14:00 and Yahoo fails:
    STALE waits 15 minutes from the resumed check, not from 11:00 (no burst on opening)."""
    ok = [True]
    monkeypatch.setattr("monitor.data.yahoo.fetch_quotes", lambda ts: {
        t: {"price": 10.0, "prev_close": 10.0, "date": "2026-06-30"} for t in ts} if ok[0] else {})
    r = rules("STALE")
    with time_machine.travel(datetime(2026, 6, 30, 11, 0), tick=False):
        watch.gather(r, held=["AAA.F"], watched=[], port=None, drift=None, buffer_dir=tmp_path)
    ok[0] = False
    lapsed = {}
    for m in (0, 14, 15):
        with time_machine.travel(datetime(2026, 6, 30, 14, m), tick=False):
            since = datetime.now() if m == 0 else since          # local clock, as the service takes it
            s = watch.gather(r, held=["AAA.F"], watched=[], port=None, drift=None, buffer_dir=tmp_path, since=since)
        lapsed[m] = s["quotes"]["AAA.F"]["lapsed"]
    assert lapsed == {0: False, 14: False, 15: True}
