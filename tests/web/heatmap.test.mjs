import { test } from "node:test";
import assert from "node:assert/strict";
import { heatColor } from "../../web/app/render/heatmap.js";

test("diverging colour: green positive, red negative, transparent missing", () => {
  assert.match(heatColor(1), /^rgba\(0,230,118,0\.85\)$/);
  assert.match(heatColor(-0.5), /^rgba\(255,61,61,0\.43\)$/);
  assert.equal(heatColor(null), "transparent");
});
