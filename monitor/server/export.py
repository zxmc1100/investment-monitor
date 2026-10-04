"""`python -m monitor export` — the public static site for GitHub Pages.

Writes docs/index.html (the SPA switched to static mode), docs/app/ (web assets),
docs/data/<SCREEN>.json for every live screen flagged public — each payload passed
through public_view(), so only explicitly public nodes are published — and
docs/data/screens.json listing every core screen: a published one with its full entry, a public one
that could not compute with `cold` (the reason), a private one as {id, fkey, title, public: false}
and never a data file; a local add-on's private screens (monitor.plugins) are not even named. All
three parts are built in a temp folder beside them and swapped in only once every screen computed: a
failure midway leaves the last published snapshot whole.

The terminal's store is never written and its private preferences never published: a fresh
export computes into a throw-away store, and --cached re-assembles each screen from the stored
parts (read-only) — both with DEFAULT prefs (OPT exports with TARGET HRP).
"""
import argparse
import json
import os
import shutil
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

from monitor import config, plugins
from monitor.server.engine import Engine
from monitor.server.redact import public_view
from monitor.server.store import Store
from monitor.server.stream import Broker

LIVE_META = '<meta name="im-mode" content="live">'
STATIC_META = '<meta name="im-mode" content="static">'


NOTHING = "NOTHING TO PUBLISH — no input/portfolio.csv"
EXAMPLE = ("input/portfolio.csv is still the example portfolio (examples/portfolio.example.csv) — "
           "put your own trades there before publishing")


def refuse_unpublishable(engine: Engine) -> None:
    """RuntimeError(one line) when this export must not run: a settings error (the numbers would use
    defaults you did not choose), no portfolio, or the untouched example portfolio."""
    config.refresh_settings()
    if config.SETTINGS_ERROR:
        raise RuntimeError(f"{config.SETTINGS_ERROR} — fix it before publishing")
    if not any(s.public and s.status == "live" and s.needs_portfolio for s in engine.screens.values()):
        return
    csv = Path(engine.ctx.portfolio_csv)
    if not csv.exists():
        raise RuntimeError(NOTHING)
    example = config.EXAMPLES_DIR / "portfolio.example.csv"
    if example.exists() and csv.read_bytes() == example.read_bytes():
        raise RuntimeError(EXAMPLE)


def export(out_dir: Path = config.DOCS_DIR, *, engine: Engine | None = None, cached: bool = False,
           web_dir: Path = config.WEB_DIR) -> list[Path]:
    engine = engine or Engine.default(prefs_path=None, watchlist=None, alerts=None)
    out_dir = Path(out_dir)
    if out_dir.resolve() == config.REPO_ROOT.resolve():
        raise RuntimeError("refusing to export into the repo root")
    web_dir = Path(web_dir)
    if not (web_dir / "app").is_dir():
        raise RuntimeError(f"{web_dir / 'app'} is not a directory")
    page = (web_dir / "index.html").read_text() if (web_dir / "index.html").is_file() else ""
    if LIVE_META not in page:
        raise RuntimeError("web/index.html lacks the im-mode meta tag")
    refuse_unpublishable(engine)                      # before touching docs/: the published snapshot stays
    out_dir.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".export-", suffix=".tmp", dir=out_dir))   # same disk: swaps are renames
    try:
        data = stage / "data"
        data.mkdir()
        published, entries, addon = [], [], plugins.screen_ids()
        with tempfile.TemporaryDirectory(prefix="im-export-") as tmp:
            # Default prefs (prefs_path=None), no watchlist, no alert rules, no job workers: the public
            # build never even reads your watch state, and everything runs inline here.
            src = Engine(engine.screens, engine.store if cached else Store(Path(tmp)), Broker(),
                         ctx=replace(engine.ctx, watchlist=None, alerts=None), workers=0, prefs_path=None)
            for sid, scr in engine.screens.items():
                if not scr.public or scr.params is not None:      # local terminal only: named, never published
                    if sid not in addon:
                        entries.append({"id": scr.id, "fkey": scr.fkey, "title": scr.title, "public": False})
                    continue
                if scr.status != "live":
                    entries.append(scr.entry())
                    continue
                if reason := src.cold_reason(sid):         # e.g. OPT with one position: publish the rest
                    print(f"skipped {sid}: {reason}", file=sys.stderr)
                    entries.append({**scr.entry(), "cold": reason})
                    continue
                payload = src.build_stored(sid) if cached else src.compute_now(sid, force=True)
                if payload is None:
                    raise RuntimeError(f"{sid}: no stored payload at the current code and inputs — run export without --cached")
                (data / f"{sid}.json").write_text(json.dumps(public_view(payload), allow_nan=False, separators=(",", ":")))
                published.append(sid)
                entries.append(scr.entry())
        (data / "screens.json").write_text(json.dumps({"screens": entries}, allow_nan=False))
        shutil.copytree(web_dir / "app", stage / "app")
        (stage / "index.html").write_text(page.replace(LIVE_META, STATIC_META))
        for name in ("data", "app", "index.html"):
            _swap(stage / name, out_dir / name)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return [*(out_dir / "data" / f"{sid}.json" for sid in published), out_dir / "data" / "screens.json",
            out_dir / "app", out_dir / "index.html"]


def _swap(new: Path, target: Path) -> None:
    """Put `new` in `target`'s place by renames; the old one is removed only once the new one is in."""
    old = target.with_name(f".{target.name}.old-{os.getpid()}.tmp")
    if target.exists() or target.is_symlink():
        os.replace(target, old)
    try:
        os.replace(new, target)
    except BaseException:
        if old.exists():
            os.replace(old, target)
        raise
    if old.is_dir() and not old.is_symlink():
        shutil.rmtree(old, ignore_errors=True)
    else:
        old.unlink(missing_ok=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m monitor export")
    ap.add_argument("--cached", action="store_true", help="re-assemble from the stored parts (default prefs, store untouched) instead of recomputing")
    ap.add_argument("--out", type=Path, default=config.DOCS_DIR)
    args = ap.parse_args(argv)
    try:
        written = export(args.out, cached=args.cached)
    except RuntimeError as e:                       # a known refusal: one line, no traceback
        print(e, file=sys.stderr)
        return 1
    for p in written:
        print("wrote", p)
    return 0
