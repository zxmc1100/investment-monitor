// Transport: the live server (/api + SSE) or a static export (data/*.json).
const mode = globalThis.document?.querySelector('meta[name="im-mode"]')?.content ?? "live";
export const isStatic = mode === "static";

// The server's one-line message for a failed request: monitor/server/app.py puts it in detail.error;
// Starlette's own errors carry a plain string; anything else is the HTTP status. Pure.
export function detailText(body, status) {
  const d = body?.detail;
  if (typeof d === "string" && d) return d.toUpperCase();
  if (typeof d?.error === "string" && d.error) return d.error;
  return `HTTP ${status}`;
}

// What a failed request tells the user: the server's message, never the request URL. Pure.
export const why = (e) => e?.detail ?? String(e?.message ?? e).toUpperCase();

async function json(url, init) {
  let r;
  try {
    r = await fetch(url, { cache: "no-store", ...init });
  } catch (e) {
    throw Object.assign(new Error(`${url}: ${e?.message ?? e}`), { status: 0, detail: "SERVER UNREACHABLE" });
  }
  if (!r.ok && r.status !== 202) {
    let body = null;
    try { body = await r.json(); } catch { /* not JSON: the status says it */ }
    const msg = detailText(body, r.status);
    throw Object.assign(new Error(`${url}: ${msg}`), { status: r.status, detail: msg });
  }
  return r.json();
}

export const registry = () => json(isStatic ? "data/screens.json" : "api/screens");
const path = (base, id, param) => `${base}/${id}${param ? `/${encodeURIComponent(param)}` : ""}`;

export const screen = (id, param) => json(isStatic ? `data/${id}.json` : path("api/screen", id, param));
export const ensure = (id, param) => (isStatic ? Promise.resolve(null) : json(path("api/ensure", id, param), { method: "POST" }));
export const refresh = (id, tier, param) =>
  json(`${path("api/refresh", id, param)}${tier ? `?tier=${encodeURIComponent(tier)}` : ""}`, { method: "POST" });
export const build = (id) => json(path("api/build", id), { method: "POST" });
export const prefs = () => (isStatic ? Promise.resolve(null) : json("api/prefs"));
export const setTarget = (name) => json("api/prefs", {
  method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ target: name }) });
export const jobs = () => json("api/jobs");
const send = (url, method, body) => json(url, {
  method, headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });
export const lookup = (q) => json(`api/lookup?q=${encodeURIComponent(q)}`);
export const watch = (ticker) => send("api/watchlist", "POST", { ticker });
export const unwatch = (ticker) => send(`api/watchlist/${encodeURIComponent(ticker)}`, "DELETE");

export const alerts = () => (isStatic ? Promise.resolve(null) : json("api/alerts"));
export const addAlert = (text) => send("api/alerts", "POST", { text });
export const delAlert = (id) => send(`api/alerts/${encodeURIComponent(id)}`, "DELETE");
export const ack = (body) => send("api/alerts/ack", "POST", body);

// TRADES: every write names the file version (etag) it was made on — 409 when the file changed meanwhile.
const tradePath = (id) => `api/trades/${encodeURIComponent(id)}`;
export const trades = () => json("api/trades");
export const addTrade = (etag, trade) => send("api/trades", "POST", { etag, trade });
export const editTrade = (id, etag, trade) => send(tradePath(id), "PUT", { etag, trade });
export const delTrade = (id, etag) => send(tradePath(id), "DELETE", { etag });
export const previewTrades = (text, mode) => send("api/trades/preview", "POST", { text, mode });
export const importTrades = (etag, text, mode) => send("api/trades/import", "POST", { etag, text, mode });
export const resetTrades = (etag) => send("api/trades/reset", "POST", { etag });
export const undoTrades = (etag) => send("api/trades/undo", "POST", { etag });

export function subscribe(onEvent) {
  if (isStatic) return () => {};
  const es = new EventSource("api/stream");
  es.addEventListener("screen", (e) => onEvent("screen", JSON.parse(e.data)));
  es.addEventListener("job", (e) => onEvent("job", JSON.parse(e.data)));
  es.addEventListener("alerts", (e) => onEvent("alerts", JSON.parse(e.data)));
  es.onopen = () => onEvent("conn", { ok: true });
  es.onerror = () => onEvent("conn", { ok: false });
  return () => es.close();
}
