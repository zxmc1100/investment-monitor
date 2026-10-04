"""ALERT with no rule shows the forms the server accepts: web/app/cmd.js ALERT_USAGE is
monitor/alerts/rules.py USAGE word for word."""
import re
from pathlib import Path

from monitor.alerts.rules import USAGE

CMD = Path(__file__).resolve().parents[2] / "web" / "app" / "cmd.js"


def test_alert_usage_matches_the_rule_parser():
    m = re.search(r'export const ALERT_USAGE = ((?:"[^"]*"\s*\+?\s*)+);', CMD.read_text(encoding="utf-8"))
    assert m, "ALERT_USAGE missing from web/app/cmd.js"
    assert "".join(re.findall(r'"([^"]*)"', m.group(1))) == USAGE
