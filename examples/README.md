# Examples

An invented portfolio — not anyone's real trades — so a fresh clone has something to show.
`.venv/bin/python -m monitor init` copies these files into `input/` (only where a file is missing; it never
overwrites). `input/` is gitignored: your own files never leave your machine.

| File | Copied to | What it is |
|---|---|---|
| `portfolio.example.csv` | `input/portfolio.csv` | your trades — the only file you need |
| `interest.example.csv` | `input/interest.csv` | interest paid on cash (optional) |
| `settings.example.toml` | `input/settings.toml` | fee, dividend tax, overrides (optional, every key documented) |

## Your data

`input/portfolio.csv` — one row per trade, oldest first:

```
Date,Ticker,Action,Shares,Price,PricePerShare
2024-12-16,SAP.DE,buy,4,961.00,240.00
```

- `Date` — trade date, `YYYY-MM-DD`.
- `Ticker` — the Yahoo Finance ticker of a **euro listing**, with its exchange suffix (`SAP.DE` Xetra,
  `ASML.AS` Amsterdam, `MC.PA` Paris). Names, sectors and countries are looked up for you. Prices are
  taken as euros: a line Yahoo quotes in another currency (e.g. `KO`, US dollars) is flagged
  `CCY USD: KO` in the status bar — use the same company's euro listing (Xetra `.DE`, Frankfurt `.F`)
  instead.
- `Action` — `buy`, `sell` or `bonus` (Saveback / bonus shares: received, not paid for).
- `Shares` — number of shares; fractions are fine.
- `Price` — the total in EUR, fees included (what left or reached your account). On a `bonus` row
  it is the value Trade Republic books for the shares; nothing was paid, so it counts as gain.
- `PricePerShare` — EUR per share, for display only.

`input/interest.csv` — `Date,Amount` (EUR received). Shown in ACCOUNTING, never part of ROI.

`input/settings.toml` — see `settings.example.toml`; without it the defaults apply (Trade Republic,
1 EUR per order, German dividend tax). Tickers there (e.g. `savings_plan_tickers`) are
as written in your CSV's Ticker column.
