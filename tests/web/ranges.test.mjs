import { test } from "node:test";
import assert from "node:assert/strict";
import { lastValue, rangeStart, sliceFrom, valueAt } from "../../web/app/ranges.js";

const DAY = 86400;
const END = Date.UTC(2026, 5, 30) / 1000;                 // 2026-06-30
const XS = Array.from({ length: 400 }, (_, i) => END - (399 - i) * DAY);

test("range starts", () => {
  assert.equal(rangeStart(XS, "1M"), END - 31 * DAY);
  assert.equal(rangeStart(XS, "YTD"), Date.UTC(2026, 0, 1) / 1000);
  assert.equal(rangeStart(XS, "ALL"), -Infinity);
  assert.equal(rangeStart([], "1M"), -Infinity);
});
test("sliceFrom trims x and every series together", () => {
  const out = sliceFrom(XS, [{ name: "A", y: XS.map((_, i) => i) }], END - 9 * DAY);
  assert.equal(out.x.length, 10);
  assert.deepEqual(out.series[0].y, [390, 391, 392, 393, 394, 395, 396, 397, 398, 399]);
  assert.equal(out.series[0].name, "A");
});
test("sliceFrom always keeps two points", () => {
  assert.equal(sliceFrom([1, 2, 3], [{ y: [1, 2, 3] }], 99).x.length, 2);
});
test("lastValue skips trailing nulls", () => {
  assert.equal(lastValue([1, 2, null, null]), 2);
  assert.equal(lastValue([null]), null);
});

test("valueAt reads the cursor index, falling back to the last value before a gap", () => {
  const y = [1, 2, null, 4, null];
  assert.equal(valueAt(y, 1), 2);
  assert.equal(valueAt(y, 2), 2);
  assert.equal(valueAt(y, null), 4);
  assert.equal(valueAt(y, 99), 4);
  assert.equal(valueAt([null, null], 1), null);
});
