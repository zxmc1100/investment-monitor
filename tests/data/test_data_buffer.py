"""Unit tests for the on-disk data buffer (TTL cache + last-good fallback)."""

from datetime import date

import pandas as pd
import pytest

from monitor.data import buffer as BUF
from monitor.data.buffer import (
    cached_market_caps,
    cached_price_history,
)


@pytest.fixture
def bufdir(tmp_path):
    return tmp_path / "buffer"


class Counter:
    """Fake fetch that records call count and returns a scripted value."""

    def __init__(self, value):
        self.calls = 0
        self.value = value

    def __call__(self, *args, **kwargs):
        self.calls += 1
        return self.value() if callable(self.value) else self.value


def test_history_ttl_hit_then_force(bufdir):
    frame = pd.DataFrame({"AAA.F": [1.0, 2.0]}, index=pd.bdate_range("2024-01-01", periods=2))
    fetch = Counter(frame)
    a = cached_price_history(["AAA.F"], buffer_dir=bufdir, _fetch=fetch)
    b = cached_price_history(["AAA.F"], buffer_dir=bufdir, _fetch=fetch)  # within TTL
    assert fetch.calls == 1                      # second call served from cache
    pd.testing.assert_frame_equal(a, b)
    cached_price_history(["AAA.F"], buffer_dir=bufdir, _fetch=fetch, force=True)
    assert fetch.calls == 2                       # force bypasses cache


GOOD = pd.DataFrame({"AAA.F": [1.0, 2.0], "BBB.F": [3.0, 4.0]}, index=pd.bdate_range("2024-01-01", periods=2))
NEWER = pd.DataFrame({"AAA.F": [1.0, 2.0, 2.5]}, index=pd.bdate_range("2024-01-01", periods=3))   # BBB.F left out


def _age(bufdir, days):
    """Make the cached history (and when each line was last seen) `days` old."""
    import json
    import os
    import time
    for f in bufdir.glob("hist_*"):
        if f.suffix == ".json":
            f.write_text(json.dumps({t: time.time() - days * 86400 for t in json.loads(f.read_text(encoding="utf-8"))}), encoding="utf-8")
        os.utime(f, (time.time() - days * 86400,) * 2)


@pytest.mark.parametrize("throttled", [
    pd.DataFrame(),                                                        # Yahoo throttled: empty
    pd.DataFrame({"AAA.F": [1.0, 2.0], "BBB.F": [float("nan")] * 2},       # one line came back empty
                 index=pd.bdate_range("2024-01-01", periods=2)),
])
def test_a_throttled_answer_keeps_the_lines_it_dropped_while_the_cache_is_recent(bufdir, throttled):
    cached_price_history(["AAA.F", "BBB.F"], buffer_dir=bufdir, _fetch=Counter(GOOD))
    got = cached_price_history(["AAA.F", "BBB.F"], buffer_dir=bufdir, _fetch=Counter(throttled), force=True)
    pd.testing.assert_frame_equal(got, GOOD, check_like=True, check_freq=False)
    again = cached_price_history(["AAA.F", "BBB.F"], buffer_dir=bufdir, _fetch=Counter(throttled))
    pd.testing.assert_frame_equal(again, GOOD, check_like=True, check_freq=False)     # and on disk


def test_a_partial_answer_brings_new_bars_and_fills_only_the_missing_line(bufdir):
    cached_price_history(["AAA.F", "BBB.F"], buffer_dir=bufdir, _fetch=Counter(GOOD))
    got = cached_price_history(["AAA.F", "BBB.F"], buffer_dir=bufdir, _fetch=Counter(NEWER), force=True)
    assert list(got["AAA.F"]) == [1.0, 2.0, 2.5]                           # the new bar is there
    assert list(got["BBB.F"].dropna()) == [3.0, 4.0]                       # the dropped line, from the cache


def test_a_merge_resets_the_ttl_instead_of_refetching_every_call(bufdir):
    cached_price_history(["AAA.F", "BBB.F"], buffer_dir=bufdir, _fetch=Counter(GOOD))
    _age(bufdir, 1)                                                        # past the 12 h TTL, inside 3 days
    partial = Counter(NEWER)
    cached_price_history(["AAA.F", "BBB.F"], buffer_dir=bufdir, _fetch=partial)
    cached_price_history(["AAA.F", "BBB.F"], buffer_dir=bufdir, _fetch=partial)
    assert partial.calls == 1


def test_a_line_unseen_for_longer_than_the_fill_window_is_dropped(bufdir):
    from monitor import config
    cached_price_history(["AAA.F", "BBB.F"], buffer_dir=bufdir, _fetch=Counter(GOOD))
    _age(bufdir, config.HISTORY_FILL_DAYS + 1)
    got = cached_price_history(["AAA.F", "BBB.F"], buffer_dir=bufdir, _fetch=Counter(NEWER))
    assert list(got.columns) == ["AAA.F"] and len(got) == 3                 # the new frame as it is


def test_a_line_kept_alive_by_merges_still_expires_from_when_it_was_last_seen(bufdir):
    """Each merge rewrites the file (TTL reset), but the fill window counts from the last time Yahoo
    actually priced the line — a delisted name is not carried forever."""
    from monitor import config
    cached_price_history(["AAA.F", "BBB.F"], buffer_dir=bufdir, _fetch=Counter(GOOD))
    _age(bufdir, config.HISTORY_FILL_DAYS - 1)
    got = cached_price_history(["AAA.F", "BBB.F"], buffer_dir=bufdir, _fetch=Counter(NEWER), force=True)
    assert "BBB.F" in got.columns                                          # still inside the window: filled
    _age(bufdir, config.HISTORY_FILL_DAYS + 1)                             # BBB.F last seen > 3 days ago
    got = cached_price_history(["AAA.F", "BBB.F"], buffer_dir=bufdir, _fetch=Counter(NEWER), force=True)
    assert list(got.columns) == ["AAA.F"]


def test_an_answer_that_prices_nothing_is_not_cached(bufdir):
    fetch = Counter(pd.DataFrame())
    assert cached_price_history(["AAA.F"], buffer_dir=bufdir, _fetch=fetch).empty
    cached_price_history(["AAA.F"], buffer_dir=bufdir, _fetch=fetch)
    assert fetch.calls == 2                                                # asked again, not served empty


def test_history_raw_and_adjusted_are_cached_apart_and_default_key_unchanged(bufdir):
    import hashlib
    frame = pd.DataFrame({"AAA.F": [1.0, 2.0]}, index=pd.bdate_range("2024-01-01", periods=2))
    seen = []

    def fetch(tickers, **kw):
        seen.append(kw.get("adjusted", True))
        return frame
    cached_price_history(["AAA.F"], buffer_dir=bufdir, _fetch=fetch)
    cached_price_history(["AAA.F"], buffer_dir=bufdir, _fetch=fetch, adjusted=False)
    cached_price_history(["AAA.F"], buffer_dir=bufdir, _fetch=fetch, adjusted=False)   # cached
    assert seen == [True, False]                  # raw is its own entry, then a cache hit
    old = hashlib.md5("AAA.F|5y".encode()).hexdigest()[:12]
    files = sorted(p.name for p in bufdir.glob("hist_*.pkl"))
    assert len(files) == 2 and f"hist_{old}.pkl" in files   # the default (adjusted) key is the old one


def test_history_ttl_expired_refetches(bufdir):
    frame = pd.DataFrame({"AAA.F": [1.0]}, index=pd.bdate_range("2024-01-01", periods=1))
    fetch = Counter(frame)
    cached_price_history(["AAA.F"], buffer_dir=bufdir, _fetch=fetch, ttl_hours=12)
    cached_price_history(["AAA.F"], buffer_dir=bufdir, _fetch=fetch, ttl_hours=0)  # expired
    assert fetch.calls == 2


def test_a_file_stamped_a_moment_ahead_of_the_clock_is_not_fresh_for_a_zero_ttl(tmp_path):
    """Windows' coarse clock (Python 3.11) can read a moment BEFORE a file just written: its age was
    negative, under a TTL of 0, so 'fresh' — and an expired history was never refetched (CI flake)."""
    import os
    import time
    from monitor.data import buffer
    f = tmp_path / "x.json"
    f.write_text("{}", encoding="utf-8")
    ahead = time.time() + 0.5
    os.utime(f, (ahead, ahead))
    assert not buffer._fresh(f, 0)
    assert buffer._fresh(f, 12)                    # a real TTL still holds a just-written file


def test_market_caps_keep_last_good_on_partial_fetch(bufdir):
    full = Counter({"JPM": 5e11, "BAC": 3e11})
    cached_market_caps(["JPM", "BAC"], buffer_dir=bufdir, _fetch=full)
    # later fetch fails for BAC (returns only JPM); force to bypass TTL
    partial = Counter({"JPM": 6e11})
    out = cached_market_caps(["JPM", "BAC"], buffer_dir=bufdir, _fetch=partial, force=True)
    assert out["JPM"] == 6e11                     # new value wins
    assert out["BAC"] == 3e11                     # last-good retained


from monitor.data.buffer import cached_quotes


def _q(p, prev=None, d="2026-06-30"):
    return {"price": p, "prev_close": prev, "date": d}


def test_quotes_cold_fetches_and_buffers(bufdir):
    fetch = Counter({"A": _q(10.0, 9.0), "B": _q(5.0, 5.5)})
    quotes, stale, as_of = cached_quotes(["A", "B"], buffer_dir=bufdir, _fetch=fetch)
    assert quotes["A"]["price"] == 10.0 and quotes["A"]["prev_close"] == 9.0
    assert stale == {} and as_of is not None and fetch.calls == 1


def test_quotes_nonforce_serves_buffer_without_network(bufdir):
    fetch = Counter({"A": _q(10.0)})
    cached_quotes(["A"], buffer_dir=bufdir, _fetch=fetch)
    quotes, stale, _ = cached_quotes(["A"], buffer_dir=bufdir, _fetch=fetch)
    assert fetch.calls == 1 and quotes["A"]["price"] == 10.0 and stale == {}


def test_quotes_force_failure_keeps_last_good_and_flags_stale(bufdir):
    _, _, first_ts = cached_quotes(["A"], buffer_dir=bufdir, _fetch=Counter({"A": _q(10.0, 9.5)}))
    quotes, stale, as_of = cached_quotes(["A"], force=True, buffer_dir=bufdir, _fetch=Counter({"A": None}))
    assert quotes["A"]["price"] == 10.0          # last-good, never cost basis
    assert as_of == first_ts                     # not "now": nothing fresh was served
    assert set(stale) == {"A"}


def test_quotes_fetch_exception_degrades_to_last_good(bufdir):
    cached_quotes(["A"], buffer_dir=bufdir, _fetch=Counter({"A": _q(10.0)}))

    def boom(_):
        raise ConnectionError("yahoo down")
    quotes, stale, _ = cached_quotes(["A"], force=True, buffer_dir=bufdir, _fetch=boom)
    assert quotes["A"]["price"] == 10.0 and set(stale) == {"A"}


def test_quotes_never_seen_ticker_is_none(bufdir):
    quotes, stale, _ = cached_quotes(["A", "NEW"], buffer_dir=bufdir,
                                     _fetch=Counter({"A": _q(1.0), "NEW": None}))
    assert quotes["NEW"] is None and "NEW" not in stale


def test_quotes_empty_ticker_list(bufdir):
    assert cached_quotes([], buffer_dir=bufdir, _fetch=Counter({})) == ({}, {}, None)


def test_quotes_as_of_is_newest_served_ts_not_now(bufdir):
    import time_machine
    with time_machine.travel("2026-10-05 09:00:00+00:00", tick=False):
        _, _, first = cached_quotes(["A"], buffer_dir=bufdir, _fetch=Counter({"A": _q(10.0)}))
    with time_machine.travel("2026-10-05 10:00:00+00:00", tick=False):
        _, stale, as_of = cached_quotes(["A"], force=True, buffer_dir=bufdir, _fetch=Counter({"A": None}))
    assert as_of == first and set(stale) == {"A"}


def test_cached_dividends_ttl_and_last_good(tmp_path):
    calls = []

    def fetch(tks):
        calls.append(list(tks))
        return {"A": pd.Series([0.5], index=pd.to_datetime(["2025-03-03"]))} if len(calls) == 1 else {}

    d1 = BUF.cached_dividends(["A", "B"], buffer_dir=tmp_path, _fetch=fetch)
    assert d1 == {"A": [["2025-03-03", 0.5]]}
    assert BUF.cached_dividends(["A", "B"], buffer_dir=tmp_path, _fetch=fetch) == d1     # fresh: no refetch
    assert len(calls) == 1
    assert BUF.cached_dividends(["A", "B"], force=True, buffer_dir=tmp_path, _fetch=fetch) == d1   # failed refetch keeps last-good
    assert len(calls) == 2


def test_cached_dividends_stores_only_finite_amounts(tmp_path):
    def fetch(tks):
        return {"A": pd.Series([0.5, float("nan"), float("inf")], index=pd.to_datetime(["2025-03-03", "2025-06-02", "2025-09-01"]))}

    assert BUF.cached_dividends(["A"], buffer_dir=tmp_path, _fetch=fetch) == {"A": [["2025-03-03", 0.5]]}


# ── movers and events ───────────────────────────────────────

ROWS = {"AAA": {"day": 1.5, "d5": 3.0, "volx": 2.0, "bar": "2026-10-02"}}


def test_movers_cached_for_ttl_then_refetched(bufdir):
    import time_machine
    fetch = Counter(ROWS)
    with time_machine.travel("2026-10-02 10:00:00", tick=False):
        rows, at, stale = BUF.cached_movers(["AAA"], buffer_dir=bufdir, _fetch=fetch)
        assert rows == ROWS and at == "2026-10-02T10:00:00" and not stale
    with time_machine.travel("2026-10-02 10:14:00", tick=False):
        BUF.cached_movers(["AAA"], buffer_dir=bufdir, _fetch=fetch)
    assert fetch.calls == 1
    with time_machine.travel("2026-10-02 10:15:00", tick=False):
        BUF.cached_movers(["AAA"], buffer_dir=bufdir, _fetch=fetch)
    assert fetch.calls == 2


def test_movers_batch_failure_keeps_last_good_flags_stale_and_waits(bufdir):
    import time_machine
    with time_machine.travel("2026-10-02 10:00:00", tick=False):
        BUF.cached_movers(["AAA"], buffer_dir=bufdir, _fetch=Counter(ROWS))
    dead = Counter({})                                   # Yahoo returns nothing for the whole batch
    with time_machine.travel("2026-10-02 10:20:00", tick=False):
        rows, at, stale = BUF.cached_movers(["AAA"], buffer_dir=bufdir, _fetch=dead)
        assert rows == ROWS and at == "2026-10-02T10:00:00" and stale
    with time_machine.travel("2026-10-02 10:25:00", tick=False):
        BUF.cached_movers(["AAA"], buffer_dir=bufdir, _fetch=dead)
    assert dead.calls == 1                               # not retried inside the TTL


def test_movers_never_fetched_and_failing(bufdir):
    def boom(_):
        raise RuntimeError("down")
    assert BUF.cached_movers(["AAA"], buffer_dir=bufdir, _fetch=boom)[::2] == ({}, True)
    assert BUF.cached_movers([], buffer_dir=bufdir, _fetch=boom) == ({}, None, False)


def test_events_per_ticker_ttl_failure_retry_and_past_filter(bufdir):
    import time_machine
    ev = {"A": [{"date": "2026-10-05", "kind": "EARNINGS", "amount": None}], "B": []}
    fetch = Counter(ev)
    with time_machine.travel("2026-10-02 10:00:00", tick=False):
        assert BUF.cached_events(["A", "B"], buffer_dir=bufdir, _fetch=fetch) == ev
        BUF.cached_events(["A"], buffer_dir=bufdir, _fetch=fetch)
    assert fetch.calls == 1
    partial = Counter({"B": []})                         # A's calendar fails this time
    with time_machine.travel("2026-10-03 10:01:00", tick=False):
        assert BUF.cached_events(["A", "B"], buffer_dir=bufdir, _fetch=partial)["A"] == ev["A"]
    with time_machine.travel("2026-10-03 10:30:00", tick=False):
        BUF.cached_events(["A", "B"], buffer_dir=bufdir, _fetch=partial)
    assert partial.calls == 1                            # A is re-asked after an hour, not every minute
    with time_machine.travel("2026-10-03 11:02:00", tick=False):
        BUF.cached_events(["A", "B"], buffer_dir=bufdir, _fetch=partial)
    assert partial.calls == 2
    with time_machine.travel("2026-10-06 09:00:00", tick=False):    # the event date has passed
        assert BUF.cached_events(["A"], buffer_dir=bufdir, _fetch=partial) == {"A": []}


def test_movers_unreadable_tried_is_expired_not_an_error(bufdir):
    import json
    fetch = Counter(ROWS)
    BUF.cached_movers(["AAA"], buffer_dir=bufdir, _fetch=fetch)
    path = next(bufdir.glob("movers_*.json"))
    raw = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**raw, "tried": "yesterday-ish"}), encoding="utf-8")
    rows, at, stale = BUF.cached_movers(["AAA"], buffer_dir=bufdir, _fetch=fetch)
    assert rows == ROWS and fetch.calls == 2


def test_events_unreadable_asked_is_expired_not_an_error(bufdir):
    import json
    ev = {"A": [{"date": "2999-01-01", "kind": "EARNINGS", "amount": None}]}
    fetch = Counter(ev)
    BUF.cached_events(["A"], buffer_dir=bufdir, _fetch=fetch)
    path = bufdir / "events.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    path.write_text(json.dumps({**raw, "asked": {"A": "not-a-time"}}), encoding="utf-8")
    assert BUF.cached_events(["A"], buffer_dir=bufdir, _fetch=fetch) == ev and fetch.calls == 2


def test_movers_write_prunes_other_sets_older_than_a_week(bufdir):
    import os
    import time
    bufdir.mkdir(parents=True, exist_ok=True)
    old, recent = bufdir / "movers_old000000000.json", bufdir / "movers_new000000000.json"
    for p, days in ((old, 8), (recent, 2)):
        p.write_text("{}", encoding="utf-8")
        os.utime(p, (time.time() - days * 86400,) * 2)
    BUF.cached_movers(["AAA"], buffer_dir=bufdir, _fetch=Counter(ROWS))
    assert not old.exists() and recent.exists()
    assert len(list(bufdir.glob("movers_*.json"))) == 2          # the new set's file + the recent one


def test_events_fetch_gets_the_same_today_as_the_filter(bufdir):
    import time_machine
    seen = []

    def fetch(due, today=None):
        seen.append(today)
        return {t: [] for t in due}
    with time_machine.travel("2026-10-02 10:00:00", tick=False):
        BUF.cached_events(["A"], buffer_dir=bufdir, _fetch=fetch)
        BUF.cached_events(["B"], buffer_dir=bufdir, _fetch=fetch, today=date(2026, 10, 9))
    assert seen == [date(2026, 10, 2), date(2026, 10, 9)]


def test_quotes_fresh_s_skips_recent_successes_only(bufdir):
    import time_machine
    with time_machine.travel("2026-10-05 09:00:00", tick=False):
        cached_quotes(["A"], buffer_dir=bufdir, _fetch=Counter({"A": _q(10.0)}))
    with time_machine.travel("2026-10-05 09:00:50", tick=False):
        cached_quotes(["B"], buffer_dir=bufdir, _fetch=Counter({"B": _q(5.0)}))
    asked = []

    def fetch(ts):
        asked.append(sorted(ts))
        return {t: _q(11.0) for t in ts}
    with time_machine.travel("2026-10-05 09:01:00", tick=False):     # A is 60 s old, B 10 s
        quotes, stale, _ = cached_quotes(["A", "B"], force=True, fresh_s=45, buffer_dir=bufdir, _fetch=fetch)
        assert asked == [["A"]] and stale == {}
        assert quotes["A"]["price"] == 11.0 and quotes["B"]["price"] == 5.0
        cached_quotes(["B"], force=True, fresh_s=45, buffer_dir=bufdir, _fetch=fetch)
        assert asked == [["A"]]                                     # nothing due: no fetch at all
        cached_quotes(["B"], force=True, buffer_dir=bufdir, _fetch=fetch)
        assert asked == [["A"], ["B"]]                              # default fresh_s=0 = today's behaviour


def test_never_quoted_reads_the_quote_buffer_without_fetching_or_writing(bufdir):
    assert BUF.never_quoted(["A", "B"], buffer_dir=bufdir) == set()           # no buffer yet: no claim
    cached_quotes(["A", "B"], buffer_dir=bufdir, _fetch=Counter({"A": _q(10.0), "B": None}))
    path = bufdir / "quotes.json"
    before = (path.read_text(encoding="utf-8"), path.stat().st_mtime_ns)
    assert BUF.never_quoted(["A", "B", "C"], buffer_dir=bufdir) == {"B", "C"}
    assert (path.read_text(encoding="utf-8"), path.stat().st_mtime_ns) == before


def test_history_seen_says_when_each_line_was_really_priced(bufdir):
    """A reader may cap each line at the sessions before its fetch: a line filled from the cache keeps the
    time Yahoo last priced it, not the merge's."""
    import os
    import time
    tk = ["AAA.F", "BBB.F"]
    assert BUF.history_seen(tk, buffer_dir=bufdir) == {}                        # no cache yet
    cached_price_history(tk, buffer_dir=bufdir, _fetch=Counter(GOOD))
    _age(bufdir, 2)
    then = BUF.history_seen(tk, buffer_dir=bufdir)
    assert set(then) == set(tk) and all(time.time() - v > 86400 for v in then.values())
    cached_price_history(tk, buffer_dir=bufdir, _fetch=Counter(NEWER), force=True)   # BBB.F throttled
    now = BUF.history_seen(tk, buffer_dir=bufdir)
    assert time.time() - now["AAA.F"] < 60 and now["BBB.F"] == pytest.approx(then["BBB.F"])
    for f in bufdir.glob("hist_*.seen.json"):                                   # an older cache, no sidecar
        f.unlink()
    born = next(bufdir.glob("hist_*.pkl")).stat().st_mtime
    assert BUF.history_seen(tk, buffer_dir=bufdir) == {"AAA.F": born, "BBB.F": born}
    assert BUF.history_seen(tk, period="1y", buffer_dir=bufdir) == {} and os.listdir(bufdir)


# ── cached_info: Yahoo identity, 30 days per name; a failure is retried after a day ────────────

def test_info_is_cached_thirty_days_and_a_failure_one_day(bufdir, monkeypatch):
    import time_machine
    calls = []
    answers = {"GOOD.F": {"name": "Good AG", "sector": "Industrials", "country": "Germany"}, "BAD.F": None}

    def fetch(t):
        calls.append(t)
        return answers[t]
    with time_machine.travel("2026-06-01 10:00", tick=False):
        got = BUF.cached_info(["GOOD.F", "BAD.F"], buffer_dir=bufdir, _fetch=fetch)
        assert got == {"GOOD.F": answers["GOOD.F"], "BAD.F": None}
        assert sorted(calls) == ["BAD.F", "GOOD.F"]
        BUF.cached_info(["GOOD.F", "BAD.F"], buffer_dir=bufdir, _fetch=fetch)
        assert len(calls) == 2                                      # both served from the cache
    with time_machine.travel("2026-06-02 10:01", tick=False):        # a day later: only the failure again
        BUF.cached_info(["GOOD.F", "BAD.F"], buffer_dir=bufdir, _fetch=fetch)
        assert calls[2:] == ["BAD.F"]
    answers["GOOD.F"] = None                                        # Yahoo hiccups at the 30-day refresh
    with time_machine.travel("2026-07-02 10:00", tick=False):
        got = BUF.cached_info(["GOOD.F"], buffer_dir=bufdir, _fetch=fetch)
        assert calls[3:] == ["GOOD.F"] and got["GOOD.F"]["name"] == "Good AG"   # last-good kept
    with time_machine.travel("2026-07-02 18:00", tick=False):
        BUF.cached_info(["GOOD.F"], buffer_dir=bufdir, _fetch=fetch)
        assert len(calls) == 4                                      # the failed refresh waits a day


def test_info_fetch_exception_counts_as_a_failure(bufdir):
    def boom(t):
        raise RuntimeError("down")
    assert BUF.cached_info(["X.F"], buffer_dir=bufdir, _fetch=boom) == {"X.F": None}
    assert BUF.cached_info([], buffer_dir=bufdir, _fetch=boom) == {}


def test_a_profile_kept_before_funds_and_crypto_were_told_apart_is_asked_again(bufdir):
    """info.json from before `kind` existed: an ETF / crypto line was 'Unknown' there — ask again, once."""
    import json
    from datetime import datetime
    now = datetime.now().isoformat(timespec="seconds")
    bufdir.mkdir(parents=True, exist_ok=True)
    (bufdir / "info.json").write_text(json.dumps({"data": {"CSPX.AS": {"name": "S&P 500 ETF", "sector": None,
                                                                       "country": None}},
                                                  "asked": {"CSPX.AS": [now, True]}}), encoding="utf-8")
    calls = []
    fresh = {"name": "S&P 500 ETF", "sector": None, "country": None, "kind": "FUND", "sectors": {"Financials": 1.0}}
    got = BUF.cached_info(["CSPX.AS"], buffer_dir=bufdir, _fetch=lambda t: calls.append(t) or fresh)
    assert calls == ["CSPX.AS"] and got["CSPX.AS"]["kind"] == "FUND"
    BUF.cached_info(["CSPX.AS"], buffer_dir=bufdir, _fetch=lambda t: calls.append(t) or fresh)
    assert calls == ["CSPX.AS"]                                            # kept 30 days from now
