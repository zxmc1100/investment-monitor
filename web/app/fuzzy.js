// Fuzzy ranking for the command bar. Pure.
export function score(query, text) {
  const q = String(query).trim().toUpperCase();
  const t = String(text).toUpperCase();
  if (!q) return -Infinity;
  if (t === q) return 1000;
  if (t.startsWith(q)) return 800 - t.length;
  const at = t.indexOf(q);
  if (at >= 0) return 600 - at;
  let from = 0, gaps = 0;
  for (const ch of q) {
    const j = t.indexOf(ch, from);
    if (j < 0) return -Infinity;
    gaps += j - from;
    from = j + 1;
  }
  return 400 - gaps;
}

// keys(item) -> [primary, secondary, ...]; each later key is worth 300 less than the one before.
export function rank(query, items, keys = (x) => [x.label], limit = 8) {
  return items
    .map((it, n) => [Math.max(...keys(it).map((k, j) => score(query, k) - 300 * j)), n, it])
    .filter(([s]) => s > -Infinity)
    .sort((a, b) => b[0] - a[0] || a[1] - b[1])
    .slice(0, limit)
    .map(([, , it]) => it);
}
