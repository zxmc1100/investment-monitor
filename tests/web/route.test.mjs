import { test } from "node:test";
import assert from "node:assert/strict";
import { changesHash, hashOf, keyOf, popBack, pushBack, routeOf } from "../../web/app/route.js";

test("plain and parametrized hashes", () => {
  assert.deepEqual(routeOf("#/PORT"), { id: "PORT", param: null });
  assert.deepEqual(routeOf("#/sec/sap.de"), { id: "SEC", param: "SAP.DE" });
  assert.deepEqual(routeOf(""), { id: "PORT", param: null });
  assert.deepEqual(routeOf("#/"), { id: "PORT", param: null });
});
test("hash and key round-trip", () => {
  assert.equal(hashOf({ id: "SEC", param: "ASML.AS" }), "#/SEC/ASML.AS");
  assert.equal(hashOf({ id: "OPT", param: null }), "#/OPT");
  assert.equal(keyOf({ id: "SEC", param: "SAP.DE" }), "SEC~SAP.DE");
  assert.equal(keyOf({ id: "PORT", param: null }), "PORT");
  assert.deepEqual(routeOf(hashOf({ id: "SEC", param: "SAP.DE" })), { id: "SEC", param: "SAP.DE" });
});
test("a malformed escape falls back to the raw parameter instead of throwing", () => {
  assert.deepEqual(routeOf("#/SEC/%E0"), { id: "SEC", param: "%E0" });
  assert.deepEqual(routeOf("#/sec/abc%zz"), { id: "SEC", param: "ABC%ZZ" });
});
test("back stack: A -> B -> C, Esc, Esc returns to A, then PORT", () => {
  let st = [];
  st = pushBack(st, "#/SEC/A");                 // A -> B
  st = pushBack(st, "#/SEC/B");                 // B -> C
  let r = popBack(st, "#/SEC/C");
  assert.equal(r.to, "#/SEC/B");
  r = popBack(r.stack, r.to);
  assert.equal(r.to, "#/SEC/A");
  r = popBack(r.stack, r.to);
  assert.equal(r.to, "#/PORT");
  assert.deepEqual(r.stack, []);
});
test("back stack: SEC/A -> SEC/B -> Esc -> Esc ends on the screen before SEC/A", () => {
  let st = pushBack([], "#/RISK");              // RISK -> SEC/A
  st = pushBack(st, "#/SEC/A");                 // SEC/A -> SEC/B
  let r = popBack(st, "#/SEC/B");
  assert.equal(r.to, "#/SEC/A");
  r = popBack(r.stack, r.to);
  assert.equal(r.to, "#/RISK");
});
test("back stack: duplicates collapse, the current route is never a target, size is bounded", () => {
  let st = [];
  for (const h of ["#/PORT", "#/SEC/A", "#/SEC/B", "#/SEC/A", "#/SEC/B", "#/SEC/A"]) st = pushBack(st, h);
  assert.deepEqual(st, ["#/PORT", "#/SEC/B", "#/SEC/A"]);
  const r = popBack(st, "#/SEC/A");             // on SEC/A: skip it, go to SEC/B
  assert.equal(r.to, "#/SEC/B");
  assert.deepEqual(r.stack, ["#/PORT"]);
  assert.deepEqual(pushBack(["#/PORT"], ""), ["#/PORT"]);
  let big = [];
  for (let i = 0; i < 100; i++) big = pushBack(big, `#/SEC/T${i}`);
  assert.equal(big.length, 20);
  assert.equal(big.at(-1), "#/SEC/T99");
});

test("a step back to the hash we are already on fires no hashchange, so it must not arm backNav", () => {
  assert.equal(changesHash("#/SEC/X", "#/PORT"), true);
  assert.equal(changesHash("#/PORT", "#/PORT"), false);
});
