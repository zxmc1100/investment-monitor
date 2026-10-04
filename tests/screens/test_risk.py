"""RISK payload: structure, VaR, stress rows, correlation symmetry, drawdown, privacy."""
import json
from pathlib import Path

import pytest
import time_machine

from monitor.screens import risk
from monitor.screens.base import Ctx
from monitor.server.redact import public_view
from tests import fakes_yf

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "portfolio_small.csv"
META = {"computed_at": "2026-06-30T14:00:00", "tiers": {}, "code_version": "x", "prefs": {"target": "HRP"}}


@pytest.fixture
def payload(tmp_path, monkeypatch):
    fakes_yf.install(monkeypatch)
    with time_machine.travel("2026-06-30 14:00:00+00:00", tick=False):
        ctx = Ctx(force=True, buffer_dir=tmp_path / "buffer", portfolio_csv=FIX, equity_log=None)
        yield risk.assemble({t: risk.compute(t, ctx) for t in risk.SCREEN.tiers}, dict(META))


def panel(p, pid):
    return next(q for q in p["panels"] if q["id"] == pid)


def test_panels_and_strict_json(payload):
    assert [q["id"] for q in payload["panels"]] == ["summary", "riskcap", "corr", "stress", "drawdown"]
    json.dumps(payload, allow_nan=False)


def test_var_is_negative_and_euro_var_is_private(payload):
    items = {i["k"]: i for i in panel(payload, "summary")["items"]}
    assert items["VaR95 1D"]["v"] < 0 and items["VaR95 1D"]["vis"] == "public"
    assert items["VaR95 1D €"]["vis"] == "private" and items["VaR95 1D €"]["v"] < 0


def test_stress_rows_cover_all_scenarios(payload):
    rows = panel(payload, "stress")["rows"]
    assert [r["key"] for r in rows] == ["GFC", "COVID", "RATES22", "SPX10", "SPX20", "W1D", "W5D", "W21D"]
    assert all("/" in r["est"] for r in rows)


def test_correlation_is_symmetric_with_unit_diagonal(payload):
    c = panel(payload, "corr")
    n = len(c["labels"])
    for i in range(n):
        assert c["cells"][i][i] == pytest.approx(1.0)
        for j in range(n):
            assert c["cells"][i][j] == pytest.approx(c["cells"][j][i])


def test_drawdown_never_positive(payload):
    ys = [y for s in panel(payload, "drawdown")["series"] for y in s["y"] if y is not None]
    assert ys and max(ys) <= 1e-9


def test_public_view_drops_euro_figures(payload):
    pub = json.dumps(public_view(payload))
    assert "VaR95 1D €" not in pub and '"loss"' not in pub
    assert [q["id"] for q in public_view(payload)["panels"]] == ["summary", "riskcap", "corr", "stress", "drawdown"]


def test_summary_context_and_help_explain_the_two_measurement_bases(payload):
    s = panel(payload, "summary")
    assert s["context"] == {"text": "TODAY'S WEIGHTS · 1Y DAILY · DD = REALIZED PATH", "vis": "public"}
    helps = {h["h"]: h for h in payload["help"]}
    mix, dd = helps["VOL / BETA SPX / VaR (this screen)"], helps["DRAWDOWN / MAX DD / CUR DD"]
    assert mix["vis"] == dd["vis"] == "public"
    assert "PORT" in mix["body"] and "realized" in mix["body"] and "realized" in dd["body"]
    assert "40%" in helps["STRESS"]["body"] and "WORST" in helps["STRESS"]["body"]
    pub = public_view(payload)
    assert pub["panels"][0]["context"]["text"] == s["context"]["text"]
    assert {"VOL / BETA SPX / VaR (this screen)", "DRAWDOWN / MAX DD / CUR DD", "STRESS"} <= {h["h"] for h in pub["help"]}
    assert "€" not in json.dumps(pub, ensure_ascii=False)


def test_stress_table_keys_on_the_scenario_without_a_key_column(payload):
    st = panel(payload, "stress")
    assert st["key"] == "scn" and "key" not in [c["k"] for c in st["cols"]]
    assert len({r["scn"] for r in st["rows"]}) == len(st["rows"]) == 8
    pub = next(q for q in public_view(payload)["panels"] if q["id"] == "stress")
    assert [c["k"] for c in pub["cols"]] == ["scn", "port", "worst", "est"]
    assert len(pub["rows"]) == 8 and all(set(r) == {"scn", "port", "worst", "est"} for r in pub["rows"])
