import { test } from "node:test";
import assert from "node:assert/strict";
import { kpi, yearsTable } from "../../web/app/render/kpi.js";

const heads = (html) => [...html.matchAll(/<th[^>]*>([^<]*)<\/th>/g)].map((m) => m[1]);

test("the year table: first and last day's value, growth, time-weighted, the gain", () => {
  const html = yearsTable([{ label: "2025", first: 1000, end: 1200, v: 29.8, v2: 22.3, gain: 150, fmt: "pct+" }]);
  assert.deepEqual(heads(html), ["YEAR", "FIRST DAY €", "LAST DAY €", "GROWTH", "TIME-WEIGHTED", "GAIN €"]);
  assert.ok(html.includes("+29.8%") && html.includes("+22.3%"));
  assert.ok(!html.includes("YOUR METHOD") && !html.includes("CHANGE"));
});
test("public: growth and time-weighted, no euros", () => {
  assert.deepEqual(heads(yearsTable([{ label: "2025", v: 29.8, v2: 22.3, fmt: "pct+" }])), ["YEAR", "GROWTH", "TIME-WEIGHTED"]);
});
test("a € + % pair (`eur`): one cell two columns wide, € then (%); public (no `eur`): the % alone", () => {
  const body = () => ({ classList: { add() {} }, style: {}, innerHTML: "" });
  const b = body();
  kpi(b, { id: "s", items: [{ k: "DAY", v: 0.59, fmt: "pct+:2", eur: 54.22, size: "big" }] });
  assert.match(b.innerHTML, /class="kv duo big"/);
  assert.match(b.innerHTML, /<span class="up">\+€54\.22<\/span><span class="up">\(\+0\.59%\)<\/span>/);
  const pub = body();
  kpi(pub, { id: "s", items: [{ k: "DAY", v: 0.59, fmt: "pct+:2" }] });
  assert.ok(!pub.innerHTML.includes("duo") && pub.innerHTML.includes(">+0.59%<"));
});
