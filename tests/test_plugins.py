"""monitor.plugins: a local add-on in private/ (gitignored) may add screens and number keys, `python -m
monitor` commands, settings keys and Ctx paths. Without one — or with MONITOR_PRIVATE=0, as this suite runs —
the core is unchanged; a broken one is one log line and the core runs alone."""
import json
import logging
import sys
import textwrap
from pathlib import Path

import pytest

from monitor import config, plugins
from monitor.screens.base import Ctx, Screen

PLUGIN = """
from pathlib import Path
from monitor.screens.base import Screen

def register(hooks):
    hooks.add_screen(Screen(id="XTRA", title="Extra", fkey=5, tiers=("daily",)), before="ALRT")
    hooks.set_fkey("ALRT", 6)
    hooks.add_command("hello", lambda argv: print("hello", *argv) or 0, "say hello (an add-on command)")
    hooks.add_setting("xtra_note", "", lambda v: str(v), "XTRA_NOTE")
    hooks.ctx_path("xtra", Path("xtra.json"))
"""


def core() -> dict:
    return {s.id: s for s in (Screen("PORT", "Portfolio", 1), Screen("MKT", "Market", 4),
                              Screen("ALRT", "Alerts", 5))}


@pytest.fixture
def addon(tmp_path, monkeypatch):
    """write(source) lays out private/monitor_private/plugin.py under tmp_path and loads it (MONITOR_PRIVATE=1).
    The settings registry, sys.path and sys.modules are restored afterwards."""
    root = tmp_path / "private"
    (root / "monitor_private").mkdir(parents=True)
    (root / "monitor_private" / "__init__.py").write_text("")
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(plugins, "ADDON_DIR", root)
    monkeypatch.setattr(plugins, "_hooks", None)
    monkeypatch.setenv("MONITOR_PRIVATE", "1")
    saved = dict(config._SETTINGS), dict(config._CONSTANTS)

    def drop_modules():
        for m in [m for m in sys.modules if m.split(".")[0] == "monitor_private"]:
            del sys.modules[m]

    def write(src: str) -> plugins.Hooks:
        (root / "monitor_private" / "plugin.py").write_text(textwrap.dedent(src))
        drop_modules()
        return plugins.load()

    drop_modules()
    yield write
    monkeypatch.undo()                  # sys.path back first — then nothing of this add-on may stay on it
    sys.path[:] = [p for p in sys.path if not p.startswith(str(tmp_path))]
    drop_modules()
    for const in set(config._CONSTANTS) - set(saved[1]):
        vars(config).pop(const, None)
    config._SETTINGS.clear()
    config._SETTINGS.update(saved[0])
    config._CONSTANTS.clear()
    config._CONSTANTS.update(saved[1])
    config.refresh_settings(force=True)


def test_the_suite_runs_without_an_addon():
    """MONITOR_PRIVATE=0 (tests/conftest.py): the registry is the core's — keys 1–5, no gap, no add-on."""
    from monitor.screens import SCREENS
    assert not plugins.load().screens and plugins.commands() == {} and plugins.ctx_paths() == {}
    assert {s.id: s.fkey for s in SCREENS.values()} == {"PORT": 1, "OPT": 2, "RISK": 3, "SEC": None, "MKT": 4,
                                                         "ALRT": 5}


def test_no_addon_directory_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(plugins, "ADDON_DIR", tmp_path / "private")
    monkeypatch.setattr(plugins, "_hooks", None)
    monkeypatch.setenv("MONITOR_PRIVATE", "1")
    reg = core()
    assert plugins.apply_screens(reg) is reg and list(reg) == ["PORT", "MKT", "ALRT"]
    assert plugins.commands() == {} and plugins.ctx_paths() == {} and plugins.reload_dirs() == []


def test_monitor_private_0_skips_the_addon(addon, monkeypatch):
    monkeypatch.setenv("MONITOR_PRIVATE", "0")
    assert addon(PLUGIN) == plugins.Hooks() and "monitor_private.plugin" not in sys.modules
    assert plugins.reload_dirs() == []


def test_an_addon_adds_screens_keys_commands_settings_and_paths(addon, settings_file, capsys):
    hooks = addon(PLUGIN)
    reg = plugins.apply_screens(core())
    assert [(s.id, s.fkey) for s in reg.values()] == [("PORT", 1), ("MKT", 4), ("XTRA", 5), ("ALRT", 6)]
    assert plugins.screen_ids() == {"XTRA"} and plugins.ctx_paths() == {"xtra": Path("xtra.json")}
    assert plugins.reload_dirs() == [str(plugins.ADDON_DIR)]
    from monitor.__main__ import main
    assert main(["hello", "world"]) == 0 and capsys.readouterr().out == "hello world\n"
    assert main(["--help"]) == 0 and "say hello (an add-on command)" in capsys.readouterr().out
    settings_file.write_text('xtra_note = "kept"\n')       # its key is a known setting now, bound to a constant
    config.refresh_settings(force=True)
    assert config.SETTINGS_ERROR is None and config.XTRA_NOTE == "kept" and hooks.settings[0][0] == "xtra_note"


def test_the_engine_hands_the_addons_paths_to_its_screens(addon, monkeypatch):
    addon(PLUGIN)
    from monitor.server.engine import Engine
    eng = Engine.default(prefs_path=None, watchlist=None, alerts=None)
    assert eng.ctx.paths == {"xtra": Path("xtra.json")}
    assert Engine.default(prefs_path=None, watchlist=None, alerts=None, paths={"xtra": None}).ctx.paths == {"xtra": None}
    assert Ctx().paths == {}


@pytest.mark.parametrize("src", [
    "raise ImportError('no module named numpy_that_is_missing')",
    "def register(hooks):\n    hooks.add_command('export', lambda argv: 0)",      # a core command
    "def register(hooks):\n    hooks.add_setting('order_fee_eur', 0, float)",    # a core setting
])
def test_a_broken_addon_is_one_log_line_and_the_core_runs_alone(addon, caplog, src):
    with caplog.at_level(logging.WARNING, logger="monitor.plugins"):
        hooks = addon(src)
    assert hooks == plugins.Hooks()
    assert [r.levelno for r in caplog.records] == [logging.WARNING] and "not loaded" in caplog.text
    assert list(plugins.apply_screens(core())) == ["PORT", "MKT", "ALRT"]
    assert "order_fee_eur" in config._SETTINGS and config.SETTINGS_ERROR is None


def test_two_screens_on_one_key_leave_the_core_registry(addon, caplog):
    addon("""
        from monitor.screens.base import Screen
        def register(hooks):
            hooks.add_screen(Screen(id="XTRA", title="Extra", fkey=5))
    """)
    with caplog.at_level(logging.WARNING, logger="monitor.plugins"):
        reg = plugins.apply_screens(core())
    assert [(s.id, s.fkey) for s in reg.values()] == [("PORT", 1), ("MKT", 4), ("ALRT", 5)]
    assert "not applied" in caplog.text


def test_an_addon_screen_with_a_core_id_replaces_it_in_place(addon):
    addon("""
        from monitor.screens.base import Screen
        def register(hooks):
            hooks.add_screen(Screen(id="MKT", title="My Market", fkey=4))
    """)
    reg = plugins.apply_screens(core())
    assert list(reg) == ["PORT", "MKT", "ALRT"] and reg["MKT"].title == "My Market"


def test_the_export_never_names_an_addons_private_screen(addon, tmp_path, monkeypatch):
    """A local add-on's private screens are not even listed in the public snapshot's screens.json."""
    from monitor.server.engine import Engine
    from monitor.server.export import export
    from monitor.server.store import Store
    from tests.server.helpers import Recorder, make_screen
    addon("""
        from monitor.screens.base import Screen
        def register(hooks):
            hooks.add_screen(Screen(id="XTRA", title="Extra", fkey=8, tiers=("daily",)))
    """)
    web = tmp_path / "web"
    (web / "app").mkdir(parents=True)
    (web / "index.html").write_text('<head><meta name="im-mode" content="live"></head>')
    pub, _, _ = make_screen(tmp_path, monkeypatch, panels=[])
    reg = plugins.apply_screens({"FAKE": pub, "SEC": Screen("SEC", "Security", None, params=lambda ctx: [])})
    eng = Engine(reg, Store(tmp_path / "store"), Recorder(), ctx=Ctx(buffer_dir=tmp_path))
    export(tmp_path / "docs", engine=eng, web_dir=web)
    listed = [s["id"] for s in json.loads((tmp_path / "docs" / "data" / "screens.json").read_text())["screens"]]
    assert listed == ["FAKE", "SEC"] and not list((tmp_path / "docs" / "data").glob("XTRA*"))


def test_a_registry_built_while_the_addon_loads_is_patched_once_it_has(addon):
    """The add-on's register() imports core screens — the first import of monitor.screens may happen right
    there, mid-load: that registry is patched as soon as the load is done, never left without the add-on."""
    addon("""
        from monitor import plugins
        from monitor.screens.base import Screen
        BUILT = plugins.apply_screens({"PORT": Screen("PORT", "Portfolio", 1)})    # as monitor.screens would

        def register(hooks):
            hooks.add_screen(Screen(id="XTRA", title="Extra", fkey=2))
    """)
    assert list(sys.modules["monitor_private.plugin"].BUILT) == ["PORT", "XTRA"]
