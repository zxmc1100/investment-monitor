"""The static app's fallback table of private screens (web/app/cmd.js LOCAL_ONLY, read only when an old
data/screens.json lacks the `public: false` entries) is the terminal's own: every screen that is never
exported, with its number key — so 5 on GitHub Pages names ALRT as local only."""
import json
import re
from pathlib import Path

from monitor.screens import SCREENS

CMD = Path(__file__).resolve().parents[2] / "web" / "app" / "cmd.js"


def test_local_only_matches_the_private_screens():
    m = re.search(r"export const LOCAL_ONLY = (\{[^}]*\});", CMD.read_text(encoding="utf-8"))
    assert m, "LOCAL_ONLY table missing from web/app/cmd.js"
    table = json.loads(re.sub(r"(\w+):", r'"\1":', m.group(1)))
    assert table == {s.id: s.fkey for s in SCREENS.values() if not s.public}
    assert table == {"ALRT": 5, "SEC": None}
