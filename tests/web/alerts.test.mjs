import { test } from "node:test";
import assert from "node:assert/strict";
import { badge, noticeText, summary } from "../../web/app/alerts.js";

test("badge: none, amber, red when any active alert points down", () => {
  assert.equal(badge(0, false), null);
  assert.deepEqual(badge(1, false), { text: "▲ 1 ALERT", cls: "am" });
  assert.deepEqual(badge(3, true), { text: "▲ 3 ALERTS", cls: "dn" });
});
test("summary of GET /api/alerts", () => {
  assert.deepEqual(summary({ active: [{ down: false }, { down: true }] }), { active: 2, down: true });
  assert.deepEqual(summary(null), { active: 0, down: false });
});
test("one notice per batch", () => {
  assert.equal(noticeText([]), null);
  assert.equal(noticeText([{ id: "E3", msg: "SAP.DE 172.40 < 180" }]), "ALERT E3 · SAP.DE 172.40 < 180");
  assert.equal(noticeText([{ id: "E3", msg: "A" }, { id: "E4", msg: "B" }]), "2 NEW ALERTS · A · ALRT");   // only F1 is an F-key
});
