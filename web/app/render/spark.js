// Inline SVG sparkline for table cells.
export function spark(values, w = 44, h = 10) {
  const v = (values ?? []).filter((x) => x !== null && x !== undefined);
  if (v.length < 2) return "";
  const lo = Math.min(...v), hi = Math.max(...v), span = hi - lo || 1;
  const pts = v.map((y, i) => `${((i / (v.length - 1)) * w).toFixed(1)},${(h - 0.5 - ((y - lo) / span) * (h - 1)).toFixed(1)}`).join(" ");
  return `<svg class="spark ${v[v.length - 1] >= v[0] ? "up" : "dn"}" viewBox="0 0 ${w} ${h}" width="${w}" height="${h}"><polyline points="${pts}"/></svg>`;
}
