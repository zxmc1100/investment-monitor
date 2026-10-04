import { test } from "node:test";
import assert from "node:assert/strict";
import { buildBadges, buildRequest, buildsFromJobs, mergeBuilds, trackBuild } from "../../web/app/build.js";

const ev = (state, extra = {}) => ({ type: "job", screen: "MODEL", tier: "heavy", force: true, state, ...extra });

test("a forced heavy job shows as BUILD with its stage, then clears when done", () => {
  let b = trackBuild({}, ev("queued"));
  assert.deepEqual(buildBadges(b), [{ cls: "am pulse", text: "MODEL BUILD QUEUED" }]);
  b = trackBuild(b, ev("running", { progress: "2/7 GRID" }));
  assert.deepEqual(buildBadges(b), [{ cls: "am pulse", text: "MODEL BUILD 2/7 GRID" }]);
  assert.deepEqual(trackBuild(b, ev("done")), {});
});
test("a failed build stays as ERR <screen> with the child's last stderr line until a build succeeds", () => {
  const b = trackBuild({}, ev("failed", { error: "BuildFailed: ValueError: universe empty" }));
  assert.deepEqual(buildBadges(b), [{ cls: "dn", text: "ERR MODEL · ValueError: universe empty" }]);
  assert.deepEqual(trackBuild(b, ev("done", { force: false })), b);      // a reload is not a build
  assert.deepEqual(trackBuild(b, ev("done")), {});
});
test("unforced heavy loads and other tiers are ignored; long errors are clipped", () => {
  assert.deepEqual(trackBuild({}, ev("running", { force: false })), {});
  assert.deepEqual(trackBuild({}, { ...ev("running"), tier: "quote" }), {});
  const b = trackBuild({}, ev("failed", { error: "BuildFailed: " + "x".repeat(200) }));
  assert.equal(buildBadges(b)[0].text.length, "ERR MODEL · ".length + 60);
});

test("BUILD names its duration, or lists the screens that build", () => {
  const screens = [{ id: "PORT", build: null }, { id: "MODEL", build: "~10 MIN" }, { id: "BATCH", build: "~12 MIN" }];
  assert.deepEqual(buildRequest("MODEL", screens), { ok: true, text: "MODEL BUILD STARTED · ~10 MIN" });
  assert.deepEqual(buildRequest("PORT", screens), { ok: false, text: "PORT HAS NOTHING TO BUILD — BUILD MODEL · BUILD BATCH" });
  assert.deepEqual(buildRequest(null, screens), { ok: false, text: "BUILD <MODEL|BATCH>" });
  assert.deepEqual(buildRequest("PORT", [{ id: "PORT", build: null }]), { ok: false, text: "PORT HAS NOTHING TO BUILD" });
});

test("the badges are rebuilt from /api/jobs: a page opened mid-build shows it, a restarted server clears it", () => {
  const jobs = [                                    // newest first, as GET /api/jobs lists them
    { id: 7, screen: "MODEL", tier: "heavy", force: true, state: "running", progress: "2/7 GRID" },
    { id: 6, screen: "BATCH", tier: "heavy", force: true, state: "failed", error: "BuildFailed: RuntimeError: x" },
    { id: 5, screen: "MODEL", tier: "heavy", force: true, state: "failed", error: "BuildFailed: old" },
    { id: 4, screen: "MODEL", tier: "quote", force: true, state: "done" },
    { id: 3, screen: "BATCH", tier: "heavy", force: false, state: "running" },
  ];
  assert.deepEqual(buildBadges(buildsFromJobs(jobs)),
    [{ cls: "am pulse", text: "MODEL BUILD 2/7 GRID" }, { cls: "dn", text: "ERR BATCH · RuntimeError: x" }]);
  assert.deepEqual(buildsFromJobs([]), {});        // the server restarted: the build it was running is gone
  assert.deepEqual(buildsFromJobs(undefined), {});
});

test("a /api/jobs snapshot never overwrites a screen an SSE event updated while it was in flight", () => {
  const now = { MODEL: { run: null, err: "x" }, BATCH: { run: "2/3 STAGE B", err: null } };   // BATCH: an event after the request
  const snap = { MODEL: { run: "1/7 FETCH", err: null }, BATCH: { run: "1/3 STAGE A", err: null } };
  assert.deepEqual(mergeBuilds(now, snap, new Set(["BATCH"])), { MODEL: { run: "1/7 FETCH", err: null }, BATCH: { run: "2/3 STAGE B", err: null } });
  assert.deepEqual(mergeBuilds(now, {}, new Set()), {});                              // untouched: the snapshot wins
  assert.deepEqual(mergeBuilds({}, snap, new Set(["MODEL"])), { BATCH: { run: "1/3 STAGE A", err: null } });   // MODEL's build ended
});
