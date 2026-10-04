# data/ — the market universe

Nothing here is about your portfolio: your trades live in `input/` (gitignored), the terminal's caches in
`local/` (gitignored). These files describe the *market* the terminal looks things up in.

## `data/universe/`

| file | tracked | what it is |
|------|---------|------------|
| `universe_meta.csv` | ✅ | the Trade-Republic-tradeable stock universe, one row per name: ticker (Yahoo), name, sector, country, isin, currency, `delisting_date` / `exit_reason` (blank = live), med_turnover (median daily EUR turnover). |
| `sector_map.json` | ✅ | ticker → sector, from Yahoo profiles. |
| `tr_universe.csv`, `tr_ticker_map.json` | ❌ optional | Trade Republic's own list (isin, name, country) and its ISIN → Yahoo ticker map. When both exist, the lookup lists their names and takes turnover and exits from `universe_meta.csv`. |

What reads them: the security lookup (`WATCH <name>`, a ticker typed on the command bar, `SEC <ticker>` for
any tradeable name), MKT's movers (the `LIQUID_N` most liquid live names by `med_turnover`) and the names,
sectors and countries of new holdings. With none of the files the lookup is empty, never an error.

Anything else in `data/` is ignored by git (`.gitignore`): it is generated on your machine.
