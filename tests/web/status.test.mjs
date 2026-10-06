import { test } from "node:test";
import assert from "node:assert/strict";
import { metaBadges, warnBadges } from "../../web/app/status.js";

test("no meta, no badges", () => {
  assert.deepEqual(metaBadges(undefined), []);
  assert.deepEqual(metaBadges({}), []);
});
test("never-quoted and stale lines", () => {
  assert.deepEqual(metaBadges({ stale: { "SAP.DE": null, "ALV.DE": "2026-10-01", "SIE.DE": "2026-10-02" } }).map((b) => b.text),
    ["NO PRICE SAP.DE", "STALE 2 ALV.DE SIE.DE"]);
});
test("currency warnings: ready lines, or {ticker: currency} grouped per currency, in the dn style", () => {
  assert.deepEqual(warnBadges(["CCY USD: AAPL"]).map(({ cls, text }) => ({ cls, text })), [{ cls: "dn", text: "CCY USD: AAPL" }]);
  assert.deepEqual(warnBadges({ MSFT: "USD", AAPL: "usd", "7203.T": "JPY" }).map((b) => b.text), ["CCY JPY: 7203.T", "CCY USD: AAPL MSFT"]);
  assert.deepEqual(warnBadges({ ccy: { AAPL: "USD" } }).map((b) => b.text), ["CCY USD: AAPL"]);
  for (const none of [undefined, null, [], {}, "x", 3, [null, 2]]) assert.deepEqual(warnBadges(none), [], String(none));
  assert.deepEqual(metaBadges({ stale: { "SAP.DE": null }, warn: { AAPL: "USD" } }).map((b) => b.text), ["NO PRICE SAP.DE", "CCY USD: AAPL"]);
});

test("the status line fits its room by dropping whole items, least important first; the most important always stay", async () => {
  const { fitHidden } = await import("../../web/app/status.js");
  // ALERT(90) Q(20) D(20) CODE(30) LIVE(100) CLOCK(10), gap 12
  const items = [{ p: 90, w: 60 }, { p: 20, w: 110 }, { p: 20, w: 110 }, { p: 30, w: 180 }, { p: 100, w: 50 }, { p: 10, w: 120 }];
  const all = 60 + 110 + 110 + 180 + 50 + 120 + 5 * 12;
  assert.deepEqual([...fitHidden(items, all, 12)], []);                        // fits: nothing hidden
  assert.deepEqual([...fitHidden(items, all - 1, 12)].sort(), [5]);            // the clock goes first
  assert.deepEqual([...fitHidden(items, 300, 12)].sort(), [2, 3, 5]);          // CODE CHANGED, D go; Q fits back
  assert.deepEqual([...fitHidden(items, 10, 12)].sort(), [1, 2, 3, 5]);        // ALERT and LIVE never drop
});
