"""Screen contract.

A screen is a declarative record: identity (mnemonic, number key), the refresh tiers it
depends on, the modules whose source defines its code version, and two functions:

  compute(tier, ctx[, param]) -> part     the heavy work for one tier; must be picklable
  assemble(parts, meta[, param]) -> dict   cheap; turns {tier: part} into the typed-panel payload

Payload shape — every value-bearing node carries vis; ABSENT = private:
  {"screen", "title", "context": {"text", "vis"}, "meta": {...}, "help": [{"h", "body", "vis"}],
   "panels": [{"id", "n", "title", "type", "span", "rows_span"?, "vis", "context"?, ...}]}
  kpi     items[{k, v, fmt, vis}]
  table   key, sort[col, dir], cols[{k, label, fmt, vis, align?}],
          rows[{<col>: value, _closed?, _stale?}], total?, drives?
  chart   x[unix s], series[{name, y[], kind: "line"|"markers", role, vis}], ranges?, legend?, yfmt?
          — or follows: <table id> + series_by_key{<row key>: {x, series}}
  ledger  lines[{label, v, fmt, op, strong?, vis} | {sep: true, vis}]
  bars    items[{label, v, fmt, vis}]
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd
from typing import Any, Callable, Mapping

from monitor import config
from monitor.config import EQUITY_LOG, PORTFOLIO_CSV

TIERS = ("quote", "daily", "heavy")
# What a screen built from your trades shows while there are none yet — where a registry without TRADES
# (no_trades) says to put them.
NO_PORTFOLIO = ("NO PORTFOLIO YET — put your trades in input/portfolio.csv, or restart the terminal for the example "
                "(python -m monitor init)")


def no_trades(screens: Mapping[str, "Screen"]) -> str:
    """The cold view of a screen built from your trades while there are none (no input/portfolio.csv, or a
    header alone — as START FRESH leaves it): TRADES by the number key this registry gives it (a local add-on
    may move it), else NO_PORTFOLIO."""
    t = screens.get("TRADES")
    if t is None or t.status != "live":
        return NO_PORTFOLIO
    how = f"press {t.fkey} (TRADES)" if t.fkey else "type TRADES"
    return f"NO TRADES YET — {how}: add your trades, paste many or import your broker's CSV"


@dataclass(frozen=True)
class Ctx:
    force: bool = False                 # bypass buffers, fetch live
    buffer_dir: Path | None = None      # None = monitor.data.buffer default
    portfolio_csv: Path = PORTFOLIO_CSV
    equity_log: Path | None = EQUITY_LOG   # None = don't append the daily equity log
    watchlist: Path | None = None       # None = no watchlist (tests, the public export)
    alerts: Path | None = None          # None = no alert rules file (tests, the public export)
    # named state files a local add-on's screens read (monitor.plugins Hooks.ctx_path); absent = none
    paths: Mapping[str, Path | None] = field(default_factory=dict)


def rebuild_day(d: date) -> date:
    """The day a `monthly` screen is rebuilt in d's month (monitor.server.schedule): its second business
    day — the first session of the month must have closed for the old period's last rebalance to run."""
    return (pd.Timestamp(d.year, d.month, 1) + pd.offsets.BDay(0) + pd.offsets.BDay(1)).date()


def code_version(deps: tuple[str, ...]) -> str:
    """Hash of the dependency modules' source files — changes whenever their code does."""
    h = hashlib.sha1()
    for name in sorted(deps):
        spec = importlib.util.find_spec(name)
        if spec is None or not spec.origin:
            raise ModuleNotFoundError(name)
        h.update(name.encode())
        h.update(Path(spec.origin).read_bytes())
    return h.hexdigest()[:12]


def input_fingerprint(ctx: Ctx) -> str:
    """Hash of what you put in input/: portfolio.csv and interest.csv (bytes) and the settings in
    effect — re-read first if settings.toml changed. Folded into a stored part's version (see
    Screen.version), so editing any of them makes the tiers of the screens that read them due."""
    config.refresh_settings()
    h = hashlib.sha1()
    csv = Path(ctx.portfolio_csv)
    for f in (csv, csv.with_name("interest.csv")):
        try:
            h.update(f.read_bytes())
        except OSError:
            h.update(b"<absent>")
        h.update(b"\0")
    h.update(json.dumps(config.SETTINGS, sort_keys=True).encode())
    return h.hexdigest()[:12]


@dataclass(frozen=True)
class Screen:
    id: str
    title: str
    fkey: int | None = None
    status: str = "live"                # live | soon
    public: bool = False                # exported to docs/ by `python -m monitor export`
    tiers: tuple[str, ...] = ()
    deps: tuple[str, ...] = ()
    compute: Callable[[str, Ctx], Any] | None = None
    assemble: Callable[[dict, dict], dict] | None = None
    params: Callable[[Ctx], list[str]] | None = None   # None = unparametrized
    accept: Callable[[Ctx, str], bool] | None = None   # None = a param must be one of params(ctx)
    param_name: str = "TICKER"          # what the param is called: SEC <TICKER>
    unknown_param: str = "NOT A TRADEABLE TICKER"      # the 404 message for a param it does not accept
    # A heavy tier with `build_cmd` runs the child process `python <build_cmd…>` (e.g. "-m monitor
    # build x") only when forced — BUILD <screen> or the month-start schedule (which appends
    # `--scheduled`), never REFRESH; compute("heavy") then just loads what it wrote. `build_eta` is the
    # build's rough duration for the BUILD notice. `stamp` = newest input mtime (epoch s): a newer stamp
    # makes every tier due.
    build_cmd: tuple[str, ...] = ()
    build_eta: str = ""
    stamp: Callable[[Ctx], float | None] | None = None
    monthly: bool = False               # the server rebuilds it at each month's start (server/schedule.py)
    # A monthly screen's build is current once built on or after the rebuild day AND current(ctx) (e.g. its
    # data holds a session of the new period — a holiday can push the first session past the rebuild day)
    current: Callable[[Ctx], bool] | None = None
    needs_portfolio: bool = False       # without trades in ctx.portfolio_csv: a cold view (no_trades), no compute
    uses_inputs: bool = False           # reads the book or the settings: parts are stale when those change
    cold: Callable[[Ctx], str | None] | None = None    # a further one-line reason it cannot compute now
    # Cheap and local (no network): its due tiers compute in the request itself — never queued behind the
    # network tiers — and only its code or your inputs make them due, never age (TRADES).
    inline: bool = False

    def run(self, tier: str, ctx: Ctx, param: str | None = None) -> Any:
        return self.compute(tier, ctx) if self.params is None else self.compute(tier, ctx, param)

    def build(self, parts: dict, meta: dict, param: str | None = None) -> dict:
        return self.assemble(parts, meta) if self.params is None else self.assemble(parts, meta, param)

    def accepts(self, ctx: Ctx, param: str) -> bool:
        """May this screen open for `param`? params(ctx) lists the known ones (autocomplete);
        `accept` widens that (SEC: any tradeable universe ticker)."""
        if self.params is None:
            return False
        return self.accept(ctx, param) if self.accept is not None else param in self.params(ctx)

    def entry(self) -> dict:
        return {"id": self.id, "title": self.title, "fkey": self.fkey, "status": self.status,
                "public": self.public, "tiers": list(self.tiers),
                "param": self.params is not None,
                **({"param_name": self.param_name, "unknown_param": self.unknown_param}
                   if self.params is not None else {}),
                "build": (self.build_eta or "BUILD") if self.build_cmd else None}

    def code_version(self) -> str:
        return code_version(self.deps)

    def version(self, ctx: Ctx) -> str:
        """What a stored part must match to count as current: the code version, plus the input
        fingerprint for a screen that reads your inputs."""
        code = self.code_version()
        return f"{code}+{input_fingerprint(ctx)}" if self.uses_inputs else code


def screen_key(sid: str, param: str | None = None) -> str:
    """Store/job key: 'PORT' or 'SEC~SAP.DE' ('~' never occurs in tickers)."""
    return sid if param is None else f"{sid}~{param}"


def split_key(key: str) -> tuple[str, str | None]:
    sid, _, param = key.partition("~")
    return sid, (param or None)
