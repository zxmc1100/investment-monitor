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
// its `twr` and `cash` too, a band's `lo` / `hi`. `start`: the first point's index in x (0: nothing before it).
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
    ...(s.cash ? { cash: cut(s.cash) } : {}), ...(s.lo ? { lo: cut(s.lo), hi: cut(s.hi) } : {}) })) };
}

// TWR is offered when every line has a time-weighted curve (`twr`: growth of 1 €, money moves taken out).
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

// Each line's money-weighted return over the window (Modified Dietz — the YTD KPI's method): the gain since its
// first point (the close before the window) over the money at work — the value held then, plus each euro put in
// (less each taken out) counted for the share of the window left after its day. A flow on the point measured
// has had no time (weight 0); `from` is the window's opening day. y in %, `inv` and `cash` as windowRoi; the gain
// to a point is y·I − y₀·I₀. `inception`: nothing before the window — no value held, measured from the first
// point (its buys count in full).
export function windowMwr(series, inv, xs, from, inception = false) {
  const has = (v) => v !== null && v !== undefined;
  const p0 = inception ? xs[0] : from;
  return series.map((s) => {
    const c = (k) => s.cash?.[k] ?? 0;
    const y0 = inception ? 0 : s.y[0];
    if (!has(y0) || !has(inv[0])) return { ...s, y: s.y.map(() => null) };
    const i0 = inception ? 0 : inv[0], c0 = inception ? 0 : c(0);
    const v0 = inception ? 0 : (1 + y0 / 100) * i0 - c0, p = (y0 / 100) * i0;
    let s1 = 0, s2 = 0, prevI = i0, prevC = c0;     // Σ net flow, Σ net flow × its time
    return { ...s, y: s.y.map((y, k) => {
      if (!inception && k === 0) return 0;
      const flow = (inv[k] - prevI) - (c(k) - prevC);
      prevI = inv[k]; prevC = c(k);
      s1 += flow; s2 += flow * xs[k];
      if (!has(y)) return null;
      const span = xs[k] - p0;
      const work = v0 + (span > 0 ? (xs[k] * s1 - s2) / span : s1);
      return work > 0 ? (((y / 100) * inv[k] - p) / work) * 100 : null;
    }) };
  });
}

// The views a chart offers: ROI (its own lines; over a window, the ROI formula on it) and MWR need the money put
// in (`inv`, PORT's ROI chart), TWR every line's time-weighted curve (`twr`). None of it (a public snapshot):
// no views, the lines as they are.
export function chartModes(p, series) {
  const twr = canNorm(series);
  return Array.isArray(p.inv) ? ["ROI", "MWR", ...(twr ? ["TWR"] : [])] : twr ? ["ROI", "TWR"] : [];
}

// What each view is, for its chip's tooltip and the help.
export const MODE_TITLES = {
  ROI: "ROI — return on investment (⌥R · Alt+R): the gain over every euro committed. Over a window: the gain "
    + "since its start over the value held then plus every euro bought since — a buy made yesterday counts in full",
  MWR: "MWR — money-weighted return, Modified Dietz (⌥M · Alt+M): the gain over the money at work, each euro "
    + "counted for the share of the window it was invested — the YTD KPI's method",
  TWR: "TWR — time-weighted return (⌥T · Alt+T): each line's growth with every buy, sale and dividend taken out of "
    + "its day — how the holdings performed, the standard to compare with an index; YTD TWR's method",
};

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

// A series sent from x index `at` (OPT's future lines and bands: only their own points travel) padded with nulls to
// the chart's `n` points; one without `at` as it is.
export function expandAt(series, n) {
  const pad = (a, at) => (a ? [...Array(at).fill(null), ...a, ...Array(Math.max(0, n - at - a.length)).fill(null)].slice(0, n) : a);
  return (series ?? []).map((s) => (Number.isInteger(s.at)
    ? { ...s, ...(s.y ? { y: pad(s.y, s.at) } : {}), ...(s.lo ? { lo: pad(s.lo, s.at), hi: pad(s.hi, s.at) } : {}) }
    : s));
}

// The future of a chart that has one (OPT's PAST & FUTURE): `todayIdx` the last past point; a horizon keeps that
// many months after it. Every per-point array of a series is cut alike (y, and a band's lo / hi).
export const HORIZON_MONTHS = { "6M": 6, "1Y": 12, "3Y": 36 };
export function cutHorizon(x, series, todayIdx, months) {
  if (todayIdx === null || todayIdx === undefined) return { x, series };
  const n = Math.min(x.length, todayIdx + 1 + months), cut = (a) => a?.slice(0, n);
  return { x: x.slice(0, n), series: series.map((s) => ({ ...s, y: cut(s.y), ...(s.lo ? { lo: cut(s.lo), hi: cut(s.hi) } : {}) })) };
}

// Where a chart's legend rests with no cursor: its last point — or, for a chart with a future, today (`todayIdx` in
// the full x; `start` the shown period's first index), never the horizon's end. Pure.
export function legendRest(n, todayIdx, start = 0) {
  if (todayIdx === null || todayIdx === undefined) return n - 1;
  return Math.max(0, Math.min(n - 1, todayIdx - start));
}

// A legend line's value at the cursor `idx`: the line itself up to today (`todayIdx`, in the same coordinates); past
// today, its projected median — the series "<name> →" (YOU's future is keeping your mix: "NOW →") — or, without one,
// the line itself. Never today's value under a future date. Pure.
export function legendValue(series, line, idx, todayIdx) {
  if (todayIdx !== null && todayIdx !== undefined && idx > todayIdx) {
    const fut = series.find((s) => s.name === `${line.name === "YOU" ? "NOW" : line.name} →`);
    if (fut) return valueAt(fut.y, idx);
  }
  return valueAt(line.y, idx);
}
