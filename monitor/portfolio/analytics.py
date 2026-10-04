"""
Portfolio quant analytics: ROI time series, risk metrics, calendar-year returns, XIRR.
Cash-flow matched benchmarks (same EUR amount, less the same order fee, on same dates).
All returns in EUR terms with FX conversion for USD-denominated instruments.
"""

import time
import warnings
import numpy as np
import pandas as pd
import yfinance as yf
from datetime import date as _date, datetime

from monitor import config
from monitor.data.instruments import BENCHMARKS, TICKER_MAP
from monitor.portfolio.ledger import ADDS, DUST


# ── Helpers ──────────────────────────────────────────────────────────────────

class PriceHistoryError(RuntimeError):
    """Yahoo answered without the history a correct ROI needs (it throttles bursts with empty
    frames). The build fails so the last good result stays on screen — positions are never
    silently valued at cost because a download came back empty."""


RETRIES = 3                                     # attempts per batch download, backed off


def _held_now(transactions) -> set[str]:
    """Tickers still held — a remainder at or below ledger.DUST is an exit, as in the ledger."""
    sh: dict[str, float] = {}
    for t in transactions:
        sh[t["ticker"]] = sh.get(t["ticker"], 0.0) + (t["shares"] if t["action"] in ADDS else
                                                       -t["shares"] if t["action"] == "sell" else 0.0)
    return {tk for tk, n in sh.items() if n > DUST}


def _norm(h: pd.Series) -> pd.Series:
    """Strip timezone, normalize to midnight."""
    if h.index.tz is not None:
        h.index = h.index.tz_localize(None)
    h.index = h.index.normalize()
    return h.sort_index()


def _price_on(h: pd.Series, date: pd.Timestamp) -> float | None:
    """What a line is worth on `date`: the last close on or before it — a day without a bar (31 Dec
    on Xetra, a holiday) keeps the close before, never the next session's (that put the new year's
    first move into the old year). Before the first bar: the first bar."""
    past = h[h.index <= date]
    if not past.empty:
        return float(past.values.flatten()[-1])
    return float(h.values.flatten()[0]) if not h.empty else None


def _fill_price(h: pd.Series, date: pd.Timestamp) -> float | None:
    """Where a buy dated `date` fills: the first close on or after it (a Sunday trade fills at
    Monday's close). After the last bar: the last bar."""
    future = h[h.index >= date]
    if not future.empty:
        return float(future.values.flatten()[0])
    return float(h.values.flatten()[-1]) if not h.empty else None


# ── Core time-series builder ──────────────────────────────────────────────────

def build_roi_timeseries(transactions: list[dict], dividends=(),
                         never_priced: set[str] | None = None) -> tuple[pd.Series, dict, dict]:
    """
    Returns (portfolio_roi_series, {benchmark_name: roi_series}, {key: eur_series}).

    The first two are pd.Series with DatetimeIndex, values = cumulative ROI %.
    The third maps each ticker to its EUR position value per business day (NaN when
    not held), plus "__cash__" (running sale proceeds + dividends, NaN before the first
    sell or dividend, absent if there are none) and "__total__" (holdings + cash — the same figure that
    feeds the ROI numerator, so it reconciles to the sum of the other keys).

    It also carries "__roi__" -> {ticker: ROI % series}, each position's own
    (value + its sale proceeds + dividends) / its own buys - 1, i.e. the portfolio formula
    applied per position (portfolio ROI = (holdings + sale proceeds + dividends) / buys - 1), plus "__total__" for the portfolio line itself. Same index and same
    NaN gaps as the EUR series, so the two views are swappable on one chart — except days a
    position holds only bonus shares (nothing invested yet: no % to divide by).

    And "__twr__" -> {benchmark name: growth of 1 €}, time-weighted (each buy's money taken out of
    its day, so the line is the benchmark's EUR total return less the order fees), on that
    benchmark's ROI index — your own line's is twr_index on the holdings (snapshot.daily_tier).

    Bonus rows (ledger.ADDS) add shares — so value — but no money: never in `total_invested`,
    a position's own buys, or the benchmarks' cash flows. Their value is gain.

    `never_priced` (tickers in your CSV Yahoo has never quoted — PORT's daily tier reads them off the quote
    buffer): only those held lines may come back without history, carried at cost; any other gap
    raises PriceHistoryError. None (a caller without a quote buffer, e.g. a batch job): one missing held
    line is tolerated. Without
    an EUR/USD rate the USD benchmarks are left out.
    """
    buys = [t for t in transactions if t["action"] == "buy"]
    if not buys:
        return pd.Series(dtype=float), {}, {}

    start_date = min(t["date"] for t in buys)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")

        # --- Batch download: portfolio tickers + benchmarks + EUR/USD in 2 calls ---
        port_tickers = list(set(t["ticker"] for t in transactions))
        yf_port_tickers = [TICKER_MAP.get(tk, tk) for tk in port_tickers]
        bm_tickers = [ticker for ticker, _ in BENCHMARKS.values()] + ["EURUSD=X"]

        def _once(tickers: list[str], start: str, adjusted: bool) -> dict[str, pd.Series]:
            result = {}
            try:
                raw = yf.download(tickers, start=start, auto_adjust=adjusted, progress=False)
                close = raw["Close"] if "Close" in raw else raw
                if isinstance(close, pd.Series) or len(tickers) == 1:
                    s = (close if isinstance(close, pd.Series) else close.iloc[:, 0]).dropna()
                    if not s.empty:
                        result[tickers[0]] = _norm(s)
                else:
                    for tk in tickers:
                        if tk in close.columns:
                            s = close[tk].dropna()
                            if not s.empty:
                                result[tk] = _norm(s)
            except Exception:
                pass
            return result

        def _batch_download(tickers: list[str], start: str, adjusted: bool, need) -> dict[str, pd.Series]:
            """Download multiple tickers at once, return {ticker: Close series}. Retried (backed
            off) while a needed ticker is missing — Yahoo throttles bursts with empty frames. Every
            attempt's lines are kept: two partial answers can price the whole book together."""
            best: dict[str, pd.Series] = {}
            for attempt in range(RETRIES):
                best.update(_once(tickers, start, adjusted))
                if not (set(need) - set(best)):
                    break
                if attempt < RETRIES - 1:
                    time.sleep(1.2 * (attempt + 1))
            return best

        # Holdings at REAL closes (split- but not dividend-adjusted): Yahoo's adjusted closes
        # lower old prices by every later dividend, which fakes a loss against your buy price;
        # dividends are counted as cash instead. Benchmarks stay adjusted = total return.
        held = _held_now(transactions)
        if never_priced is not None and len(held) > 1 and held <= set(never_priced):
            never_priced = set()          # no quote for ANY held line: Yahoo is down, not a listing gap
        expected = held - set(never_priced or ())
        port_data = _batch_download(yf_port_tickers, start_date, adjusted=False,
                                    need={TICKER_MAP.get(tk, tk) for tk in expected})
        # A held line Yahoo has never priced (no quote ever — `never_priced`, from the quote buffer)
        # is carried at cost, however many there are; any other missing held line means the answer
        # was throttled — fail, so the last good result stays. Without `never_priced` (a caller with no
        # quote buffer) one missing line is tolerated, two or all of them fail.
        missing = sorted(tk for tk in held if TICKER_MAP.get(tk, tk) not in port_data)
        bad = ([tk for tk in missing if tk not in never_priced] if never_priced is not None else
               missing if held and (len(missing) >= 2 or len(missing) == len(held)) else [])
        if bad:
            raise PriceHistoryError(f"no price history from Yahoo for {', '.join(bad)}")
        bm_data   = _batch_download(bm_tickers, start_date, adjusted=True, need=())   # optional lines

        # Map back: orig portfolio ticker → series
        port_hist: dict[str, pd.Series] = {}
        for orig, yft in zip(port_tickers, yf_port_tickers):
            if yft in port_data:
                port_hist[orig] = port_data[yft]

        eurusd = bm_data.get("EURUSD=X", pd.Series(dtype=float))

        bm_hists: dict[str, tuple[pd.Series, str]] = {}
        for name, (ticker, currency) in BENCHMARKS.items():
            # no EUR/USD rate → the USD lines are dropped, never valued as if they were euros
            if ticker in bm_data and (currency != "USD" or not eurusd.empty):
                bm_hists[name] = (bm_data[ticker], currency)

    biz_days = pd.bdate_range(start=start_date, end=datetime.today())

    # Chronological queue — consumed with a `<=` pointer so weekend/holiday-dated
    # transactions (e.g. Tradegate Sunday trades) apply on the next business day
    # instead of being silently skipped.
    txn_queue = sorted(transactions, key=lambda x: (x["date"], x["action"]))
    txn_idx = 0
    div_queue = sorted(dividends, key=lambda d: d["date"])
    div_idx = 0

    # ── Portfolio series (cash-flow matched, same formula as benchmarks) ─────────
    # Return = (current_value / total_invested - 1), matching the benchmark formula
    # at line 204. This makes pp-delta comparisons meaningful.
    holdings: dict[str, float] = {}
    avg_cost: dict[str, float] = {}
    total_invested = 0.0
    cash_in = 0.0   # sale proceeds + dividends stay in the return calc (otherwise sells look like losses)
    port_vals: dict[str, float] = {}
    # Per-asset EUR value on each business day. Filled inside the SAME day-walk that
    # produces the ROI line, from the same prices and the same `<=` txn pointer, so
    # the two charts can never disagree.
    asset_vals: dict[str, dict[str, float]] = {}
    cash_vals: dict[str, float] = {}
    total_vals: dict[str, float] = {}
    seen_cash = False
    # Per-asset ROI %, on the SAME formula as the portfolio line: a position's own
    # (value + its sale proceeds + dividends) / its own buys - 1. Rebasing the EUR curve instead
    # (value / first value) would score buying more shares as a gain.
    invested_tk: dict[str, float] = {}
    cash_tk: dict[str, float] = {}
    roi_vals: dict[str, dict[str, float]] = {}

    def _position_values(dt: pd.Timestamp) -> dict[str, float]:
        vals: dict[str, float] = {}
        for tk, sh in holdings.items():
            if sh <= 0:
                continue
            p = _price_on(port_hist[tk], dt) if tk in port_hist else avg_cost.get(tk)
            if p is None:
                p = avg_cost.get(tk, 0.0)
            vals[tk] = sh * p
        return vals

    for date in biz_days:
        ds = str(date.date())
        while txn_idx < len(txn_queue) and txn_queue[txn_idx]["date"] <= ds:
            txn = txn_queue[txn_idx]
            txn_idx += 1
            tk = txn["ticker"]
            if txn["action"] in ADDS:
                sh, pps = float(txn["shares"]), float(txn["pps"])
                prev = holdings.get(tk, 0.0)
                new  = prev + sh
                avg_cost[tk] = (prev * avg_cost.get(tk, pps) + sh * pps) / new if new else pps
                holdings[tk] = new
                if txn["action"] == "buy":          # a bonus is shares received, not money spent
                    total_invested += float(txn["price"])
                    invested_tk[tk] = invested_tk.get(tk, 0.0) + float(txn["price"])
            elif txn["action"] == "sell":
                holdings[tk] = max(0.0, holdings.get(tk, 0.0) - float(txn["shares"]))
                cash_in += float(txn["price"])
                cash_tk[tk] = cash_tk.get(tk, 0.0) + float(txn["price"])
                seen_cash = True

        while div_idx < len(div_queue) and div_queue[div_idx]["date"] <= ds:
            dv = div_queue[div_idx]
            div_idx += 1
            cash_in += dv["eur"]
            cash_tk[dv["ticker"]] = cash_tk.get(dv["ticker"], 0.0) + dv["eur"]
            seen_cash = True

        if total_invested == 0:
            continue

        pos_vals = _position_values(date)
        value = sum(pos_vals.values()) + cash_in
        port_vals[ds] = round((value / total_invested - 1) * 100, 4)

        for tk, v in pos_vals.items():
            asset_vals.setdefault(tk, {})[ds] = v
            # Only while held, so the % line starts and ends exactly where the € one does.
            if invested_tk.get(tk):                 # bonus shares only: nothing invested to divide by
                roi_vals.setdefault(tk, {})[ds] = (v + cash_tk.get(tk, 0.0)) / invested_tk[tk] * 100 - 100
        if seen_cash:
            cash_vals[ds] = cash_in      # NaN before the first sale or dividend
        total_vals[ds] = value
        roi_vals.setdefault("__total__", {})[ds] = port_vals[ds]

    portfolio_series = pd.Series(port_vals)
    portfolio_series.index = pd.to_datetime(portfolio_series.index)

    def _daily(vals: dict[str, float]) -> pd.Series:
        """Dict of 'YYYY-MM-DD' -> value, reindexed on every business day.

        Reindexing leaves NaN where the position wasn't held, so plotly breaks the
        line instead of drawing it along the axis — and a re-buy after a full exit
        renders as two segments rather than one phantom stroke.
        """
        s = pd.Series(vals, dtype=float)
        if not s.empty:
            s.index = pd.to_datetime(s.index)
        return s.reindex(biz_days)

    asset_values: dict[str, pd.Series] = {tk: _daily(v) for tk, v in asset_vals.items()}
    if cash_vals:
        asset_values["__cash__"] = _daily(cash_vals)
    asset_values["__total__"] = _daily(total_vals)
    # Nested under a reserved key: callers iterate the top level for tickers, and
    # every consumer already filters keys starting with "__".
    asset_values["__roi__"] = {tk: _daily(v) for tk, v in roi_vals.items()}

    # ── Benchmark series ──────────────────────────────────────────────────────
    buy_events = sorted([(t["date"], float(t["price"]),
                          0.0 if t["ticker"] in config.SAVINGS_PLAN_TICKERS
                          else min(config.ORDER_FEE_EUR, float(t["price"]))) for t in buys],
                        key=lambda x: x[0])
    benchmark_series: dict[str, pd.Series] = {}
    benchmark_twr: dict[str, pd.Series] = {}

    for name, (bm_hist, currency) in bm_hists.items():
        bm_shares = 0.0
        bm_invested = 0.0
        buy_idx = 0
        bm_vals: dict[str, float] = {}
        # Time-weighted growth on the same walk. A benchmark buys at the close it is valued at, so its
        # money comes in at the END of the day: (value − money put in) / last value — the price move,
        # less the order fee (the money put in includes it). The first buy has no last value: value /
        # money put in. (Your own line counts money from the start of its day — twr_index — because
        # real buys fill during the day.)
        twr, prev_eur, put_in = 1.0, 0.0, 0.0
        twr_vals: dict[str, float] = {}

        for date in biz_days:
            ds = str(date.date())

            while buy_idx < len(buy_events) and buy_events[buy_idx][0] <= ds:
                bdate, eur_amt, fee = buy_events[buy_idx]
                bts = pd.Timestamp(bdate)
                bm_px = _fill_price(bm_hist, bts)
                if bm_px:
                    if currency == "USD" and not eurusd.empty:
                        fx = _fill_price(eurusd, bts) or 1.0
                        bm_shares += ((eur_amt - fee) * fx) / bm_px
                    else:
                        bm_shares += (eur_amt - fee) / bm_px
                    bm_invested += eur_amt
                    put_in += eur_amt
                buy_idx += 1

            if bm_invested == 0:
                continue

            bm_px_now = _price_on(bm_hist, date)
            if bm_px_now is None:
                continue

            native_val = bm_shares * bm_px_now
            if currency == "USD" and not eurusd.empty:
                fx_now = _price_on(eurusd, date) or float(eurusd.iloc[-1])
                eur_val = native_val / fx_now
            else:
                eur_val = native_val

            bm_vals[ds] = round((eur_val / bm_invested - 1) * 100, 4)
            if prev_eur > 0:
                twr *= (eur_val - put_in) / prev_eur
            elif put_in > 0:
                twr *= eur_val / put_in
            twr_vals[ds] = twr
            prev_eur, put_in = eur_val, 0.0

        s = pd.Series(bm_vals)
        s.index = pd.to_datetime(s.index)
        benchmark_series[name] = s
        t = pd.Series(twr_vals, dtype=float)
        t.index = pd.to_datetime(t.index)
        benchmark_twr[name] = t

    # Each benchmark's time-weighted growth, under a reserved key like "__roi__" (see the docstring).
    asset_values["__twr__"] = benchmark_twr
    return portfolio_series, benchmark_series, asset_values


# ── Calendar-year returns ───────────────────────────────────────────────────────

_KINDS = ("buy", "sell", "dividend")


def _money_moves(transactions: list[dict], dividends) -> list[tuple[str, str, float]]:
    """(date, kind, eur) for every euro moving into (buy) or out of (sell, dividend) the holdings.
    A bonus moves no money; interest never touches the holdings."""
    out = [(t["date"], t["action"], float(t["price"])) for t in transactions if t["action"] in ("buy", "sell")]
    return out + [(d["date"], "dividend", float(d["eur"])) for d in dividends]


def _on_index(day: str, index: pd.DatetimeIndex) -> pd.Timestamp | None:
    """First index date on or after `day` — the `<=` pointer rule of build_roi_timeseries
    (weekend-dated trades land on the next business day). None if the index ends before it."""
    i = int(index.searchsorted(pd.Timestamp(day))) if len(index) else 0
    return index[i] if i < len(index) else None


def daily_flows(transactions: list[dict], dividends, index: pd.DatetimeIndex) -> pd.DataFrame:
    """EUR moving into (buy) and out of (sell, dividend — cash leaving the holdings) the holdings
    per date of `index`, one column per kind, all positive; bonus shares move nothing. Each move
    lands on the first index date on or after its date; moves after the last index date are not in
    the frame (year_returns carries them in its live step). The one day-mapping year_returns uses."""
    flows = pd.DataFrame(0.0, index=index, columns=list(_KINDS))
    for day, kind, eur in _money_moves(transactions, dividends):
        at = _on_index(day, index)
        if at is not None:
            flows.loc[at, kind] += eur
    return flows


def _day_steps(vals: np.ndarray, flows: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Each index date's time-weighted step r_t = (V_t + W_t) / (V_{t−1} + B_t) on holdings values
    `vals` and daily_flows `flows`, and whether money was at work (V_{t−1} + B_t > 0; else r_t = 1).
    V_{t−1} = 0 before the index. The one TWR day year_returns and twr_index chain."""
    base = np.concatenate([[0.0], vals[:-1]]) + flows["buy"].to_numpy()
    at_work = base > 0
    out = vals + (flows["sell"] + flows["dividend"]).to_numpy()
    return np.where(at_work, out / np.where(at_work, base, 1.0), 1.0), at_work


def twr_index(hold: pd.Series, transactions: list[dict], dividends) -> pd.Series:
    """Growth of 1 € in the holdings, time-weighted: year_returns' daily steps chained over `hold`'s
    index (1.0 until money is at work). Two points divided give the TWR between them — what a
    normalized (NORM) chart draws; across a calendar year it is the year table's closes-only `twr`."""
    hold = hold.fillna(0.0).astype(float)
    index = hold.index if isinstance(hold.index, pd.DatetimeIndex) else pd.DatetimeIndex(hold.index)
    step, _ = _day_steps(hold.to_numpy(), daily_flows(transactions, dividends, index))
    return pd.Series(np.cumprod(step), index=index)


def year_returns(hold: pd.Series, transactions: list[dict], dividends, *,
                 live_value: float | None = None, today: _date | None = None) -> list[dict]:
    """Every calendar year from the first trade to `today`, newest first, measured two ways on the
    holdings value `hold` (EUR per business day; cash from sells and dividends excluded):

      simple = gain / (start + buys − sells) x 100    the spreadsheet: value on 1 Jan + net money
               gain = end + sells + dividends − start − buys     added; new money dilutes it
      twr    = (Π r_t − 1) x 100,  r_t = (V_t + W_t) / (V_{t−1} + B_t)     time-weighted

    TWR convention: money coming in (B_t, the day's buys) counts from the START of its day, money
    going out (W_t, the day's sells + dividends) at the END of its day — so a same-day move of newly
    bought shares (bought intraday, fees paid) is measured on the new money too, never charged to a
    small old base alone. Days with V_{t−1} + B_t ≤ 0 (nothing at work) are skipped; before the
    first trade V_{t−1} = 0. Bonus shares move no money: their value is return in both methods.

    `start` = value at the last date of the previous year (0 before the first trade); `end` = the
    last value in the year. Money moves land on index dates through daily_flows (first date on or
    after), so a weekend trade at a year boundary falls in the same year in both methods. With
    `live_value` the current year ends at it instead: the chain runs over closes before today, then
    one final step to live_value carrying every move dated today or later in the index, plus every
    move dated after the last index date up to today — whatever its calendar year (a daily part
    that ended 30 Dec does not turn a 31 Dec buy into this year's gain). Moves dated after today are
    not counted. Rows: {year, start, end, buys, sells, dividends, gain, simple, twr}; simple/twr
    None when undefined (no money at work).
    """
    if not transactions:
        return []
    today = today or _date.today()
    tday = pd.Timestamp(today)
    hold = hold.fillna(0.0).astype(float)
    index = hold.index if isinstance(hold.index, pd.DatetimeIndex) else pd.DatetimeIndex(hold.index)
    vals = hold.to_numpy()
    flows = daily_flows(transactions, dividends, index)
    money_in = flows["buy"].to_numpy()                     # B_t
    money_out = (flows["sell"] + flows["dividend"]).to_numpy()   # W_t
    step, at_work = _day_steps(vals, flows)
    last = index[-1] if len(index) else None
    tail = {k: 0.0 for k in _KINDS}                        # after the last index date, up to today
    for day, kind, eur in _money_moves(transactions, dividends):
        ts = pd.Timestamp(day)
        if (last is None or ts > last) and ts <= tday:
            tail[kind] += eur

    first_year = int(min(t["date"] for t in transactions)[:4])
    rows = []
    for y in range(today.year, first_year - 1, -1):
        live = live_value is not None and y == today.year
        prev = vals[index.year < y]
        start = float(prev[-1]) if len(prev) else 0.0
        in_year = np.asarray(index.year == y)
        closes = in_year & np.asarray(index < tday) if live else in_year
        sums = flows[in_year].sum()
        if live:                                         # the tail is in live_value only
            sums = sums + pd.Series(tail)

        factor, steps = 1.0, 0
        for i in np.flatnonzero(closes & at_work):
            factor *= step[i]
            steps += 1
        if live:
            before = vals[np.asarray(index < tday)]
            v_prev = float(before[-1]) if len(before) else 0.0
            late = in_year & ~closes                         # index dates from today on
            b_live = float(money_in[late].sum()) + tail["buy"]
            w_live = float(money_out[late].sum()) + tail["sell"] + tail["dividend"]
            if v_prev + b_live > 0:
                factor *= (live_value + w_live) / (v_prev + b_live)
                steps += 1
            end = float(live_value)
        else:
            year_vals = vals[in_year]
            end = float(year_vals[-1]) if len(year_vals) else start

        buys, sells, divs = float(sums["buy"]), float(sums["sell"]), float(sums["dividend"])
        gain = end + sells + divs - start - buys
        base = start + buys - sells
        rows.append({"year": y, "start": start, "end": end, "buys": buys, "sells": sells,
                     "dividends": divs, "gain": gain,
                     "simple": gain / base * 100 if base > 0 else None,
                     "twr": (factor - 1) * 100 if steps else None})
    return rows


# ── Quant metrics ─────────────────────────────────────────────────────────────

def compute_quant_metrics(
    portfolio_series: pd.Series,
    sp500_series: pd.Series | None = None,
    rf_annual_pct: float = 4.5,
) -> dict:
    """
    Compute standard quant risk/return metrics.
    portfolio_series: cumulative ROI % (e.g. 35.9 means +35.9%).
    """
    if len(portfolio_series) < 10:
        return {}

    # Index level: starts at 1.0
    lvl = (portfolio_series / 100 + 1).dropna()
    daily_ret = lvl.pct_change().dropna()

    if len(daily_ret) < 5:
        return {}

    n_days  = len(daily_ret)
    n_years = n_days / 252
    rf_d    = rf_annual_pct / 252 / 100

    total_roi  = float(portfolio_series.iloc[-1])
    cagr       = ((1 + total_roi / 100) ** (1 / n_years) - 1) * 100 if n_years > 0 else 0
    vol        = float(daily_ret.std()) * (252 ** 0.5) * 100

    excess = daily_ret - rf_d
    sharpe = float(excess.mean() / daily_ret.std() * (252 ** 0.5)) if daily_ret.std() > 0 else 0

    down = daily_ret[daily_ret < rf_d]
    sortino_denom = float(down.std()) * (252 ** 0.5) if len(down) > 0 and down.std() > 0 else 1e-9
    sortino = float((daily_ret.mean() - rf_d) * 252 / sortino_denom)

    # Max drawdown
    cum = (1 + daily_ret).cumprod()
    roll_max = cum.cummax()
    dd_series = (cum - roll_max) / roll_max
    max_dd = float(dd_series.min()) * 100
    current_dd = float(dd_series.iloc[-1]) * 100
    calmar = cagr / abs(max_dd) if max_dd != 0 else 0

    # VaR / CVaR 95%
    var_95  = float(daily_ret.quantile(0.05)) * 100
    cvar_95 = float(daily_ret[daily_ret <= daily_ret.quantile(0.05)].mean()) * 100

    win_rate = float((daily_ret > 0).mean() * 100)

    # Beta / Alpha vs S&P 500
    beta = alpha = tracking_error = info_ratio = None
    if sp500_series is not None and len(sp500_series) > 10:
        sp_lvl   = (sp500_series / 100 + 1).dropna()
        sp_ret   = sp_lvl.pct_change().dropna()
        common   = daily_ret.index.intersection(sp_ret.index)
        if len(common) > 10:
            p = daily_ret[common].values
            s = sp_ret[common].values
            cov_mat = np.cov(p, s)
            beta    = float(cov_mat[0, 1] / cov_mat[1, 1]) if cov_mat[1, 1] > 0 else None
            if beta is not None:
                alpha_d = float(p.mean() - beta * s.mean())
                alpha   = round(alpha_d * 252 * 100, 2)
                te      = float((p - s).std()) * (252 ** 0.5) * 100
                ir      = float((p - s).mean() * 252 * 100) / (te + 1e-9)
                tracking_error = round(te, 2)
                info_ratio     = round(ir, 2)

    return {
        "total_roi":       round(total_roi, 2),
        "cagr":            round(cagr, 2),
        "volatility":      round(vol, 2),
        "sharpe":          round(sharpe, 2),
        "sortino":         round(sortino, 2),
        "max_drawdown":    round(max_dd, 2),
        "current_drawdown": round(current_dd, 2),
        "calmar":          round(calmar, 2),
        "var_95":          round(var_95, 2),
        "cvar_95":         round(cvar_95, 2),
        "win_rate":        round(win_rate, 1),
        "beta":            round(beta, 2) if beta is not None else None,
        "alpha":           round(alpha, 2) if alpha is not None else None,
        "tracking_error":  tracking_error,
        "info_ratio":      info_ratio,
        "best_day":        round(float(daily_ret.max()) * 100, 2),
        "worst_day":       round(float(daily_ret.min()) * 100, 2),
        "n_trading_days":  n_days,
    }


def xirr(flows: list[tuple], bracket=(-0.9999, 10.0)) -> float | None:
    """
    Money-weighted (internal) rate of return, annualised, from dated cash flows.

    flows: list of (datetime.date, amount). Convention: money OUT of pocket is
    negative (buys), money IN is positive (sells + today's portfolio value).
    Solves Sum cf_i / (1+r)^(days_i/365) = 0 for r. None if no sign change.
    """
    from scipy.optimize import brentq
    if len(flows) < 2:
        return None
    t0 = min(d for d, _ in flows)
    years = [(d - t0).days / 365.0 for d, _ in flows]
    amts = [float(a) for _, a in flows]
    if not (min(amts) < 0 < max(amts)):
        return None

    def npv(r):
        return sum(a / (1 + r) ** y for a, y in zip(amts, years))

    try:
        return float(brentq(npv, *bracket, maxiter=200))
    except Exception:
        return None
