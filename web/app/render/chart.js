// uPlot line/marker chart. Uses the global `uPlot` from the vendored IIFE build.
import { esc } from "../dom.js";
import { fmt, fmtDate, timeTicks } from "../fmt.js";
import { chartModes, cutHorizon, defaultRange, expandAt, HORIZON_MONTHS, lastValue, legendRest, legendValue, MODE_TITLES, normalize, rangeEnd, rangeStart, rebase, sliceFrom, valueAt, windowMwr, windowRoi } from "../ranges.js";

const PALETTE = ["#ffa028", "#4fc3f7", "#e040fb", "#00e676", "#ffeb3b", "#ff7043", "#9575cd", "#26a69a", "#bdbdbd"];
const ROLE = { primary: "#ffffff", buy: "#00e676", sell: "#ff3d3d" };
const AXIS_FMT = { eur: "eur:0", "eur+": "eur+:0", pct: "pct:0", "pct+": "pct+:0" };
const GAP_FMT = { eur: "eur+:0", "eur+": "eur+:0", pct: "pct+:0", "pct+": "pct+:0" };    // a spread: signed
const SPREAD = "#ff80ab";                                                                 // in no line's palette

const color = (s, i) => s.color ?? ROLE[s.role] ?? PALETTE[i % PALETTE.length];
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

// uPlot's data, series options (after x) and bands for `series`: a line or markers as before, `dash` dotted;
// a band (kind "band": lo / hi) two invisible lines with the area between them filled in its colour, faint.
export function plotData(x, series, colorOf) {
  const data = [x], specs = [{}], bands = [];
  series.forEach((s, i) => {
    const c = colorOf(s, i);
    if (s.kind === "band") {
      data.push(s.lo, s.hi);
      specs.push({ label: `${s.name} lo`, stroke: "transparent", points: { show: false }, spanGaps: false },
                 { label: `${s.name} hi`, stroke: "transparent", points: { show: false }, spanGaps: false });
      bands.push({ series: [data.length - 1, data.length - 2], fill: `${c}26` });
      return;
    }
    data.push(s.y);
    specs.push({ label: s.name, stroke: c, width: s.role === "primary" ? 1.8 : 1, spanGaps: false,
      ...(s.dash ? { dash: [4, 4] } : {}), ...(s.scale ? { scale: s.scale } : {}),
      ...(s.kind === "markers"
        ? { paths: () => null, points: { show: true, size: 6, fill: c, stroke: c } }
        : { points: { show: false } }) });
  });
  return { data, specs, bands };
}

// The point under a chart's cursor (SEC's price, DRAWDOWN … ; with a legend, nearestLine's): its date, the first line's value
// and, with `base` (SEC: the average cost), the P&L % the right axis reads. Pure (tui panels.draw_chart).
export function pointTip(lines, x, i, yfmt, base) {
  const v = i === null || i === undefined || !lines.length ? null : valueAt(lines[0].y, i);
  if (v === null || v === undefined) return null;
  return { v, date: fmtDate(new Date(x[i] * 1000).toISOString().slice(0, 10)), value: fmt(v, yfmt),
    pct: base ? fmt((v / base - 1) * 100, "pct+") : null };
}

// Two lines left on a chart with a legend: the first's lead over the second, day by day (YOU over a benchmark: its
// alpha, in the chart's unit — points of ROI), on a scale of its own that the right axis reads and names. Pure.
export function spreadLine(a, b) {
  const gap = (v, w) => (v === null || v === undefined || w === null || w === undefined ? null : v - w);
  return { name: `${a.name} − ${b.name}`, kind: "line", scale: "spread", color: SPREAD, dash: true,
    y: a.y.map((v, i) => gap(v, b.y[i])) };
}

// The line whose point at index i lies nearest the pointer's height `top` (pixels; `toPos(v, line)` maps a value
// there, on that line's scale) — the one a chart with a legend names in its tip. Pure.
export function nearestLine(lines, i, top, toPos) {
  let best = null, gap = Infinity;
  if (i === null || i === undefined) return null;
  for (const s of lines) {
    const v = valueAt(s.y, i);
    const d = v === null || v === undefined ? Infinity : Math.abs(toPos(v, s) - top);
    if (d < gap) [best, gap] = [s, d];
  }
  return best;
}

export function chart(body, p, ui) {
  const raw = chartSource(p, ui.followKey(p));
  if (!raw?.x || raw.x.length < 2 || !raw.series?.length) { body.innerHTML = `<div class="empty">NO DATA</div>`; return; }
  // A chart with a future (OPT's AHEAD): its future lines and bands padded to the x (ranges.expandAt), then
  // cut to the horizon chip — before any range or drag, so today's index still holds.
  const horizon = p.horizons ? (p.horizons.includes(ui.chartHorizon(p.id)) ? ui.chartHorizon(p.id) : p.horizon) : null;
  const padded = { ...raw, series: expandAt(raw.series, raw.x.length) };
  const src = horizon ? { ...padded, ...cutHorizon(padded.x, padded.series, p.today_idx, HORIZON_MONTHS[horizon]) } : padded;
  // A period dragged on the chart is a range of its own ({from, to, prev}) until a named range or a
  // double-click; a following chart drops it when its row changes (another curve, other dates).
  // a named range this chart does not offer (left by another view of the panel: WHAT IF's FOLLOWED) is not its own
  const key = ui.followKey(p), dflt = defaultRange(p), kept = ui.chartRange(p.id) ?? dflt;
  const saved = typeof kept === "string" && kept !== dflt && !(p.ranges ?? []).includes(kept) ? dflt : kept;
  const range = typeof saved === "object" && saved.key !== key ? saved.prev ?? dflt : saved;
  const dragged = typeof range === "object";
  // The view (PORT's ROI chart: ranges.chartModes), each line measured from the close before the period shown or
  // dragged — ROI (default): the ROI formula on the window (ranges.windowRoi; ALL: the lines as they are) · MWR:
  // money-weighted, Modified Dietz (ranges.windowMwr) · TWR: time-weighted (ranges.normalize).
  const modes = src.x === p.x ? chartModes(p, src.series) : [];
  const mode = modes.includes(ui.chartMode(p.id)) ? ui.chartMode(p.id) : modes[0] ?? null;
  const from = rangeStart(src.x, range), inception = from <= src.x[0];
  const twr = mode === "TWR", mwr = mode === "MWR" && Array.isArray(p.inv);
  const roiWin = mode === "ROI" && range !== "ALL" && Array.isArray(p.inv);
  const sliced = sliceFrom(src.x, src.series, from, rangeEnd(range), twr || mwr || roiWin);
  const inv = mwr || roiWin ? p.inv.slice(sliced.start, sliced.start + sliced.x.length) : null;
  const x = sliced.x, series = twr ? normalize(sliced.series, sliced.start === 0)
    : mwr ? windowMwr(sliced.series, inv, x, from, inception)
    : roiWin ? windowRoi(sliced.series, inv, inception) : p.rebase ? rebase(sliced.series) : sliced.series;
  const isoAt = (i) => new Date(x[i] * 1000).toISOString().slice(0, 10);
  const lines = series.filter((s) => s.kind !== "markers" && s.kind !== "band" && !s.nolegend);
  // at rest a chart with a past and a future reads today; a future alone (OPT's AHEAD) its horizon's end
  const rest = legendRest(x.length, p.horizons && p.today_idx ? p.today_idx : null, sliced.start);
  const todayAt = p.horizons ? p.today_idx - sliced.start : null;                     // past it: the projections
  const legend = p.legend === "rank"
    ? [...lines].sort((a, b) => (valueAt(b.y, rest) ?? -Infinity) - (valueAt(a.y, rest) ?? -Infinity)) : [];
  const chips = (p.ranges ?? []).map((r) => `<span class="${r === range ? "on" : ""}" data-r="${esc(r)}">${esc(r)}</span>`);
  if (dragged) chips.push(`<span class="on">${esc(fmtDate(isoAt(0)))}–${esc(fmtDate(isoAt(x.length - 1)))}</span>`);
  for (const m of modes) chips.push(`<span class="mode${m === mode ? " on" : ""}" data-mode="${m}" title="${esc(MODE_TITLES[m])}">${m}</span>`);
  for (const h of p.horizons ?? []) chips.push(`<span class="hz${h === horizon ? " on" : ""}" data-h="${esc(h)}">${esc(h)}</span>`);
  const ranges = chips.length ? `<div class="ranges">${chips.join("")}</div>` : "";
  // Fixed-width legend (colgroup + CSS): a value gaining a digit must never resize the plot.
  // A legend line clicked is hidden from the plot (ui.toggleLine; dimmed here, still read): a crowded chart thinned.
  const off = legend.length ? ui.hiddenLines(p.id) : new Set();
  const legendHtml = legend.length ? `<table class="legend"><colgroup><col><col class="v"></colgroup><tr class="asof"><td colspan="2"></td></tr>${legend.map((s) =>
    `<tr${off.has(s.name) ? ' class="off"' : ""} data-name="${esc(s.name)}" title="click: ${off.has(s.name) ? "show" : "hide"} this line"><td style="color:${color(s, series.indexOf(s))}">${esc(s.name)}</td><td class="r" data-s="${series.indexOf(s)}"></td></tr>`
  ).join("")}</table>` : "";
  body.innerHTML = `${ranges}<div class="plotwrap"><div class="plot"></div>${legendHtml}</div>`;
  const lg = body.querySelector(".legend");
  const paint = (idx) => {
    if (!lg) return;
    lg.querySelector(".asof td").textContent = fmtDate(isoAt(idx ?? rest));
    lg.querySelectorAll("td[data-s]").forEach((td) => {
      const f = fmt(legendValue(series, series[Number(td.dataset.s)], idx ?? rest, todayAt), p.yfmt);
      td.textContent = f.text;
      td.className = `r ${f.cls}`;
    });
  };
  paint(null);
  body.querySelectorAll(".ranges span[data-r]").forEach((el) => el.addEventListener("click", () => ui.setRange(p.id, el.dataset.r)));
  body.querySelectorAll(".ranges [data-mode]").forEach((el) => el.addEventListener("click", () => ui.setMode(p.id, el.dataset.mode)));
  body.querySelectorAll(".ranges [data-h]").forEach((el) => el.addEventListener("click", () => ui.setHorizon(p.id, el.dataset.h)));
  body.querySelectorAll(".legend tr[data-name]").forEach((el) => el.addEventListener("click", () => ui.toggleLine(p.id, el.dataset.name)));

  // The dragged period becomes the chart's range — drawn again from the data, so every view restarts at its
  // start and a refresh keeps it. After uPlot's own mouse-up handling (the redraw replaces the plot).
  const back = dragged ? range.prev ?? dflt : range;
  const zoom = (u) => {
    const { left, width } = u.select;
    if (width < 3) return;
    const span = { from: u.posToVal(left, "x"), to: u.posToVal(left + width, "x"), prev: back, key };
    setTimeout(() => ui.setRange(p.id, span), 0);
  };
  const el = body.querySelector(".plot");
  const size = () => ({ width: Math.max(el.clientWidth, 100), height: Math.max(el.clientHeight, 60) });
  const yfmt = AXIS_FMT[p.yfmt] ?? "num:0";
  const shown = lines.filter((s) => !off.has(s.name));
  const spread = legend.length && shown.length === 2 ? spreadLine(shown[0], shown[1]) : null;
  const plotted = [...series.filter((s) => !off.has(s.name)), ...(spread ? [spread] : [])];
  const { data, specs, bands } = plotData(x, plotted, (s) => color(s, series.indexOf(s)));
  // a dot on the point under the cursor and its tip beside it, turned away from the nearer edges — with a legend,
  // on the shown line nearest the pointer, named in its colour
  const tip = Object.assign(document.createElement("div"), { className: "tip", hidden: true });
  const showTip = (u) => {
    const line = !legend.length ? lines[0] : nearestLine(spread ? [...shown, spread] : shown, u.cursor.idx, u.cursor.top,
      (v, s) => u.valToPos(v, s.scale ?? "y"));
    const t = u.cursor.left >= 0 && line ? pointTip([line], x, u.cursor.idx, p.yfmt, p.pnl_base) : null;
    tip.hidden = !t;
    if (!t) return;
    const left = u.valToPos(x[u.cursor.idx], "x"), top = u.valToPos(t.v, line.scale ?? "y"), c = color(line, series.indexOf(line));
    tip.className = `tip${left > u.over.clientWidth / 2 ? " l" : ""}${top < 40 ? " b" : ""}`;
    Object.assign(tip.style, { left: `${left}px`, top: `${top}px` });
    tip.style.setProperty("--c", legend.length ? c : "");
    tip.innerHTML = `<div>${legend.length ? `<span style="color:${c}">${esc(line.name)}</span> ` : ""}<span class="dim">${
      esc(t.date)}</span> ${esc(t.value.text)}${t.pct ? ` <span class="${t.pct.cls}">${esc(t.pct.text)}</span>` : ""}</div>`;
  };
  const plot = new uPlot({
    ...size(),
    legend: { show: false },
    hooks: { setCursor: [(u) => { paint(u.cursor.idx); showTip(u); }], setSelect: [zoom], ...(p.vlines?.length ? { draw: [(u) => drawMarks(u, p.vlines, p.vlabels)] } : {}) },
    // Drag across a period to zoom into it (zoom); a double-click goes back to the range before.
    cursor: { points: { show: false }, drag: { x: true, y: false, setScale: false } },
    scales: { x: { time: true } },
    axes: [{ ...axis(), values: (u, ticks, _i, _space, incr) => timeTicks(ticks, incr) },
      { ...axis(), size: 58, values: (u, ticks) => ticks.map((t) => fmt(t, yfmt).text) },
      // two lines left: their spread's own scale on the right, named there (its legend)
      ...(spread ? [{ ...axis(), scale: "spread", side: 1, size: 58, stroke: SPREAD, grid: { show: false },
        label: spread.name, labelSize: 16, labelFont: axis().font,
        values: (u, ticks) => ticks.map((t) => fmt(t, GAP_FMT[p.yfmt] ?? "num:0").text) }] : []),
      // SEC's price: the same scale on the right as P&L % against the average cost (p.pnl_base)
      ...(p.pnl_base ? [{ ...axis(), scale: "y", side: 1, size: 52, grid: { show: false },
        values: (u, ticks) => ticks.map((t) => fmt((t / p.pnl_base - 1) * 100, "pct+:0").text) }] : [])],
    series: specs,
    bands,
  }, data, el);
  plot.over.append(tip);
  // out past the plot on the right (the axis): the present, not a one-pixel target at the edge
  el.addEventListener("mousemove", (e) => {
    const r = plot.over.getBoundingClientRect();
    if (e.clientX > r.right) plot.setCursor({ left: plot.valToPos(x[rest], "x"), top: Math.min(Math.max(e.clientY - r.top, 0), r.height) });
  });
  // never mid-drag: uPlot has just snapped the selection to the edge it left by — a cursor at -10 dragged it left
  el.addEventListener("mouseleave", (e) => { if (!e.buttons) plot.setCursor({ left: -10, top: -10 }); });
  if (dragged) plot.over.addEventListener("dblclick", () => ui.setRange(p.id, back));
  const ro = new ResizeObserver(() => plot.setSize(size()));
  ro.observe(el);
  ui.onDispose(() => { ro.disconnect(); plot.destroy(); });
}
