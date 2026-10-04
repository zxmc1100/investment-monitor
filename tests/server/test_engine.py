import dataclasses
import threading
import time
from datetime import datetime, timedelta

import pytest

from monitor import config
from monitor.screens.base import Ctx
from monitor.server.engine import Engine
from monitor.server.store import Store
from tests.server.helpers import Recorder, make_screen


def _engine(tmp_path, scr):
    rec = Recorder()
    return Engine({scr.id: scr}, Store(tmp_path / "store"), rec, ctx=Ctx(buffer_dir=tmp_path)), rec


def test_cold_screen_runs_all_tiers_then_assembles(tmp_path, monkeypatch):
    scr, calls, _ = make_screen(tmp_path, monkeypatch)
    eng, rec = _engine(tmp_path, scr)
    assert {j.tier for j in eng.ensure_fresh("FAKE")} == {"quote", "daily"}
    assert eng.runner.wait_idle(5)
    p = eng.payload("FAKE")
    assert set(p["meta"]["tiers"]) == {"quote", "daily"}
    assert p["meta"]["code_version"] == scr.code_version()
    assert ("quote", True) in calls and ("daily", False) in calls      # quote always live
    assert any(e["type"] == "screen" and e["id"] == "FAKE" for e in rec.events)


def test_fresh_tiers_are_not_rerun(tmp_path, monkeypatch):
    scr, calls, _ = make_screen(tmp_path, monkeypatch)
    eng, _ = _engine(tmp_path, scr)
    eng.ensure_fresh("FAKE")
    eng.runner.wait_idle(5)
    assert eng.ensure_fresh("FAKE") == [] and len(calls) == 2


def test_tier_ages_follow_policy(tmp_path, monkeypatch):
    scr, _, _ = make_screen(tmp_path, monkeypatch)
    eng, _ = _engine(tmp_path, scr)
    eng.compute_now("FAKE")
    now = datetime.now()
    assert eng.due_tiers("FAKE", now=now) == []
    assert eng.due_tiers("FAKE", now=now + timedelta(seconds=config.QUOTE_INTERVAL_S * 0.5)) == []
    assert eng.due_tiers("FAKE", now=now + timedelta(seconds=config.QUOTE_INTERVAL_S * 0.95)) == ["quote"]
    assert eng.due_tiers("FAKE", now=now + timedelta(seconds=config.QUOTE_INTERVAL_S + 1)) == ["quote"]
    assert eng.due_tiers("FAKE", now=now + timedelta(hours=config.DAILY_TTL_H, seconds=1)) == ["quote", "daily"]


def test_code_change_serves_old_payload_flags_it_and_recomputes_everything(tmp_path, monkeypatch):
    scr, _, dep = make_screen(tmp_path, monkeypatch)
    eng, _ = _engine(tmp_path, scr)
    eng.compute_now("FAKE")
    dep.write_text("X = 2  # edited\n")
    assert eng.payload("FAKE") is not None
    assert eng.live("FAKE")["code_changed"] is True
    assert eng.due_tiers("FAKE") == ["quote", "daily"]
    eng.ensure_fresh("FAKE")
    eng.runner.wait_idle(5)
    assert eng.live("FAKE")["code_changed"] is False


def test_failure_keeps_last_payload_and_reports_error(tmp_path, monkeypatch):
    fail = {}
    scr, _, _ = make_screen(tmp_path, monkeypatch, fail=fail)
    eng, _ = _engine(tmp_path, scr)
    before = eng.compute_now("FAKE")
    fail["quote"] = True
    eng.refresh("FAKE", ["quote"])
    eng.runner.wait_idle(5)
    assert eng.payload("FAKE")["meta"]["computed_at"] == before["meta"]["computed_at"]
    err = eng.live("FAKE")["error"]
    assert err["tier"] == "quote" and "quote broke" in err["error"] and "Traceback" in err["trace"]


def test_assemble_waits_until_every_tier_exists(tmp_path, monkeypatch):
    scr, _, _ = make_screen(tmp_path, monkeypatch)
    eng, _ = _engine(tmp_path, scr)
    eng.runner.submit("FAKE", "quote")
    eng.runner.wait_idle(5)
    assert eng.payload("FAKE") is None


def test_tier_computes_never_overlap(tmp_path, monkeypatch):
    scr, _, _ = make_screen(tmp_path, monkeypatch)
    gauge, peaks, lock = [0], [], threading.Lock()

    def compute(tier, ctx):
        with lock:
            gauge[0] += 1
            peaks.append(gauge[0])
        time.sleep(0.05)
        with lock:
            gauge[0] -= 1
        return {"tier": tier}
    eng, _ = _engine(tmp_path, dataclasses.replace(scr, compute=compute))
    eng.ensure_fresh("FAKE")
    assert eng.runner.wait_idle(5)
    assert max(peaks) == 1 and eng.payload("FAKE") is not None


def test_compute_now_is_synchronous_and_forced(tmp_path, monkeypatch):
    scr, calls, _ = make_screen(tmp_path, monkeypatch)
    eng, _ = _engine(tmp_path, scr)
    p = eng.compute_now("FAKE")
    assert p["parts"]["quote"]["tier"] == "quote" and calls == [("quote", True), ("daily", True)]


def test_unreadable_part_is_recomputed_not_frozen(tmp_path, monkeypatch):
    scr, _, _ = make_screen(tmp_path, monkeypatch)
    eng, _ = _engine(tmp_path, scr)
    eng.compute_now("FAKE")
    (tmp_path / "store" / "FAKE.daily.pkl").write_bytes(b"garbage")
    assert eng.assemble("FAKE") is None
    assert "daily" in eng.due_tiers("FAKE")


def test_heavy_tier_never_auto_runs(tmp_path, monkeypatch):
    scr, _, dep = make_screen(tmp_path, monkeypatch)
    eng, _ = _engine(tmp_path, dataclasses.replace(scr, tiers=("quote", "daily", "heavy")))
    assert {j.tier for j in eng.ensure_fresh("FAKE")} == {"quote", "daily"}
    assert eng.runner.wait_idle(5)
    assert eng.payload("FAKE") is None
    eng.refresh("FAKE", ["heavy"])
    assert eng.runner.wait_idle(5)
    assert eng.payload("FAKE") is not None
    dep.write_text("X = 2  # edited\n")
    assert eng.due_tiers("FAKE") == ["quote", "daily"]
    eng.ensure_fresh("FAKE")
    assert eng.runner.wait_idle(5)
    assert eng.payload("FAKE")["meta"]["code_version"] == eng.screens["FAKE"].code_version()


def test_assemble_rechecks_before_invalidating(tmp_path, monkeypatch):
    scr, _, _ = make_screen(tmp_path, monkeypatch)
    eng, _ = _engine(tmp_path, scr)
    eng.compute_now("FAKE")
    real = eng.store.get_part
    first = {"quote": True}

    def raced(key, tier):                  # the read lands before the concurrent write
        if tier == "quote" and first.pop("quote", False):
            return None
        return real(key, tier)
    monkeypatch.setattr(eng.store, "get_part", raced)
    assert eng.assemble("FAKE") is None
    assert eng.store.part_info("FAKE", "quote") is not None and real("FAKE", "quote") is not None


@pytest.mark.parametrize("tier,force", [("daily", False), ("quote", True)],
                         ids=["the-12h-daily-refresh", "a-forced-quote-poke"])
def test_a_watchlist_change_reruns_an_mkt_tier_already_computing(tmp_path, monkeypatch, tier, force):
    """A running MKT job read the old watchlist: WATCH must not be absorbed by it, forced or not —
    the tier runs again once it finishes, so the new name gets its row, 52W POS and NEXT EVENT."""
    scr, calls, _ = make_screen(tmp_path, monkeypatch, sid="MKT")
    started, release = threading.Event(), threading.Event()

    def compute(t, ctx):
        calls.append((t, ctx.force))
        if calls == [(tier, force)]:
            started.set()
            release.wait(5)
        return {"tier": t}
    eng, _ = _engine(tmp_path, dataclasses.replace(scr, compute=compute))
    eng.runner.submit("MKT", tier, force=force)            # already computing when WATCH lands
    assert started.wait(5)
    eng.watchlist_changed()
    release.set()
    assert eng.runner.wait_idle(5)
    assert [c for c in calls if c[0] == tier] == [(tier, force), (tier, True)]
    assert {c[0] for c in calls} == {"quote", "daily"}


def test_prune_drops_old_sec_keys_nobody_traded_or_watches(tmp_path, monkeypatch):
    scr, _, _ = make_screen(tmp_path, monkeypatch, sid="SEC", params=["HELD.F"])
    eng, _ = _engine(tmp_path, scr)
    week_ago = (datetime.now() - timedelta(days=8)).isoformat(timespec="seconds")
    for p in ("HELD.F", "OLD.F", "FRESH.F"):
        eng.compute_now("SEC", param=p)
    for p in ("HELD.F", "OLD.F"):                                  # age their parts past 7 days
        for t in scr.tiers:
            rec = eng.store.get_part(f"SEC~{p}", t)
            eng.store.put_part(f"SEC~{p}", t, rec["data"], rec["code"])
            (tmp_path / "store" / f"SEC~{p}.{t}.json").write_text(
                f'{{"at": "{week_ago}", "code": "{rec["code"]}"}}')
    assert eng.prune_params() == ["SEC~OLD.F"]
    assert eng.store.keys("SEC~", scr.tiers) == {"SEC~HELD.F", "SEC~FRESH.F"}     # held kept, fresh kept
    assert eng.payload("SEC", "OLD.F") is None and eng.payload("SEC", "FRESH.F")


def test_a_failed_tier_is_not_auto_resubmitted_for_a_while_but_refresh_runs_at_once(tmp_path, monkeypatch):
    """A failing daily job must not re-run (holding _NET_LOCK) on every 60 s poke."""
    import time_machine
    fail = {"daily": True}
    scr, calls, _ = make_screen(tmp_path, monkeypatch, fail=fail)
    eng, _ = _engine(tmp_path, scr)
    eng.ensure_fresh("FAKE")
    assert eng.runner.wait_idle(5)
    daily = lambda: sum(1 for c in calls if c[0] == "daily")       # noqa: E731
    assert daily() == 1 and eng.due_tiers("FAKE") == ["daily"]     # still due: no part
    assert eng.ensure_fresh("FAKE") == [] and daily() == 1           # ... but backing off
    eng.refresh("FAKE", ["daily"])                                   # a manual REFRESH is not held back
    assert eng.runner.wait_idle(5) and daily() == 2
    later = datetime.now() + timedelta(minutes=config.FAILED_RETRY_MIN, seconds=5)
    with time_machine.travel(later, tick=False):
        assert [j.tier for j in eng.ensure_fresh("FAKE")] == ["quote", "daily"]
        assert eng.runner.wait_idle(5)
    assert daily() == 3


def test_a_tier_that_succeeds_again_is_not_held_back(tmp_path, monkeypatch):
    fail = {"daily": True}
    scr, calls, _ = make_screen(tmp_path, monkeypatch, fail=fail)
    eng, _ = _engine(tmp_path, scr)
    eng.ensure_fresh("FAKE")
    assert eng.runner.wait_idle(5)
    fail["daily"] = False
    eng.refresh("FAKE", ["daily"])
    assert eng.runner.wait_idle(5) and eng.payload("FAKE") is not None
    eng.store.invalidate_part("FAKE", "daily")                       # due again right away
    assert [j.tier for j in eng.ensure_fresh("FAKE")] == ["daily"]
    assert eng.runner.wait_idle(5)


def test_a_screen_that_needs_the_portfolio_stays_cold_without_one(tmp_path, monkeypatch):
    from monitor.screens.base import NO_PORTFOLIO
    scr, calls, _ = make_screen(tmp_path, monkeypatch)
    needy = dataclasses.replace(scr, needs_portfolio=True)
    csv = tmp_path / "portfolio.csv"
    eng = Engine({"FAKE": needy}, Store(tmp_path / "store"), Recorder(),
                 ctx=Ctx(buffer_dir=tmp_path, portfolio_csv=csv))
    assert eng.cold_reason("FAKE") == NO_PORTFOLIO
    assert eng.ensure_fresh("FAKE") == [] and eng.refresh("FAKE") == [] and calls == []
    with pytest.raises(RuntimeError, match="NO PORTFOLIO YET"):
        eng.compute_now("FAKE")
    csv.write_text("Date,Ticker,Action,Shares,Price,PricePerShare\n")
    assert eng.cold_reason("FAKE") is None and {j.tier for j in eng.ensure_fresh("FAKE")} == {"quote", "daily"}
    assert eng.runner.wait_idle(5) and eng.payload("FAKE") is not None
    plain, _ = _engine(tmp_path / "p", scr)
    assert plain.cold_reason("FAKE") is None                       # MKT-like screens never go cold


def test_opt_and_risk_need_two_priced_positions(tmp_path):
    import json as _json

    from monitor.screens import SCREENS
    from monitor.screens.common import NEEDS_TWO
    csv = tmp_path / "portfolio.csv"
    head = "Date,Ticker,Action,Shares,Price,PricePerShare\n"
    csv.write_text(head + "2025-01-06,AAA.F,buy,10,1000.00,100.00\n")
    eng = Engine(dict(SCREENS), Store(tmp_path / "store"), Recorder(),
                 ctx=Ctx(buffer_dir=tmp_path / "buf", portfolio_csv=csv, equity_log=None), workers=0)
    assert eng.cold_reason("OPT") == NEEDS_TWO and eng.cold_reason("RISK") == NEEDS_TWO
    assert eng.cold_reason("PORT") is None and eng.ensure_fresh("OPT") == []
    csv.write_text(head + "2025-01-06,AAA.F,buy,10,1000.00,100.00\n2025-02-03,BBB.F,buy,20,1000.00,50.00\n")
    assert eng.cold_reason("OPT") is None                                 # no quote buffer yet: both count
    (tmp_path / "buf").mkdir()
    (tmp_path / "buf" / "quotes.json").write_text(_json.dumps({"AAA.F": {"price": 1.0, "prev_close": 1.0,
                                                                         "date": "2026-06-30", "ts": "x"},
                                                               "BBB.F": None}))
    assert eng.cold_reason("RISK") == NEEDS_TWO                           # Yahoo never priced BBB.F
    csv.write_text("not,a,ledger\n1,2,3\n")
    assert eng.cold_reason("OPT") is None                                 # unreadable book: the compute reports it
