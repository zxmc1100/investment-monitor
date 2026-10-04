// SVG scatter/line chart for numeric x (the efficient frontier). No uPlot needed.
import { esc } from "../dom.js";
import { fmt } from "../fmt.js";
import { extent, lin, niceTicks } from "../scale.js";

const ROLE = { cloud: "#5a3a10", frontier: "#ffa028", now: "#ffffff", target: "#00e676", port: "#4fc3f7" };
const W = 640, H = 230, L = 46, R = 70, T = 8, B = 22;

// Marker labels: each tries spots around its own dot (right, left, above, below, then one line
// further out) and takes the first that overlaps no placed label or marker and sits closer to its own
// dot than to any other; if every spot fails, the least-bad one. A label never drifts far. Pure.
export function placeLabels(items, lineH = 11, charW = 6) {
  const dots = items.map((it) => ({ px: it.px ?? it.x - 6, py: it.py ?? it.y + 5 }));
  const box = (x, y, w) => ({ x0: x, x1: x + w, y0: y - lineH + 2, y1: y + 2 });
  const hit = (a, b) => a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1;
  const dotBox = ({ px, py }) => ({ x0: px - 4, x1: px + 4, y0: py - 4, y1: py + 4 });
  const placed = new Array(items.length);
  const order = items.map((_, i) => i).sort((a, b) => dots[a].py - dots[b].py || dots[a].px - dots[b].px);
  for (const i of order) {
    const it = items[i], { px, py } = dots[i], w = it.text.length * charW;
    const first = [it.x - px, it.y - py];                       // the caller's spot (right, a little above)
    const spots = [first, [6, 9], [-w - 6, -5], [-w - 6, 9], [6, 2], [-w - 6, 2], [-w / 2, -8], [-w / 2, 15],
      [6, -5 - lineH], [6, 9 + lineH], [-w - 6, -5 - lineH], [-w - 6, 9 + lineH]];
    let best = null;
    for (const [dx, dy] of spots) {
      const b = box(px + dx, py + dy, w);
      const near = (d) => Math.hypot(Math.max(b.x0 - d.px, 0, d.px - b.x1), Math.max(b.y0 - d.py, 0, d.py - b.y1));
      const own = near(dots[i]);
      const clashes = placed.filter((q) => q && hit(b, q.b)).length
        + dots.filter((d, j) => j !== i && (hit(b, dotBox(d)) || near(d) < own)).length;   // never nearer another dot
      if (!best || clashes < best.clashes) best = { x: px + dx, y: py + dy, b, clashes };
      if (clashes === 0) break;
    }
    placed[i] = best;
  }
  return items.map((it, i) => ({ ...it, x: placed[i].x, y: placed[i].y }));
}

// Markers whose dots overlap (within `r`, chained) share one label; members top to bottom, the
// group anchored at its topmost dot. Pure.
export function groupMarkers(marks, r = 8) {
  const groups = [];
  for (const m of [...marks].sort((a, b) => a.py - b.py || a.px - b.px)) {
    const g = groups.find((x) => x.members.some((o) => Math.hypot(o.px - m.px, o.py - m.py) <= r));
    if (g) g.members.push(m); else groups.push({ members: [m] });
  }
  return groups.map((g) => ({ ...g, px: g.members[0].px, py: g.members[0].py }));
}

// Decimals a tick step needs (2.5 → 1, 5 → 0, 0.25 → 2). Pure.
export function tickDecimals(ticks) {
  const step = ticks.length > 1 ? Math.abs(ticks[1] - ticks[0]) : 1;
  let d = 0;
  while (d < 3 && Math.abs(Math.round(step * 10 ** d) - step * 10 ** d) > 1e-9) d++;
  return d;
}

export function scatter(body, p) {
  const all = p.series ?? [];
  const xs = extent(all.flatMap((s) => s.x ?? [])), ys = extent(all.flatMap((s) => s.y ?? []));
  if (!xs || !ys) { body.innerHTML = `<div class="empty">NO DATA</div>`; return; }
  const pad = ([lo, hi], f) => [lo - (hi - lo) * f, hi + (hi - lo) * f];   // no dot on the frame
  const [xd, yd] = [pad(xs, 0.04), pad(ys, 0.08)];
  const sx = lin(xd, [L, W - R]), sy = lin(yd, [H - B, T]);
  const xt = niceTicks(...xd, 6), yt = niceTicks(...yd, 5);
  const xf = (v) => esc(fmt(v, `${(p.xfmt ?? "num").split(":")[0]}:${tickDecimals(xt)}`).text);
  const yf = (v) => esc(fmt(v, `${(p.yfmt ?? "num").split(":")[0]}:${tickDecimals(yt)}`).text);
  const grid = xt.map((t) => `<line class="g" x1="${sx(t)}" x2="${sx(t)}" y1="${T}" y2="${H - B}"/><text class="tx" x="${sx(t)}" y="${H - 6}">${xf(t)}</text>`).join("")
    + yt.map((t) => `<line class="g" x1="${L}" x2="${W - R}" y1="${sy(t)}" y2="${sy(t)}"/><text class="ty" x="${L - 4}" y="${sy(t) + 3}">${yf(t)}</text>`).join("");
  const pts = (s) => (s.x ?? []).map((x, i) => [x, s.y?.[i]]).filter(([x, y]) => Number.isFinite(x) && Number.isFinite(y));
  const color = (s) => ROLE[s.role] ?? "#bdbdbd";
  const marks = all.map((s) => {
    const c = color(s), ps = pts(s);
    if (s.kind === "line") return `<polyline fill="none" stroke="${c}" stroke-width="1.5" points="${ps.map(([x, y]) => `${sx(x).toFixed(1)},${sy(y).toFixed(1)}`).join(" ")}"/>`;
    const r = s.kind === "marker" ? 4 : 1.4;
    return ps.map(([x, y]) => `<circle cx="${sx(x).toFixed(1)}" cy="${sy(y).toFixed(1)}" r="${r}" fill="${c}"/>`).join("");
  }).join("");
  const marks2 = all.filter((s) => s.kind === "marker").flatMap((s) =>
    pts(s).map(([x, y]) => ({ name: String(s.name), c: color(s), px: sx(x), py: sy(y) })));
  const labels = placeLabels(groupMarkers(marks2).map((g) => ({ x: g.px + 6, y: g.py - 5, px: g.px, py: g.py,
    text: g.members.map((m) => m.name).join(" · "), members: g.members })))
    .map((l) => `<text class="lb" x="${l.x.toFixed(1)}" y="${l.y.toFixed(1)}">${l.members.map((m, i) =>
      `${i ? '<tspan fill="#9e9e9e"> · </tspan>' : ""}<tspan fill="${m.c}">${esc(m.name)}</tspan>`).join("")}</text>`).join("");
  body.innerHTML = `<svg class="scatter" viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMidYMid meet">${grid}${marks}${labels}</svg>`;
}
