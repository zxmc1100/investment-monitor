"""`python -m monitor init`: input/ from the examples, only where a file is missing —
it never overwrites, and says what it did."""
import shutil
from pathlib import Path

import pytest

from monitor import init as I
from monitor.__main__ import main

EX = Path(__file__).resolve().parent.parent / "examples"
FILES = {"portfolio.csv": "portfolio.example.csv", "interest.csv": "interest.example.csv",
         "settings.toml": "settings.example.toml"}


def test_creates_input_from_the_examples(tmp_path):
    inp = tmp_path / "input"
    lines = I.init(inp, EX)
    for dest, src in FILES.items():
        assert (inp / dest).read_text(encoding="utf-8") == (EX / src).read_text(encoding="utf-8")
        assert any(f"created input/{dest}" in line for line in lines), lines
    assert any(line.startswith("next: start the terminal (start-mac.command / start-windows.bat")
               for line in lines), lines


def test_never_overwrites_and_fills_only_the_gaps(tmp_path):
    inp = tmp_path / "input"
    inp.mkdir()
    (inp / "portfolio.csv").write_text("my trades\n", encoding="utf-8")
    lines = I.init(inp, EX)
    assert (inp / "portfolio.csv").read_text(encoding="utf-8") == "my trades\n"
    assert any("kept input/portfolio.csv" in line for line in lines)
    assert (inp / "interest.csv").exists() and (inp / "settings.toml").exists()
    (inp / "settings.toml").write_text("order_fee_eur = 2\n", encoding="utf-8")
    again = I.init(inp, EX)
    assert (inp / "settings.toml").read_text(encoding="utf-8") == "order_fee_eur = 2\n"
    assert all("created" not in line for line in again)


def test_a_dangling_symlink_counts_as_present(tmp_path):
    inp = tmp_path / "input"
    inp.mkdir()
    try:
        (inp / "portfolio.csv").symlink_to(tmp_path / "elsewhere.csv")   # e.g. a worktree's link
    except OSError:                                                      # Windows without the privilege
        pytest.skip("this account may not create symlinks")
    I.init(inp, EX)
    assert (inp / "portfolio.csv").is_symlink() and not (tmp_path / "elsewhere.csv").exists()


def test_cli_dispatch_prints_what_it_did(tmp_path, monkeypatch, capsys):
    ex = tmp_path / "examples"
    shutil.copytree(EX, ex)
    monkeypatch.setattr(I, "EXAMPLES_DIR", ex)
    monkeypatch.setattr(I.config, "INPUT_DIR", tmp_path / "input")
    assert main(["init"]) == 0
    out = capsys.readouterr().out
    assert "created input/portfolio.csv" in out and (tmp_path / "input" / "portfolio.csv").exists()
    assert main([]) == 0 and "init" in capsys.readouterr().out                # listed in the help


def test_messages_never_name_a_venv_path(tmp_path):
    """A downloaded ZIP is started with a double-click: what people read says how to start it (or the
    generic `python -m monitor init`), never `.venv/bin/python`, which is not even the path on Windows."""
    import monitor.__main__ as M
    from monitor.screens.base import NO_PORTFOLIO
    texts = {"cold view": NO_PORTFOLIO, "examples/README.md": (EX / "README.md").read_text(encoding="utf-8"),
             "help": M.usage({}), "init": "\n".join(I.init(tmp_path / "input", EX))}
    for name, text in texts.items():
        assert ".venv/bin" not in text and ".venv\\Scripts" not in text, name
    assert "python -m monitor init" in NO_PORTFOLIO and "input/portfolio.csv" in NO_PORTFOLIO
    assert "python -m monitor init" in M.usage({})
    for name in ("init", "examples/README.md"):
        assert "start-mac.command" in texts[name] and "start-windows.bat" in texts[name], name


def test_input_being_a_file_or_missing_examples_is_one_line_not_a_traceback(tmp_path, monkeypatch, capsys):
    (tmp_path / "input").write_text("oops", encoding="utf-8")
    monkeypatch.setattr(I.config, "INPUT_DIR", tmp_path / "input")
    assert main(["init"]) == 1
    err = capsys.readouterr().err.strip()
    assert "input" in err and "not a folder" in err and "\n" not in err
    monkeypatch.setattr(I.config, "INPUT_DIR", tmp_path / "fresh")
    monkeypatch.setattr(I, "EXAMPLES_DIR", tmp_path / "no-examples")
    assert main(["init"]) == 1
    err = capsys.readouterr().err.strip()
    assert "no-examples" in err and "missing" in err and "\n" not in err


def test_readme_explains_bonus_price_and_plan_tickers():
    text = (EX / "README.md").read_text(encoding="utf-8") + (EX / "settings.example.toml").read_text(encoding="utf-8")
    assert "bonus" in text and "books" in text
    assert text.count("as written in your CSV's Ticker column") >= 2
