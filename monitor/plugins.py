"""Local add-ons: code that lives outside this repository, on your machine only.

A directory `private/` at the repo root (gitignored) holding a package `monitor_private` with a module
`plugin` is an add-on. load() appends `private/` to sys.path, imports `monitor_private.plugin` and calls its
`register(hooks)` once per process. Through Hooks the add-on may
  * add screens (add_screen, optionally placed before another id; an id the core already uses is replaced
    in place) and set number keys (set_fkey) — monitor.screens applies them to SCREENS;
  * add `python -m monitor` subcommands (add_command: main(argv) -> exit code, plus the line
    `python -m monitor --help` shows for it; init / serve / export stay the core's);
  * add input/settings.toml keys (add_setting, optionally bound to a monitor.config constant);
  * name state files its screens read from Ctx.paths (ctx_path).
MONITOR_PRIVATE=0 skips the add-on (the test suite runs that way, as a fresh clone does). Any error while
loading it is one log line and the core runs without it: nothing it registered is applied."""
from __future__ import annotations

import importlib
import logging
import os
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

from monitor import config

ADDON_DIR = config.REPO_ROOT / "private"
MODULE = "monitor_private.plugin"
CORE_COMMANDS = ("init", "serve", "export")
log = logging.getLogger("monitor.plugins")


@dataclass
class Hooks:
    """What an add-on registered (empty without one)."""
    screens: list = field(default_factory=list)      # [(Screen, before: screen id | None)]
    fkeys: dict = field(default_factory=dict)        # {screen id: number key | None}
    commands: dict = field(default_factory=dict)     # {name: (main, help line)}
    settings: list = field(default_factory=list)     # [(key, default, check, constant | None)]
    paths: dict = field(default_factory=dict)        # {name: Path | None} -> Ctx.paths

    def add_screen(self, screen: Any, *, before: str | None = None) -> None:
        self.screens.append((screen, before))

    def set_fkey(self, sid: str, fkey: int | None) -> None:
        self.fkeys[sid] = fkey

    def add_command(self, name: str, main: Callable[[list[str]], int], help: str = "") -> None:
        if name in CORE_COMMANDS:
            raise ValueError(f"{name!r} is a core command")
        self.commands[name] = (main, help)

    def add_setting(self, key: str, default: Any, check: Callable[[Any], Any], constant: str | None = None) -> None:
        self.settings.append((key, default, check, constant))

    def ctx_path(self, name: str, path: Path | None) -> None:
        self.paths[name] = path


_hooks: Hooks | None = None
_loading = False                 # register() is running: it imports core screens, whose registry waits
_waiting: list[dict] = []        # core registries built meanwhile, patched once the add-on has loaded


def enabled(root: Path | None = None) -> bool:
    """An add-on directory exists and MONITOR_PRIVATE is not 0."""
    return os.environ.get("MONITOR_PRIVATE", "").strip() != "0" and Path(root or ADDON_DIR).is_dir()


def load(root: Path | None = None) -> Hooks:
    """The add-on's registrations, loaded on the first call (empty Hooks without an add-on)."""
    global _hooks, _loading
    if _hooks is None and not _loading:
        _loading = True
        try:
            _hooks = _load(Path(root or ADDON_DIR))
        finally:
            _loading = False
        while _waiting:
            _apply(_waiting.pop(0))
    return _hooks if _hooks is not None else Hooks()


def _load(root: Path) -> Hooks:
    if not enabled(root):
        return Hooks()
    hooks = Hooks()
    try:
        if str(root) not in sys.path:
            sys.path.append(str(root))       # after the repo: the add-on can never shadow a core module
        importlib.import_module(MODULE).register(hooks)
        config.extend_settings(hooks.settings)
    except Exception as e:                   # an import error, a bad registration: the core runs alone
        log.warning("local add-on in %s not loaded — %s: %s", root, type(e).__name__, e)
        return Hooks()
    return hooks


def apply_screens(screens: dict) -> dict:
    """Apply the add-on's screens and number keys to `screens` (the core registry) in place and return
    it. Two screens on one number key, or a key for an unknown id: one log line, the core registry stays.
    Called while the add-on loads (its register() imported the core's screens first), it is patched once
    the load is done."""
    if _loading:
        _waiting.append(screens)
        return screens
    load()
    return _apply(screens)


def _apply(screens: dict) -> dict:
    hooks = _hooks or Hooks()
    if not hooks.screens and not hooks.fkeys:
        return screens
    try:
        items = list(screens.items())
        for scr, before in hooks.screens:
            ids = [k for k, _ in items]
            if scr.id in ids:
                items[ids.index(scr.id)] = (scr.id, scr)
            elif before in ids:
                items.insert(ids.index(before), (scr.id, scr))
            else:
                items.append((scr.id, scr))
        out = dict(items)
        for sid, fkey in hooks.fkeys.items():
            out[sid] = replace(out[sid], fkey=fkey)
        keys = [s.fkey for s in out.values() if s.fkey is not None]
        if len(keys) != len(set(keys)):
            raise ValueError(f"two screens share a number key ({sorted(keys)})")
    except Exception as e:
        log.warning("local add-on screens not applied — %s: %s", type(e).__name__, e)
        return screens
    screens.clear()
    screens.update(out)
    return screens


def screen_ids() -> set[str]:
    """Ids of the screens the add-on registered (the public export never names its private ones)."""
    return {scr.id for scr, _ in load().screens}


def commands() -> dict:
    """{name: (main, help line)} the add-on adds to `python -m monitor`."""
    return dict(load().commands)


def ctx_paths() -> dict:
    """{name: path} the add-on's screens read from Ctx.paths."""
    return dict(load().paths)


def reload_dirs() -> list[str]:
    """Directories the auto-reloading server also watches: the add-on's, when enabled."""
    return [str(ADDON_DIR)] if enabled() else []
