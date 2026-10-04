import { test } from "node:test";
import assert from "node:assert/strict";
import { detailText, why } from "../../web/app/api.js";

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
