import { test } from "node:test";
import assert from "node:assert/strict";
import { alphaLine, chartSource, nearestLine, plotData, pointTip, spreadLine } from "../../web/app/render/chart.js";
import { followsLater } from "../../web/app/render/table.js";
import { rebase } from "../../web/app/ranges.js";

const EQ = { follows: "reg", x: [1, 2, 3], series: [{ name: "BOOK", y: [100, 110, 121] }],
  series_by_key: { 7: { series: [{ name: "REC", y: [null, 50, 55] }] }, 8: { x: [5, 6], series: [{ name: "OWN", y: [1, 2] }] } } };

test("a following block without x shares the panel's x and adds the panel's series after its own", () => {
  assert.deepEqual(chartSource(EQ, 7), { x: [1, 2, 3], series: [{ name: "REC", y: [null, 50, 55] }, { name: "BOOK", y: [100, 110, 121] }], note: null });
  assert.deepEqual(chartSource(EQ, "8"), { x: [5, 6], series: [{ name: "OWN", y: [1, 2] }] });
  assert.equal(chartSource(EQ, 9), null);
  const plain = { x: [1, 2], series: [] };
  assert.equal(chartSource(plain, null), plain);
});
test("rebase starts every series at 100 where the first series starts", () => {
  const out = rebase([{ name: "REC", y: [null, 40, 60] }, { name: "BOOK", y: [100, 200, 300] }, { name: "LATE", y: [null, null, 4] }]);
  assert.deepEqual(out.map((s) => s.y), [[null, 100, 150], [null, 100, 150], [null, null, 100]]);
  assert.equal(out[1].name, "BOOK");
  assert.deepEqual(rebase([{ y: [null, null] }]), [{ y: [null, null] }]);
});
test("a panel following a table drawn after it is drawn again once that table has a cursor", () => {
  const panels = [{ id: "book" }, { id: "equity", follows: "registry" }, { id: "posval", follows: "book" }, { id: "registry" }];
  assert.deepEqual(followsLater(panels), ["equity"]);
});

test("a curve-less row keeps the shared lines with its note; the book's own row does not draw it twice", () => {
  const eq = { ...EQ, series_by_key: { ...EQ.series_by_key, 5: { series: [], note: "NO CURVE FOR Your portfolio" },
    6: { series: [{ name: "ENSEMBLE", role: "primary", y: [100, 110, 121] }], hide: ["BOOK"] } } };
  assert.deepEqual(chartSource(eq, 5), { x: [1, 2, 3], series: [{ name: "BOOK", y: [100, 110, 121] }], note: "NO CURVE FOR Your portfolio" });
  assert.deepEqual(chartSource(eq, 6).series.map((s) => s.name), ["ENSEMBLE"]);
});

test("a band becomes two hidden lines and a filled area; a dashed line keeps its colour", () => {
  const series = [
    { name: "YOU", role: "primary", y: [1, 2] },
    { name: "HRP →", color: "#00e676", dash: true, nolegend: true, y: [null, 3] },
    { name: "HRP 90%", kind: "band", color: "#00e676", y: [null, null], lo: [null, 1], hi: [null, 5] },
  ];
  const { data, specs, bands } = plotData([10, 20], series, (s) => s.color ?? "#fff");
  assert.equal(data.length, 1 + 2 + 2);                                    // x, two lines, lo + hi
  assert.deepEqual(data[3], [null, 1]); assert.deepEqual(data[4], [null, 5]);
  assert.deepEqual(specs[2].dash, [4, 4]); assert.equal(specs[2].stroke, "#00e676");   // specs[0]: uPlot's x
  assert.equal(specs[1].dash, undefined);
  assert.deepEqual(bands, [{ series: [4, 3], fill: "#00e67626" }]);
});

test("a legend-less chart's tip reads the first line's point: date, value, and P&L % against a base", () => {
  const lines = [{ name: "PRICE", y: [100, null, 125] }, { name: "AVG COST", y: [80, 80, 80] }];
  const x = [Date.UTC(2026, 9, 1) / 1000, Date.UTC(2026, 9, 2) / 1000, Date.UTC(2026, 9, 5) / 1000];
  const t = pointTip(lines, x, 2, "num", 100);
  assert.equal(t.v, 125);
  assert.match(t.date, /2026|26/);
  assert.equal(t.pct.text, "+25.0%");
  assert.equal(t.pct.cls, "up");
  assert.equal(pointTip(lines, x, 1, "num", 100).v, 100);            // a gap reads the last close before it
  assert.equal(pointTip(lines, x, 0, "num", null).pct, null);        // no base: no %
  assert.equal(pointTip(lines, x, null, "num", 100), null);
  assert.equal(pointTip([], x, 0, "num", 100), null);
});

test("a chart with a legend names the line nearest the pointer at the cursor's index", () => {
  const a = { name: "A", y: [10, 20, null] }, b = { name: "B", y: [0, 5, 6] }, toPos = (v) => 100 - v;   // up = smaller px
  assert.equal(nearestLine([a, b], 0, 97, toPos), b);               // 3 px from B (at 100), 7 from A (at 90)
  assert.equal(nearestLine([a, b], 1, 82, toPos), a);               // A at 80 px, B at 95
  assert.equal(nearestLine([a, b], 2, 82, toPos), a);               // a gap reads the last point before it
  assert.equal(nearestLine([a, b], null, 82, toPos), null);
  assert.equal(nearestLine([], 1, 82, toPos), null);
});

test("two lines left: the first's lead over the second on a scale of its own, a gap where either has none", () => {
  const s = spreadLine({ name: "YOU", y: [1, 5, null, 8] }, { name: "SPX", y: [1, 2, 3, null] });
  assert.equal(s.name, "YOU − SPX");
  assert.equal(s.scale, "spread");
  assert.deepEqual(s.y, [0, 3, null, null]);
  const { specs } = plotData([1, 2, 3, 4], [s], (l) => l.color);
  assert.equal(specs[1].scale, "spread");                            // uPlot draws it against the right axis
  assert.deepEqual(specs[1].dash, [4, 4]);
});

test("alpha: the first line's steps less β times the second's, summed — β and a constant edge recovered", () => {
  // b steps up and down; a = 0.8·b + 0.1 % a step, exactly: β 0.80, alpha 0.1 point a step
  const rb = [0.01, -0.02, 0.015, 0.005, -0.01, 0.02, -0.005, 0.01, -0.015, 0.012, 0.003, -0.008];
  const level = (r) => r.reduce((l, x) => [...l, l[l.length - 1] * (1 + x)], [1]).map((v) => (v - 1) * 100);
  const b = { name: "SPX", y: [null, ...level(rb)] };                 // a leading gap: starts where both have a point
  const a = { name: "YOU", y: [5, ...level(rb.map((x) => 0.8 * x + 0.001))] };
  const al = alphaLine(a, b);
  assert.equal(al.name, "ALPHA β0.80");
  assert.equal(al.scale, "spread");
  assert.equal(al.y[0], null);
  assert.equal(al.y[1], 0);
  assert.ok(Math.abs(al.y[al.y.length - 1] - rb.length * 0.1) < 1e-9);   // 12 steps × 0.1 point
  assert.equal(alphaLine({ name: "A", y: [1, 2, 3] }, { name: "B", y: [1, 2, 3] }), null);   // under 10 steps
  assert.equal(alphaLine(a, { name: "FLAT", y: a.y.map(() => 0) }), null);                  // no variance: no β
});
