import { test } from "node:test";
import assert from "node:assert/strict";
import { barClick, keyAt } from "../../web/app/tabbar.js";

test("the open screen's key closes it; any other key opens as before", () => {
  const at = (run, current, more = {}) => barClick({ run, current, overlay: false, max: false, ...more });
  assert.equal(at("OPT", "PORT"), "open");
  assert.equal(at("OPT", "OPT"), "back");
  assert.equal(at("PORT", "PORT"), null);                          // home stays
  assert.equal(at("OPT", "OPT", { max: true }), "escape");          // a maximized panel closes first
  assert.equal(at("PORT", "PORT", { overlay: true }), "escape");
  assert.equal(at("HELP", "OPT"), "open");
  assert.equal(at("HELP", "OPT", { overlay: true }), "escape");     // HELP again closes help
  assert.equal(at("HELP", "OPT", { max: true }), "open");
});

test("a finger picks the key under it, or the nearest past either end or in a gap", () => {
  const boxes = [{ left: 10, right: 60 }, { left: 62, right: 120 }, { left: 122, right: 180 }];
  assert.equal(keyAt(boxes, 30), 0);
  assert.equal(keyAt(boxes, 100), 1);
  assert.equal(keyAt(boxes, 121.5), 2);
  assert.equal(keyAt(boxes, 0), 0);
  assert.equal(keyAt(boxes, 400), 2);
  assert.equal(keyAt([], 50), -1);
});
