import { test } from "node:test";
import assert from "node:assert/strict";
import { fmt, fmtClock, fmtDate, fmtStamp, timeTicks } from "../../web/app/fmt.js";

test("eur groups thousands with two decimals; negatives put the sign first", () => {
  assert.deepEqual(fmt(24812.4, "eur"), { text: "€24,812.40", cls: "" });
  assert.equal(fmt(-5, "eur").text, "-€5.00");
  assert.equal(fmt(1234.5, "eur:0").text, "€1,235");
});
test("signed formats carry sign and tone", () => {
  assert.deepEqual(fmt(182.314, "eur+"), { text: "+€182.31", cls: "up" });
  assert.deepEqual(fmt(-1.8, "pct+"), { text: "-1.8%", cls: "dn" });
  assert.deepEqual(fmt(0.74, "pct+:2"), { text: "+0.74%", cls: "up" });
});
test("values that round to zero are neutral, never -0.0", () => {
  assert.deepEqual(fmt(-0.04, "pct+"), { text: "0.0%", cls: "" });
  assert.deepEqual(fmt(-0.001, "num:2"), { text: "0.00", cls: "" });
});
test("missing and non-finite values render as an em dash", () => {
  for (const v of [null, undefined, NaN, Infinity]) assert.deepEqual(fmt(v, "eur"), { text: "—", cls: "na" });
});
test("digits argument, integers and plain percent", () => {
  assert.equal(fmt(1234.5678, "num:4").text, "1,234.5678");
  assert.equal(fmt(1234.5, "int").text, "1,235");
  assert.equal(fmt(17.94, "pct").text, "17.9%");
});
test("dates render Bloomberg-style", () => {
  assert.equal(fmtDate("2026-09-29"), "29 SEP 26");
  assert.deepEqual(fmt("2026-01-05", "date"), { text: "05 JAN 26", cls: "dim" });
});
test("side colors buys and sells", () => {
  assert.equal(fmt("BUY", "side").cls, "up");
  assert.equal(fmt("SELL", "side").cls, "dn");
});
test("a non-number under a numeric format falls back to text", () => {
  assert.deepEqual(fmt("TOTAL", "pct"), { text: "TOTAL", cls: "" });
});

test("pct+r reverses the tone: a positive gap is bad", () => {
  assert.deepEqual(fmt(2.5, "pct+r"), { text: "+2.5%", cls: "dn" });
  assert.deepEqual(fmt(-1.0, "pct+r"), { text: "-1.0%", cls: "up" });
});
test("basis points, multiples and held/watched marks", () => {
  assert.deepEqual(fmt(3.24, "bp+"), { text: "+3.2bp", cls: "up" });
  assert.deepEqual(fmt(-12, "bp+:0"), { text: "-12bp", cls: "dn" });
  assert.deepEqual(fmt(2.345, "mult"), { text: "2.3×", cls: "" });
  assert.deepEqual(fmt("H", "mark"), { text: "●", cls: "am" });
  assert.deepEqual(fmt("W", "mark"), { text: "★", cls: "am" });
  assert.deepEqual(fmt("HW", "mark"), { text: "●★", cls: "am" });
});

test("status-bar times: today's HH:MM:SS, older ones DD MON YY HH:MM; the clock DD MON YY HH:MM:SS", () => {
  const now = new Date(2026, 9, 4, 14, 3, 22);
  assert.equal(fmtStamp(new Date(2026, 9, 4, 9, 5, 7).toISOString(), now), "09:05:07");
  assert.equal(fmtStamp(new Date(2026, 9, 3, 17, 45, 1).toISOString(), now), "03 OCT 26 17:45");
  assert.equal(fmtStamp(null, now), "—");
  assert.equal(fmtStamp("garbage", now), "—");
  assert.equal(fmtClock(now), "04 OCT 26 14:03:22");
});
test("chart time ticks: years, then MON YY, then DD MON — never US M/D or mixed case", () => {
  const t = (y, m, d) => new Date(y, m, d).getTime() / 1000;
  assert.deepEqual(timeTicks([t(2024, 0, 1), t(2025, 0, 1)], 365 * 86400), ["2024", "2025"]);
  assert.deepEqual(timeTicks([t(2026, 8, 1), t(2026, 9, 1)], 30 * 86400), ["SEP 26", "OCT 26"]);
  assert.deepEqual(timeTicks([t(2026, 9, 1), t(2026, 9, 8)], 7 * 86400), ["01 OCT", "08 OCT"]);
});
