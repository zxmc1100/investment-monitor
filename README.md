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

## Your trades

The first start shows an invented example portfolio. Press **6** (TRADES) and **START FRESH** to clear it, then:

- **add** a trade in the form — ticker (type a name: your holdings and every Trade Republic stock are suggested;
  pick a euro listing such as `SAP.DE`), buy / sell / bonus, shares, and **either** the price per share **or**
  the total you paid, fees included: the other follows. No fee is added unless you fill in FEE. Or type
  `BUY SAP.DE 4 @ 240` (price per share) or `BUY SAP.DE 4 = 961` (total) in the command bar, from any screen;
- **paste many** rows from a spreadsheet, or **import your broker's CSV** — a header is optional, column names
  may be English, German or Italian, and every row is previewed (or says what is wrong) before anything is saved;
- **edit** a trade (Enter on its row) or **delete** it (Del).

Your trades stay on your computer, in `input/portfolio.csv`. **UNDO** (a button on TRADES, or the command) puts
back the file as it was before the last change. Every change first copies the previous file to `input/backups/`
— the one before your first change and before each START FRESH or replace are kept for good. You may also edit
the file by hand — the terminal picks the change up. One row per trade, oldest first; TRADES writes it like this:

```
Date,Ticker,Action,Shares,Price,PricePerShare
2025-01-15,SAP.DE,buy,4,961.00,240.00
```

`Price` is the total in EUR with fees (what left or reached your account), `PricePerShare` is for display,
`Action` is `buy`, `sell` or `bonus` (shares received free, e.g. Saveback). Dates may be `DD.MM.YYYY`, and
Excel's `;` with a decimal comma works; a thousands separator (`1.234,56`) is refused with a message naming
the row. Tickers are Yahoo's, of a **EUR listing** (`SAP.DE`, `ASML.AS`, `AIR.PA`, `ENEL.MI` …).

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
| `6` TRADES | add, paste or import your trades; edit or delete them |

Type a ticker (or `SEC <ticker>`) for one security. Keys: ↑↓ PgUp PgDn move the cursor · Enter opens
the row · Shift+←→ sort column, Shift+↑↓ direction · Tab next panel · Alt+1…9 maximize a panel ·
Alt+↑↓ command history · drag a chart to zoom, double-click to reset · F1 or `?` help.

Commands (`HELP` lists them all): `BUY SAP.DE 4 @ 240` · `SELL ALV.DE 2 = 820 2026-03-02` ·
`BONUS IWDA.AS 0.15 = 15` · `UNDO` · `START FRESH` · `WATCH <name|ticker>` · `UNWATCH <ticker>` ·
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
your watchlist or alerts, and never the single-security, alerts or trades screens. It refuses while your
portfolio is missing, empty, still the example, or your settings have an error.

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
