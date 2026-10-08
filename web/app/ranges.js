// Chart range slicing and legend ranking. Pure. x values are unix seconds. A range is a name ("1M" …
// "ALL") or a period dragged on the chart: {from, to, prev} (prev: the named range it came from).
const DAY = 86400;

export function rangeStart(xs, range) {
  if (range && typeof range === "object") return range.from;
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

export const rangeEnd = (range) => (range && typeof range === "object" ? range.to : Infinity);

// The points from `start` to `end`. `anchor`: open on the last point BEFORE start instead — the close a
// period's return is measured from (YTD: the year before's last close). Every series slices with x,
// its `twr` and `cash` too. `start`: the first point's index in x (0: nothing before the window).
export function sliceFrom(x, series, start, end = Infinity, anchor = false) {
  let i = 0;
  while (i < x.length && x[i] < start) i++;
  if (anchor && i > 0) i--;
  let j = i;
  while (j < x.length && x[j] <= end) j++;
  j = Math.max(j, Math.min(i + 2, x.length));            // never fewer than two points
  i = Math.min(i, Math.max(0, j - 2));
  const cut = (a) => a?.slice(i, j);
  return { x: x.slice(i, j), start: i, series: series.map((s) => ({ ...s, y: cut(s.y), ...(s.twr ? { twr: cut(s.twr) } : {}),
    ...(s.cash ? { cash: cut(s.cash) } : {}) })) };
}

// NORM is offered when every line has a time-weighted curve (`twr`: growth of 1 €, money moves taken out).
export function canNorm(series) {
  const lines = (series ?? []).filter((s) => s.kind !== "markers");
  return lines.length > 0 && lines.every((s) => Array.isArray(s.twr));
}

// Each line as its time-weighted return in % since its first point in the window (0 there): who did best
// in the window, buys and sells not counted as gains or losses. `inception`: the window has no close
// before it (ALL) — measured from before the first money (`twr` = 1), so the first day's fee and move
// count. A line without `twr` has no honest normalized value and comes back empty.
export function normalize(series, inception = false) {
  const has = (v) => v !== null && v !== undefined;
  return series.map((s) => {
    const base = inception ? 1 : (s.twr ?? []).find(has);
    return { ...s, y: s.y.map((_, k) => (base && has(s.twr[k]) ? (s.twr[k] / base - 1) * 100 : null)) };
  });
}

// Each line's ROI over the window, by the ROI formula: the gain since its first point (the close before the
// window) over the money in it then — the value held, as if bought that day — plus every euro put in since.
// y in %, `inv` the money put in to each point (every line's: a benchmark buys with yours), `cash` on your line
// its sales and dividends to each point (a benchmark never sells). With V = (1 + y)·I − C:
//   ROI = (V + C − C₀) / (V₀ + I − I₀) − 1 = ((1 + y)·I − C₀) / (y₀·I₀ − C₀ + I) − 1      (0 at the first point)
// `inception`: nothing before the window — the ROI itself. No money in at the first point: no window ROI.
export function windowRoi(series, inv, inception = false) {
  if (inception) return series;
  const has = (v) => v !== null && v !== undefined;
  return series.map((s) => {
    const y0 = s.y[0], i0 = inv[0], c0 = s.cash?.[0] ?? 0;
    const ok = has(y0) && has(i0);
    return { ...s, y: s.y.map((y, k) => {
      const den = ok && has(y) && has(inv[k]) ? (y0 / 100) * i0 - c0 + inv[k] : 0;
      return den > 0 ? (((1 + y / 100) * inv[k] - c0) / den - 1) * 100 : null;
    }) };
  });
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
