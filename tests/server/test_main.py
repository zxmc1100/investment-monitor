"""`python -m monitor`: the core's commands (init, serve, export); anything else is unknown — a local add-on's
commands are covered in tests/test_plugins.py."""
from monitor.__main__ import main


def test_help_lists_exactly_the_core_commands_and_an_unknown_one_is_refused(capsys):
    assert main([]) == 0
    listed = [ln.split()[0] for ln in capsys.readouterr().out.splitlines() if ln.startswith("  ") and ln.strip()]
    assert listed == ["init", "serve", "export"]
    assert main(["nope"]) == 2 and "unknown command: nope" in capsys.readouterr().err
