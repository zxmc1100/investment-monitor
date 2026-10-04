"""Shared portfolio metadata: sector and country maps, ETF sector look-through, the sector-cap
exposure matrix — used by PORT, OPT and RISK alike. input/settings.toml's [sectors] and [countries]
tables are merged over the built-in maps (yours win).
"""
from monitor import config

# ── ETF sector decomposition ─────────────────────────────────────────────────
_MSCI_WORLD_WEIGHTS = {   # iShares MSCI World — approximate weights (rebalanced quarterly)
    "Information Technology": 0.240,
    "Financials":             0.155,
    "Healthcare":             0.120,
    "Industrials":            0.110,
    "Consumer Discretionary": 0.105,
    "Communication Services": 0.080,
    "Consumer Staples":       0.060,
    "Energy":                 0.050,
    "Materials":              0.040,
    "Real Estate":            0.025,
    "Utilities":              0.015,
}
ETF_SECTOR_WEIGHTS = {
    "IWDA.AS": _MSCI_WORLD_WEIGHTS,
    "EUNL.F":  _MSCI_WORLD_WEIGHTS,   # EUNL.F = IWDA Frankfurt listing, same ISIN
}
PORTFOLIO_ETFS = set(ETF_SECTOR_WEIGHTS)

# ── Built-in sectors (yours: input/settings.toml [sectors]; others: TR universe, then Yahoo) ──
PORTFOLIO_SECTOR_MAP: dict[str, str] = {}

ALL_SECTORS = sorted(set(PORTFOLIO_SECTOR_MAP.values()) | set(_MSCI_WORLD_WEIGHTS))


def sector_exposure_matrix(tickers: list[str]) -> tuple[list[str], list[list[float]]]:
    """
    Build a (sector × asset) exposure matrix S where S[i][j] = fraction of asset j
    attributed to sector i. Stocks load 1.0 onto their PORTFOLIO_SECTOR_MAP sector;
    ETFs are decomposed via ETF_SECTOR_WEIGHTS (normalized to sum 1).

    Returns (sectors, matrix). Only sectors with non-zero exposure are included.
    """
    exposure: dict[str, dict[str, float]] = {}  # sector -> {ticker: frac}
    for tk in tickers:
        if tk in ETF_SECTOR_WEIGHTS:
            w = ETF_SECTOR_WEIGHTS[tk]
            tot = sum(w.values()) or 1.0
            for sec, sw in w.items():
                exposure.setdefault(sec, {})[tk] = sw / tot
        else:
            sec = PORTFOLIO_SECTOR_MAP.get(tk, "Unknown")
            exposure.setdefault(sec, {})[tk] = 1.0

    sectors = sorted(exposure)
    matrix = [[exposure[sec].get(tk, 0.0) for tk in tickers] for sec in sectors]
    return sectors, matrix


# ── Country look-through (PORT ALLOCATION detail) ─────────────────────────────

# Built-in issuer countries (yours: input/settings.toml [countries]; others: TR universe, then Yahoo).
PORTFOLIO_COUNTRY_MAP: dict[str, str] = {}

_MSCI_WORLD_COUNTRIES = {   # iShares MSCI World — approximate country weights (rebalanced quarterly)
    "United States": 0.720, "Japan": 0.055, "United Kingdom": 0.035, "Canada": 0.030,
    "France": 0.027, "Switzerland": 0.023, "Germany": 0.022, "Australia": 0.016,
    "Netherlands": 0.012, "Sweden": 0.008, "Denmark": 0.007, "Italy": 0.007, "Spain": 0.007,
    "Hong Kong": 0.004, "Singapore": 0.004, "Finland": 0.002, "Belgium": 0.002, "Israel": 0.002,
    "Norway": 0.002, "Ireland": 0.002, "Other developed": 0.013,
}
ETF_COUNTRY_WEIGHTS = {"IWDA.AS": _MSCI_WORLD_COUNTRIES, "EUNL.F": _MSCI_WORLD_COUNTRIES}

REGION_OF = {
    "United States": "North America", "Canada": "North America",
    "Japan": "Asia-Pacific", "Australia": "Asia-Pacific", "Hong Kong": "Asia-Pacific",
    "Singapore": "Asia-Pacific", "New Zealand": "Asia-Pacific",
    "United Kingdom": "Europe", "France": "Europe", "Switzerland": "Europe", "Germany": "Europe",
    "Netherlands": "Europe", "Sweden": "Europe", "Denmark": "Europe", "Italy": "Europe",
    "Spain": "Europe", "Finland": "Europe", "Belgium": "Europe", "Norway": "Europe",
    "Ireland": "Europe", "Austria": "Europe", "Portugal": "Europe",
    "Taiwan": "Emerging", "China": "Emerging", "South Korea": "Emerging", "India": "Emerging",
    "Brazil": "Emerging",
    "Israel": "Middle East", "Other developed": "Other", "Unknown": "Unknown",
    "Poland": "Europe", "Luxembourg": "Europe",
}

# ── one vocabulary for lines no map knows (monitor.screens.identity) ──────────
# Yahoo / TR-universe sector names → the GICS names of this book (PORTFOLIO_SECTOR_MAP, SECTOR_PROXIES).
YAHOO_SECTORS = {"Technology": "Information Technology", "Financial Services": "Financials",
                 "Consumer Cyclical": "Consumer Discretionary", "Consumer Defensive": "Consumer Staples",
                 "Basic Materials": "Materials", "Health Care": "Healthcare"}
# The TR universe's issuer-country codes → the names REGION_OF knows.
COUNTRY_CODES = {
    "US": "United States", "CA": "Canada", "GB": "United Kingdom", "DE": "Germany", "FR": "France",
    "JP": "Japan", "AU": "Australia", "SE": "Sweden", "NO": "Norway", "CH": "Switzerland", "IT": "Italy",
    "FI": "Finland", "HK": "Hong Kong", "ES": "Spain", "PL": "Poland", "NL": "Netherlands", "BE": "Belgium",
    "DK": "Denmark", "AT": "Austria", "IE": "Ireland", "IL": "Israel", "PT": "Portugal", "TW": "Taiwan",
    "CN": "China", "KR": "South Korea", "IN": "India", "BR": "Brazil", "SG": "Singapore", "NZ": "New Zealand",
    "LU": "Luxembourg",
}
_JUNK = {"", "—", "-", "Unknown", "N/A", "None"}
COUNTRY_ALIASES = {"USA": "United States", "UK": "United Kingdom"}      # data/universe/universe_meta.csv


def sector_name(s) -> str | None:
    """A sector label in this book's vocabulary; None for a missing / placeholder one."""
    s = str(s).strip() if s is not None else ""
    return None if s in _JUNK else YAHOO_SECTORS.get(s, s)


def country_name(c) -> str | None:
    """A country name from a TR-universe code or a spelled-out name; None for a missing one."""
    c = str(c).strip() if c is not None else ""
    if c in _JUNK:
        return None
    return COUNTRY_CODES.get(c.upper(), c) if len(c) == 2 else COUNTRY_ALIASES.get(c, c)


_BUILTIN_SECTORS, _BUILTIN_COUNTRIES = dict(PORTFOLIO_SECTOR_MAP), dict(PORTFOLIO_COUNTRY_MAP)


@config.on_settings
def _merge_settings() -> None:
    """Your [sectors] / [countries] over the built-ins, in place — re-run whenever settings.toml changes."""
    # your labels in the book's vocabulary: "Technology" -> Information Technology, "DE" -> Germany
    config.merge_over(PORTFOLIO_SECTOR_MAP, _BUILTIN_SECTORS,
                      {t: sector_name(v) or v for t, v in config.SETTINGS["sectors"].items()})
    config.merge_over(PORTFOLIO_COUNTRY_MAP, _BUILTIN_COUNTRIES,
                      {t: country_name(v) or v for t, v in config.SETTINGS["countries"].items()})
    ALL_SECTORS[:] = sorted(set(PORTFOLIO_SECTOR_MAP.values()) | set(_MSCI_WORLD_WEIGHTS))


_merge_settings()


def _split(ticker: str, kind: str, labels: dict[str, str] | None = None) -> tuple[dict[str, float], bool]:
    """One line's exposure by sector or country (fractions summing to 1) and whether it is an
    ETF look-through."""
    etfs = ETF_SECTOR_WEIGHTS if kind == "sector" else ETF_COUNTRY_WEIGHTS
    if ticker in etfs:
        w = etfs[ticker]
        tot = sum(w.values()) or 1.0
        return {k: v / tot for k, v in w.items()}, True
    names = PORTFOLIO_SECTOR_MAP if kind == "sector" else PORTFOLIO_COUNTRY_MAP
    return {(labels or {}).get(ticker) or names.get(ticker, "Unknown"): 1.0}, False


def exposure_breakdown(weights: dict[str, float], kind: str, labels: dict[str, str] | None = None) -> list[dict]:
    """Look-through allocation of a book by "sector" or "country".

    weights: {ticker: fraction of the book}. labels: {ticker: sector or country} as resolved by the
    caller (monitor.screens.identity) for lines the built-in maps do not know; absent → the maps.
    Returns [{label, w, parts}] sorted by weight (largest first, then label), where parts =
    [(ticker, fraction of the book, via_etf)] largest first. Sector output lists all ALL_SECTORS
    (zeros included); countries list those with exposure.
    """
    out: dict[str, dict] = {}
    for tk, w in weights.items():
        split, via_etf = _split(tk, kind, labels)
        for label, f in split.items():
            row = out.setdefault(label, {"label": label, "w": 0.0, "parts": []})
            row["w"] += w * f
            row["parts"].append((tk, w * f, via_etf))
    if kind == "sector":
        for label in ALL_SECTORS:
            out.setdefault(label, {"label": label, "w": 0.0, "parts": []})
    for row in out.values():
        row["parts"].sort(key=lambda p: -p[1])
    return sorted(out.values(), key=lambda r: (-r["w"], r["label"]))


def region_totals(countries: list[dict]) -> list[dict]:
    """Sum an exposure_breakdown(…, "country") by REGION_OF; [{label, w}] largest first."""
    tot: dict[str, float] = {}
    for row in countries:
        region = REGION_OF.get(row["label"], "Other")
        tot[region] = tot.get(region, 0.0) + row["w"]
    return [{"label": k, "w": v} for k, v in sorted(tot.items(), key=lambda kv: (-kv[1], kv[0]))]
