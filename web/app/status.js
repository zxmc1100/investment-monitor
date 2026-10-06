// Status-bar badges a payload's meta asks for. Pure.
export function metaBadges(meta) {
  const out = [];
  const stale = Object.entries(meta?.stale ?? {});
  const never = stale.filter(([, ts]) => ts == null).map(([t]) => t);
  const old = stale.filter(([, ts]) => ts != null).map(([t]) => t);
  if (never.length) out.push({ cls: "dn", text: `NO PRICE ${never.join(" ")}`, title: "never quoted: carried at cost" });
  if (old.length) out.push({ cls: "dn", text: `STALE ${old.length} ${old.join(" ")}`, title: "quote failed or older than two business days: last good price kept" });
  return [...out, ...warnBadges(meta?.warn)];
}

const CCY_TITLE = "Yahoo quotes these lines in another currency, but the terminal values every line in EUR: "
  + "put the line's EUR listing (e.g. .DE, .F) in your CSV";

// meta.warn: lines the terminal cannot value as it should — those Yahoo quotes in a currency other
// than EUR. Either ready lines (["CCY USD: AAPL"]) or {ticker: currency} (also under `ccy`), grouped
// per currency: CCY USD: AAPL MSFT. Anything else, or none, shows nothing.
export function warnBadges(warn) {
  if (Array.isArray(warn)) return warn.filter((x) => typeof x === "string" && x).map((text) => ({ cls: "dn", text, title: text.startsWith("CCY") ? CCY_TITLE : "" }));
  if (!warn || typeof warn !== "object") return [];
  const map = warn.ccy && typeof warn.ccy === "object" ? warn.ccy : warn;
  const by = {};
  for (const [t, c] of Object.entries(map)) if (typeof c === "string" && c) (by[c.toUpperCase()] ??= []).push(t);
  return Object.keys(by).sort().map((c) => ({ cls: "dn", text: `CCY ${c}: ${by[c].sort().join(" ")}`, title: CCY_TITLE }));
}

// The status line's items that do not fit `room` px (gap px between items): the least important (lowest p)
// go first, ties from the right; then any of them that fits again in the room left comes back, most
// important first. Items at KEEP or above (alerts, LIVE/OFFLINE) never go. Pure: [{p, w}] → Set of indexes.
export const KEEP = 90;

export function fitHidden(items, room, gap) {
  const width = (shown) => shown.reduce((s, i) => s + items[i].w, 0) + gap * Math.max(0, shown.length - 1);
  let shown = items.map((_, i) => i);
  const order = [...shown].sort((a, b) => items[a].p - items[b].p || b - a);
  const hidden = [];
  for (const i of order) {
    if (width(shown) <= room || items[i].p >= KEEP) break;
    shown = shown.filter((j) => j !== i);
    hidden.push(i);
  }
  for (const i of [...hidden].sort((a, b) => items[b].p - items[a].p || a - b)) {
    const back = [...shown, i].sort((a, b) => a - b);
    if (width(back) <= room) { shown = back; hidden.splice(hidden.indexOf(i), 1); }
  }
  return new Set(hidden);
}
