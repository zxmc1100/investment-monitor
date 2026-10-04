"""Guard on the committed public snapshot: docs/data/*.json must hold no private data."""
import json
import re
from pathlib import Path

import pytest

from monitor import config
from monitor.alerts import watchlist
from monitor.screens import SCREENS

DATA = Path(__file__).resolve().parents[2] / "docs" / "data"
FILES = sorted(DATA.glob("*.json")) if DATA.exists() else []
PRIVATE_COLS = {"shrs", "avg", "last", "value", "pnl", "amt", "now_eur", "tgt_eur", "d_eur", "shares", "loss", "side",
                "mark"}
FORBIDDEN_KEYS = PRIVATE_COLS | {"stale", "code_version", "_closed"}
PRIVATE_PANELS = {"posval", "accounting", "activity", "ticket", "names", "events"}
PRIVATE_KPIS = {"VALUE", "DAY P&L", "TOTAL P&L", "REALIZED", "UNREALIZED", "VaR95 1D €"}


@pytest.mark.skipif(not FILES, reason="no exported docs/data yet")
@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_exported_payload_has_no_private_data(path):
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    assert "€" not in text
    assert "stale" not in data.get("meta", {})

    def walk(node):
        if isinstance(node, dict):
            bad = FORBIDDEN_KEYS & set(node)
            assert not bad, f"private keys in public snapshot: {bad}"
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    for p in data.get("panels", []):
        assert p["id"] not in PRIVATE_PANELS
        assert not PRIVATE_COLS & {c["k"] for c in p.get("cols", [])}
        assert not any(PRIVATE_COLS & set(r) for r in p.get("rows", []))
        assert not PRIVATE_KPIS & {i.get("k") for i in p.get("items", [])}


PUBLISHED = {f"{s.id}.json" for s in SCREENS.values() if s.public and s.params is None}   # the core's public screens


def private_files(names) -> list[str]:
    """The names among `names` that are neither screens.json nor a public core screen's payload — a private
    screen's, a parametrized screen's or a local add-on's: none may ever be in docs/data."""
    return sorted(n for n in names if n != "screens.json" and n not in PUBLISHED)


def test_the_guard_catches_every_private_screen_file():
    assert PUBLISHED == {"PORT.json", "OPT.json", "RISK.json", "MKT.json"}
    assert private_files(["PORT.json", "OPT.json", "RISK.json", "MKT.json", "screens.json"]) == []
    assert private_files(["ALRT.json", "SEC~NVD.F.json", "XTRA.json", "XTRA~A1.json", "PORT.json"]) == [
        "ALRT.json", "SEC~NVD.F.json", "XTRA.json", "XTRA~A1.json"]


@pytest.mark.skipif(not FILES, reason="no exported docs/data yet")
def test_no_private_screen_and_no_watchlist_only_ticker_is_published():
    names = {p.name for p in FILES}
    assert not private_files(names), f"private screens in the public snapshot: {private_files(names)}"
    entries = json.loads((DATA / "screens.json").read_text(encoding="utf-8"))["screens"] if (DATA / "screens.json").exists() else []
    listed = {s["id"] for s in entries if s.get("public", True)}      # an old screens.json listed public ones only
    assert not private_files(f"{i}.json" for i in listed), listed
    watched = watchlist.load(config.WATCHLIST_FILE)
    if not watched:
        pytest.skip("no local watchlist")                 # the guard below would pass vacuously
    pages = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in FILES}
    public_rows = ([r for p in pages.get("PORT.json", {}).get("panels", []) if p["id"] == "positions"
                    for r in p.get("rows", [])]
                   + [r for p in pages.get("MKT.json", {}).get("panels", []) if p["id"] in {"gainers", "losers", "spikes"}
                      for r in p.get("rows", [])])
    public_names = {r.get("tkr") for r in public_rows}
    public_labels = {r.get("name") for r in public_rows}   # a watched name may also be a holding or a mover
    text = "".join(p.read_text(encoding="utf-8") for p in FILES)
    leaked = [w["ticker"] for w in watched if w["ticker"] not in public_names and f'"{w["ticker"]}"' in text]
    assert not leaked, f"watchlist-only tickers in the public snapshot: {leaked}"
    leaked = [w["name"] for w in watched if w["ticker"] not in public_names and w["name"] not in public_labels
              and f'"{w["name"]}"' in text]
    assert not leaked, f"watchlist-only names in the public snapshot: {leaked}"


@pytest.mark.skipif(not FILES, reason="no exported docs/data yet")
@pytest.mark.parametrize("path", [p for p in FILES if p.name != "screens.json"], ids=lambda p: p.name)
def test_every_alt_key_in_the_public_text_names_an_existing_panel(path):
    """"ALT+7 DETAIL" must maximize panel 7 on the public page too — panel numbers are never
    renumbered by the redactor, so a gap is fine but a dangling key is not."""
    data = json.loads(path.read_text(encoding="utf-8"))
    numbers = {p.get("n") for p in data.get("panels", [])}
    mentioned = {int(n) for n in re.findall(r"(?i)\balt\+(\d+)", path.read_text(encoding="utf-8"))}
    assert mentioned <= numbers, f"{path.name}: ALT+n names a missing panel: {sorted(mentioned - numbers)}"
