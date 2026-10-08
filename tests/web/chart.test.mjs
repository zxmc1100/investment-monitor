import { test } from "node:test";
import assert from "node:assert/strict";
import { chartSource, plotData } from "../../web/app/render/chart.js";
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
