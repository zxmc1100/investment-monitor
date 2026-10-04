// Chart range slicing and legend ranking. Pure. x values are unix seconds.
const DAY = 86400;

export function rangeStart(xs, range) {
  if (!xs?.length) return -Infinity;
  const last = xs[xs.length - 1];
  switch (range) {
    case "1M": return last - 31 * DAY;
    case "6M": return last - 183 * DAY;
    case "1Y": return last - 366 * DAY;
    case "YTD": return Date.UTC(new Date(last * 1000).getUTCFullYear(), 0, 1) / 1000;
    default: return -Infinity;
  }
}

export function sliceFrom(x, series, start) {
  let i = 0;
  while (i < x.length && x[i] < start) i++;
  i = Math.min(i, Math.max(0, x.length - 2));            // never fewer than two points
  return { x: x.slice(i), series: series.map((s) => ({ ...s, y: s.y.slice(i) })) };
}

// Value of a series at the cursor index, falling back to the last value before it (a gap);
// a null index (no cursor) gives the last value.
export function valueAt(ys, i) {
  if (i === null || i === undefined) return lastValue(ys);
  for (let k = Math.min(i, ys.length - 1); k >= 0; k--) if (ys[k] !== null && ys[k] !== undefined) return ys[k];
  return null;
}

export function lastValue(ys) {
  for (let i = ys.length - 1; i >= 0; i--) if (ys[i] !== null && ys[i] !== undefined) return ys[i];
  return null;
}

// Rebase every series to 100 at the first point where the FIRST series has a value (a chart following a
// table: the cursor row's curve), so a curve and its comparisons start together in the visible window.
// Points before that are dropped; a series that starts later starts at 100 itself.
export function rebase(series) {
  const has = (v) => v !== null && v !== undefined;
  const i0 = (series[0]?.y ?? []).findIndex(has);
  if (i0 < 0) return series;
  return series.map((s) => {
    let j = i0;
    while (j < s.y.length && !has(s.y[j])) j++;
    const base = s.y[j];
    return { ...s, y: s.y.map((v, k) => (k < i0 || !has(v) || !base ? null : (v / base) * 100)) };
  });
}
