import { test } from "node:test";
import assert from "node:assert/strict";
import { viewOf } from "../../web/app/render/index.js";

test("a panel with views is the view its chips chose — the default, else the first — keeping its chips", () => {
  const views = { VALUE: { id: "book", type: "chart", follows: "positions" }, ACTIVITY: { id: "book", type: "table" } };
  const p = { id: "book", n: 4, picks: ["VALUE", "ACTIVITY"], pick: "VALUE", views };
  const a = viewOf(p, "ACTIVITY");
  assert.equal(a.type, "table");
  assert.equal(a.follows, undefined);                       // the chosen view's own keys only
  assert.deepEqual([a.picks, a.pick, a.views], [p.picks, "VALUE", views]);
  assert.equal(viewOf(a, "VALUE").follows, "positions");    // resolved again from a resolved one
  assert.equal(viewOf(p, "GONE").type, "chart");            // an unknown pick: the default
  const plain = { id: "x", type: "kpi" };
  assert.equal(viewOf(plain, "ANY"), plain);
});
