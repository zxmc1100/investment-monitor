// Linear scales and round tick values for SVG charts. Pure.
export function extent(values) {
  let lo = Infinity, hi = -Infinity;
  for (const v of values) if (v !== null && v !== undefined && Number.isFinite(v)) { lo = Math.min(lo, v); hi = Math.max(hi, v); }
  if (lo === Infinity) return null;
  return lo === hi ? [lo - 1, hi + 1] : [lo, hi];
}

export function niceTicks(lo, hi, n = 5) {
  const span = hi - lo, raw = span / n, mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => span / s <= n) ?? 10 * mag;
  const out = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(Number(v.toFixed(10)));
  return out;
}

export const lin = ([d0, d1], [r0, r1]) => (v) => r0 + ((v - d0) / (d1 - d0)) * (r1 - r0);
