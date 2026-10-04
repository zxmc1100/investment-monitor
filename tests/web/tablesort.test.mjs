import { test } from "node:test";
import assert from "node:assert/strict";
import { nextSortCol, sortRows } from "../../web/app/tablesort.js";

const ROWS = [{ t: "A", v: 2 }, { t: "B", v: null }, { t: "C", v: 5 }, { t: "D", v: NaN }, { t: "E", v: -1 }, { t: "F" }];

test("descending puts nulls, NaN and missing last, stable among them", () => {
  assert.deepEqual(sortRows(ROWS, "v", "desc").map((r) => r.t), ["C", "A", "E", "B", "D", "F"]);
});
test("ascending also puts them last", () => {
  assert.deepEqual(sortRows(ROWS, "v", "asc").map((r) => r.t), ["E", "A", "C", "B", "D", "F"]);
});
test("strings compare case-insensitively and input is not mutated", () => {
  const rows = [{ t: "b" }, { t: "A" }, { t: "c" }];
  assert.deepEqual(sortRows(rows, "t", "asc").map((r) => r.t), ["A", "b", "c"]);
  assert.deepEqual(rows.map((r) => r.t), ["b", "A", "c"]);
});
test("nextSortCol skips sparklines and wraps", () => {
  const cols = [{ k: "tkr" }, { k: "wt" }, { k: "p1y", fmt: "spark" }];
  assert.equal(nextSortCol(cols, "tkr", 1), "wt");
  assert.equal(nextSortCol(cols, "wt", 1), "tkr");
  assert.equal(nextSortCol(cols, "tkr", -1), "wt");
});
test("nextSortCol skips columns hidden in a narrow panel (lo)", () => {
  const cols = [{ k: "sector" }, { k: "wt" }, { k: "etf", lo: true }, { k: "day" }];
  const hidden = new Set(["etf"]);
  assert.equal(nextSortCol(cols, "wt", 1, hidden), "day");
  assert.equal(nextSortCol(cols, "day", -1, hidden), "wt");
  assert.equal(nextSortCol(cols, "wt", 1), "etf");                       // shown: still reachable
  assert.equal(nextSortCol(cols, "day", 1, hidden), "sector");           // wraps over the hidden one
});
