import { test } from "node:test";
import assert from "node:assert/strict";
import { detailText, keepOffline, why } from "../../web/app/api.js";

test("a failed request reads the server's one-line message, else the status", () => {
  assert.equal(detailText({ detail: { error: "UNKNOWN SCREEN NOPE", id: "NOPE" } }, 404), "UNKNOWN SCREEN NOPE");
  assert.equal(detailText({ detail: "Not Found" }, 404), "NOT FOUND");                    // Starlette's own
  assert.equal(detailText({ detail: [{ loc: ["body"], msg: "field required" }] }, 422), "HTTP 422");
  assert.equal(detailText(null, 500), "HTTP 500");
});
test("what the user sees is the message, never the request URL", () => {
  const e = Object.assign(new Error("api/refresh/PORT?tier=heavy: PORT HAS NO TIER 'HEAVY'"), { detail: "PORT HAS NO TIER 'HEAVY'" });
  assert.equal(why(e), "PORT HAS NO TIER 'HEAVY'");
  assert.equal(why(new Error("boom")), "BOOM");
});

test("the offline copy is kept on another device only — never on this Mac's loopback names", () => {
  const nav = () => { const seen = []; return { seen, serviceWorker: { register: (u) => { seen.push(u); return Promise.resolve(); } } }; };
  const ipad = nav();
  assert.equal(keepOffline(ipad, { hostname: "my-mac.tail1234.ts.net" }), true);
  assert.deepEqual(ipad.seen, ["sw.js"]);
  for (const hostname of ["localhost", "127.0.0.1"]) {
    const mac = nav();
    assert.equal(keepOffline(mac, { hostname }), false);
    assert.deepEqual(mac.seen, []);
  }
  assert.equal(keepOffline({}, { hostname: "my-mac.local" }), false);                 // plain http: no service worker
});
