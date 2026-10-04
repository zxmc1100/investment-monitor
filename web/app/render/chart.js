// uPlot line/marker chart. Uses the global `uPlot` from the vendored IIFE build.
import { esc } from "../dom.js";
import { fmt, fmtDate, timeTicks } from "../fmt.js";
import { canNorm, lastValue, normalize, rangeEnd, rangeStart, rebase, sliceFrom, valueAt } from "../ranges.js";

const PALETTE = ["#ffa028", "#4fc3f7", "#e040fb", "#00e676", "#ffeb3b", "#ff7043", "#9575cd", "#26a69a", "#bdbdbd"];
const ROLE = { primary: "#ffffff", buy: "#00e676", sell: "#ff3d3d" };
const AXIS_FMT = { eur: "eur:0", "eur+": "eur+:0", pct: "pct:0", "pct+": "pct+:0" };

const color = (s, i) => ROLE[s.role] ?? PALETTE[i % PALETTE.length];
const axis = () => ({ stroke: "#8a8a8a", grid: { stroke: "#1c1c1c", width: 1 }, ticks: { stroke: "#2b2b2b", width: 1 },
  font: '10px "SF Mono", ui-monospace, Menlo, monospace' });

// What a chart draws: its own x/series, or — following a table — the cursor row's block. A block
// without its own x shares the panel's x and adds the panel's series after its own (the cursor row's
// curve, then the comparisons every row shares), less those it `hide`s (a row that is one of them); its
// `note` says what the row lacks (NO CURVE FOR …, shown in the header). Pure.
export function chartSource(p, key) {
  if (!p.follows) return p;
  const blk = (p.series_by_key ?? {})[key];
  if (!blk) return null;
  if (blk.x) return blk;
  const hide = new Set(blk.hide ?? []);
  return { x: p.x, series: [...(blk.series ?? []), ...(p.series ?? []).filter((s) => !hide.has(s.name))], note: blk.note ?? null };
}

// Dashed vertical marks with a label (e.g. where a backtest's validation and test windows begin).
function drawMarks(u, xs, labels) {
  const { ctx } = u, { left, top, width, height } = u.bbox, px = devicePixelRatio || 1;
  ctx.save();
  ctx.strokeStyle = "#5a5a5a"; ctx.fillStyle = "#8a8a8a"; ctx.setLineDash([3 * px, 3 * px]);
  ctx.font = `${10 * px}px "SF Mono", ui-monospace, Menlo, monospace`;
  xs.forEach((t, i) => {
    const at = Math.round(u.valToPos(t, "x", true));
    if (at < left || at > left + width) return;
    ctx.beginPath(); ctx.moveTo(at, top); ctx.lineTo(at, top + height); ctx.stroke();
    ctx.fillText(labels?.[i] ?? "", at + 3 * px, top + 10 * px);
  });
  ctx.restore();
}

export function chart(body, p, ui) {
  const src = chartSource(p, ui.followKey(p));
  if (!src?.x || src.x.length < 2 || !src.series?.length) { body.innerHTML = `<div class="empty">NO DATA</div>`; return; }
  // A period dragged on the chart is a range of its own ({from, to, prev}) until a named range or a
  // double-click; a following chart drops it when its row changes (another curve, other dates).
  const key = ui.followKey(p), saved = ui.chartRange(p.id) ?? "ALL";
  const range = typeof saved === "object" && saved.key !== key ? saved.prev ?? "ALL" : saved;
  const dragged = typeof range === "object";
  // NORM: each line's time-weighted return from the close before the period (see ranges.normalize).
  const normable = canNorm(src.series), norm = normable && ui.chartNorm(p.id);
  const sliced = sliceFrom(src.x, src.series, rangeStart(src.x, range), rangeEnd(range), norm);
  const x = sliced.x, series = norm ? normalize(sliced.series) : p.rebase ? rebase(sliced.series) : sliced.series;
  const isoAt = (i) => new Date(x[i] * 1000).toISOString().slice(0, 10);
  const lines = series.filter((s) => s.kind !== "markers");
  const legend = p.legend === "rank"
    ? [...lines].sort((a, b) => (lastValue(b.y) ?? -Infinity) - (lastValue(a.y) ?? -Infinity)) : [];
  const chips = (p.ranges ?? []).map((r) => `<span class="${r === range ? "on" : ""}" data-r="${esc(r)}">${esc(r)}</span>`);
  if (dragged) chips.push(`<span class="on">${esc(fmtDate(isoAt(0)))}–${esc(fmtDate(isoAt(x.length - 1)))}</span>`);
  if (normable) chips.push(`<span class="norm${norm ? " on" : ""}" data-norm title="Alt+N">NORM</span>`);
  const ranges = chips.length ? `<div class="ranges">${chips.join("")}</div>` : "";
  // Fixed-width legend (colgroup + CSS): a value gaining a digit must never resize the plot.
  const legendHtml = legend.length ? `<table class="legend"><colgroup><col><col class="v"></colgroup><tr class="asof"><td colspan="2"></td></tr>${legend.map((s) =>
    `<tr><td style="color:${color(s, series.indexOf(s))}">${esc(s.name)}</td><td class="r" data-s="${series.indexOf(s)}"></td></tr>`
  ).join("")}</table>` : "";
  body.innerHTML = `${ranges}<div class="plotwrap"><div class="plot"></div>${legendHtml}</div>`;
  const lg = body.querySelector(".legend");
  const paint = (idx) => {
    if (!lg) return;
    lg.querySelector(".asof td").textContent = fmtDate(isoAt(idx ?? x.length - 1));
    lg.querySelectorAll("td[data-s]").forEach((td) => {
      const f = fmt(valueAt(series[Number(td.dataset.s)].y, idx), p.yfmt);
      td.textContent = f.text;
      td.className = `r ${f.cls}`;
    });
  };
  paint(null);
  body.querySelectorAll(".ranges span[data-r]").forEach((el) => el.addEventListener("click", () => ui.setRange(p.id, el.dataset.r)));
  body.querySelector(".ranges [data-norm]")?.addEventListener("click", () => ui.toggleNorm(p.id));

  // The dragged period becomes the chart's range — drawn again from the data, so NORM restarts at its
  // start and a refresh keeps it. After uPlot's own mouse-up handling (the redraw replaces the plot).
  const back = dragged ? range.prev ?? "ALL" : range;
  const zoom = (u) => {
    const { left, width } = u.select;
    if (width < 3) return;
    const span = { from: u.posToVal(left, "x"), to: u.posToVal(left + width, "x"), prev: back, key };
    setTimeout(() => ui.setRange(p.id, span), 0);
  };
  const el = body.querySelector(".plot");
  const size = () => ({ width: Math.max(el.clientWidth, 100), height: Math.max(el.clientHeight, 60) });
  const yfmt = AXIS_FMT[p.yfmt] ?? "num:0";
  const plot = new uPlot({
    ...size(),
    legend: { show: false },
    hooks: { setCursor: [(u) => paint(u.cursor.idx)], setSelect: [zoom], ...(p.vlines?.length ? { draw: [(u) => drawMarks(u, p.vlines, p.vlabels)] } : {}) },
    // Drag across a period to zoom into it (zoom); a double-click goes back to the range before.
    cursor: { points: { show: false }, drag: { x: true, y: false, setScale: false } },
    scales: { x: { time: true } },
    axes: [{ ...axis(), values: (u, ticks, _i, _space, incr) => timeTicks(ticks, incr) },
      { ...axis(), size: 58, values: (u, ticks) => ticks.map((t) => fmt(t, yfmt).text) }],
    series: [{}, ...series.map((s, i) => ({
      label: s.name, stroke: color(s, i), width: s.role === "primary" ? 1.8 : 1, spanGaps: false,
      ...(s.kind === "markers"
        ? { paths: () => null, points: { show: true, size: 6, fill: color(s, i), stroke: color(s, i) } }
        : { points: { show: false } }),
    }))],
  }, [x, ...series.map((s) => s.y)], el);
  if (dragged) plot.over.addEventListener("dblclick", () => ui.setRange(p.id, back));
  const ro = new ResizeObserver(() => plot.setSize(size()));
  ro.observe(el);
  ui.onDispose(() => { ro.disconnect(); plot.destroy(); });
}
