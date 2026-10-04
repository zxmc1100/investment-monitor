// Heavy builds (a screen whose registry entry has `build` runs a child process) start only on
// BUILD <screen> (or the month-start schedule of a `monthly` one) — REFRESH just reloads their output. Their
// forced `heavy` job events carry `progress` ("2/7 GRID") while running and, on failure, `error`
// (BuildFailed: <the child's last stderr line>). The status bar shows every screen's build. Pure.
const MAX_ERR = 60;

export function trackBuild(builds, ev) {
  if (ev?.tier !== "heavy" || !ev.force) return builds;          // an unforced heavy job is a cheap reload
  const out = { ...builds };
  if (ev.state === "failed") out[ev.screen] = { run: null, err: String(ev.error ?? "failed").replace(/^BuildFailed: /, "") };
  else if (ev.state === "done") delete out[ev.screen];
  else out[ev.screen] = { run: ev.progress || (ev.state === "queued" ? "QUEUED" : "STARTING"), err: null };
  return out;
}

// The same state from GET /api/jobs (running and recent jobs, newest first), replayed oldest first: on
// boot a page opened mid-build shows its badge at once, and after the event stream reconnects (a
// server reload) a badge whose build died with the old server goes.
export function buildsFromJobs(jobs) {
  return [...(jobs ?? [])].sort((a, b) => a.id - b.id).reduce(trackBuild, {});
}

// Fold a /api/jobs snapshot into the live state without losing a newer SSE event: a screen whose heavy
// job event arrived while the request was in flight (`touched`) keeps its current entry (or none).
export function mergeBuilds(current, snapshot, touched) {
  const out = {};
  for (const [s, b] of Object.entries(snapshot ?? {})) if (!touched.has(s)) out[s] = b;
  for (const s of touched) if (current?.[s]) out[s] = current[s];
  return out;
}

export function buildBadges(builds) {
  return Object.entries(builds).map(([s, b]) => (b.run !== null
    ? { cls: "am pulse", text: `${s} BUILD ${b.run}` }
    : { cls: "dn", text: `ERR ${s} · ${b.err.slice(0, MAX_ERR)}` }));
}

// What BUILD <id> does: the notice for a screen that builds (registry entry `build` = its rough
// duration), or the list of screens that do — the server's wording (monitor/server/app.py).
export function buildRequest(id, screens) {
  const s = (screens ?? []).find((x) => x.id === id);
  if (s?.build) return { ok: true, text: `${id} BUILD STARTED · ${s.build}` };
  const builds = (screens ?? []).filter((x) => x.build).map((x) => x.id);
  if (!id) return { ok: false, text: `BUILD <${builds.join("|") || "SCREEN"}>` };
  return { ok: false, text: `${id} HAS NOTHING TO BUILD${builds.length ? ` — ${builds.map((b) => `BUILD ${b}`).join(" · ")}` : ""}` };
}
