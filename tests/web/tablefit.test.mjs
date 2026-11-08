import { test } from "node:test";
import assert from "node:assert/strict";
import { isTextCol, pickCols, pickSort } from "../../web/app/render/table.js";

test("only free-text columns shrink and ellipsize; numbers, tickers, dates and sparks keep their width", () => {
  assert.equal(isTextCol({ k: "name", fmt: "text" }), true);
  assert.equal(isTextCol({ k: "x" }), true);                       // no fmt = text
  for (const fmt of ["pct+", "num:2", "eur", "int", "mult", "bp+", "tkr", "date", "mark", "side", "spark"])
    assert.equal(isTextCol({ k: "x", fmt }), false, fmt);
});

test("a total row's blank cell keeps its column's classes: a text column never turns into a width:1% column", async () => {
  const { blankCell, colClass } = await import("../../web/app/render/table.js");
  assert.equal(blankCell({ k: "name", fmt: "text" }), '<td class="tx"></td>');
  assert.equal(blankCell({ k: "sector" }), '<td class="tx"></td>');
  assert.equal(blankCell({ k: "etf", fmt: "tkr", lo: true }), '<td class="lo"></td>');   // hides with its column
  assert.equal(blankCell({ k: "wt", fmt: "pct", align: "r" }), '<td class="r"></td>');
  assert.equal(colClass({ k: "x", fmt: "text", align: "r", hl: true, lo: true }), "r hl tx lo");
});

test("several text columns share the free width by how much text each holds, not equally", async () => {
  const { textShares } = await import("../../web/app/render/table.js");
  const cols = [{ k: "id", fmt: "tkr" }, { k: "text", label: "RULE", fmt: "text" }, { k: "state", label: "STATE", fmt: "text" },
                { k: "last", label: "LAST FIRED", fmt: "text" }, { k: "v", label: "V", fmt: "num:2" }];
  const rows = [{ id: "A1", text: "PORT DAY ≤ -2% AND A LONG TAIL OF WORDS", state: "ARMED", last: "—", v: 1 },
                { id: "A2", text: "PORT DRIFT ≥ 10pp", state: "FIRED", last: "03 OCT 19:25", v: 2 }];
  const s = textShares(cols, rows);
  assert.deepEqual(Object.keys(s), ["text", "state", "last"]);                 // text columns only
  assert.ok(s.text > s.last && s.last > s.state, JSON.stringify(s));            // longest text, widest share
  assert.ok(Math.abs(s.text + s.state + s.last - 100) < 0.5);
  assert.deepEqual(textShares(cols.slice(0, 2), rows), {});                      // one text column takes the rest by itself
});

test("period chips: a table shows the chosen period's columns, and a sort on one moves to its twin", () => {
  const cols = [{ k: "tkr" }, { k: "day", pick: "DAY" }, { k: "daye", pick: "DAY" }, { k: "w1", pick: "1W" },
    { k: "w1e", pick: "1W" }, { k: "wt" }];
  assert.deepEqual(pickCols(cols, "1W").map((c) => c.k), ["tkr", "w1", "w1e", "wt"]);
  assert.equal(pickSort(cols, "daye", "DAY", "1W"), "w1e");
  assert.equal(pickSort(cols, "wt", "DAY", "1W"), "wt");                 // not a period column: stays
});

test("a fit table's pages: what fits whole per page, the page the cursor is on — never cut", async () => {
  const { pageOf } = await import("../../web/app/render/table.js");
  assert.deepEqual(pageOf(40, 26, 0), { per: 26, pages: 2, page: 0 });
  assert.deepEqual(pageOf(40, 26, 26), { per: 26, pages: 2, page: 1 });    // row 27 is on page 2
  assert.deepEqual(pageOf(40, 26, 39), { per: 26, pages: 2, page: 1 });
  assert.deepEqual(pageOf(10, 26, 3), { per: 26, pages: 1, page: 0 });     // it all fits: one page, no chips
  assert.deepEqual(pageOf(40, 0, -1), { per: 1, pages: 40, page: 0 });     // no room measured yet, no cursor
});
