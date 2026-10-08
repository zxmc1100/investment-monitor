import { test } from "node:test";
import assert from "node:assert/strict";
import { canNorm, chartModes, cutHorizon, expandAt, HORIZON_MONTHS, lastValue, normalize, rangeEnd, rangeStart, sliceFrom, valueAt, windowMwr, windowRoi } from "../../web/app/ranges.js";

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

const nearly = (a, b) => a.forEach((v, i) => (b[i] === null ? assert.equal(v, null)
  : assert.ok(Math.abs(v - b[i]) < 1e-9, `${v} vs ${b[i]}`)));

test("a window's ROI is the ROI formula on it: the gain since its first point over the money in it then", () => {
  // you: 1000 € in, worth 1100 (ROI +10 %); then 500 € more bought, 100 € sold, worth 1600 (ROI (1600+100)/1500−1)
  const inv = [1000, 1500], you = { role: "primary", y: [10, (1700 / 1500 - 1) * 100], cash: [0, 100] };
  // window from the first point: (1600 + 100 − 0) / (1100 + 500) − 1 — the 1100 held as if bought then
  nearly(windowRoi([you], inv)[0].y, [0, (1700 / 1600 - 1) * 100]);
  // a benchmark (no cash): worth 1100 then, 1500 € in → worth 1650: (1650) / (1100 + 500) − 1
  const bench = { role: "bench", y: [10, 10] };
  nearly(windowRoi([bench], inv)[0].y, [0, (1650 / 1600 - 1) * 100]);
  // money taken out before the window does not count in it
  const sold = { role: "primary", y: [20, 30], cash: [200, 200] };          // 1000 in: worth 1000+… cash 200 already out
  nearly(windowRoi([sold], [1000, 1000])[0].y, [0, (1300 - 200) / (1200 - 200) * 100 - 100]);
});

test("a window with nothing before it (inception) is the ROI itself; a line without money in has none", () => {
  const you = { role: "primary", y: [5, 7], cash: [0, 0] };
  assert.deepEqual(windowRoi([you], [100, 200], true)[0].y, [5, 7]);
  assert.deepEqual(windowRoi([{ role: "bench", y: [null, 3] }], [100, 200])[0].y, [null, null]);
});

test("a window's money-weighted return (Modified Dietz): the gain over the money at work, each euro for the share of the window it was in", () => {
  const D = 86400;
  // the close before the window (day −1): 1000 € in, worth 1100. Day 10: 500 € bought (worth 1620 after).
  // Day 30: 100 € out (a sale), worth 1650. The window opens on day 0.
  const xs = [-1 * D, 10 * D, 30 * D], inv = [1000, 1500, 1500];
  const you = { role: "primary", y: [10, (1620 - 1500) / 1500 * 100, (1650 + 100 - 1500) / 1500 * 100], cash: [0, 0, 100] };
  // day 10: gain 20 over 1100 (the buy that day has had no time); day 30: gain 150 over 1100 + 500·20/30
  nearly(windowMwr([you], inv, xs, 0)[0].y, [0, 20 / 1100 * 100, 150 / (1100 + 500 * 20 / 30) * 100]);
  // a benchmark: the same money in, nothing out — worth 1100, then 1650 after the buy, then 1800
  const bench = { role: "bench", y: [10, (1650 / 1500 - 1) * 100, (1800 / 1500 - 1) * 100] };
  nearly(windowMwr([bench], inv, xs, 0)[0].y, [0, 50 / 1100 * 100, 200 / (1100 + 500 * 20 / 30) * 100]);
});

test("money-weighted from the first trade (inception): nothing held before, the first day's buys count in full", () => {
  const D = 86400, xs = [0, 10 * D], inv = [1000, 1000];
  const you = { role: "primary", y: [-1, 5], cash: [0, 0] };
  nearly(windowMwr([you], inv, xs, 0, true)[0].y, [-1, 5]);
});

test("the views a chart offers: ROI and MWR need the money put in, TWR the time-weighted curves", () => {
  const twr = [{ y: [1], twr: [1] }], plain = [{ y: [1] }];
  assert.deepEqual(chartModes({ inv: [1] }, twr), ["ROI", "MWR", "TWR"]);
  assert.deepEqual(chartModes({ inv: [1] }, plain), ["ROI", "MWR"]);
  assert.deepEqual(chartModes({}, twr), ["ROI", "TWR"]);
  assert.deepEqual(chartModes({}, plain), []);              // a public snapshot: none of it, no chips
});

test("a horizon cuts the future after today: today's index plus its months, every array alike", () => {
  const x = [1, 2, 3, 4, 5, 6], s = [{ y: [1, 2, 3, null, null, null] }, { y: [null, null, 3, 4, 5, 6], lo: [0, 0, 1, 2, 3, 4], hi: [9, 9, 5, 6, 7, 8] }];
  const out = cutHorizon(x, s, 2, 2);
  assert.deepEqual(out.x, [1, 2, 3, 4, 5]);
  assert.deepEqual(out.series[1].hi, [9, 9, 5, 6, 7]);
  assert.deepEqual(out.series[0].y, [1, 2, 3, null, null]);
  assert.equal(HORIZON_MONTHS["3Y"], 36);
  assert.deepEqual(cutHorizon(x, s, null, 2).x, x);                       // a chart without a future: unchanged
});

test("a future series sent from index `at` is padded to the chart's x; one without `at` is left as it is", () => {
  const s = expandAt([{ y: [1, 2] }, { at: 2, y: [5, 6] }, { kind: "band", at: 3, lo: [1, 2], hi: [3, 4] }], 5);
  assert.deepEqual(s[0].y, [1, 2]);
  assert.deepEqual(s[1].y, [null, null, 5, 6, null]);
  assert.deepEqual(s[2].lo, [null, null, null, 1, 2]);
  assert.deepEqual(s[2].hi, [null, null, null, 3, 4]);
});

test("a period slices a band's lo / hi with its x", () => {
  const out = sliceFrom([1, 2, 3, 4], [{ kind: "band", lo: [0, 1, 2, 3], hi: [5, 6, 7, 8] }], 2, 3);
  assert.deepEqual(out.x, [2, 3]);
  assert.deepEqual(out.series[0].lo, [1, 2]);
  assert.deepEqual(out.series[0].hi, [6, 7]);
});
