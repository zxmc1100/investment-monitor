import os
import shutil
from pathlib import Path

# Before anything imports monitor.config: the suite never reads your input/settings.toml — every
# test runs on the defaults (an empty fixture file), whatever the machine it runs on.
os.environ["MONITOR_SETTINGS"] = str(Path(__file__).resolve().parent / "fixtures" / "no-settings.toml")
# ...and never loads a local add-on (private/, monitor.plugins): it runs as a fresh clone does.
os.environ["MONITOR_PRIVATE"] = "0"

import pytest
import time_machine

from monitor.data import buffer as BUF
from monitor.portfolio import equity_log
from tests import fakes_yf

FIXTURES = Path(__file__).resolve().parent / "fixtures"
FROZEN = "2026-06-30 14:00:00+00:00"


@pytest.fixture(autouse=True)
def _fixture_universe(monkeypatch):
    """Every test sees the 11-name fixture universe, never the real (gitignored) TR files."""
    from monitor import config
    monkeypatch.setattr(config, "UNIVERSE_DIR", FIXTURES / "universe")


@pytest.fixture(autouse=True)
def _no_yahoo_profiles(monkeypatch):
    """Who-is lookups for a ticker no map knows (monitor.screens.identity) never reach Yahoo in a
    test: the profile fetch answers "unknown" unless a test scripts it."""
    from monitor.data import nasdaq, yahoo
    monkeypatch.setattr(yahoo, "fetch_info", lambda ticker: None)
    # nor do dividend pay dates (monitor.data.buffer.cached_pay_dates): no US home line is ever found
    monkeypatch.setattr(yahoo, "us_listing", lambda name: None)
    monkeypatch.setattr(yahoo, "dividend_pair", lambda symbol: None)
    monkeypatch.setattr(nasdaq, "dividend_history", lambda symbol, timeout=15: [])


@pytest.fixture
def port_env(tmp_path, monkeypatch):
    """The fixture portfolio on fake Yahoo, time frozen, a private buffer, no equity-log writes: yields
    the portfolio CSV's path (under tmp_path/input)."""
    fakes_yf.install(monkeypatch)
    (tmp_path / "input").mkdir()
    shutil.copy(FIXTURES / "portfolio_small.csv", tmp_path / "input" / "portfolio.csv")
    monkeypatch.setattr(BUF, "BUFFER_DIR", tmp_path / "buffer")
    monkeypatch.setattr(equity_log, "append_snapshot", lambda *a, **k: None)
    with time_machine.travel(FROZEN, tick=False):
        yield tmp_path / "input" / "portfolio.csv"


@pytest.fixture
def settings_file(tmp_path):
    """Point monitor.config at a tmp settings.toml (re-read on change, like the live server); the
    suite's empty settings come back — re-read — afterwards."""
    from monitor import config
    orig = config.SETTINGS_FILE
    path = tmp_path / "settings.toml"
    config.SETTINGS_FILE = path
    config.refresh_settings(force=True)
    yield path
    config.SETTINGS_FILE = orig
    config.refresh_settings(force=True)
