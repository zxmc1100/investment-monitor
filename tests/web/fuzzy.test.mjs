import { test } from "node:test";
import assert from "node:assert/strict";
import { rank, score } from "../../web/app/fuzzy.js";

test("exact beats prefix beats substring beats subsequence; misses are -Infinity", () => {
  assert.ok(score("PORT", "PORT") > score("POR", "PORT"));
  assert.ok(score("POR", "PORT") > score("ORT", "PORT"));
  assert.ok(score("ORT", "PORT") > score("PT", "PORT"));
  assert.equal(score("XYZ", "PORT"), -Infinity);
  assert.equal(score("", "PORT"), -Infinity);
});
const ITEMS = [
  { label: "MKT", desc: "Market Overview" },
  { label: "PORT", desc: "Portfolio Monitor" },
  { label: "SAP.DE", desc: "SAP SE · Software" },
];
test("labels outrank descriptions; non-matches are dropped", () => {
  assert.deepEqual(rank("po", ITEMS, (c) => [c.label, c.desc]).map((c) => c.label), ["PORT", "SAP.DE"]);
  assert.deepEqual(rank("sap", ITEMS, (c) => [c.label, c.desc]).map((c) => c.label), ["SAP.DE"]);
  assert.deepEqual(rank("", ITEMS), []);
});
test("limit caps results and ties keep input order", () => {
  const many = Array.from({ length: 20 }, (_, i) => ({ label: `A${i}` }));
  const out = rank("A", many, (c) => [c.label], 5);
  assert.equal(out.length, 5);
  assert.deepEqual(out.map((c) => c.label), ["A0", "A1", "A2", "A3", "A4"]);
});
