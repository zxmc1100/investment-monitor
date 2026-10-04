"""Static checks on the SPA: index.html's assets exist; every relative ES import resolves."""
import re
from pathlib import Path

WEB = Path(__file__).resolve().parents[2] / "web"


def test_index_references_exist():
    html = (WEB / "index.html").read_text()
    refs = re.findall(r'(?:src|href)="(app/[^"]+)"', html)
    assert len(refs) == 4, refs
    missing = [r for r in refs if not (WEB / r).exists()]
    assert not missing, missing
    assert '<meta name="im-mode" content="live">' in html


def test_relative_imports_resolve():
    bad = []
    for f in sorted((WEB / "app").rglob("*.js")):
        if "vendor" in f.parts:
            continue
        for spec in re.findall(r'from\s+"(\.{1,2}/[^"]+)"', f.read_text()):
            if not (f.parent / spec).resolve().exists():
                bad.append(f"{f.relative_to(WEB)} -> {spec}")
    assert not bad, bad
