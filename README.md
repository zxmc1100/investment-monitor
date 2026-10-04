# Investment Monitor

[![tests](https://github.com/zxmc1100/investment-monitor/actions/workflows/tests.yml/badge.svg)](https://github.com/zxmc1100/investment-monitor/actions/workflows/tests.yml)

A keyboard-driven, Bloomberg-style terminal for your own broker trade history. It runs on your computer,
in your browser, with prices from Yahoo Finance — no account, no API key. Defaults fit a Trade Republic
account taxed in Germany; any broker works if you can write your trades as a CSV.

> Not financial advice. Not affiliated with Trade Republic or Yahoo. Quotes can lag ~15 minutes.

## What you need

- **Python 3.11 or newer** — free from [python.org](https://www.python.org/downloads/).
- **An internet connection** — prices come from Yahoo, and the first start downloads what the terminal needs.

## Install & start on macOS

1. Install Python from [python.org](https://www.python.org/downloads/) (the macOS installer).
2. On this page click **Code → Download ZIP**, then double-click the ZIP to unzip it.
3. Double-click **`start-mac.command`** in the unzipped folder.
   - If macOS says it cannot verify the developer: open **System Settings → Privacy & Security**, scroll
     down and click **Open Anyway** (on older macOS: right-click the file → **Open**). Only the first time.
4. The first start installs what it needs (about 2 minutes). Then your browser opens
   **http://localhost:8000** with an example portfolio.

Keep the Terminal window open while you use it; close it to stop. Next time, double-click again.

## Install & start on Windows

1. Install Python from [python.org](https://www.python.org/downloads/). If the installer shows the box
   **"Add python.exe to PATH"**, tick it.
2. On this page click **Code → Download ZIP**. Right-click the ZIP → **Extract All**.
3. Double-click **`start-windows.bat`** in the extracted folder.
   - If Windows says it protected your PC: click **More info → Run anyway**. Only the first time.
4. The first start installs what it needs (about 2 minutes). Then your browser opens
   **http://localhost:8000** with an example portfolio.

Keep the black window open while you use it; close it to stop. Next time, double-click again.

## Linux / terminal

`./start.sh` — the same setup, then the server (Ctrl-C stops it). On Debian/Ubuntu you may first need
`sudo apt install python3-venv`. Arguments go to the server: `./start.sh --port 8001 --no-open`.

## Your data

Everything you own lives in the `input/` folder and never leaves your computer. The first start copies the
example there; replace `input/portfolio.csv` with your own trades — the terminal picks the change up by itself.

`input/portfolio.csv` — one row per trade, oldest first:

```
Date,Ticker,Action,Shares,Price,PricePerShare
2025-01-15,SAP.DE,buy,4,961.00,240.00
2025-06-02,SAP.DE,sell,2,520.00,261.00
```

| Column | Meaning |
|---|---|
| `Date` | trade date, `YYYY-MM-DD` (`DD.MM.YYYY` and `DD/MM/YYYY` work too — always day first) |
| `Ticker` | the Yahoo ticker of a **EUR listing** (`SAP.DE`, `ASML.AS`, `AIR.PA`, `ENEL.MI` …); other currencies are flagged, not converted |
| `Action` | `buy`, `sell` or `bonus` (shares received for free, e.g. Saveback) |
| `Shares` | number of shares, fractions allowed |
| `Price` | total in EUR including fees — what left or reached your account |
| `PricePerShare` | EUR per share, display only |

Excel is fine: keep the column names and save as CSV (UTF-8 if offered). European Excel's `;` between
fields with a decimal comma (`15.01.2025;SAP.DE;buy;4;961,00;240,00`) works. Leave out thousands
separators (`1.234,56`): such a row is refused with a message naming the row and column.

Optional: `input/interest.csv` (`Date,Amount`, interest on cash — shown, never part of ROI) and
`input/settings.toml` (order fee, savings-plan tickers, dividend tax, name/sector overrides — every key is
documented in `examples/settings.example.toml`; edits apply without a restart). Names, sectors and
countries of your holdings are looked up automatically. More in `examples/README.md`.

## Use it

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

## Update

Download the new ZIP and unzip it, then copy your `input/` folder (your trades and settings) and your
`local/` folder (watchlist, alerts and caches) into the new folder. Its first start installs again
(about 2 minutes). With git: `git pull`, then start as usual — changed requirements install by themselves.

## Publish a public snapshot (optional)

```bash
.venv/bin/python -m monitor export        # Windows: .venv\Scripts\python -m monitor export
```

It writes `docs/` — PORT, OPT, RISK and MKT, percentages only. Commit `docs/` and enable GitHub Pages
from `/docs`. The export never contains euro amounts, share counts, costs, transactions, closed positions,
your watchlist or alerts, and never the single-security or alerts screens. It refuses while your portfolio
is missing, still the example, or your settings have an error.

## Privacy

Your files stay in `input/` and `local/` (both gitignored); the server only listens on 127.0.0.1.
Requests go to Yahoo Finance only (prices, dividends, company profiles) — and, on the first start or
after an update, to pypi.org for the Python packages.

## For developers

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest tests          # Python + JS tests; fixtures only, no network
```

`requirements.txt` is what the terminal needs; `requirements-dev.txt` adds the test tools. Node is optional
(it runs the JS tests). `python -m monitor --help` lists the commands. `monitor/plugins.py` can load an
optional local add-on from a gitignored `private/` directory (extra screens and commands); the tests run
without one. CI runs the suite on Linux, macOS and Windows. License: MIT.
