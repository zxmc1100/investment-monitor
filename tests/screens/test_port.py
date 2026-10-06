"""PORT payload: structure, reconciliation, strict JSON, public redaction, staleness."""
import json
import re
from pathlib import Path

import pytest
import time_machine

import monitor.data.yahoo as Y
from monitor.screens import SCREENS, port
from monitor.screens.base import Ctx
from monitor.server.redact import public_view
from tests import fakes_yf

FIX = Path(__file__).resolve().parent.parent / "fixtures" / "portfolio_small.csv"
META = {"computed_at": "2026-06-30T14:00:00", "tiers": {}, "code_version": "x"}


@pytest.fixture
def frozen(monkeypatch):
    fakes_yf.install(monkeypatch)
    with time_machine.travel("2026-06-30 14:00:00+00:00", tick=False):
        yield


def build(tmp_path, csv=FIX):
    ctx = Ctx(force=True, buffer_dir=tmp_path / "buffer", portfolio_csv=csv, equity_log=None)
    return port.assemble({t: port.compute(t, ctx) for t in port.SCREEN.tiers}, dict(META))


def panel(p, pid):
    return next(q for q in p["panels"] if q["id"] == pid)


def kpi(p, pid, k):
    return next(i["v"] for i in panel(p, pid)["items"] if i["k"] == k)


def test_panel_order_and_numbering(frozen, tmp_path):
    p = build(tmp_path)
    assert [q["id"] for q in p["panels"]] == ["summary", "risk", "positions", "roi", "posval",
                                              "accounting", "allocation", "activity", "dividends"]
    assert [q["n"] for q in p["panels"]] == list(range(1, 10))


def test_value_weights_and_roi_reconcile(frozen, tmp_path):
    p = build(tmp_path)
    pos = panel(p, "positions")
    open_rows = [r for r in pos["rows"] if not r.get("_closed")]
    assert kpi(p, "summary", "VALUE") == pytest.approx(sum(r["value"] for r in open_rows))
    assert pos["total"]["value"] == pytest.approx(kpi(p, "summary", "VALUE"))
    assert sum(r["wt"] for r in open_rows) == pytest.approx(100.0)
    assert kpi(p, "summary", "ROI") == pytest.approx(kpi(p, "summary", "TOTAL P&L") / 3990.0 * 100)


def test_roi_lines_carry_their_time_weighted_growth_privately(frozen, tmp_path):
    """NORM draws each line from its `twr` (growth of 1 € with buys, sells and dividends taken out of
    their days). Your line's is the YTD TWR KPI's chain; the public view keeps none of it — with the
    ROI lines it would give away when, and how much, money was added."""
    from datetime import date
    from monitor.portfolio.analytics import year_returns
    ctx = Ctx(force=True, buffer_dir=tmp_path / "buffer", portfolio_csv=FIX, equity_log=None)
    parts = {t: port.compute(t, ctx) for t in port.SCREEN.tiers}
    p = port.assemble(parts, dict(META))
    roi = panel(p, "roi")
    assert [s["name"] for s in roi["series"]][0] == "YOU" and len(roi["series"]) > 1
    for s in roi["series"]:
        assert len(s["twr"]) == len(roi["x"]) and s["twr"][-1] > 0
    you = roi["series"][0]["twr"]
    jan1 = max(i for i, t in enumerate(roi["x"]) if t < 1767225600)           # 2025's last close
    q, d = parts["quote"], parts["daily"]
    closes = year_returns(d["hold"], q["txns"], q["dividends"], today=date(2026, 6, 30))[0]   # no live step
    assert (you[-1] / you[jan1] - 1) * 100 == pytest.approx(closes["twr"], abs=0.01)   # twr has 4 decimals
    pub = panel(public_view(p), "roi")
    assert pub["series"] and not any("twr" in s for s in pub["series"])


def test_port_payload_is_strict_json(frozen, tmp_path):
    json.dumps(build(tmp_path), allow_nan=False)


def test_closed_and_partial_positions(frozen, tmp_path):
    rows = {r["tkr"]: r for r in panel(build(tmp_path), "positions")["rows"]}
    assert rows["CCC.F"]["_closed"] is True and rows["CCC.F"]["value"] == 0.0
    assert rows["CCC.F"]["pnlp"] == pytest.approx(10.0)       # sold 40 @22 vs avg cost 20
    assert not rows["BBB.F"].get("_closed")                    # partial sell stays open


def test_never_quoted_ticker_is_flagged_not_hidden(frozen, tmp_path, monkeypatch):
    real = Y.fetch_quotes
    monkeypatch.setattr(Y, "fetch_quotes", lambda ts: {**real(ts), "DDD.F": None})
    p = build(tmp_path)
    assert p["meta"]["stale"] == {"DDD.F": None}
    row = next(r for r in panel(p, "positions")["rows"] if r["tkr"] == "DDD.F")
    assert row["_stale"] is True and row["last"] is None
    pub = next(r for r in panel(public_view(p), "positions")["rows"] if r["tkr"] == "DDD.F")
    assert pub["_stale"] is True


def _numbers(node, out):
    if isinstance(node, bool):
        return out
    if isinstance(node, (int, float)):
        out.add(float(node))
    elif isinstance(node, dict):
        for v in node.values():
            _numbers(v, out)
    elif isinstance(node, list):
        for v in node:
            _numbers(v, out)
    return out


def test_public_view_leaks_nothing_private(frozen, tmp_path):
    canary = tmp_path / "canary.csv"
    canary.write_text("Date,Ticker,Action,Shares,Price,PricePerShare\n"
                      "2025-01-06,AAA.F,buy,4242.4242,5237.07,1.2345\n"
                      "2025-02-03,BBB.F,buy,20,1000.00,50.00\n", encoding="utf-8")
    p = build(tmp_path, canary)
    pub = public_view(p)
    s = json.dumps(pub)
    assert "4242.4242" not in s and "5237.07" not in s
    assert [q["id"] for q in pub["panels"]] == ["summary", "risk", "positions", "roi", "allocation"]
    assert [c["k"] for c in panel(pub, "positions")["cols"]] == ["tkr", "name", "day", "wt", "pnlp", "p1y"]
    assert [i["k"] for i in panel(pub, "summary")["items"]] == ["DAY %", "ROI", "XIRR /YR", "YTD", "YTD TWR"]
    private = set()
    for q in p["panels"]:
        private.update(y["gain"] for y in q.get("years", []) if isinstance(y.get("gain"), float))
        for i in q.get("items", []) + q.get("lines", []):
            if i.get("vis") != "public" and isinstance(i.get("v"), float):
                private.add(i["v"])
        priv_cols = [c["k"] for c in q.get("cols", []) if c.get("vis") != "public"]
        for r in q.get("rows", []):
            private.update(r[k] for k in priv_cols if isinstance(r.get(k), float))
    private = {v for v in private if abs(v) >= 10 and v != round(v)}
    assert private and not (private & _numbers(pub, set()))
    for r in panel(pub, "positions")["rows"]:
        if r.get("p1y"):
            assert r["p1y"][0] == 100.0


def test_public_alt_keys_name_the_panel_they_mention(frozen, tmp_path):
    """HELP and the ALLOCATION context say "Alt+7": the public view keeps panel numbers, so the key
    still maximizes ALLOCATION there (private panels leave gaps, nothing is renumbered)."""
    pub = public_view(build(tmp_path))
    numbers = {q["n"]: q["id"] for q in pub["panels"]}
    mentioned = {int(n) for n in re.findall(r"(?i)\balt\+(\d+)", json.dumps(pub))}
    assert 7 in mentioned and numbers[7] == "allocation"
    assert mentioned <= set(numbers), f"ALT+n names a missing panel: {sorted(mentioned - set(numbers))}"


def test_allocation_sums_to_100(frozen, tmp_path):
    items = panel(build(tmp_path), "allocation")["items"]
    assert sum(i["v"] for i in items) == pytest.approx(100.0)


def test_activity_latest_first_max_10(frozen, tmp_path):
    rows = panel(build(tmp_path), "activity")["rows"]
    assert len(rows) == 7 and rows[0]["date"] == "2026-03-02"
    assert [r["date"] for r in rows] == sorted((r["date"] for r in rows), reverse=True)


def test_registry_order_and_port_entry():
    assert list(SCREENS) == ["PORT", "OPT", "RISK", "SEC", "MKT", "ALRT", "TRADES"]
    assert [s.fkey for s in SCREENS.values()] == [1, 2, 3, None, 4, 5, 6]
    e = SCREENS["PORT"].entry()
    assert e["status"] == "live" and e["public"] is True and e["tiers"] == ["quote", "daily"]
    assert all(s.status == "live" for s in SCREENS.values())


def _epoch_of(d):
    import pandas as pd
    return int(pd.Timestamp(d).timestamp())


def test_posval_markers_follow_exits_and_rebuys(frozen, tmp_path):
    csv = tmp_path / "rebuy.csv"
    csv.write_text("Date,Ticker,Action,Shares,Price,PricePerShare\n"
                   "2025-01-06,AAA.F,buy,10,1000.00,100.00\n"
                   "2025-01-06,BBB.F,buy,10,500.00,50.00\n"
                   "2025-04-01,AAA.F,sell,10,1200.00,120.00\n"
                   "2025-09-01,AAA.F,buy,10,1000.00,100.00\n", encoding="utf-8")
    sv = panel(build(tmp_path, csv), "posval")["series_by_key"]["AAA.F"]
    x = sv["x"]
    ser = {s["name"]: s["y"] for s in sv["series"]}
    buys = [x[i] for i, v in enumerate(ser["BUY"]) if v is not None]
    sells = [x[i] for i, v in enumerate(ser["SELL"]) if v is not None]
    assert buys == [_epoch_of("2025-01-06"), _epoch_of("2025-09-01")]
    assert len(sells) == 1 and sells[0] <= _epoch_of("2025-04-01")
    gap = [ser["AAA.F"][i] for i, t in enumerate(x)
           if _epoch_of("2025-04-02") <= t <= _epoch_of("2025-08-29")]
    assert gap and all(v is None for v in gap)


def test_port_payload_stays_under_300kb(frozen, tmp_path):
    import pandas as pd
    days = pd.bdate_range("2024-11-18", periods=13)
    lines = ["Date,Ticker,Action,Shares,Price,PricePerShare"]
    for n, d in enumerate(days, 1):
        lines.append(f"{d.date()},T{n:02d}.F,buy,10,500.00,50.00")
    for n in (1, 4, 9):
        lines.append(f"2025-08-01,T{n:02d}.F,sell,4,240.00,60.00")
    csv = tmp_path / "many.csv"
    csv.write_text("\n".join(lines) + "\n", encoding="utf-8")
    size = len(json.dumps(build(tmp_path, csv)))
    assert size < 300_000


def test_accounting_ledger_has_private_dividends_line_that_adds_up(frozen, tmp_path, monkeypatch):
    import pandas as pd
    monkeypatch.setattr(fakes_yf, "DIVIDENDS", {"AAA.F": pd.Series([2.0], index=pd.to_datetime(["2025-03-03"]))})
    lines = panel(build(tmp_path), "accounting")["lines"]
    labels = [l.get("label") for l in lines]
    i = labels.index("Realized")
    assert labels[i:i + 4] == ["Realized", "Dividends (net)", "Bonus", "Unrealized"]
    by = lambda k: next(l for l in lines[i:] if l.get("label") == k)
    assert by("Dividends (net)").get("vis") != "public"
    # 15 shares held before the ex-date x 2.0, after 26.375 % German tax
    assert by("Dividends (net)")["v"] == pytest.approx(30.0 * (1 - 0.26375))
    assert (by("Realized")["v"] + by("Dividends (net)")["v"] + by("Bonus")["v"] + by("Unrealized")["v"]
            == pytest.approx(by("Total P&L")["v"]))


def test_allocation_detail_covers_every_sector_and_country(frozen, tmp_path):
    p = panel(build(tmp_path), "allocation")
    assert len(p["sectors"]) >= 11 and all(isinstance(r["text"], str) for r in p["sectors"])
    assert sum(r["v"] for r in p["sectors"]) == pytest.approx(100.0)
    assert sum(r["v"] for r in p["countries"]) == pytest.approx(100.0)
    assert sum(r["v"] for r in p["regions"]) == pytest.approx(100.0)
    pub = next(q for q in public_view(build(tmp_path))["panels"] if q["id"] == "allocation")
    assert {"sectors", "countries", "regions"} <= set(pub) and "€" not in json.dumps(pub)


def test_day_column_names_the_session_when_it_is_not_today(tmp_path, monkeypatch):
    fakes_yf.install(monkeypatch)
    with time_machine.travel("2026-07-04 12:00:00+00:00", tick=False):     # a Saturday
        p = build(tmp_path)
    cols = {c["k"]: c["label"] for c in panel(p, "positions")["cols"]}
    assert cols["day"] == "DAY% · FRI"
    assert panel(p, "summary")["context"]["text"].endswith("· DAY = FRI 03 JUL")


def test_day_column_is_plain_on_a_trading_day(frozen, tmp_path):
    p = build(tmp_path)
    assert {c["k"]: c["label"] for c in panel(p, "positions")["cols"]}["day"] == "DAY%"
    assert "DAY =" not in panel(p, "summary")["context"]["text"]


# ── bonus shares, interest on cash, YTD two ways + per-year table ───────────────────────────

def _with(tmp_path, *rows, interest=None, dividends=None):
    csv = tmp_path / "book" / "portfolio.csv"
    csv.parent.mkdir(exist_ok=True)
    csv.write_text(FIX.read_text(encoding="utf-8").rstrip("\n") + "\n" + "".join(r + "\n" for r in rows), encoding="utf-8")
    if interest is not None:
        (csv.parent / "interest.csv").write_text("Date,Amount\n" + "".join(f"{d},{a}\n" for d, a in interest), encoding="utf-8")
    if dividends is not None:
        (csv.parent / "dividends.csv").write_text("PayDate,Ticker,Shares,Gross,Tax,Net\n" + "".join(
            ",".join(map(str, d)) + "\n" for d in dividends), encoding="utf-8")
    return csv


def test_the_dividends_panel_lists_upcoming_due_and_paid_with_ex_and_pay_dates(frozen, tmp_path, monkeypatch):
    """AAA.F: paid 20 Mar (the broker's file) for its 2 Mar ex date — 18 days; the 15 Jun one is due ~3 Jul by
    that gap; the calendar's next: ex 10 Sep, pays 28 Sep. Only the paid one is in ACCOUNTING's dividends."""
    import datetime as dt
    import pandas as pd
    monkeypatch.setattr(fakes_yf, "DIVIDENDS", {"AAA.F": pd.Series([1.0, 1.0], index=pd.to_datetime(["2026-03-02",
                                                                                                         "2026-06-15"]))})
    monkeypatch.setattr(fakes_yf, "CALENDARS", {"AAA.F": {"Ex-Dividend Date": dt.date(2026, 9, 10),
                                                          "Dividend Date": dt.date(2026, 9, 28)}})
    p = build(tmp_path, _with(tmp_path, dividends=[("2026-03-20", "AAA.F", 10, 10.0, 2.64, 7.36)]))
    div = panel(p, "dividends")
    assert div["vis"] == "private" and [c["k"] for c in div["cols"]] == ["tkr", "ex", "pay", "shrs", "ps", "net", "status"]
    rows = [(r["status"], r["ex"], r["pay"]) for r in div["rows"]]
    assert rows == [("NEXT", "2026-09-10", "28 SEP 26"), ("DUE", "2026-06-15", "~03 JUL 26"),
                    ("PAID", "2026-03-02", "20 MAR 26")]
    acct = panel(p, "accounting")["lines"]
    assert _line(acct, "Dividends (net)")["v"] == pytest.approx(7.36)
    assert _line(acct, "Dividends due (not yet paid)")["v"] > 0
    assert "dividends" not in [q["id"] for q in public_view(p)["panels"]]


def _line(lines, label):
    return next(l for l in lines if l.get("label") == label)


def test_accounting_shows_bonus_and_interest_and_still_reconciles(frozen, tmp_path):
    csv = _with(tmp_path, "2026-04-01,AAA.F,bonus,0.1,12.00,120.00",
                interest=[("2026-01-01", "3.95"), ("2026-02-01", "4.10")])
    lines = panel(build(tmp_path, csv), "accounting")["lines"]
    v = lambda k: _line(lines, k)["v"]
    assert v("Bonus") == pytest.approx(12.0) and v("Gross deposits") == pytest.approx(3990.0)
    assert v("Gross deposits") - v("Cash returned") == pytest.approx(v("Net invested"))
    assert v("Net cost basis") + v("Unrealized") == pytest.approx(v("Market value"))
    assert v("Realized") + v("Dividends (net)") + v("Bonus") + v("Unrealized") == pytest.approx(v("Total P&L"))
    labels = [l.get("label") for l in lines]
    t = labels.index("Total P&L")
    assert lines[t + 1].get("sep") and labels[t + 2:t + 4] == ["Dividends due (not yet paid)", "Interest on cash (not in ROI)"]
    interest = lines[t + 3]
    assert interest["v"] == pytest.approx(8.05) and interest["fmt"] == "eur+" and interest.get("vis") != "public"


def test_interest_never_moves_roi_or_total_pnl(frozen, tmp_path):
    base = build(tmp_path, _with(tmp_path))
    paid = build(tmp_path, _with(tmp_path, interest=[("2026-01-01", "50.00")]))
    for k in ("ROI", "TOTAL P&L", "XIRR /YR", "YTD", "YTD TWR"):
        assert kpi(paid, "summary", k) == pytest.approx(kpi(base, "summary", k)), k


def test_posval_draws_a_bonus_as_a_buy(frozen, tmp_path):
    csv = _with(tmp_path, "2026-04-01,AAA.F,bonus,0.1,12.00,120.00")
    sv = panel(build(tmp_path, csv), "posval")["series_by_key"]["AAA.F"]
    ser = {s["name"]: s["y"] for s in sv["series"]}
    buys = [sv["x"][i] for i, y in enumerate(ser["BUY"]) if y is not None]
    assert _epoch_of("2026-04-01") in buys
    assert all(y is None for y in ser["SELL"])


def test_summary_has_both_ytds_and_a_year_table(frozen, tmp_path):
    """YTD: this year's growth, money-weighted — the gain over the money at work, payments never growth;
    YTD TWR: the time-weighted return. The year table: first- and last-day value, growth, time-weighted, gain."""
    from monitor.portfolio import snapshot
    p = build(tmp_path)
    s = panel(p, "summary")
    keys = [i["k"] for i in s["items"]]
    assert keys.index("YTD TWR") == keys.index("YTD") + 1
    years = s["years"]
    assert [y["label"] for y in years] == ["2026", "2025"]                  # newest first
    assert all(y["fmt"] == "pct+" and y["vis"] == "public" and {"v", "v2", "first", "end", "gain"} <= set(y)
               for y in years)
    assert kpi(p, "summary", "YTD") == years[0]["v"] and kpi(p, "summary", "YTD TWR") == years[0]["v2"]
    # 2025: from the close of the first day held (6 Jan) to 31 Dec; 2026: from 31 Dec to the live value
    hold = snapshot.daily_tier(snapshot.load_book(FIX), buffer_dir=tmp_path / "buffer")["hold"]
    first, end = float(hold.loc["2025-01-06"]), float(hold.loc[:"2025-12-31"].iloc[-1])
    assert (years[1]["first"], years[1]["end"]) == (pytest.approx(first), pytest.approx(end))
    assert 0 < years[1]["v"] < years[1]["gain"] / first * 100        # money added later dilutes, never adds
    assert years[0]["first"] == pytest.approx(end) and years[0]["end"] == pytest.approx(kpi(p, "summary", "VALUE"))
    assert years[1]["gain"] == pytest.approx(end + 560 - 3350)               # bought 3350, sold 560
    assert years[0]["gain"] == pytest.approx(kpi(p, "summary", "VALUE") + 880 - end - 640)


def test_public_year_table_keeps_the_percentages_and_drops_euros(frozen, tmp_path):
    """Public: growth and time-weighted %, like XIRR — no first- or last-day values, no euro gain."""
    p = build(tmp_path)
    pub = panel(public_view(p), "summary")
    assert [y["label"] for y in pub["years"]] == [y["label"] for y in panel(p, "summary")["years"]]
    assert all(set(y) == {"label", "v", "v2", "fmt", "vis"} for y in pub["years"])
    assert "€" not in json.dumps(public_view(p))


def test_help_explains_net_dividends_and_both_ytds_publicly_without_euros(frozen, tmp_path):
    pub = public_view(build(tmp_path))["help"]
    by = {h["h"]: h["body"] for h in pub}
    assert "26.375 % dividend tax" in by["ROI"]          # settings: dividend_tax
    assert "YTD TWR" in by["YTD"] and "Alt+1" in by["YTD"]
    assert "€" not in json.dumps(pub)


def test_year_gains_add_up_to_total_pnl(frozen, tmp_path, monkeypatch):
    import pandas as pd
    monkeypatch.setattr(fakes_yf, "DIVIDENDS", {"AAA.F": pd.Series([2.0], index=pd.to_datetime(["2025-03-03"])),
                                                "BBB.F": pd.Series([1.5], index=pd.to_datetime(["2026-02-02"]))})
    p = build(tmp_path, _with(tmp_path, "2026-04-01,AAA.F,bonus,0.1,12.00,120.00"))
    total = _line(panel(p, "accounting")["lines"], "Total P&L")["v"]
    assert sum(y["gain"] for y in panel(p, "summary")["years"]) == pytest.approx(total, abs=0.01)


def test_benchmark_help_says_dividends_after_tax_vs_total_return_before_tax(frozen, tmp_path):
    body = next(h["body"] for h in public_view(build(tmp_path))["help"] if h["h"] == "ROI vs SAME CASH ELSEWHERE")
    assert "after tax" in body and "total return before tax" in body and "€" not in body


ZED = {"name": "Zed AG", "sector": "Technology", "country": "Germany"}     # Yahoo's profile (faked)


def test_a_new_holding_is_named_and_placed_without_a_code_edit(frozen, tmp_path, monkeypatch):
    monkeypatch.setattr(Y, "fetch_info", lambda t: ZED if t == "ZZZ.DE" else None)
    csv = _with(tmp_path, "2026-03-02,RHM.DE,buy,2,1000.00,500.00", "2026-03-02,ZZZ.DE,buy,10,1000.00,100.00")
    p = build(tmp_path, csv)
    names = {r["tkr"]: r["name"] for r in panel(p, "positions")["rows"]}
    assert names["RHM.DE"] == "Rheinmetall" and names["ZZZ.DE"] == "Zed AG"      # TR universe, Yahoo
    assert names["AAA.F"] == "AAA.F"                                              # nobody knows it
    alloc = panel(p, "allocation")
    held = {r["label"] for r in alloc["sectors"] if r["v"]}
    assert {"Industrials", "Information Technology"} <= held
    assert {"Germany"} <= {r["label"] for r in alloc["countries"]}


def test_help_follows_settings_edited_while_running(frozen, tmp_path, monkeypatch):
    from monitor import config
    monkeypatch.setattr(config, "ORDER_FEE_EUR", 2.5)
    monkeypatch.setattr(config, "DIVIDEND_TAX", 0.15)
    by = {h["h"]: h["body"] for h in build(tmp_path)["help"]}
    assert "2.5 EUR order fee" in by["ROI vs SAME CASH ELSEWHERE"] and "15 % dividend tax" in by["ROI"]


def test_a_line_quoted_in_another_currency_is_warned_about(frozen, tmp_path, monkeypatch):
    """Prices are taken as euros (EUR listings only): a held line Yahoo quotes in USD is named in
    meta.warn for the status bar (CCY USD: BBB.F) — PORT, OPT and RISK alike; never in the public view."""
    from monitor.screens import opt, risk
    monkeypatch.setitem(fakes_yf.CURRENCIES, "BBB.F", "USD")
    p = build(tmp_path)
    assert p["meta"]["warn"] == ["CCY USD: BBB.F"]
    assert "warn" not in public_view(p)["meta"]
    ctx = Ctx(force=False, buffer_dir=tmp_path / "buffer", portfolio_csv=FIX, equity_log=None)
    for scr in (opt, risk):
        assert scr.assemble({t: scr.compute(t, ctx) for t in scr.SCREEN.tiers}, dict(META))["meta"]["warn"] == [
            "CCY USD: BBB.F"]
    from monitor.server.engine import Engine
    from monitor.server.store import Store
    from tests.server.helpers import Recorder
    eng = Engine({"PORT": port.SCREEN}, Store(tmp_path / "screens"), Recorder(), ctx=ctx)
    assert eng.compute_now("PORT")["meta"]["warn"] == ["CCY USD: BBB.F"]          # the local view keeps it
    assert eng.payload("PORT")["meta"]["warn"] == ["CCY USD: BBB.F"]
    monkeypatch.setitem(fakes_yf.CURRENCIES, "BBB.F", "EUR")
    assert build(tmp_path)["meta"]["warn"] == []


def test_the_context_dates_the_first_trade_dd_mon_yy(frozen, tmp_path):
    assert build(tmp_path)["context"]["text"].endswith(" · EUR · SINCE 06 JAN 25")


def test_positions_give_up_shares_and_average_cost_before_scrolling_sideways(frozen, tmp_path):
    cols = {c["k"]: c for c in panel(build(tmp_path), "positions")["cols"]}
    assert cols["shrs"].get("lo") and cols["avg"].get("lo")
    assert not any(cols[k].get("lo") for k in ("tkr", "value", "wt", "pnl", "pnlp"))
