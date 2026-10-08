"""Paths and settings for the whole package — the one place to change them.

Your personal values live in input/settings.toml (optional, gitignored; every key is documented
in examples/settings.example.toml) and are read once, here. MONITOR_SETTINGS=<path> points at
another file (the test suite uses it so it never reads yours)."""
import json
import math
import os
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INPUT_DIR = REPO_ROOT / "input"
LOCAL_DIR = REPO_ROOT / "local"
BUFFER_DIR = LOCAL_DIR / "buffer"
SCREENS_DIR = BUFFER_DIR / "screens"
DOCS_DIR = REPO_ROOT / "docs"
WEB_DIR = REPO_ROOT / "web"
PORTFOLIO_CSV = INPUT_DIR / "portfolio.csv"
EQUITY_LOG = LOCAL_DIR / "equity_log.csv"
EXAMPLES_DIR = REPO_ROOT / "examples"    # the invented starter files `python -m monitor init` copies
_EXPLICIT_SETTINGS = Path(os.environ["MONITOR_SETTINGS"]) if os.environ.get("MONITOR_SETTINGS") else None
SETTINGS_FILE = _EXPLICIT_SETTINGS or INPUT_DIR / "settings.toml"


# ── input/settings.toml ──────────────────────────────────────────────────────
def _number(lo: float, hi: float | None, what: str):
    def check(v):
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) \
                or v < lo or (hi is not None and v >= hi):
            raise ValueError(what)
        return float(v)
    return check


def _tickers(v):
    if not isinstance(v, list) or not all(isinstance(t, str) and t.strip() for t in v):
        raise ValueError('must be a list of tickers, e.g. ["IWDA.AS"]')
    return tuple(t.strip() for t in v)


def _table(v):
    if not isinstance(v, dict) or not all(isinstance(x, str) and x.strip() for x in v.values()):
        raise ValueError('must be a table of "TICKER" = "text" lines')
    return {str(k).strip(): x.strip() for k, x in v.items()}


# key -> (default, validator). Defaults fit a Trade Republic account taxed in Germany.
_SETTINGS = {
    "order_fee_eur": (1.0, _number(0, None, "must be a number >= 0")),
    "savings_plan_tickers": ((), _tickers),
    "dividend_tax": (0.26375, _number(0, 1, "must be a fraction between 0 and 1, e.g. 0.26375")),
    "tickers": ({}, _table),
    "isins": ({}, _table),                  # ISIN -> ticker, for a broker export the terminal cannot place
    "names": ({}, _table),
    "sectors": ({}, _table),
    "countries": ({}, _table),
}


def load_settings(path: Path, explicit: bool = False) -> tuple[dict, str | None]:
    """(settings, error). A missing file gives the defaults (and says so when the path was given
    explicitly, via MONITOR_SETTINGS). A file that is not valid TOML gives every default; a bad or
    unknown key falls back to its default alone. `error` is one line naming the file and each
    offending key (None when clean) — never an exception, so the server always starts."""
    path = Path(path)
    out = {k: dict(d) if isinstance(d, dict) else d for k, (d, _) in _SETTINGS.items()}   # fresh tables
    try:
        shown = path.resolve().relative_to(REPO_ROOT).as_posix()      # input/settings.toml on Windows too
    except ValueError:
        shown = path
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8-sig"))      # a Windows editor's BOM is fine
    except FileNotFoundError:
        return out, (f"{shown}: MONITOR_SETTINGS points at a missing file — using the defaults"
                     if explicit else None)
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as e:
        return out, f"{shown}: not valid TOML ({e}) — using the defaults"
    bad = []
    for key, value in raw.items():
        if key not in _SETTINGS:
            bad.append(f"unknown key {key!r} ignored")
            continue
        try:
            out[key] = _SETTINGS[key][1](value)
        except ValueError as e:
            d = _SETTINGS[key][0]
            bad.append(f"{key} {e} — default {json.dumps(list(d) if isinstance(d, tuple) else d)} used")
    return out, (f"{shown}: " + "; ".join(bad)) if bad else None


# module constant -> settings key; refresh_settings() re-binds them all
_CONSTANTS = {"ORDER_FEE_EUR": "order_fee_eur", "SAVINGS_PLAN_TICKERS": "savings_plan_tickers",
              "DIVIDEND_TAX": "dividend_tax"}
_on_settings: list = []                   # merge hooks (instruments / meta maps), run after each (re-)read
_settings_stamp = None                    # (mtime_ns, size) of SETTINGS_FILE when last read


def _stamp(path: Path):
    try:
        st = Path(path).stat()
    except OSError:
        return None
    return st.st_mtime_ns, st.st_size


def on_settings(fn):
    """Register `fn()` to run after every settings (re-)read — the map merges use it."""
    _on_settings.append(fn)
    return fn


def refresh_settings(force: bool = False) -> bool:
    """Re-read SETTINGS_FILE when it changed (mtime or size) since the last read — a stat, so cheap
    enough for every freshness check — re-bind the constants and re-run the merge hooks, so an edit
    to settings.toml takes effect without a restart. True when it re-read."""
    global SETTINGS, SETTINGS_ERROR, _settings_stamp
    stamp = _stamp(SETTINGS_FILE)
    if not force and stamp == _settings_stamp:
        return False
    _settings_stamp = stamp
    SETTINGS, SETTINGS_ERROR = load_settings(SETTINGS_FILE, explicit=Path(SETTINGS_FILE) == _EXPLICIT_SETTINGS)
    globals().update({name: SETTINGS[key] for name, key in _CONSTANTS.items()})
    for fn in _on_settings:
        fn()
    return True


def extend_settings(items) -> None:
    """Add settings keys — a local add-on's (monitor.plugins): (key, default, check, constant | None) each,
    `check` as the validators above (raise ValueError), `constant` the module name it binds. All or none:
    an existing key raises ValueError. Then settings.toml is re-read so they apply."""
    items = list(items)
    for key, _default, check, const in items:
        if key in _SETTINGS or (const and (const in _CONSTANTS or const in globals())) or not callable(check):
            raise ValueError(f"settings key {key!r}: exists or has no validator")
    for key, default, check, const in items:
        _SETTINGS[key] = (default, check)
        if const:
            _CONSTANTS[const] = key
    if items:
        refresh_settings(force=True)


def merge_over(target: dict, builtin: dict, yours: dict) -> None:
    """target := builtin overlaid with yours, IN PLACE (every importer holds the same dict object)."""
    want = {**builtin, **yours}
    for k in [k for k in target if k not in want]:
        del target[k]
    target.update(want)


SETTINGS: dict = {}
SETTINGS_ERROR: str | None = None
ORDER_FEE_EUR: float = 1.0          # per buy order (settings: order_fee_eur)
SAVINGS_PLAN_TICKERS: tuple = ()    # savings-plan executions carry no order fee (settings: savings_plan_tickers)
# Default: German Abgeltungsteuer incl. Soli; foreign withholding is credited against it — what lands
# in the account, approximately (settings: dividend_tax).
DIVIDEND_TAX: float = 0.26375
refresh_settings(force=True)

# Optimizer
LOOKBACK_DAYS = 365      # window for the COVARIANCE estimate (the trustworthy input)
RF = 0.045               # risk-free rate (annual)
LONG_ONLY = True
MAX_W = 0.35             # max single-position weight
BL_DELTA = 2.5           # Black-Litterman market risk-aversion (standard ≈ 2.5)
BL_TAU = 0.05            # Black-Litterman prior uncertainty scale
# Optional subjective views for Black-Litterman. Empty = pure market-implied.
# Example: [{"assets": {"SAP.DE": 1}, "ret": 0.12, "confidence": 0.5}]
BL_VIEWS: list[dict] = []

# Trade Republic order costs (ORDER_FEE_EUR, SAVINGS_PLAN_TICKERS above, from settings) are charged
# to the cash-flow-matched benchmark buys so they pay what you paid (your CSV amounts include fees,
# which cannot be split out per row).

# Refresh policy
QUOTE_INTERVAL_S = 60    # quote tier max age while a tab is open
BENCH_QUOTE_S = 300      # PORT re-asks the benchmark lines' live quotes at most this often
DAILY_TTL_H = 12         # daily tier max age
FAILED_RETRY_MIN = 10    # a tier whose last run failed is not auto-run again sooner (REFRESH still runs at once)
TRADES_SETTLE_S = 2.5    # after TRADES writes your trades, the portfolio screens recompute once the burst settles
HISTORY_FILL_DAYS = 3    # a history line Yahoo leaves out is filled from the cache only if priced this recently

# Server
HOST = "127.0.0.1"
PORT = 8000
SERVICE_PORT = 47800     # the macOS service's (monitor.server.service): 8000 stays free for other projects
SERVICE_IDLE_MIN = 15    # the service's terminal stops this long after the last tab closes

# Optimizer targets
PORTFOLIOS = ("MINVAR", "RP", "HRP", "BLSHARPE", "BLSAME")
DEFAULT_TARGET = "HRP"
PREFS_FILE = BUFFER_DIR / "prefs.json"

# Market & alerts
UNIVERSE_DIR = REPO_ROOT / "data" / "universe"    # TR universe lookup (tr_* files are gitignored)
WATCHLIST_FILE = BUFFER_DIR / "watchlist.json"
ALERTS_FILE = BUFFER_DIR / "alerts.json"
ALERT_INTERVAL_S = 60    # the alert loop's cadence while a terminal tab is connected
STALE_ALERT_MIN = 15     # the STALE alert waits this long without a good quote (a Yahoo hiccup is not news)
LIQUID_N = 500           # MKT movers rank the N most liquid live TR stocks
MOVERS_TTL_MIN = 15      # movers refetch at most this often (one batched download)

# Builds: screens whose heavy tier runs a child process (Screen.build_cmd — none in the core; monitor.plugins)
SCHEDULE_FILE = BUFFER_DIR / "schedule.json"      # month-start rebuilds: the day each screen was last tried
BUILD_TIMEOUT_MIN = 60    # a build child running longer is killed (and its build lock counts as stale)
