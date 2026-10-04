import { test } from "node:test";
import assert from "node:assert/strict";
import { cellValues, changed } from "../../web/app/diff.js";

const pay = (value, wt, roi) => ({
  panels: [
    { id: "summary", type: "kpi", items: [{ k: "VALUE", v: value }, { k: "ROI", v: roi }] },
    { id: "positions", type: "table", key: "tkr",
      cols: [{ k: "tkr" }, { k: "wt" }, { k: "p1y", fmt: "spark" }],
      rows: [{ tkr: "SAP.DE", wt, p1y: [1, wt] }], total: { tkr: "TOTAL", wt: 100 } },
    { id: "acct", type: "ledger", lines: [{ label: "Gross", v: 10 }, { sep: true }] },
    { id: "alloc", type: "bars", items: [{ label: "Tech", v: 40 }] },
  ],
});

test("keys cover kpi, table rows, totals, ledger and bars; sparks excluded", () => {
  assert.deepEqual([...cellValues(pay(1, 2, 3)).keys()].sort(), [
    "acct|Gross|v", "alloc|Tech|v", "positions|SAP.DE|tkr", "positions|SAP.DE|wt",
    "positions|__total__|tkr", "positions|__total__|wt", "summary|ROI|v", "summary|VALUE|v",
  ]);
});
test("only changed existing cells are flagged", () => {
  assert.deepEqual([...changed(pay(1, 2, 3), pay(1.5, 2, 3))], ["summary|VALUE|v"]);
  assert.deepEqual([...changed(pay(1, 2, 3), pay(1, 7, 3))], ["positions|SAP.DE|wt"]);
  assert.equal(changed(pay(1, 2, 3), pay(1 + 1e-12, 2, 3)).size, 0);
});
test("first paint and null transitions", () => {
  assert.equal(changed(null, pay(1, 2, 3)).size, 0);
  assert.deepEqual([...changed(pay(1, 2, null), pay(1, 2, 3))], ["summary|ROI|v"]);
});

test("heatmap cells are flashable by row and column label", () => {
  const h = (v) => ({ panels: [{ id: "corr", type: "heatmap", labels: ["A", "B"], cells: [[1, v], [v, 1]] }] });
  assert.deepEqual([...changed(h(0.5), h(0.6))].sort(), ["corr|A|B", "corr|B|A"]);
});
