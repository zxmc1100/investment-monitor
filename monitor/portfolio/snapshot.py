"""Portfolio state in two refresh tiers.

quote_tier — live quotes → positions, accounting, day P&L        (seconds)
daily_tier — price history → ROI vs benchmarks, per-asset curves,
             risk metrics, held-ticker history                    (minutes)

Shared by the PORT / OPT / RISK / SEC screens. Every figure keeps the canonical
formulas: ROI = (holdings value + cash from sells + dividends) / all buys − 1 (analytics). Bonus
shares are not buys (their value is gain); interest on cash is reported but never in ROI. A ticker
with no quote ever is carried at cost by the ledger summary and reported in `missing`;
a failed live fetch keeps the last-good price and is reported in `stale` — callers must
surface both.
"""
import logging
from datetime import date
from pathlib import Path

import pandas as pd

from monitor import config
from monitor.data.buffer import (cached_dividends, cached_events, cached_market_caps, cached_ohlc, cached_pay_dates,
                                 cached_price_history, cached_quotes, never_quoted)
from monitor.data.instruments import BENCHMARKS
from monitor.portfolio import equity_log
from monitor.portfolio.analytics import build_roi_timeseries, compute_quant_metrics, twr_index, xirr
from monitor.portfolio.dividends import cash, combine
from monitor.portfolio.ledger import compute_portfolio_summary, load_interest, load_paid_dividends, parse_portfolio

log = logging.getLogger(__name__)


def load_book(csv_path: str | Path) -> dict:
    """The trade ledger: holdings, realized detail, transactions (see parse_portfolio), plus
    `interest` — cash interest from `interest.csv` next to the CSV (see ledger.load_interest) — and
    `paid_dividends` — what your broker paid, from `dividends.csv` (see ledger.load_paid_dividends)."""
    book = parse_portfolio(csv_path)
    book["interest"] = load_interest(Path(csv_path).with_name("interest.csv"))
    book["paid_dividends"] = load_paid_dividends(Path(csv_path).with_name("dividends.csv"))
    return book


def accounting(txns: list[dict], totals: dict, today: date | None = None, dividends=(), interest=(),
               due: float = 0.0) -> dict:
    """Transparent accounting — every line reconciles to the next; dividends are cash received (net of tax,
    on their pay date). Bonus shares are not deposits but sit in the cost basis at their booked value, so
    their value counts as gain: total P&L = value + sells + dividends − deposits. Interest on cash and
    dividends `due` (ex date passed, not yet paid) are reported only — never in total P&L, ROI or XIRR."""
    today = today or date.today()
    gross_deposits = sum(t["price"] for t in txns if t["action"] == "buy")
    bonus = sum((t["price"] for t in txns if t["action"] == "bonus"), 0.0)
    cash_returned = sum(t["price"] for t in txns if t["action"] == "sell")
    realized = totals["realized_pnl"]
    cost_of_sold = cash_returned - realized          # cost basis of shares sold
    net_cost_basis = gross_deposits + bonus - cost_of_sold   # cost basis still at work (open)
    current_value = totals["current_value"]          # live market value of open
    unrealized = current_value - net_cost_basis
    dividends_eur = sum(d["eur"] for d in dividends)
    total_pnl = realized + unrealized + dividends_eur + bonus
    net_invested = gross_deposits - cash_returned    # out-of-pocket cash
    simple_roi = total_pnl / gross_deposits * 100 if gross_deposits else 0.0

    # Money-weighted return (XIRR): dated cash flows, buys negative, sells, dividends and value
    # today positive. A bonus moved no money: not a flow.
    flows = [(date.fromisoformat(t["date"]), -t["price"] if t["action"] == "buy" else t["price"])
             for t in txns if t["action"] in ("buy", "sell")]
    flows += [(date.fromisoformat(d["date"]), d["eur"]) for d in dividends]
    flows.append((today, current_value))
    mwr = xirr(flows)
    hold_years = (today - min(f[0] for f in flows)).days / 365.0
    mwr_cumulative = ((1 + mwr) ** hold_years - 1) * 100 if mwr is not None else None
    return dict(gross_deposits=gross_deposits, cash_returned=cash_returned, realized=realized,
                cost_of_sold=cost_of_sold, net_cost_basis=net_cost_basis,
                current_value=current_value, unrealized=unrealized, total_pnl=total_pnl,
                net_invested=net_invested, simple_roi=simple_roi, mwr=mwr,
                mwr_cumulative=mwr_cumulative, hold_years=hold_years, dividends=dividends_eur,
                bonus=bonus, interest=sum((i["eur"] for i in interest), 0.0), dividends_due=due)


def log_equity(path: str | Path, acct: dict, today: date | None = None) -> None:
    """Append today's accounting snapshot to the auditable daily equity log."""
    equity_log.append_snapshot(path, dict(
        date=str(today or date.today()), current_value=round(acct["current_value"], 2),
        net_cost_basis=round(acct["net_cost_basis"], 2), gross_deposits=round(acct["gross_deposits"], 2),
        cash_returned=round(acct["cash_returned"], 2), realized_pnl=round(acct["realized"], 2),
        unrealized_pnl=round(acct["unrealized"], 2), total_pnl=round(acct["total_pnl"], 2)))


def old_bars(quotes: dict, today: date | None = None) -> dict[str, str]:
    """Quotes whose last daily bar is older than two business days (one holiday tolerated)."""
    cutoff = (pd.Timestamp(today or date.today()) - pd.offsets.BDay(2)).date()
    return {t: q["date"] for t, q in quotes.items()
            if q and q.get("date") and date.fromisoformat(q["date"]) < cutoff}


def dividend_records(book: dict, *, force: bool = False, buffer_dir: Path | None = None) -> list[dict]:
    """Every dividend — PAID, DUE, UPCOMING — with ex date, pay date and net (see portfolio.dividends): your
    broker's dividends.csv, Yahoo's per-share history for every ticker ever traded (buffered 24 h), its
    calendar for the lines you hold (the next ex date; buffered 24 h) and the home lines' real pay dates as PORT
    last gathered them (data.buffer.cached_pay_dates — read here, never asked: every tier sees the same dates)."""
    tickers = sorted({t["ticker"] for t in book["transactions"]})
    if not tickers:
        return []
    held = sorted(book["holdings"])
    events = cached_events(held, buffer_dir=buffer_dir) if held else {}
    calendar = {t: {"ex": e["date"], "pay": e.get("pay"), "amount": e.get("amount")}
                for t, evs in events.items() for e in evs if e["kind"] == "EX-DIV"}
    return combine(book["transactions"], cached_dividends(tickers, force=force, buffer_dir=buffer_dir),
                   book.get("paid_dividends", []), calendar, tax=config.DIVIDEND_TAX,
                   homes=cached_pay_dates(None, buffer_dir=buffer_dir))


def dividends(book: dict, *, force: bool = False, buffer_dir: Path | None = None) -> list[dict]:
    """The dividends that are cash — paid, on their pay date (see dividend_records, portfolio.dividends.cash)."""
    return cash(dividend_records(book, force=force, buffer_dir=buffer_dir))


def marked(book: dict, quotes: dict) -> dict:
    """The book marked to `quotes` (pure): prices, the ledger summary, the open positions, and the
    day P&L / DAY % — each position's move from its previous close, over the value at that close.
    PORT's quote tier and the alert job's PORT DAY / PORT WEIGHT (monitor.alerts.watch) share it."""
    prices = {t: (q["price"] if q else None) for t, q in quotes.items()}
    summary = compute_portfolio_summary(book, prices)
    positions = [p for p in summary["positions"] if p["position_value"] > 0]
    day_pnl = sum(p["shares"] * (quotes[p["ticker"]]["price"] - quotes[p["ticker"]]["prev_close"])
                  for p in positions
                  if quotes.get(p["ticker"]) and quotes[p["ticker"]]["prev_close"] is not None)
    value = sum(p["position_value"] for p in positions)
    base = value - day_pnl
    return dict(prices=prices, summary=summary, positions=positions,
                day_pnl=day_pnl, day_pct=(day_pnl / base * 100) if base > 0 else None)


def ccy_warn(quotes: dict) -> list[str]:
    """Prices are taken as euros (EUR listings only): one 'CCY <ccy>: <tickers>' line per foreign
    currency a quoted line is priced in (Yahoo's quote currency, monitor.data.yahoo.fetch_quotes) — the
    status bar shows them. A quote without a currency (older buffer, daily fallback) says nothing."""
    by: dict[str, list[str]] = {}
    for t, q in sorted(quotes.items()):
        c = (q or {}).get("ccy")
        if c and c != "EUR":
            by.setdefault(c, []).append(t)
    return [f"CCY {c}: {' '.join(ts)}" for c, ts in sorted(by.items())]


def quote_tier(book: dict, *, force: bool = False, buffer_dir: Path | None = None, _fetch=None) -> dict:
    holdings = book["holdings"]
    quotes, stale, as_of = cached_quotes(list(holdings), force=force, buffer_dir=buffer_dir, _fetch=_fetch)
    stale = {**old_bars(quotes), **stale}        # a failed live fetch keeps its last-good ts
    m = marked(book, quotes)
    records = dividend_records(book, buffer_dir=buffer_dir)   # never forced: the 24 h caches refetch themselves
    divs = cash(records)
    return dict(quotes=quotes, prices=m["prices"], stale=stale, as_of=as_of, warn=ccy_warn(quotes),
                missing=sorted(t for t, q in quotes.items() if q is None),
                summary=m["summary"], positions=m["positions"],
                acct=accounting(book["transactions"], m["summary"]["totals"], dividends=divs,
                                interest=book.get("interest", []),
                                due=sum(r["net"] for r in records if r["status"] == "DUE")),
                day_pnl=m["day_pnl"], day_pct=m["day_pct"],
                txns=book["transactions"], realized=book["realized"], dividends=divs, dividend_records=records)


BENCH_FX = "EURUSD=X"       # the USD benchmark lines' rate, as the ROI walk reads it (USD per EUR)


def bench_quotes(*, force: bool = False, buffer_dir: Path | None = None, _fetch=None) -> dict[str, dict]:
    """{ticker: quote} for the ROI chart's benchmark lines and BENCH_FX — what PORT marks today's point of each
    benchmark with (analytics.bench_live). The shared quote buffer, re-asked at most every
    config.BENCH_QUOTE_S. Good quotes only: a failed or stale one is left out, and its line keeps the daily
    part's point."""
    tickers = [t for t, _ in BENCHMARKS.values()] + [BENCH_FX]
    quotes, stale, _ = cached_quotes(tickers, force=force, fresh_s=config.BENCH_QUOTE_S, buffer_dir=buffer_dir,
                                     _fetch=_fetch)
    return {t: q for t, q in quotes.items() if q is not None and t not in stale and q.get("date")}


def holdings_value(asset_values: dict) -> pd.Series:
    """EUR value of everything held per business day: the per-ticker curves summed (not held = 0).
    Cash from sells and dividends is excluded — it left the holdings (see analytics.year_returns)."""
    curves = [s for k, s in asset_values.items() if not k.startswith("__") and isinstance(s, pd.Series)]
    if not curves:
        return pd.Series(dtype=float, index=pd.DatetimeIndex([]))
    return pd.concat(curves, axis=1).fillna(0.0).sum(axis=1)


def daily_tier(book: dict, *, force: bool = False, buffer_dir: Path | None = None) -> dict:
    """ROI vs benchmarks, per-asset curves, risk metrics and held-ticker history, plus `hold` (the
    holdings value per business day) — analytics.year_returns' input; PORT pairs it with the quote
    tier's fresh transactions and dividends (PORT's YTD comes from analytics.year_returns) — and
    `twr`, the holdings' time-weighted growth (analytics.twr_index; PORT's normalized ROI chart). Only a
    held line the quote buffer has never priced may lack history (it is carried at cost, as the quote
    tier does); any other gap raises analytics.PriceHistoryError."""
    txns = book["transactions"]
    divs = dividends(book, force=force, buffer_dir=buffer_dir)
    roi_series, bm_series, asset_values = build_roi_timeseries(
        txns, dividends=divs, never_priced=never_quoted(book["holdings"], buffer_dir=buffer_dir))
    metrics = compute_quant_metrics(roi_series, bm_series.get("S&P 500"))
    held = sorted(book["holdings"])
    history = (cached_price_history(held, period="5y", force=force, buffer_dir=buffer_dir)
               if held else pd.DataFrame())
    hold = holdings_value(asset_values)
    return dict(roi_series=roi_series, bm_series=bm_series, asset_values=asset_values,
                metrics=metrics, history=history, hold=hold, twr=twr_index(hold, txns, divs))


SPX_PROXY = BENCHMARKS["S&P 500"][0]          # CSPX.AS — EUR-listed, matches the ROI benchmark


def risk_inputs(book: dict, *, force: bool = False, buffer_dir: Path | None = None) -> dict:
    """Buffered inputs for riskmodel.build_model (kwargs): position values from BUFFERED quotes,
    5y closes, market caps and the S&P 500 proxy. The network is hit whenever a buffer is cold
    or past its TTL: quotes (cold only — never forced here), 5y closes (12 h), dividends inside
    quote_tier (24 h) and market caps (24 h) — ETFs never get a cap, so a book holding ETFs
    re-asks Yahoo for their caps on every call. `force` refetches closes and caps."""
    q = quote_tier(book, force=False, buffer_dir=buffer_dir)
    values = {p["ticker"]: p["position_value"] for p in q["positions"]}
    tickers = list(values)
    history = (cached_price_history(tickers, period="5y", force=force, buffer_dir=buffer_dir)
               if tickers else pd.DataFrame())
    caps = cached_market_caps(tickers, force=force, buffer_dir=buffer_dir) if tickers else {}
    spx = cached_price_history([SPX_PROXY], period="5y", force=force, buffer_dir=buffer_dir)
    return dict(values=values, history=history, caps=caps,
                spx=spx[SPX_PROXY] if SPX_PROXY in spx.columns else None)


def long_history(tickers, *, force: bool = False, buffer_dir: Path | None = None):
    """Full-history closes for stress windows (24 h cache) and ^GSPC in EUR (None if either
    the index or the EURUSD=X rate is unavailable; never a USD fallback). A failed closes fetch
    degrades to an empty frame. Both failures are logged as warnings, never raised."""
    tickers = sorted(tickers)
    try:
        hist = (cached_price_history(tickers, period="max", ttl_hours=24, force=force, buffer_dir=buffer_dir)
                if tickers else pd.DataFrame())
    except Exception as e:          # every window leg then estimates from beta (or is None without SPX)
        log.warning("full-history closes for %s unavailable (%s): stress windows fall back to beta",
                    ", ".join(tickers), e)
        hist = pd.DataFrame()
    try:
        gspc = cached_ohlc("^GSPC", "max", ttl_hours=24, force=force, buffer_dir=buffer_dir)["Close"]
        fx = cached_ohlc("EURUSD=X", "max", ttl_hours=24, force=force, buffer_dir=buffer_dir)["Close"]
        fx_on = fx.dropna().reindex(fx.dropna().index.union(gspc.index)).ffill().reindex(gspc.index)
        spx = (gspc / fx_on).dropna()
    except Exception as e:
        log.warning("S&P 500 in EUR unavailable (%s): beta-estimated stress legs are None", e)
        spx = None
    return hist, spx
