import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";

const SRC = readFileSync(new URL("../../web/sw.js", import.meta.url), "utf-8");
const ORIGIN = "https://my-mac.tail1234.ts.net";

// web/sw.js in a fake worker: `mac` answers a URL (a Response, or throws: asleep; "hang": never answers).
function worker(mac) {
  const store = new Map(), on = {}, asked = [];
  const caches = {
    open: async () => ({ put: async (href, res) => { store.set(href, await res.text()); } }),
    match: async (href) => (store.has(href) ? new Response(store.get(href)) : undefined),
  };
  const fetch = (req) => {
    const href = typeof req === "string" ? req : req.url;
    asked.push(href);
    const a = mac(href);
    if (a === "hang") return new Promise(() => {});
    return a instanceof Error ? Promise.reject(a) : Promise.resolve(a);
  };
  const self = { addEventListener: (t, fn) => { on[t] = fn; }, location: new URL(ORIGIN),
    registration: { scope: `${ORIGIN}/` }, skipWaiting() {}, clients: { claim: async () => {} } };
  vm.runInNewContext(SRC, { self, caches, fetch, URL, Promise, setTimeout: (fn) => setTimeout(fn, 5), clearTimeout });
  const get = async (path, method = "GET", settle = true) => {   // settle: wait for the copies too
    let answer = null;
    const waits = [];
    on.fetch({ request: { url: `${ORIGIN}${path}`, method }, respondWith: (p) => { answer = p; },
      waitUntil: (p) => waits.push(p) });
    const res = answer && await (await answer).text();
    if (settle) await Promise.all(waits);
    return res;
  };
  return { get, store, asked };
}

const ok = (body) => new Response(body, { status: 200 });

test("online: the Mac answers and its answer is kept; offline or asleep: the kept one shows", async () => {
  let up = true;
  const w = worker((href) => (up ? ok(`fresh ${href.slice(ORIGIN.length)}`) : new Error("offline")));
  assert.equal(await w.get("/api/screen/PORT"), "fresh /api/screen/PORT");
  up = false;
  assert.equal(await w.get("/api/screen/PORT"), "fresh /api/screen/PORT");
  const slow = worker(() => "hang");
  slow.store.set(`${ORIGIN}/`, "kept page");
  assert.equal(await slow.get("/", "GET", false), "kept page");                                     // waited WAIT_MS, then the copy
});

test("only a 200 is kept: a cold screen (202) never replaces the last payload", async () => {
  let res = ok("payload");
  const w = worker(() => res);
  await w.get("/api/screen/PORT");
  res = new Response("cold", { status: 202 });
  assert.equal(await w.get("/api/screen/PORT"), "cold");                              // online: what the Mac says
  assert.equal(w.store.get(`${ORIGIN}/api/screen/PORT`), "payload");
});

test("the screens list keeps every screen without a parameter, so each opens offline", async () => {
  const list = { screens: [{ id: "PORT", status: "live", param: false }, { id: "SEC", status: "live", param: true },
    { id: "SOON", status: "soon", param: false }, { id: "MKT", status: "live", param: false }] };
  const w = worker((href) => ok(href.endsWith("/api/screens") ? JSON.stringify(list) : `payload ${href.slice(ORIGIN.length)}`));
  await w.get("/api/screens");
  assert.deepEqual([...w.store.keys()].sort(),
    [`${ORIGIN}/api/screen/MKT`, `${ORIGIN}/api/screen/PORT`, `${ORIGIN}/api/screens`]);
});

test("the live stream, writes and other sites pass straight through", async () => {
  const w = worker(() => ok("x"));
  assert.equal(await w.get("/api/stream"), null);
  assert.equal(await w.get("/api/refresh/PORT", "POST"), null);
  assert.deepEqual(w.asked, []);
});
