import { test } from "node:test";
import assert from "node:assert/strict";
import { cellFmt, followed, startCursor } from "../../web/app/render/table.js";
import { placeLabels } from "../../web/app/render/scatter.js";

const T = { rows: [{ k: "A" }], context: { text: "target ctx" }, follows: "portfolios",
  rows_by_key: { RP: { rows: [{ k: "B" }], context: "rp ctx" }, NOW: { rows: [], context: "no trades" } } };

test("a following table shows the followed key's rows and summary", () => {
  assert.deepEqual(followed(T, "RP"), { rows: [{ k: "B" }], context: "rp ctx" });
  assert.deepEqual(followed(T, "NOW"), { rows: [], context: "no trades" });
});
test("unknown key or a plain table falls back to its own rows and context", () => {
  assert.deepEqual(followed(T, "ZZZ"), { rows: [{ k: "A" }], context: "target ctx" });
  assert.deepEqual(followed({ rows: [{ k: "X" }] }, null), { rows: [{ k: "X" }], context: "" });
});
test("cursor keeps its row, else starts on the hinted row, else the first", () => {
  const rows = [{ id: "HRP" }, { id: "RP" }, { id: "NOW" }];
  assert.equal(startCursor(rows, "id", "NOW", "RP"), "NOW");
  assert.equal(startCursor(rows, "id", null, "RP"), "RP");
  assert.equal(startCursor(rows, "id", "GONE", "MISSING"), "HRP");
  assert.equal(startCursor([], "id", null, "RP"), null);
});
test("numeric row keys (MKT's board #) match the string the DOM hands back", () => {
  const rows = [{ "#": 1 }, { "#": 2 }, { "#": 3 }];
  assert.equal(startCursor(rows, "#", "2", undefined), 2);     // arrow / click → data-key "2"
  assert.equal(startCursor(rows, "#", null, "3"), 3);
  assert.equal(startCursor(rows, "#", "9", undefined), 1);
});
const box = (l, lineH = 11, charW = 6) => ({ x0: l.x, x1: l.x + l.text.length * charW, y0: l.y - lineH + 2, y1: l.y + 2 });
const hit = (a, b) => a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1;
test("marker labels that would overlap are moved apart; a lone label keeps its spot", () => {
  const out = placeLabels([{ x: 100, y: 50, text: "BLSAME" }, { x: 101, y: 51, text: "BLSHARPE" }, { x: 300, y: 50, text: "RP" }], 11, 6);
  assert.deepEqual([out[2].x, out[2].y], [300, 50]);           // far away: untouched
  assert.ok(!hit(box(out[0]), box(out[1])));                    // close: no overlap
});
test("a row's _fmt overrides its column's format", () => {
  const c = { k: "lvl", fmt: "num:2" };
  assert.equal(cellFmt({ lvl: 1.1734, _fmt: { lvl: "num:4" } }, c), "num:4");
  assert.equal(cellFmt({ lvl: 5000 }, c), "num:2");
  assert.equal(cellFmt({ lvl: 1, _fmt: { day: "bp+" } }, c), "num:2");
});
test("frontier tick labels keep the decimals their step needs (2.5 % steps are not rounded to 13 %)", async () => {
  const { tickDecimals } = await import("../../web/app/render/scatter.js");
  assert.equal(tickDecimals([10, 12.5, 15, 17.5]), 1);
  assert.equal(tickDecimals([10, 15, 20]), 0);
  assert.equal(tickDecimals([0.25, 0.5, 0.75]), 2);
  assert.equal(tickDecimals([5]), 0);
});
test("a crowded marker's label stays next to its dot and never overlaps another label", () => {
  // five markers within a few pixels (the frontier's low-risk corner)
  const pts = [["HRP", 60, 160], ["MINVAR", 61, 157], ["RP", 63, 150], ["NOW", 70, 140], ["BLSAME", 72, 136]];
  const out = placeLabels(pts.map(([text, x, y]) => ({ x: x + 6, y: y - 5, px: x, py: y, text })), 11, 6);
  for (const l of out) {                                       // the nearest edge of the label box
    const b = box(l), dx = Math.max(b.x0 - l.px, 0, l.px - b.x1), dy = Math.max(b.y0 - l.py, 0, l.py - b.y1);
    assert.ok(Math.hypot(dx, dy) <= 14, `${l.text} is ${Math.hypot(dx, dy).toFixed(0)} from its dot`);
  }
  for (let i = 0; i < out.length; i++) for (let j = i + 1; j < out.length; j++)
    assert.ok(!hit(box(out[i]), box(out[j])), `${out[i].text} overlaps ${out[j].text}`);
  const dist = (l, px, py) => { const b = box(l); return Math.hypot(Math.max(b.x0 - px, 0, px - b.x1), Math.max(b.y0 - py, 0, py - b.y1)); };
  for (const l of out) for (const o of out) if (o !== l)       // never read as another dot's label
    assert.ok(dist(l, l.px, l.py) <= dist(l, o.px, o.py), `${l.text} sits closer to ${o.text}'s dot`);
});
test("markers whose dots overlap share one label, names top to bottom", async () => {
  const { groupMarkers } = await import("../../web/app/render/scatter.js");
  const g = groupMarkers([{ name: "NOW", px: 84.7, py: 144.4 }, { name: "MINVAR", px: 68.9, py: 156.8 },
    { name: "RP", px: 80.2, py: 145.8 }, { name: "HRP", px: 68.1, py: 161.7 }, { name: "BLSHARPE", px: 167.1, py: 107.7 },
    { name: "BLSAME", px: 84.7, py: 141.4 }], 8);
  assert.deepEqual(g.map((x) => x.members.map((m) => m.name)), [["BLSHARPE"], ["BLSAME", "NOW", "RP"], ["MINVAR", "HRP"]]);
});
