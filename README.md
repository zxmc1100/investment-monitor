# Investment Monitor

A keyboard-driven, Bloomberg-style terminal for your own broker trade history. It runs on your
computer in your browser, prices everything from Yahoo Finance (via `yfinance`) and needs no
account and no API key. Defaults fit a Trade Republic account taxed in Germany; any broker works
if you can write your trades as a CSV.

> Not financial advice. Not affiliated with Trade Republic or Yahoo. Quotes can lag ~15 minutes.

## 1. Install

Python 3.11+ (tested on 3.13):

```bash
git clone https://github.com/zxmc1100/investment-monitor.git
cd investment-monitor
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

## 2. Start

```bash
.venv/bin/python -m monitor init     # copies examples/ into input/ (an invented portfolio)
bash start.sh                        # opens http://localhost:8000 — Ctrl-C stops it
```

The example portfolio shows how everything looks. Then replace `input/portfolio.csv` with your
own trades; the terminal picks the change up by itself.

## 3. Your data

Everything you own lives in `input/` and never leaves your machine (it is gitignored).

`input/portfolio.csv` — one row per trade:

```
Date,Ticker,Action,Shares,Price,PricePerShare
2025-01-15,SAP.DE,buy,4,961.00,240.00
2025-06-02,SAP.DE,sell,2,520.00,261.00
```

| Column | Meaning |
|---|---|
| `Date` | trade date, `YYYY-MM-DD` |
| `Ticker` | the Yahoo ticker of a **EUR listing** (`SAP.DE`, `ASML.AS`, `AIR.PA`, `ENEL.MI` …); other currencies are flagged, not converted |
| `Action` | `buy`, `sell` or `bonus` (shares received for free, e.g. Saveback) |
| `Shares` | number of shares, fractions allowed |
| `Price` | total in EUR including fees — what left or reached your account |
| `PricePerShare` | EUR per share, display only |

Optional: `input/interest.csv` (`Date,Amount`, interest on cash — shown, never part of ROI) and
`input/settings.toml` (order fee, savings-plan tickers, dividend tax, name/sector overrides —
every key is documented in `examples/settings.example.toml`; edits apply without a restart).
Names, sectors and countries of your holdings are looked up automatically.

## 4. Use it

Type to command, Enter to run, Esc to go back. Number keys switch screens (with an empty command bar):

| Key | Screen |
|---|---|
| `1` PORT | value, P&L, ROI vs the same cash in benchmarks, risk, positions, allocation |
| `2` OPT | optimized target portfolios, frontier, trade ticket |
| `3` RISK | volatility, VaR, correlations, stress tests |
| `4` MKT | indices, FX, commodities, movers, your names and sectors, upcoming events |
| `5` ALRT | alerts |

Type a ticker (or `SEC <ticker>`) for one security. Keys: ↑↓ PgUp PgDn move the cursor · Enter opens
the row · Shift+←→ sort column, Shift+↑↓ direction · Tab next panel · Alt+1…9 maximize a panel ·
Alt+↑↓ command history · drag a chart to zoom, double-click to reset · F1 or `?` help.

Commands (`HELP` lists them all): `WATCH <name|ticker>` · `UNWATCH <ticker>` ·
`ALERT SAP.DE < 200` / `ALERT * MOVE 5` / `ALERT PORT DAY -2` · `UNALERT <id>` · `ACK <id|ALL>` ·
`TARGET HRP` · `REFRESH` · `CLOSED` · `FULL`. Alerts are checked every minute while a terminal tab
is open. `data/README.md` explains the market universe the lookup and MKT's movers use.

## 5. Publish a public snapshot (optional)

```bash
.venv/bin/python -m monitor export   # writes docs/ — PORT, OPT, RISK and MKT, percentages only
```

Commit `docs/` and enable GitHub Pages from `/docs`. The export never contains euro amounts,
share counts, costs, transactions, closed positions, your watchlist or alerts, and never the
single-security or alerts screens. It refuses while your portfolio is missing, still the example,
or your settings have an error.

## 6. Privacy

Your files stay in `input/` and `local/` (both gitignored); the server only listens on
127.0.0.1. Requests go to Yahoo Finance only (prices, dividends, company profiles).

## Development

```bash
.venv/bin/pytest tests/              # Python + JS tests; fixtures only, no network
```

Node is optional (it runs the JS tests). `monitor/plugins.py` can load an optional local add-on from a
gitignored `private/` directory (extra screens and commands); the tests run without one. License: MIT.
