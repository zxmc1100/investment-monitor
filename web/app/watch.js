// WATCH resolution. Pure: the query and /api/lookup results -> what to do.
export const fold = (s) => String(s ?? "").normalize("NFKD").replace(/[̀-ͯ]/g, "").toUpperCase().trim();

// One match → add it; an exact ticker, or the one name equal to the query ("rheinmetall" →
// RHM.DE, not "Rheinmetall (ADR)") → add it; none → NO MATCH; otherwise the user picks.
export function resolveWatch(query, results) {
  const rows = results ?? [];
  if (!rows.length) return { kind: "none" };
  const q = fold(query);
  const exact = rows.find((r) => fold(r.ticker) === q) ?? (rows.length === 1 ? rows[0] : null);
  if (exact) return { kind: "one", ticker: exact.ticker, name: exact.name };
  const named = rows.filter((r) => fold(r.name) === q);
  if (named.length === 1) return { kind: "one", ticker: named[0].ticker, name: named[0].name };
  return { kind: "several", rows };
}

// Autocomplete rows for "WATCH <query>": one `WATCH <ticker>` per lookup result.
// `verb` SEC: the rows for a name typed alone (ALLIANZ → SEC ALV.DE).
export const watchItems = (results, verb = "WATCH") => (results ?? []).map((r) => ({
  label: `${verb} ${r.ticker}`, desc: [r.name, r.sector, r.country].filter((x) => x && x !== "—").join(" · "),
  run: `${verb} ${r.ticker}` }));
