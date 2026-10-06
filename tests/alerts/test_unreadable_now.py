"""A file that is there but cannot be read right now (out of file handles — launchd gave the service 256 —,
permissions) is not a corrupt file: the loaders raise instead of reading as defaults / empty, so no check or
edit ever saves defaults over your alert rules, watchlist or prefs. A missing file still means defaults, a
corrupt one still the defaults (alerts: kept as .bad)."""
import errno
import json
from pathlib import Path

import pytest

from monitor.alerts import store as S
from monitor.alerts import watchlist as W
from monitor.alerts.rules import parse_rule
from monitor.server import prefs as P


@pytest.fixture
def emfile(monkeypatch):
    """Path.read_text fails like a process out of file handles."""
    def boom(self, *a, **k):
        raise OSError(errno.EMFILE, "Too many open files", str(self))
    return lambda: monkeypatch.setattr(Path, "read_text", boom)


def test_alerts_unreadable_now_raise_and_keep_the_file(tmp_path, emfile):
    path = tmp_path / "alerts.json"
    mine = S.add_rule(S.defaults(), parse_rule("NVD.F < 180"))[0]
    S.save(path, mine)
    before = path.read_bytes()
    emfile()
    with pytest.raises(OSError):
        S.load(path)
    assert path.read_bytes() == before and not (tmp_path / "alerts.json.bad").exists()


def test_alerts_missing_or_corrupt_still_read_as_defaults(tmp_path):
    assert S.load(tmp_path / "none.json") == S.defaults()
    bad = tmp_path / "alerts.json"
    bad.write_text("{not json", encoding="utf-8")
    assert S.load(bad) == S.defaults() and (tmp_path / "alerts.json.bad").exists()


def test_watchlist_unreadable_now_raises(tmp_path, emfile):
    path = tmp_path / "watchlist.json"
    path.write_text(json.dumps([{"ticker": "SAP.DE", "name": "SAP", "added": "2026-10-01"}]), encoding="utf-8")
    emfile()
    with pytest.raises(OSError):
        W.load(path)


def test_watchlist_missing_or_corrupt_still_reads_empty(tmp_path):
    assert W.load(tmp_path / "none.json") == []
    (tmp_path / "w.json").write_text("[oops", encoding="utf-8")
    assert W.load(tmp_path / "w.json") == []


def test_prefs_unreadable_now_raise(tmp_path, emfile):
    path = tmp_path / "prefs.json"
    path.write_text(json.dumps({"target": "HRP"}), encoding="utf-8")
    emfile()
    with pytest.raises(OSError):
        P.load(path)
