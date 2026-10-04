import { test } from "node:test";
import assert from "node:assert/strict";
import { extent, lin, niceTicks } from "../../web/app/scale.js";

test("extent ignores nulls and pads a flat range", () => {
  assert.deepEqual(extent([3, null, 1, NaN, 2]), [1, 3]);
  assert.deepEqual(extent([5, 5]), [4, 6]);
  assert.equal(extent([null]), null);
});
test("nice ticks cover the domain with round steps", () => {
  assert.deepEqual(niceTicks(0, 10, 5), [0, 2, 4, 6, 8, 10]);
  assert.deepEqual(niceTicks(12.3, 27.9, 4), [15, 20, 25]);
});
test("linear scale maps domain onto range", () => {
  const s = lin([0, 10], [100, 200]);
  assert.equal(s(0), 100);
  assert.equal(s(5), 150);
  assert.equal(s(10), 200);
});
