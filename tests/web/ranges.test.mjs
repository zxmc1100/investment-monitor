import { test } from "node:test";
import assert from "node:assert/strict";
import { canNorm, lastValue, normalize, rangeEnd, rangeStart, sliceFrom, valueAt } from "../../web/app/ranges.js";

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

test("a dragged range is its own start and end; a named one ends at the last point", () => {
  const drag = { from: END - 20 * DAY, to: END - 10 * DAY, prev: "YTD" };
  assert.equal(rangeStart(XS, drag), END - 20 * DAY);
  assert.equal(rangeEnd(drag), END - 10 * DAY);
  assert.equal(rangeEnd("1M"), Infinity);
  const out = sliceFrom(XS, [{ y: XS.map((_, i) => i) }], rangeStart(XS, drag), rangeEnd(drag));
  assert.deepEqual(out.series[0].y, [379, 380, 381, 382, 383, 384, 385, 386, 387, 388, 389]);
});
test("anchored, a window opens on the last close before it — what a period's return is measured from", () => {
  const ys = [{ y: XS.map((_, i) => i) }];
  assert.equal(sliceFrom(XS, ys, END - 9 * DAY, Infinity, true).series[0].y[0], 389);
  assert.equal(sliceFrom(XS, ys, END - 9.5 * DAY, Infinity, true).series[0].y[0], 389);
  assert.equal(sliceFrom(XS, ys, -Infinity, Infinity, true).x.length, 400);          // ALL: nothing before it
  assert.equal(sliceFrom(XS, ys, END + DAY, Infinity, true).x.length, 2);            // never fewer than two
});

test("normalize draws each line's time-weighted growth from 0 at its first point in the window", () => {
  const out = normalize([
    { name: "YOU", y: [30, 20, 40], twr: [1.2, 1.32, 1.5] },
    { name: "LATE", y: [null, 5, 6], twr: [null, 2, 2.2] },
    { name: "GAP", y: [1, null, 3], twr: [0.5, null, 0.4] },
  ]);
  const near = (a, b) => a.forEach((v, i) => (b[i] === null ? assert.equal(v, null) : assert.ok(Math.abs(v - b[i]) < 1e-9, `${v} vs ${b[i]}`)));
  near(out[0].y, [0, 10, 25]);
  near(out[1].y, [null, 0, 10]);
  near(out[2].y, [0, null, -20]);
  assert.equal(out[0].name, "YOU");
});
test("a window with nothing before it (ALL) starts before the first money: the first day's fee and move count", () => {
  const xs = [10, 20, 30], ys = [{ y: [-1, 5, 9], twr: [0.99, 1.05, 1.1] }];
  const all = sliceFrom(xs, ys, -Infinity, Infinity, true);
  assert.equal(all.start, 0);
  const near = (a, b) => a.forEach((v, i) => assert.ok(Math.abs(v - b[i]) < 1e-9, `${v} vs ${b[i]}`));
  near(normalize(all.series, true)[0].y, [-1, 5, 10]);                 // growth of 1 € before the first trade
  const later = sliceFrom(xs, ys, 25, Infinity, true);                 // anchored on x = 20: a close before it
  assert.equal(later.start, 1);
  near(normalize(later.series, later.start === 0)[0].y, [0, (1.1 / 1.05 - 1) * 100]);
});
test("a line without a time-weighted curve cannot be normalized honestly: it is left out, and NORM is only offered when every line has one", () => {
  assert.deepEqual(normalize([{ y: [1, 2] }])[0].y, [null, null]);
  assert.equal(canNorm([{ y: [1], twr: [1] }, { kind: "markers", y: [1] }]), true);
  assert.equal(canNorm([{ y: [1], twr: [1] }, { y: [2] }]), false);
  assert.equal(canNorm([]), false);
});

test("valueAt reads the cursor index, falling back to the last value before a gap", () => {
  const y = [1, 2, null, 4, null];
  assert.equal(valueAt(y, 1), 2);
  assert.equal(valueAt(y, 2), 2);
  assert.equal(valueAt(y, null), 4);
  assert.equal(valueAt(y, 99), 4);
  assert.equal(valueAt([null, null], 1), null);
});
