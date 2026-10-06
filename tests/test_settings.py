"""input/settings.toml: optional, read once by monitor.config with tomllib. Missing →
defaults; a malformed file or key → that default and a one-line error naming the file and the key.
The suite never reads the real input/settings.toml (tests/conftest.py points MONITOR_SETTINGS away)."""
import json
import os
import subprocess
import sys
from pathlib import Path

from monitor import config
from monitor.config import load_settings

REPO = Path(__file__).resolve().parent.parent

FULL = """
order_fee_eur = 0.99
savings_plan_tickers = ["VWCE.DE", "IWDA.AS"]
dividend_tax = 0.25

[tickers]
"XYZ.F" = "XYZ.DE"

[names]
"XYZ.F" = "XYZ Holding"

[sectors]
"XYZ.F" = "Industrials"

[countries]
"XYZ.F" = "Germany"
"""


def _write(tmp_path, text: str) -> Path:
    p = tmp_path / "settings.toml"
    p.write_text(text, encoding="utf-8")
    return p


def test_missing_file_gives_the_defaults(tmp_path):
    s, err = load_settings(tmp_path / "nope.toml")
    assert err is None
    assert s == {"order_fee_eur": 1.0, "savings_plan_tickers": (), "dividend_tax": 0.26375,
                 "tickers": {}, "isins": {}, "names": {}, "sectors": {}, "countries": {}}


def test_every_key_is_read(tmp_path):
    s, err = load_settings(_write(tmp_path, FULL))
    assert err is None
    assert s["order_fee_eur"] == 0.99
    assert s["savings_plan_tickers"] == ("VWCE.DE", "IWDA.AS")
    assert s["dividend_tax"] == 0.25
    assert s["tickers"] == {"XYZ.F": "XYZ.DE"} and s["names"] == {"XYZ.F": "XYZ Holding"}
    assert s["sectors"] == {"XYZ.F": "Industrials"} and s["countries"] == {"XYZ.F": "Germany"}


def test_an_integer_fee_is_a_number(tmp_path):
    s, err = load_settings(_write(tmp_path, "order_fee_eur = 0\n"))
    assert err is None and s["order_fee_eur"] == 0.0 and isinstance(s["order_fee_eur"], float)


def test_invalid_toml_keeps_every_default_and_says_where(tmp_path):
    s, err = load_settings(_write(tmp_path, "order_fee_eur = = 2\n"))
    assert s == load_settings(tmp_path / "nope.toml")[0]
    assert "settings.toml" in err and "line 1" in err and "\n" not in err


def test_a_bad_value_falls_back_for_that_key_only(tmp_path):
    s, err = load_settings(_write(tmp_path, 'order_fee_eur = "one"\ndividend_tax = 0.2\n'))
    assert s["order_fee_eur"] == 1.0 and s["dividend_tax"] == 0.2
    assert "settings.toml" in err and "order_fee_eur" in err and "\n" not in err


def test_every_bad_key_is_named_on_one_line(tmp_path):
    text = ('dividend_tax = 1.5\nsavings_plan_tickers = "IWDA.AS"\norder_fee_eur = -1\n'
            'fee = 1\n[names]\n"A.F" = 7\n')
    s, err = load_settings(_write(tmp_path, text))
    assert s == load_settings(tmp_path / "nope.toml")[0]
    for key in ("dividend_tax", "savings_plan_tickers", "order_fee_eur", "fee", "names"):
        assert key in err, key
    assert "\n" not in err


def test_the_suite_never_reads_the_real_settings_file():
    assert Path(os.environ["MONITOR_SETTINGS"]) != REPO / "input" / "settings.toml"
    assert config.SETTINGS_FILE != config.INPUT_DIR / "settings.toml"
    assert config.SETTINGS_ERROR is None


def _fresh_import(tmp_path, text: str) -> dict:
    """Import the package in a clean interpreter with MONITOR_SETTINGS -> a tmp file."""
    code = ("import json; from monitor import config as c; from monitor.data import instruments as i; "
            "from monitor.portfolio import meta as m; "
            "print(json.dumps({'fee': c.ORDER_FEE_EUR, 'plan': list(c.SAVINGS_PLAN_TICKERS), "
            "'tax': c.DIVIDEND_TAX, 'err': c.SETTINGS_ERROR, "
            "'tmap': i.TICKER_MAP, 'names': i.COMPANY_NAMES, 'sec': m.PORTFOLIO_SECTOR_MAP, "
            "'cty': m.PORTFOLIO_COUNTRY_MAP, 'builtin_sec': m._BUILTIN_SECTORS}))")
    env = {**os.environ, "MONITOR_SETTINGS": str(_write(tmp_path, text))}
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, env=env, capture_output=True, text=True, check=True, encoding="utf-8")
    return json.loads(out.stdout)


def test_module_constants_and_map_merges_come_from_the_file(tmp_path):
    got = _fresh_import(tmp_path, FULL.replace('"XYZ.F" = "XYZ Holding"',
                                               '"XYZ.F" = "XYZ Holding"\n"EUNL.F" = "MSCI World (mine)"'))
    assert (got["fee"], got["plan"], got["tax"]) == (0.99, ["VWCE.DE", "IWDA.AS"], 0.25)
    assert got["err"] is None
    assert got["tmap"]["XYZ.F"] == "XYZ.DE" and got["tmap"]["EUNL.F"] == "IWDA.AS"     # merged over built-ins
    assert got["names"]["XYZ.F"] == "XYZ Holding" and got["names"]["EUNL.F"] == "MSCI World (mine)"   # yours win
    assert got["sec"]["XYZ.F"] == "Industrials"
    assert all(got["sec"][t] == v for t, v in got["builtin_sec"].items())                  # built-ins kept
    assert got["cty"]["XYZ.F"] == "Germany"


def test_a_malformed_file_still_imports_with_defaults(tmp_path):
    got = _fresh_import(tmp_path, "[[[")
    assert got["fee"] == 1.0 and got["plan"] == [] and got["tax"] == 0.26375
    assert "settings.toml" in got["err"]


def test_default_tables_are_fresh_per_load(tmp_path):
    a, _ = load_settings(tmp_path / "nope.toml")
    a["names"]["X.F"] = "leak"
    assert load_settings(tmp_path / "nope.toml")[0]["names"] == {}


# ── polish (review fix 5) ─────────────────────────────────────────────────────

def test_a_bom_from_a_windows_editor_is_fine(tmp_path):
    p = tmp_path / "settings.toml"
    p.write_bytes("\ufefforder_fee_eur = 0.5\n".encode("utf-8"))
    s, err = load_settings(p)
    assert err is None and s["order_fee_eur"] == 0.5


def test_an_explicit_settings_path_that_is_missing_is_said(tmp_path):
    s, err = load_settings(tmp_path / "gone.toml", explicit=True)
    assert s == load_settings(tmp_path / "gone.toml")[0]
    assert "gone.toml" in err and "MONITOR_SETTINGS" in err and "missing" in err
    assert load_settings(tmp_path / "gone.toml")[1] is None             # input/settings.toml is optional


def test_list_defaults_read_as_toml_in_messages(tmp_path):
    _, err = load_settings(_write(tmp_path, 'savings_plan_tickers = "IWDA.AS"\n'))
    assert "default [] used" in err


def test_sectors_and_countries_from_settings_speak_the_books_vocabulary(settings_file):
    from monitor.portfolio import meta
    settings_file.write_text('[sectors]\n"XYZ.F" = "Technology"\n"ABC.F" = "Semis"\n'
                             '[countries]\n"XYZ.F" = "DE"\n"ABC.F" = "USA"\n', encoding="utf-8")
    config.refresh_settings()
    assert meta.PORTFOLIO_SECTOR_MAP["XYZ.F"] == "Information Technology"
    assert meta.PORTFOLIO_SECTOR_MAP["ABC.F"] == "Semis"                  # your own label is kept
    assert meta.PORTFOLIO_COUNTRY_MAP["XYZ.F"] == "Germany"
    assert meta.PORTFOLIO_COUNTRY_MAP["ABC.F"] == "United States"


def test_the_example_header_tells_how_errors_fall_back():
    text = (REPO / "examples" / "settings.example.toml").read_text(encoding="utf-8")
    assert "syntax error" in text and "every key" in text and "falls back to its default alone" in text
