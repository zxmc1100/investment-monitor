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

In the terminal, press **6** (TRADES): **START FRESH** clears the example, then add trades in the form, paste
many, or import your broker's CSV — TRADES writes `input/portfolio.csv` for you, always in the form below, and
first copies the previous file to `input/backups/` (the newest 20 are kept). You can still edit it by hand.

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

## In your own terminal, with vim keys

`python -m monitor tui`, from this folder's parent, with the Python a start file set up (the main README names it).

The same screens, numbers and commands as the browser, in a terminal window: it uses a server already running or
starts one for as long as it runs, and browser and terminal can be open together. `?` lists everything; the short of it:

| Keys | |
|---|---|
| `j` `k` · `gg` `G` · `Ctrl-D` `Ctrl-U` · `5j` · `12G` | row cursor |
| `/` `n` `N` | find a row in the table |
| `Enter` · `Esc` · `Ctrl-O` | open the row · back · back to where you came from |
| `1`…`9` · `gt` `gT` · `:` | screens · next, previous · the command bar (the browser's commands; `:q` leaves) |
| `h` `l` · `Ctrl-W h j k l` · `Tab` | panels |
| `z1`…`z9` · `Ctrl-W o` | maximize a panel (again or `Esc` restores) |
| `<` `>` · `s` | sort column · direction |
| `[` `]` · `{` `}` · `m` | a chart's range · horizon · view (ROI, MWR, TWR) |
| on a chart: `h` `l` `w` `b` `0` `$` · `v` … `Enter` · `-` | its cursor · zoom into a period · back out |
| `o` · `i` · `dd` · `u` | TRADES: add · step into the focused form · delete the row · undo (each change asks first) |

The mouse works too; to select text hold Option (elsewhere Shift) while dragging. It looks best where the terminal
takes 24-bit colour (Apple's Terminal gets the nearest of 256) at about 160 x 45 or more. Another computer's terminal:
`python -m monitor tui --url https://my-mac.tail1234.ts.net` (see below). A CSV goes in with `:import <file>`, Tab
completing the path.

## On an iPad or phone

The terminal still runs on your computer; another device opens it through [Tailscale](https://tailscale.com)
(free), a private network of your own devices that gives your computer an https address:

1. Install Tailscale on both and sign in to the same account. In its admin console (DNS) turn on MagicDNS
   and HTTPS Certificates.
2. On the computer: `tailscale serve --bg 47800` (the macOS service; a start file's terminal: `8000`). It
   prints the address, e.g. `https://my-mac.tail1234.ts.net`.
3. In `input/settings.toml`: `remote_host = "my-mac.tail1234.ts.net"`.
4. On the iPad open that address in Safari, then Share → Add to Home Screen, and open it twice while the
   computer is on.
5. Cashflow Monitor too, if you run one: `tailscale serve --bg --https=8443 47820` and, in settings.toml,
   `cashflow_remote_url = "https://my-mac.tail1234.ts.net:8443"`.

While the computer is awake the iPad is live; asleep or off, the iPad shows the last screens it fetched,
marked OFFLINE. Taps: a panel's title bar maximizes it (again: restores), a second tap on a row opens it, an
overlay's title bar closes it; the F-key bar is a glass tab bar: drag along it and let go on a screen, and the
open screen's key again closes it (on a computer too); the on-screen keyboard comes up when you tap the command
bar. Every device in your tailnet can open it: keep the tailnet to your own devices and never `tailscale funnel`
this port.
