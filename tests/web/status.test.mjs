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
