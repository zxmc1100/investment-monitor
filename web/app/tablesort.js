// Stable row sorting with missing values last in both directions. Pure.
const missing = (v) => v === null || v === undefined || (typeof v === "number" && Number.isNaN(v));

export function sortRows(rows, col, dir = "desc") {
  const sgn = dir === "asc" ? 1 : -1;
  return rows
    .map((r, i) => [r, i])
    .sort(([a, i], [b, j]) => {
      const x = a[col], y = b[col];
      if (missing(x) || missing(y)) return missing(x) && missing(y) ? i - j : missing(x) ? 1 : -1;
      if (typeof x === "number" && typeof y === "number") return (x - y) * sgn || i - j;
      return String(x).localeCompare(String(y), "en", { sensitivity: "base" }) * sgn || i - j;
    })
    .map(([r]) => r);
}

// Shift+←→ steps through the sortable columns the panel shows: sparklines never sort, and a column
// hidden in a narrow panel (`lo`, passed in `hidden`) is skipped. Pure.
export function nextSortCol(cols, current, step, hidden = new Set()) {
  const keys = cols.filter((c) => c.fmt !== "spark" && !hidden.has(c.k)).map((c) => c.k);
  if (!keys.length) return current;
  const i = keys.indexOf(current);
  return keys[(((i < 0 ? 0 : i) + step) % keys.length + keys.length) % keys.length];
}
