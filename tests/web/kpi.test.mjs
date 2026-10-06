import { test } from "node:test";
import assert from "node:assert/strict";
import { yearsTable } from "../../web/app/render/kpi.js";

const heads = (html) => [...html.matchAll(/<th[^>]*>([^<]*)<\/th>/g)].map((m) => m[1]);

test("the year table: first and last day's value, the change, time-weighted, the gain", () => {
  const html = yearsTable([{ label: "2025", first: 1000, end: 1200, chg: 20, v: 22.3, gain: 150, fmt: "pct+" }]);
  assert.deepEqual(heads(html), ["YEAR", "FIRST DAY €", "LAST DAY €", "CHANGE", "TIME-WEIGHTED", "GAIN €"]);
  assert.ok(html.includes("+20.0%") && html.includes("+22.3%"));
  assert.ok(!html.includes("YOUR METHOD"));
});
test("public: the time-weighted return alone", () => {
  assert.deepEqual(heads(yearsTable([{ label: "2025", v: 22.3, fmt: "pct+" }])), ["YEAR", "TIME-WEIGHTED"]);
});
