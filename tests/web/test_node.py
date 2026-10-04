"""Run the frontend's pure-module tests (node --test) as part of the pytest suite."""
import shutil
import subprocess
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_unit_tests_pass():
    files = sorted(str(p) for p in HERE.glob("*.test.mjs"))
    assert files, "no JS tests found"
    r = subprocess.run(["node", "--test", *files], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
