# Examples

An invented portfolio — not anyone's real trades — so a fresh download has something to show. The first
start (`start-mac.command`, `start-windows.bat` or `./start.sh`) copies these files into `input/`, as does
`python -m monitor init` — only where a file is missing; it never overwrites. `input/` is gitignored: your
own files never leave your machine.

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

- `Date` — trade date, `YYYY-MM-DD` (`DD.MM.YYYY` and `DD/MM/YYYY` work too — always day first).
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

Excel is fine: save as CSV (UTF-8 if offered) and keep the column names. A `;` between fields with a
decimal comma, as European Excel writes it (`15.01.2025;SAP.DE;buy;4;961,00;240,00`), reads the same.
No thousands separators (`1.234,56`): a row with one is refused with a message naming the row and column,
never guessed.

`input/interest.csv` — `Date,Amount` (EUR received). Shown in ACCOUNTING, never part of ROI.

`input/settings.toml` — see `settings.example.toml`; without it the defaults apply (Trade Republic,
1 EUR per order, German dividend tax). Tickers there (e.g. `savings_plan_tickers`) are
as written in your CSV's Ticker column.
