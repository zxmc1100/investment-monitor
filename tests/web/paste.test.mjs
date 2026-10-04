import { test } from "node:test";
import assert from "node:assert/strict";
import { previewTable } from "../../web/app/render/paste.js";

test("the paste preview: each row as it will be stored, or its raw text and why not; warnings stand out", () => {
  const html = previewTable({ rows: [
    { line: 2, text: "2025-01-15,SAP.DE,buy,4,961", trade: { date: "2025-01-15", ticker: "SAP.DE", action: "buy", shares: 4, pps: 240.25, price: 961 },
      error: null, warnings: [] },
    { line: 3, text: "2025-01-16,X.F,hold,1,1", trade: null, error: "UNKNOWN ACTION 'HOLD' — BUY, SELL OR BONUS", warnings: [] },
    { line: 4, text: "x", trade: { date: "2025-01-17", ticker: "Y.F", action: "sell", shares: 1, pps: 9, price: 9 }, error: null,
      warnings: ["SAME AS LINE 2"] }] });
  const rows = html.split("<tr").slice(2);
  assert.match(rows[0], /^ class="">.*15 JAN 25.*SAP\.DE.*BUY.*240\.25.*€961\.00/);
  assert.match(rows[1], /^ class="dn">.*✗.*2025-01-16,X\.F,hold,1,1.*UNKNOWN ACTION &#39;HOLD&#39;/);
  assert.match(rows[2], /^ class="hot">.*SELL.*SAME AS LINE 2/);
  assert.ok(!html.includes("<script"));
});
