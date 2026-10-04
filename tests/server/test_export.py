import json
from pathlib import Path

import pytest

from monitor.screens.base import Ctx, Screen
from monitor.server.engine import Engine
from monitor.server.export import STATIC_META, export
from monitor.server.store import Store
from tests.server.helpers import Recorder, make_screen

PANELS = [{"id": "k", "n": 1, "title": "K", "type": "kpi", "vis": "public",
           "items": [{"k": "ROI", "v": 12.5, "fmt": "pct+", "vis": "public"},
                     {"k": "VALUE", "v": 987654.32, "fmt": "eur"}]}]


@pytest.fixture
def web(tmp_path):
    w = tmp_path / "web"
    (w / "app" / "render").mkdir(parents=True)
    (w / "index.html").write_text('<head><meta name="im-mode" content="live"></head>')
    (w / "app" / "app.js").write_text("export {};")
    (w / "app" / "render" / "kpi.js").write_text("export {};")
    return w


def _engine(tmp_path, monkeypatch):
    scr, _, _ = make_screen(tmp_path, monkeypatch, panels=PANELS)
    return Engine({scr.id: scr, "SOON": Screen("SOON", "Later", 2, status="soon", public=True)},
                  Store(tmp_path / "store"), Recorder(), ctx=Ctx(buffer_dir=tmp_path))


def test_export_writes_public_static_site(tmp_path, monkeypatch, web):
    out = tmp_path / "docs"
    (out / "data").mkdir(parents=True)
    (out / "data" / "STALE.json").write_text("{}")
    export(out, engine=_engine(tmp_path, monkeypatch), web_dir=web)
    data = (out / "data" / "FAKE.json").read_text()
    assert "12.5" in data and "987654.32" not in data and "parts" not in json.loads(data)
    reg = json.loads((out / "data" / "screens.json").read_text())
    assert reg == {"screens": reg["screens"]} and [s["id"] for s in reg["screens"]] == ["FAKE", "SOON"]
    assert all({"id", "fkey", "title", "public"} <= set(s) for s in reg["screens"])   # every screen, flagged
    assert reg["screens"][1]["status"] == "soon" and not (out / "data" / "SOON.json").exists()
    assert not (out / "data" / "STALE.json").exists()
    assert STATIC_META in (out / "index.html").read_text()
    assert (out / "app" / "render" / "kpi.js").exists()


def test_export_cached_without_payload_fails(tmp_path, monkeypatch, web):
    with pytest.raises(RuntimeError, match="no stored payload"):
        export(tmp_path / "docs", engine=_engine(tmp_path, monkeypatch), cached=True, web_dir=web)


def test_parametrized_screens_are_never_exported(tmp_path, monkeypatch, web):
    scr, _, _ = make_screen(tmp_path, monkeypatch, sid="P", params=["AAA"], panels=PANELS)
    eng = Engine({"P": scr}, Store(tmp_path / "store"), Recorder(), ctx=Ctx(buffer_dir=tmp_path))
    export(tmp_path / "docs", engine=eng, web_dir=web)
    assert not list((tmp_path / "docs" / "data").glob("P*.json"))


def test_export_opt_and_risk_public_views(tmp_path, monkeypatch, web):
    import time_machine
    from monitor.screens import SCREENS
    from tests import fakes_yf
    fakes_yf.install(monkeypatch)
    fix = Path(__file__).resolve().parent.parent / "fixtures" / "portfolio_small.csv"
    with time_machine.travel("2026-06-30 14:00:00+00:00", tick=False):
        eng = Engine({k: SCREENS[k] for k in ("OPT", "RISK", "SEC")}, Store(tmp_path / "store"), Recorder(),
                     ctx=Ctx(portfolio_csv=fix, buffer_dir=tmp_path / "buf", equity_log=None))
        export(tmp_path / "docs", engine=eng, web_dir=web)
    data = tmp_path / "docs" / "data"
    assert sorted(p.name for p in data.glob("*.json")) == ["OPT.json", "RISK.json", "screens.json"]
    text = (data / "OPT.json").read_text() + (data / "RISK.json").read_text()
    for forbidden in ('"ticket"', '"now_eur"', '"d_eur"', '"loss"', "VaR95 1D €"):
        assert forbidden not in text


def _files(d: Path) -> dict:
    return {p.name: (p.stat().st_mtime_ns, p.read_bytes()) for p in sorted(d.iterdir())}


def test_export_never_writes_the_live_store(tmp_path, monkeypatch, web):
    eng = _engine(tmp_path, monkeypatch)
    eng.compute_now("FAKE")                                   # the live store holds parts + payload
    before = _files(tmp_path / "store")
    export(tmp_path / "docs", engine=eng, web_dir=web)                 # fresh: a throw-away store
    export(tmp_path / "docs2", engine=eng, web_dir=web, cached=True)   # cached: read-only re-assemble
    assert _files(tmp_path / "store") == before
    assert "12.5" in (tmp_path / "docs2" / "data" / "FAKE.json").read_text()


def test_cached_export_assembles_opt_with_default_target(tmp_path, monkeypatch, web):
    import time_machine
    from monitor.screens import SCREENS
    from tests import fakes_yf
    fakes_yf.install(monkeypatch)
    fix = Path(__file__).resolve().parent.parent / "fixtures" / "portfolio_small.csv"
    with time_machine.travel("2026-06-30 14:00:00+00:00", tick=False):
        eng = Engine({"OPT": SCREENS["OPT"]}, Store(tmp_path / "store"), Recorder(),
                     ctx=Ctx(portfolio_csv=fix, buffer_dir=tmp_path / "buf", equity_log=None),
                     prefs_path=tmp_path / "prefs.json")
        eng.compute_now("OPT")
        eng.set_target("RP")                                      # the private, stored preference
        assert eng.payload("OPT")["meta"]["prefs"]["target"] == "RP"
        export(tmp_path / "docs", engine=eng, web_dir=web, cached=True)
    pub = json.loads((tmp_path / "docs" / "data" / "OPT.json").read_text())
    tgt = next(p for p in pub["panels"] if p["id"] == "target")
    assert {i["k"]: i["v"] for i in tgt["items"]}["TARGET"] == "HRP"
    assert "TARGET RP" not in json.dumps(pub)
    assert eng.payload("OPT")["meta"]["prefs"]["target"] == "RP"   # the live payload is untouched


def test_export_never_reads_watch_state(tmp_path, web):
    seen = []

    def compute(tier, ctx):
        seen.append((ctx.watchlist, ctx.alerts))
        return {}
    scr = Screen("W", "W", 9, public=True, tiers=("quote",), deps=("monitor.config",), compute=compute,
                 assemble=lambda parts, meta: {"screen": "W", "title": "W", "meta": meta, "help": [], "panels": []})
    eng = Engine({"W": scr}, Store(tmp_path / "store"), Recorder(),
                 ctx=Ctx(buffer_dir=tmp_path, watchlist=tmp_path / "w.json", alerts=tmp_path / "a.json"))
    export(tmp_path / "docs", engine=eng, web_dir=web)
    assert seen == [(None, None)]


def test_export_publishes_mkt_public_panels_only(tmp_path, monkeypatch, web):
    import time_machine
    from monitor.alerts import watchlist
    from monitor.screens import SCREENS
    from tests import fakes_yf
    fakes_yf.install(monkeypatch)
    fix = Path(__file__).resolve().parent.parent / "fixtures" / "portfolio_small.csv"
    wl = tmp_path / "watchlist.json"
    watchlist.add(wl, "FNTN.DE", "freenet")                 # watched, not liquid: must never be published
    with time_machine.travel("2026-06-30 14:00:00+00:00", tick=False):
        eng = Engine({k: SCREENS[k] for k in ("MKT", "ALRT")}, Store(tmp_path / "store"), Recorder(),
                     ctx=Ctx(portfolio_csv=fix, buffer_dir=tmp_path / "buf", equity_log=None,
                             watchlist=wl, alerts=tmp_path / "alerts.json"))
        export(tmp_path / "docs", engine=eng, web_dir=web)
    data = tmp_path / "docs" / "data"
    assert sorted(p.name for p in data.glob("*.json")) == ["MKT.json", "screens.json"]
    text = (data / "MKT.json").read_text()
    pub = json.loads(text)
    assert [p["id"] for p in pub["panels"]] == ["indices", "fx", "cmdty", "gainers", "losers", "spikes", "sectors"]
    for forbidden in ("FNTN.DE", "freenet", "€", '"names"', '"events"', '"last"', '"stale"'):
        assert forbidden not in text
    assert len(text) < 300_000


def _book_engine(tmp_path, csv_text=None, monkeypatch=None):
    from monitor.screens import SCREENS
    from tests import fakes_yf
    if monkeypatch is not None:                   # a regression that computed anyway must never reach Yahoo
        fakes_yf.install(monkeypatch)
    csv = tmp_path / "input" / "portfolio.csv"
    if csv_text is not None:
        csv.parent.mkdir(parents=True, exist_ok=True)
        csv.write_text(csv_text)
    return Engine(dict(SCREENS), Store(tmp_path / "store"), Recorder(), ctx=Ctx(portfolio_csv=csv, buffer_dir=tmp_path, equity_log=None))


def _published(tmp_path):
    out = tmp_path / "docs"
    (out / "data").mkdir(parents=True)
    (out / "data" / "PORT.json").write_text("{}")
    return out


def test_export_without_a_portfolio_has_nothing_to_publish_and_touches_nothing(tmp_path, web, capsys, monkeypatch):
    from monitor.server import export as E
    out = _published(tmp_path)
    with pytest.raises(RuntimeError, match="^NOTHING TO PUBLISH — no input/portfolio.csv$"):
        export(out, engine=_book_engine(tmp_path), web_dir=web)
    assert (out / "data" / "PORT.json").read_text() == "{}"          # the published snapshot stays
    monkeypatch.setattr(E, "export", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("NOTHING TO PUBLISH — x")))
    assert E.main([]) == 1
    assert capsys.readouterr().err.strip() == "NOTHING TO PUBLISH — x"   # one line, no traceback


def test_export_refuses_the_untouched_example_portfolio(tmp_path, web, monkeypatch):
    from monitor.init import EXAMPLES_DIR
    out = _published(tmp_path)
    eng = _book_engine(tmp_path, (EXAMPLES_DIR / "portfolio.example.csv").read_text(), monkeypatch)
    with pytest.raises(RuntimeError, match="example portfolio"):
        export(out, engine=eng, web_dir=web)
    assert (out / "data" / "PORT.json").read_text() == "{}"


def test_export_refuses_while_settings_have_an_error(tmp_path, web, monkeypatch):
    from monitor import config
    monkeypatch.setattr(config, "SETTINGS_ERROR", "input/settings.toml: unknown key 'fee' ignored")
    out = _published(tmp_path)
    with pytest.raises(RuntimeError, match="input/settings.toml: unknown key 'fee' ignored"):
        export(out, engine=_book_engine(tmp_path, "Date,Ticker,Action,Shares,Price,PricePerShare\n", monkeypatch),
               web_dir=web)
    assert (out / "data" / "PORT.json").read_text() == "{}"


def test_export_skips_a_screen_that_cannot_compute_and_publishes_the_rest(tmp_path, web, monkeypatch, capsys):
    import time_machine

    from monitor.screens.common import NEEDS_TWO
    eng = _book_engine(tmp_path, "Date,Ticker,Action,Shares,Price,PricePerShare\n2026-01-05,AAA.F,buy,10,1000.00,100.00\n",
                       monkeypatch)
    out = tmp_path / "docs"
    with time_machine.travel("2026-06-30 14:00:00+00:00", tick=False):
        export(out, engine=eng, web_dir=web)
    names = {p.name for p in (out / "data").glob("*.json")}
    assert {"PORT.json", "MKT.json", "screens.json"} <= names and not {"OPT.json", "RISK.json"} & names
    assert f"skipped OPT: {NEEDS_TWO}" in capsys.readouterr().err
    listed = {s["id"]: s for s in json.loads((out / "data" / "screens.json").read_text())["screens"]}
    assert listed["OPT"]["public"] is True and listed["OPT"]["cold"] == NEEDS_TWO    # public, nothing to show
    assert listed["ALRT"]["public"] is False and set(listed) == set(eng.screens)


def test_a_failing_export_keeps_the_published_snapshot_whole(tmp_path, monkeypatch, web):
    """docs/data, docs/app and docs/index.html are built in a temp folder beside them and swapped in only
    once every screen computed: a screen failing midway leaves the last snapshot exactly as it was."""
    out = tmp_path / "docs"
    eng = _engine(tmp_path, monkeypatch)
    export(out, engine=eng, web_dir=web)
    before = {p.relative_to(out): p.read_bytes() for p in out.rglob("*") if p.is_file()}
    scr = eng.screens["FAKE"]

    def boom(tier, ctx):
        raise ValueError("Yahoo down")
    eng.screens["FAKE"] = __import__("dataclasses").replace(scr, compute=boom)
    (web / "app" / "new.js").write_text("export {};")
    with pytest.raises(ValueError, match="Yahoo down"):
        export(out, engine=eng, web_dir=web)
    after = {p.relative_to(out): p.read_bytes() for p in out.rglob("*") if p.is_file()}
    assert after == before and sorted(p.name for p in out.iterdir()) == ["app", "data", "index.html"]
