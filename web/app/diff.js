// Cell-level change detection between two payloads, for the amber flash. Pure.
// Keys match the data-cell attributes the renderers write: "panel|row|col".
export function cellValues(payload) {
  const m = new Map();
  for (const p of payload?.panels ?? []) {
    if (p.type === "kpi" || p.type === "bars") {
      for (const it of p.items ?? []) m.set(`${p.id}|${it.k ?? it.label}|v`, it.v);
    } else if (p.type === "ledger") {
      for (const l of p.lines ?? []) if (!l.sep) m.set(`${p.id}|${l.label}|v`, l.v);
    } else if (p.type === "heatmap") {
      const L = p.labels ?? [];
      L.forEach((r, i) => L.forEach((c, j) => m.set(`${p.id}|${r}|${c}`, p.cells?.[i]?.[j])));
    } else if (p.type === "table") {
      const cols = (p.cols ?? []).filter((c) => c.fmt !== "spark");
      for (const r of p.rows ?? []) for (const c of cols) m.set(`${p.id}|${r[p.key]}|${c.k}`, r[c.k]);
      if (p.total) for (const c of cols) if (c.k in p.total) m.set(`${p.id}|__total__|${c.k}`, p.total[c.k]);
    }
  }
  return m;
}

function same(x, y) {
  if (typeof x === "number" && typeof y === "number") {
    return Math.abs(x - y) <= 1e-9 * Math.max(1, Math.abs(x), Math.abs(y));
  }
  return x === y;
}

export function changed(prev, next) {
  const out = new Set();
  if (!prev || !next) return out;
  const before = cellValues(prev);
  for (const [k, v] of cellValues(next)) if (before.has(k) && !same(before.get(k), v)) out.add(k);
  return out;
}
