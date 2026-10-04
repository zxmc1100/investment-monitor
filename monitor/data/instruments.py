"""Static instrument metadata: how each ticker in your CSV is priced on Yahoo,
its currency, its display name, and the benchmark set. Data-layer owned so fetchers
never import portfolio code. input/settings.toml's [tickers] and [names] tables are merged over the
built-in maps (yours win)."""

from monitor import config


# Map the ticker in your CSV → yfinance price ticker, for lines Yahoo prices badly under their own symbol. A ticker
# not listed is its own Yahoo ticker. Yours go in input/settings.toml [tickers].
# EUNL.F (Frankfurt) has stale yfinance data — use IWDA.AS (same ISIN IE00B4L5Y983) for price
TICKER_MAP = {
    "EUNL.F": "IWDA.AS",  # same ISIN, IWDA.AS has live yfinance data; EUNL.F is stale
}


# Tickers priced in non-EUR
TICKER_CURRENCY: dict[str, str] = {}


# Canonical display names (built-in). Yours go in input/settings.toml [names]; a ticker nobody names
# is looked up in the TR universe, then on Yahoo (monitor.screens.identity).
COMPANY_NAMES = {
    "EUNL.F": "iShares Core MSCI World ETF",
}
_BUILTIN_TICKER_MAP, _BUILTIN_NAMES = dict(TICKER_MAP), dict(COMPANY_NAMES)


@config.on_settings
def _merge_settings() -> None:
    """Your [tickers] / [names] over the built-ins, in place — re-run whenever settings.toml changes."""
    config.merge_over(TICKER_MAP, _BUILTIN_TICKER_MAP, config.SETTINGS["tickers"])
    config.merge_over(COMPANY_NAMES, _BUILTIN_NAMES, config.SETTINGS["names"])


_merge_settings()


BENCHMARKS = {
    "S&P 500":          ("CSPX.AS", "EUR"),  # iShares Core S&P 500 UCITS ETF Acc — EUR-listed, no FX noise
    "Nasdaq 100":       ("CNDX.AS", "EUR"),  # iShares Nasdaq 100 UCITS — EUR-listed
    "MSCI World":       ("IWDA.AS", "EUR"),  # iShares Core MSCI World — EUR-listed
    "FTSE All-World":   ("VWCE.DE", "EUR"),  # Vanguard FTSE All-World Acc — developed + EM
    "Euro Stoxx 50":    ("EXW1.DE", "EUR"),  # iShares Core EURO STOXX 50
    "Emerging Markets": ("EUNM.F",  "EUR"),  # iShares MSCI EM UCITS ETF Acc — EUR-listed Frankfurt
    "Gold":             ("GLD",     "USD"),
    "Bitcoin":          ("BTC-USD", "USD"),
    "Fixed Income":     ("BND",     "USD"),
}


# ── Market board: MKT panels 1–3, in display order ─────────────────────
BOARD: tuple[tuple[str, str, tuple[tuple[str, str], ...]], ...] = (
    ("indices", "INDICES", (("^GSPC", "S&P 500"), ("^NDX", "Nasdaq 100"), ("^GDAXI", "DAX"),
                            ("^STOXX50E", "Euro Stoxx 50"), ("^FTSE", "FTSE 100"), ("^FCHI", "CAC 40"),
                            ("^N225", "Nikkei 225"), ("^HSI", "Hang Seng"))),
    ("fx", "FX · RATES · VOL", (("EURUSD=X", "EUR/USD"), ("EURGBP=X", "EUR/GBP"), ("EURCHF=X", "EUR/CHF"),
                                ("EURJPY=X", "EUR/JPY"), ("^TNX", "US 10Y"), ("^VIX", "VIX"))),
    ("cmdty", "COMMODITIES · CRYPTO", (("GC=F", "Gold"), ("SI=F", "Silver"), ("CL=F", "WTI"), ("BZ=F", "Brent"),
                                      ("BTC-EUR", "Bitcoin"), ("ETH-EUR", "Ether"))),
)
BOARD_TICKERS = [t for _, _, rows in BOARD for t, _ in rows]
YIELDS = {"^TNX"}                                    # quoted in percent: changes shown in basis points
FX_PAIRS = {"EURUSD=X", "EURGBP=X", "EURCHF=X", "EURJPY=X"}  # 4 decimals

# Portfolio sector → (proxy ETF, what it tracks). Returns are in the ETF's listing currency.
# Financials is proxied by STOXX Europe 600 Banks: the book's financials are EU banks.
SECTOR_PROXIES: dict[str, tuple[str, str]] = {
    "Information Technology": ("XLK", "US Tech"),
    "Financials": ("EXV1.DE", "EU Banks"),
    "Communication Services": ("XLC", "US Comms"),
    "Consumer Discretionary": ("XLY", "US Discretionary"),
    "Healthcare": ("XLV", "US Health Care"),
    "Industrials": ("XLI", "US Industrials"),
    "Consumer Staples": ("XLP", "US Staples"),
    "Energy": ("XLE", "US Energy"),
    "Materials": ("XLB", "US Materials"),
    "Utilities": ("XLU", "US Utilities"),
    "Real Estate": ("XLRE", "US Real Estate"),
}
