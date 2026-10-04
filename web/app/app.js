// Terminal shell: boot, hash router, render loop, command bar, status bar, overlays.
import { badge, noticeText, summary } from "./alerts.js";
import * as api from "./api.js";
import { buildBadges, buildRequest, buildsFromJobs, mergeBuilds, trackBuild } from "./build.js";
import { enterText, fillsBar, helpRows, localOnlyMsg, parse, privateAt, splitRegistry, staticNotice, verbItems } from "./cmd.js";
import { changed } from "./diff.js";
import { $, esc } from "./dom.js";
import { fmtClock, fmtDate, fmtStamp } from "./fmt.js";
import { rank } from "./fuzzy.js";
import { bindKeys } from "./keys.js";
import { followsLater } from "./render/table.js";
import { renderPanel } from "./render/index.js";
import { changesHash, hashOf, keyOf, popBack, pushBack, routeOf } from "./route.js";
import { metaBadges } from "./status.js";
import { nextSortCol } from "./tablesort.js";
import { freshText, removable, replaceText, rowLine, stands, undoText } from "./ask.js";
import { describe, editValues, fmtQty, todayIso, tradeFrom } from "./trades.js";
import { fold, resolveWatch, watchItems } from "./watch.js";

// HELP's KEYS table (its COMMANDS table is cmd.helpRows). Screens are number keys; F1 is the only F-key.
function keyRows() {
  const top = Math.max(1, ...[...S.reg.screens, ...S.reg.private].map((s) => s.fkey ?? 0));
  return [
    ["Enter", "run the command · on an empty bar: open the cursor row (e.g. a security), "
      + "set TARGET on PORTFOLIOS, acknowledge on ALRT, edit a trade on TRADES — or step into a focused form"],
    ["Del / Backspace", "delete the cursor row on TRADES (asks first)"],
    ["Tab", "autocomplete (list open) · cycle panel focus (bar empty)"],
    [`1–${top}`, "jump to screen (bar empty)"],
    ["↑ ↓ PgUp PgDn Home End", "move the row cursor"],
    ["Shift+← →", "sort by previous / next column"],
    ["Shift+↑ ↓", "sort ascending / descending"],
    ["Alt+↑ ↓", "command history"],
    ["Alt+1…9", "maximize panel n (again or Esc restores)"],
    ["drag on a chart", "zoom into that period · double-click zooms back out"],
    ["F1 or ?", "this help"],
    ["Esc", "leave a form field · clear bar · close overlay · restore panel · back from a security · leave full screen"],
  ];
}

const cmd = $("#cmd");
const S = {
  reg: { screens: [], private: [] }, id: null, param: null, key: null, back: [], backNav: false, resetScroll: false, payload: null, live: null, conn: !api.isStatic,
  running: new Set(), tables: {}, ranges: {}, max: null, focus: null,
  ac: { items: [], i: 0, moved: false }, history: [], hi: -1, disposers: {}, noticeT: null,
  lookup: { q: "", items: [] }, lookupT: null, alerts: { active: 0, down: false }, alertErr: null,
  builds: {},                                       // screen builds in progress or failed (build.js)
  forms: {},                                        // form / paste panels' values, kept across refreshes
  ask: null,                                        // the open question: {fn, rows it names, route} (ask)
  buildTouched: new Set(),                          // per in-flight /api/jobs sync: screens SSE updated meanwhile
};

// ── data flow ────────────────────────────────────────────────────────────────
async function boot() {
  try { S.reg = await registry(); } catch (e) { notice(`REGISTRY FAILED: ${api.why(e)}`, true); return; }
  if (S.reg.settings_error) notice(S.reg.settings_error, true);
  renderFkeys();
  api.subscribe(onEvent);
  syncAlerts();
  syncBuilds();
  bindKeys(keyHandlers);
  cmd.addEventListener("input", () => { S.ac.moved = false; updateAc(); });
  document.addEventListener("pointerdown", (e) => {                  // a press elsewhere: no answer — a press,
    if (S.ask && !e.target.closest("#overlay")) cancelAsk();          // so the click that opened it never closes it
  }, true);
  document.addEventListener("click", (e) => {
    const head = e.target.closest("#grid .panel > .h");
    if (head) { S.focus = head.parentElement.dataset.id; decorate(); }   // a panel's title bar focuses it
    if (!e.target.closest("#overlay, #ac, a, input, textarea, select, button, label, form, .paste, .banner")
        && !String(getSelection())) cmd.focus();
  });
  addEventListener("hashchange", route);
  const every = (S.reg.quote_interval_s ?? 60) * 1000;
  setInterval(() => { if (!document.hidden) poke(); }, every);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) poke(); });
  setInterval(tickClock, 1000);
  cmd.focus();
  route();
}

// The registry as the app uses it: in the static snapshot, private screens are only named (cmd.splitRegistry).
const registry = async () => splitRegistry(await api.registry(), api.isStatic);
const here = () => (S.param ? `${S.id} ${S.param}` : S.id);       // a route as the user writes it: SEC SAP.DE

function route() {
  if (S.ask) cancelAsk();                          // a question belongs to the screen it was asked on
  const viaBack = S.backNav;                       // this hash change is an Esc-back: don't push
  S.backNav = false;
  const r = routeOf(location.hash);
  const scr = S.reg.screens.find((s) => s.id === r.id);
  if (scr && !scr.param) r.param = null;
  const priv = !scr && S.reg.private.some((p) => p.id === r.id);       // a private screen in the snapshot
  const bad = priv ? localOnlyMsg(r.id) : !scr ? `UNKNOWN SCREEN ${r.id}` : scr.status !== "live" ? `${r.id} — SOON`
    : scr.param && !r.param ? `${r.id}: ${scr.param_name ?? "TICKER"} REQUIRED` : null;   // which params: the server decides
  if (bad) {
    notice(bad, !priv && scr?.status !== "soon");
    if (S.key) history.replaceState(null, "", hashOf({ id: S.id, param: S.param }));
    else location.replace("#/PORT");
    return;
  }
  const key = keyOf(r);
  if (key === S.key) return;
  if (S.key && !viaBack) S.back = pushBack(S.back, hashOf({ id: S.id, param: S.param }));
  Object.assign(S, { id: r.id, param: r.param, key, payload: null, live: null, max: null, focus: null, resetScroll: true });
  S.running.clear();
  load();
}

async function load() {
  const key = S.key;
  const entry = api.isStatic ? S.reg?.screens?.find((s) => s.id === S.id) : null;
  if (entry?.cold) { renderCold(entry.cold); renderStatus(); return; }   // published without data: say why
  try {
    const res = await api.screen(S.id, S.param);
    if (key !== S.key) return;
    S.live = res.live ?? null;
    S.running = new Set(S.live?.running ?? []);
    if (res.cold) { renderCold(res.reason); renderStatus(); return; }
    show(res);
  } catch (e) {
    if (key !== S.key) return;
    if (e.status === 404 && S.param) { notice(api.why(e), true); leave(); return; }
    notice(`LOAD ${here()} FAILED: ${api.why(e)}`, true);
  }
}

// Back to where the user came from after a route the server refused (no back-stack entry for it).
function leave() {
  const { to, stack } = popBack(S.back, hashOf({ id: S.id, param: S.param }));
  Object.assign(S, { back: stack, key: null, backNav: changesHash(location.hash, to) });
  location.replace(to);
}

// Build badges from the server's job list (build.js): on boot and whenever the event stream reconnects.
async function syncBuilds() {
  if (api.isStatic) return;
  const touched = new Set();                         // screens SSE updated while the request is in flight
  S.buildTouched.add(touched);
  try {
    const snap = buildsFromJobs((await api.jobs()).jobs);
    S.builds = mergeBuilds(S.builds, snap, touched);
    renderStatus();
  } catch { /* the next job event updates them */ } finally { S.buildTouched.delete(touched); }
}

async function syncAlerts() {
  if (api.isStatic) return;
  try { S.alerts = summary(await api.alerts()); renderStatus(); } catch { /* the badge waits for the next event */ }
}

async function poke() {
  if (!S.id || api.isStatic) return;
  try {
    const r = await api.ensure(S.id, S.param);
    if (r) { S.live = r.live; r.jobs.forEach((j) => S.running.add(j.tier)); renderStatus(); }
  } catch { /* the event stream reports OFFLINE */ }
}

function show(payload) {
  const prev = S.payload?.screen === payload.screen ? S.payload : null;
  S.payload = payload;
  for (const p of payload.panels ?? []) {            // a preview checked against the old trades: check it again
    const st = S.forms[formKey(p.id)];
    if (p.type === "paste" && st?.preview) st.stale = true;
  }
  if (S.ask && payload.screen === "TRADES" && !stands(S.ask, { route: S.key, rows: tradeIds() })) {
    cancelAsk("THE TRADES CHANGED — NOTHING DONE, ASK AGAIN");      // a row it names was edited or deleted meanwhile
  }
  render();
  flash(changed(prev, payload));
  renderStatus();
}

function onEvent(type, ev) {
  if (type === "conn") {
    const was = S.conn;
    S.conn = ev.ok;
    if (ev.ok && !was && S.id) load();             // server came back (code reload): re-sync
    if (ev.ok && !was) { syncAlerts(); syncBuilds(); }
    renderStatus();
    return;
  }
  if (type === "alerts") {                          // the alert loop or an edit changed the active set
    S.alerts = { active: ev.active ?? 0, down: !!ev.down };
    const msg = noticeText(ev.new);
    if (msg) notice(msg, ev.new.some((e) => e.down));
    renderStatus();
    return;
  }
  if (type === "job" && ev.screen === "ALRT" && ev.tier === "watch") {
    if (ev.state === "failed") S.alertErr = ev.error ?? "failed";
    if (ev.state === "done") S.alertErr = null;
    renderStatus();                                 // no return: the generic job handler below also sees it
  }
  if (type === "job" && ev.tier === "heavy") {      // a build: status bar <ID> BUILD 2/7 STAGE | ERR <ID> · …
    S.builds = trackBuild(S.builds, ev);
    if (ev.force) S.buildTouched.forEach((t) => t.add(ev.screen));   // newer than any snapshot in flight
    renderStatus();                                 // no return: the generic job handler below also sees it
  }
  if (type === "screen" && (ev.key ?? ev.id) === S.key) { load(); return; }
  if (type === "job" && ev.screen === S.key) {
    if (ev.state === "queued" || ev.state === "running") S.running.add(ev.tier);
    else S.running.delete(ev.tier);
    if (ev.state === "failed") S.live = { ...(S.live ?? {}), error: { tier: ev.tier, error: ev.error, at: ev.finished } };
    if (ev.state === "done" && S.live?.error?.tier === ev.tier) S.live = { ...S.live, error: null };
    renderStatus();
  }
}

// ── rendering ────────────────────────────────────────────────────────────────
function ui(pid) {
  return {
    tableState: (p) => (S.tables[p.id] ??= { sort: p.sort ?? [p.key, "asc"], cursor: null, showClosed: false }),
    sortBy(id, col) {
      const t = S.tables[id];
      t.sort = t.sort[0] === col ? [col, t.sort[1] === "desc" ? "asc" : "desc"] : [col, "desc"];
      rerender(id);
    },
    setCursor(id, key) { S.tables[id].cursor = key; S.tables[id].reveal = true; S.focus = id; rerender(id); followers(id); },
    toggleClosed(id) { const t = S.tables[id]; t.showClosed = !t.showClosed; rerender(id); },
    followKey: (p) => (p.follows ? S.tables[p.follows]?.cursor ?? null : null),
    chartRange: (id) => S.ranges[id],
    setRange(id, r) { S.ranges[id] = r; rerender(id); },
    onDispose: (fn) => (S.disposers[pid] ??= []).push(fn),
    // form / paste panels (TRADES)
    formState: (p) => (S.forms[formKey(p.id)] ??= blankForm(p)),
    today: () => todayIso(),
    rows: (p) => S.payload?.panels.find((q) => q.edit === p.id)?.rows ?? [],
    tradeCount: () => S.payload?.panels.find((q) => q.type === "table" && q.remove)?.rows?.length ?? 0,
    lookup: (q) => api.lookup(q),
    why: api.why,
    run: (text) => run(text),
    saveForm,
    clearForm(p) { S.forms[formKey(p.id)] = blankForm(p); rerender(p.id); focusField(p.id, "ticker"); },
    previewText: (text, mode) => api.previewTrades(text, mode),
    importForm,
  };
}

// ── TRADES: forms, writes, questions ─────────────────────────────────────────
const formKey = (pid) => `${S.key}|${pid}`;
const blankForm = (p) => (p.type === "paste" ? { text: "", file: null, preview: null }
  : { values: { ticker: "", action: p.fields?.find((f) => f.kind === "choice")?.options?.[0] ?? "buy", shares: "", pps: "",
    total: "", date: "", fee: "", keep: null }, edit: null });

function focusField(pid, name = null) {
  const el = document.querySelector(`#grid .panel[data-id="${CSS.escape(pid)}"] ${name ? `[data-field="${CSS.escape(name)}"]` : "[data-field]"}`);
  if (el) { S.focus = pid; decorate(); el.focus(); }
}

const savedText = (res) => [`SAVED · ${res.text}`, ...(res.warnings ?? [])].join(" · ");

// A write to your trades, made on the version the user saw — `etag`: the one a question was asked on; else this
// screen's, or (from another screen) the file's now. A 409 (changed meanwhile) reloads TRADES; the user saves again.
async function tradeWrite(call, done, etag = null) {
  try {
    const tag = etag ?? (S.id === "TRADES" && S.payload?.etag ? S.payload.etag : (await api.trades()).etag);
    const res = await call(tag);
    if (S.id === "TRADES" && S.payload) S.payload.etag = res.etag;
    done(res);
    registry().then((r) => { S.reg = r; }).catch(() => {});        // SEC's ticker list follows the book
    return res;
  } catch (e) {
    notice(api.why(e), true);
    if (e.status === 409 && S.id === "TRADES") load();
    return null;
  }
}

const tradeIds = () => (S.payload?.panels.find((q) => q.type === "table" && q.remove)?.rows ?? []).map((r) => r.id);

async function saveForm(p) {
  const st = ui(p.id).formState(p);
  const r = tradeFrom(st.values, todayIso());
  if (r.error) { notice(r.error, true); return; }
  const edit = st.edit;
  const res = await tradeWrite((etag) => (edit ? api.editTrade(edit.id, etag, r.body) : api.addTrade(etag, r.body)),
    (out) => notice(savedText(out), !!out.warnings?.length));
  if (!res) return;
  S.forms[formKey(p.id)] = { ...blankForm(p), values: { ...blankForm(p).values, action: st.values.action } };
  rerender(p.id);
  focusField(p.id, "ticker");                       // the next trade
}

function editRow(p, key) {
  const row = p.rows.find((r) => String(r[p.key]) === String(key));
  const target = S.payload?.panels.find((q) => q.id === p.edit);
  if (!row || !target) return;
  S.forms[formKey(target.id)] = { values: editValues(row), edit: { id: row.id,
    label: `${fmtDate(row.date)} ${row.tkr} ${row.action} ${fmtQty(row.shares)}` } };
  rerender(target.id);
  focusField(target.id, "ticker");
  notice("EDITING · SAVE REPLACES IT · CANCEL EDIT KEEPS IT");
}

function removeRow(p, key) {
  const row = p.rows.find((r) => String(r[p.key]) === String(key));
  if (!row) return false;
  const etag = S.payload?.etag ?? null;
  ask("DELETE THIS TRADE?", `${rowLine(row)} — the file as it is now is kept in input/backups/ and UNDO brings it back.`,
    "DELETE", () => tradeWrite((tag) => api.delTrade(row.id, tag), (res) => notice(`DELETED · ${res.text} · UNDO BRINGS IT BACK`), etag),
    [row.id]);
  return true;
}

async function importForm(p, mode) {
  const st = ui(p.id).formState(p);
  const text = st.preview?.text;
  if (text === undefined || text !== st.text) {     // ADD sends exactly what was previewed: the box changed since
    st.stale = true;
    rerender(p.id);
    notice("THE BOX CHANGED SINCE ITS PREVIEW — PREVIEWED AGAIN: CHECK IT, THEN ADD");
    return;
  }
  const go = (etag = null) => tradeWrite((tag) => api.importTrades(tag, text, mode), (res) => {
    Object.assign(st, { text: res.left ?? "", preview: null });
    if (!st.text) st.file = null;
    const n = (k, w) => `${k} ${w}${k === 1 ? "" : "S"}`;
    notice(`${mode === "replace" ? "REPLACED YOUR TRADES WITH" : "ADDED"} ${n(res.added, "TRADE")}`
      + (res.skipped ? ` · ${n(res.skipped, "LINE")} LEFT IN THE BOX TO FIX` : ""));
    rerender(p.id);
  }, etag);
  if (mode !== "replace") { go(); return; }
  try {
    const [pre, now] = await Promise.all([api.previewTrades(text, "replace"), api.trades()]);
    if (!pre.ok) { notice(pre.error ?? "NOTHING TO ADD — EVERY ROW HAS AN ERROR", true); return; }
    ask("REPLACE YOUR TRADES?", replaceText({ rows: tradeRows(now), error: now.error, lines: now.lines }, pre, st.file),
      "REPLACE", () => go(now.etag), now.rows.map((r) => r.id));
  } catch (e) { notice(api.why(e), true); }
}

// /api/trades rows as TRANSACTIONS rows (the shape the questions name)
const tradeRows = (now) => (now.rows ?? []).map((r) => ({ id: r.id, date: r.date, tkr: r.ticker,
  action: String(r.action).toUpperCase(), shares: r.shares, total: r.price }));

async function doTrade(values) {
  const r = tradeFrom(values, todayIso());
  if (r.error) { notice(r.error, true); return; }
  notice(describe(r.trade, r.fee));                 // what will be stored, then the save
  await tradeWrite((etag) => api.addTrade(etag, r.body), (res) => notice(savedText(res), !!res.warnings?.length));
}

async function startFresh() {
  let now;
  try { now = await api.trades(); } catch (e) { notice(api.why(e), true); return; }
  ask("START FRESH?", freshText({ rows: tradeRows(now), error: now.error, lines: now.lines }), "START FRESH",
    () => tradeWrite((tag) => api.resetTrades(tag), (res) => {
      notice(`STARTED FRESH · THE ${res.removed} ${now.error ? "LINES" : "TRADES"} ARE IN input/backups/ · UNDO BRINGS THEM BACK`);
      if (S.id !== "TRADES") run("TRADES");
    }, now.etag), now.rows.map((r) => r.id));
}

async function doUndo() {
  let now;
  try { now = await api.trades(); } catch (e) { notice(api.why(e), true); return; }
  if (!now.undo) { notice("NOTHING TO UNDO"); return; }
  if (!now.undo.ready) {
    notice("THE FILE CHANGED SINCE THE LAST CHANGE HERE — UNDO WOULD LOSE THAT; THE FILE BEFORE IT IS IN input/backups/", true);
    return;
  }
  ask("UNDO THE LAST CHANGE?", undoText(now.undo), "UNDO",
    () => tradeWrite((tag) => api.undoTrades(tag), (res) => notice(res.text), now.etag), []);
}

// A yes/no question in the overlay, focused: Enter on it (or its button) says yes; Esc, any other key, a click
// elsewhere, another screen, or a refresh that changes a row it names closes it unanswered.
function ask(title, text, yes, fn, rows = []) {
  openOverlay(title, `<p>${esc(text)}</p><div class="btns"><button type="button" class="btn" data-act="yes">${esc(yes)} ↵</button>`
    + `<button type="button" class="btn ghost" data-act="no">KEEP · Esc</button></div>`);
  const ov = $("#overlay");
  ov.classList.add("ask");
  S.ask = { fn, rows, route: S.key };
  $("#overlay [data-act=yes]").addEventListener("click", () => keyHandlers.confirm());
  $("#overlay [data-act=no]").addEventListener("click", () => cancelAsk());
  ov.focus();
}

function cancelAsk(why = "NOTHING CHANGED — THE QUESTION WAS CLOSED") {
  if (!S.ask) return;
  closeOverlay();
  notice(why);
}

function dispose(pid) { (S.disposers[pid] ?? []).splice(0).forEach((fn) => fn()); }

function render() {
  const p = S.payload;
  Object.keys(S.disposers).forEach(dispose);
  $("#title").innerHTML = `<b>${esc(p.screen)}</b>${esc(p.title)}`;
  $("#strip").innerHTML = `<b>${esc(p.screen)}</b><span>${esc(p.title)}</span><span class="dim">${esc(p.context?.text ?? "")}</span>`
    + (p.actions ?? []).map((a) => `<button type="button" class="btn" data-run="${esc(a.run)}" title="${esc(a.title ?? "")}">${esc(a.label)}</button>`).join("")
    + `<span class="dim hint">↑↓ row · Shift+←→ sort · Tab panel · Alt+n max · F1 help</span>`;
  $("#strip").querySelectorAll("[data-run]").forEach((el) => el.addEventListener("click", () => run(el.dataset.run)));
  const grid = $("#grid"), top = S.resetScroll ? 0 : grid.scrollTop;   // keep scroll on refresh, reset on screen change
  S.resetScroll = false;
  const refocus = keepFocus();
  grid.replaceChildren(...p.panels.map((panel) => renderPanel(panel, ui(panel.id))));
  followsLater(p.panels).forEach(rerender);         // their table now has its cursor
  fitRows(grid);
  grid.scrollTop = top;
  decorate();
  refocus();
}

// A refresh redraws the panels: the form field being typed in keeps focus, caret and scroll.
function keepFocus() {
  const a = document.activeElement, panel = a?.closest?.("#grid .panel");
  if (!panel || !a.dataset?.field) return () => {};
  const at = { pid: panel.dataset.id, field: a.dataset.field, s: a.selectionStart, e: a.selectionEnd, top: a.scrollTop };
  return () => {
    const el = document.querySelector(`#grid .panel[data-id="${CSS.escape(at.pid)}"] [data-field="${CSS.escape(at.field)}"]`);
    if (!el) return;
    el.focus({ preventScroll: true });
    try { if (at.s !== null && at.s !== undefined) el.setSelectionRange(at.s, at.e); } catch { /* a select has no caret */ }
    el.scrollTop = at.top;
  };
}

// Spare height goes to rows holding a chart/frontier/heatmap; KPI and table rows keep their
// content height (no grey band below the last row, no stretched number strips). A screen with
// no chart row gives the spare height to its last row.
const GROWS = new Set(["chart", "scatter", "heatmap"]);
function fitRows(grid) {
  grid.style.gridTemplateRows = "";
  const rows = new Map();
  for (const el of grid.querySelectorAll(":scope > .panel")) {
    const t = el.offsetTop;
    rows.set(t, (rows.get(t) ?? false) || GROWS.has(el.dataset.type));
  }
  const grow = [...rows.entries()].sort((a, b) => a[0] - b[0]).map(([, g]) => g);
  if (!grow.length) return;
  if (!grow.some(Boolean)) grow[grow.length - 1] = true;
  grid.style.gridTemplateRows = grow.map((g) => (g ? "minmax(min-content, 1fr)" : "min-content")).join(" ");
}

function rerender(id) {
  const panel = S.payload?.panels.find((q) => q.id === id);
  const old = $(`#grid .panel[data-id="${CSS.escape(id)}"]`);
  if (!panel || !old) return;
  dispose(id);
  const refocus = keepFocus();
  old.replaceWith(renderPanel(panel, ui(id)));
  decorate();
  refocus();
}

function followers(id) { for (const q of S.payload?.panels ?? []) if (q.follows === id) rerender(q.id); }

function decorate() {
  const root = document.documentElement.style;
  root.setProperty("--max-top", `${$("#cmdbar").offsetHeight}px`);
  root.setProperty("--max-bot", `${$("#fkeys").offsetHeight}px`);
  document.querySelectorAll("#grid .panel").forEach((el) => {
    el.classList.toggle("max", String(S.max) === el.dataset.n);
    el.classList.toggle("focus", S.focus === el.dataset.id);
  });
}

function renderCold(reason) {
  Object.keys(S.disposers).forEach(dispose);
  $("#title").innerHTML = `<b>${esc(S.id)}</b>${esc(S.param ?? "")}`;
  $("#strip").innerHTML = `<b>${esc(here())}</b><span class="dim">NO STORED DATA YET</span>`;
  $("#grid").style.gridTemplateRows = "";
  $("#grid").innerHTML = `<div class="cold">${esc(reason ?? "NO DATA YET · COMPUTING — the first run fetches prices and history; this view fills in by itself.")}</div>`;
}

function flash(keys) {
  for (const k of keys) {
    const el = document.querySelector(`[data-cell="${CSS.escape(k)}"]`);
    if (!el) continue;
    el.classList.remove("flash");
    void el.offsetWidth;                             // restart the animation
    el.classList.add("flash");
  }
}

// ── status bar & screen keys ─────────────────────────────────────────────────
function tickClock() {
  const el = $("#clock");
  if (el) el.textContent = fmtClock(new Date());
}

function renderStatus() {
  const meta = S.payload?.meta ?? {}, live = S.live ?? {}, out = [];
  const b = badge(S.alerts.active, S.alerts.down);
  if (b) out.push(`<span class="badge ${b.cls}" data-act="alerts" title="click: ALRT · ACK ALL clears">${esc(b.text)}</span>`);
  if (S.alertErr && S.id !== "ALRT") out.push(`<span class="dn" title="${esc(S.alertErr)}">ERR ALRT</span>`);
  for (const b of buildBadges(S.builds)) out.push(`<span class="${b.cls}">${esc(b.text)}</span>`);
  if (S.reg.settings_error) out.push(`<span class="dn" title="${esc(S.reg.settings_error)}">SETTINGS ERR</span>`);
  for (const [t, at] of Object.entries(meta.tiers ?? {})) {
    out.push(`<span class="dim" title="${esc(t.toUpperCase())} tier computed">${t[0].toUpperCase()} ${esc(fmtStamp(at))}</span>`);
  }
  for (const m of metaBadges(meta)) out.push(`<span class="${m.cls}" title="${esc(m.title ?? "")}">${esc(m.text)}</span>`);
  if (live.code_changed) out.push(`<span class="am">CODE CHANGED · RECOMPUTING</span>`);
  if (live.error) out.push(`<span class="dn err" data-act="err" title="${esc(live.error.error ?? "")}">ERR ${esc(S.id ?? "")} ${esc((live.error.tier ?? "").toUpperCase())}</span>`);
  if (api.isStatic) out.push(`<span class="dim">STATIC SNAPSHOT ${esc(fmtStamp(meta.computed_at))}</span>`);
  else if (!S.conn) out.push(`<span class="dn">● OFFLINE</span>`);
  else if (S.running.size) out.push(`<span class="am pulse">● UPDATING ${esc([...S.running].join("+").toUpperCase())}</span>`);
  else out.push(`<span class="am">● LIVE</span>`);
  out.push(`<span id="clock"></span>`);
  $("#status").innerHTML = out.join("");
  tickClock();
  $("#status [data-act=err]")?.addEventListener("click", showError);
  $("#status [data-act=alerts]")?.addEventListener("click", () => run("ALERTS"));
}

function renderFkeys() {
  const priv = S.reg.private.map((p) => ({ ...p, status: "private", title: `${p.title} · local terminal only` }));
  const keys = [...S.reg.screens, ...priv].filter((s) => s.fkey).sort((a, b) => a.fkey - b.fkey)
    .map((s) => `<span class="${s.status === "live" ? "" : "soon"}" data-run="${esc(s.id)}" title="${esc(s.title)}"><i>${esc(s.fkey)}</i>${esc(s.id)}</span>`);
  $("#fkeys").innerHTML = `<span data-run="HELP"><i>F1</i>HELP</span>${keys.join("")}<span class="hint">type to command · Esc back</span>`;
  $("#fkeys").querySelectorAll("[data-run]").forEach((el) => el.addEventListener("click", () => run(el.dataset.run)));
}

// ── command bar ──────────────────────────────────────────────────────────────
function tickers() {
  const pos = S.payload?.panels.find((p) => p.type === "table" && p.drives);
  return (pos?.rows ?? []).filter((r) => !r._closed).map((r) => ({ id: r[pos.key], name: r.name ?? "" }));
}

function candidates() {
  const names = Object.fromEntries(tickers().map((t) => [t.id, t.name]));
  const optLive = S.reg.screens.some((s) => s.id === "OPT" && s.status === "live");
  return [
    ...S.reg.screens.filter((s) => !s.param).map((s) => ({ label: s.id, desc: s.title + (s.status === "live" ? "" : " · soon"), run: s.id, dim: s.status !== "live" })),
    ...S.reg.private.filter((p) => p.fkey).map((p) => ({ label: p.id, desc: `${p.title} · local terminal only`, run: p.id, dim: true })),
    ...verbItems(ctx()),
    ...(api.isStatic ? [] : [
      ...S.reg.screens.filter((s) => s.build && s.status === "live").map((s) => ({ label: `BUILD ${s.id}`, desc: `rerun the ${s.title} build now (${s.build})`, run: `BUILD ${s.id}` })),
      ...(optLive ? (S.reg.targets ?? []).map((n) => ({ label: `TARGET ${n}`, desc: "optimizer target portfolio", run: `TARGET ${n}` })) : []),
    ]),
    ...(S.reg.params?.SEC ?? []).map((t) => ({ label: t, desc: `${names[t] ?? "security"} · SEC`, run: t })),
    ...paramItems(),
  ];
}

// Every other parametrized screen's known params (SEC's are the tickers above): "<ID> <PARAM>".
function paramItems() {
  return S.reg.screens.filter((s) => s.param && s.id !== "SEC" && s.status === "live")
    .flatMap((s) => (S.reg.params?.[s.id] ?? []).map((p) => ({ label: `${s.id} ${p}`, desc: String(s.title ?? s.id).toLowerCase(), run: `${s.id} ${p}` })));
}

// A palette row picked by Enter, click or tap: run it, or — an argument verb — put it in the bar.
function pick(item, text = item.run) {
  if (fillsBar(cmd.value, item, text)) { cmd.value = item.run; S.ac.moved = false; updateAc(); cmd.focus(); return; }
  run(text);
}

function updateAc() {
  const q = cmd.value.trim(), box = $("#ac");
  if (!q) { closeAc(); return; }
  const w = /^WATCH\s+(.+)$/i.exec(q);
  if (w && !api.isStatic) lookupSoon(w[1]);
  S.ac.items = w ? (fold(S.lookup.q) === fold(w[1]) ? watchItems(S.lookup.items) : [])
    : rank(q, candidates(), (c) => [c.label, c.desc]);
  if (!S.ac.items.length) { closeAc(); return; }
  S.ac.i = Math.min(S.ac.i, S.ac.items.length - 1);
  box.innerHTML = S.ac.items.map((c, i) => `<div class="${i === S.ac.i ? "on" : ""} ${c.dim ? "dim" : ""}" data-i="${i}"><b>${esc(c.label)}</b><span>${esc(c.desc)}</span></div>`).join("")
    + `<div class="foot">↑↓ select · Tab complete · Enter go · Esc close</div>`;
  box.hidden = false;
  box.querySelectorAll("[data-i]").forEach((el) => el.addEventListener("mousedown", (e) => { e.preventDefault(); pick(S.ac.items[Number(el.dataset.i)]); }));
}

// Debounced /api/lookup for "WATCH <query>"; the answer re-renders the list if still current.
function lookupSoon(q) {
  clearTimeout(S.lookupT);
  if (fold(q) === fold(S.lookup.q)) return;
  S.lookupT = setTimeout(async () => {
    try {
      const items = await api.lookup(q);
      S.lookup = { q, items };
      const cur = /^WATCH\s+(.+)$/i.exec(cmd.value.trim());
      if (cur && fold(cur[1]) === fold(q)) updateAc();
    } catch { /* best effort: Enter still resolves the query itself */ }
  }, 200);
}

async function doWatch(query) {
  try {
    const rows = await api.lookup(query);
    const r = resolveWatch(query, rows);
    if (r.kind === "none") { notice(`NO MATCH: ${query}`, true); return; }
    if (r.kind === "several") {
      S.lookup = { q: query, items: rows };
      cmd.value = `WATCH ${query}`;
      updateAc();
      notice(`${rows.length} MATCHES — ↑↓ PICK · ENTER`);
      return;
    }
    await api.watch(r.ticker);
    notice(`WATCHING ${r.ticker} · ${r.name}`);
    S.reg = await registry();                        // SEC's ticker list now includes it
  } catch (e) { notice(api.why(e), true); }
}

function closeAc() { const box = $("#ac"); box.hidden = true; box.innerHTML = ""; S.ac = { items: [], i: 0, moved: false }; }

const ctx = () => ({ screens: S.reg.screens, private: S.reg.private, params: S.reg.params ?? {}, targets: S.reg.targets ?? [],
  static: api.isStatic });

// Browser full screen (the Enter that runs the command is the user gesture the API requires).
function toggleFullscreen() {
  if (document.fullscreenElement) { document.exitFullscreen?.(); return; }
  const go = document.documentElement.requestFullscreen?.();
  if (!go) { notice("FULL SCREEN NOT SUPPORTED HERE", true); return; }
  go.catch((e) => notice(`FULL SCREEN BLOCKED: ${api.why(e)}`, true));
}

function run(text) {
  const raw = String(text).trim();
  const a = parse(raw, ctx());
  if (raw) S.history = [raw, ...S.history.filter((h) => h !== raw)].slice(0, 50);
  S.hi = -1;
  cmd.value = "";
  closeAc();
  const local = api.isStatic && staticNotice(a);   // the snapshot cannot run it: say so, in one phrasing
  if (local) { notice(local); return; }
  const fail = (e) => notice(api.why(e), true);
  switch (a.type) {
    case "screen": location.hash = hashOf(a); break;
    case "help": showHelp(a.screen, a.param); break;
    case "private": notice(localOnlyMsg(a.id)); break;
    case "fullscreen": toggleFullscreen(); break;
    case "refresh": {
      const id = a.screen ?? S.id, param = a.screen ? null : S.param, what = param ? `${id} ${param}` : id;
      api.refresh(id, a.tier, param).then((r) => {
        r.jobs.forEach((j) => S.running.add(j.tier));
        renderStatus();
        notice(r.jobs.length ? `REFRESH ${what} ${r.jobs.map((j) => j.tier.toUpperCase()).join("+")}` : `${what}: NOTHING TO REFRESH YET`);
      }).catch(fail);
      break;
    }
    case "build": {
      const id = a.screen ?? S.id, req = buildRequest(id, S.reg.screens);
      if (!req.ok) { notice(req.text, true); break; }
      api.build(id).then(() => notice(req.text)).catch(fail);
      break;
    }
    case "toggle-closed": {
      const t = S.payload?.panels.find((p) => p.rows?.some((r) => r._closed));
      if (t) ui(t.id).toggleClosed(t.id);
      break;
    }
    case "target":
      if (!a.name) { api.prefs().then((p) => notice(`TARGET ${p.target}`)).catch(fail); break; }
      api.setTarget(a.name).then((p) => notice(`TARGET ${p.target}`)).catch(fail);
      break;
    case "alert":
      api.addAlert(a.text).then((r) => notice(`ALERT ${r.id} · ${r.text}`)).catch(fail);
      break;
    case "unalert":
      api.delAlert(a.id).then(() => notice(`REMOVED ${a.id}`)).catch(fail);
      break;
    case "ack":
      api.ack(a.all ? { all: true } : { id: a.id }).then((r) => notice(`ACKED ${r.acked}`)).catch(fail);
      break;
    case "watch": doWatch(a.query); break;
    case "trade": doTrade(a.values); break;
    case "fresh": startFresh(); break;
    case "undo": doUndo(); break;
    case "unwatch":
      api.unwatch(a.ticker).then(async () => { notice(`UNWATCHED ${a.ticker}`); S.reg = await registry(); }).catch(fail);
      break;
    case "soon": notice(`${a.id} — SOON`); break;
    case "error": notice(a.msg, true); break;
  }
}

// ── keyboard ─────────────────────────────────────────────────────────────────
function focusedTable() {
  const tables = S.payload?.panels.filter((p) => p.type === "table") ?? [];
  return tables.find((p) => p.id === S.focus) ?? tables[0] ?? null;
}

const keyHandlers = {
  cmd,
  overlayOpen: () => !$("#overlay").hidden,
  acOpen: () => !$("#ac").hidden,
  acMove(d) { S.ac.i = (S.ac.i + d + S.ac.items.length) % S.ac.items.length; S.ac.moved = true; updateAc(); },
  acAccept() { const it = S.ac.items[S.ac.i]; if (it) { cmd.value = it.run; S.ac.moved = false; updateAc(); } },
  exec() {                                                // see cmd.enterText / cmd.fillsBar
    const it = S.ac.items[S.ac.i], text = enterText(cmd.value, ctx(), it, S.ac.moved);
    if (it) pick(it, text); else run(text);
  },
  history(d) {
    if (!S.history.length) return;
    S.hi = Math.max(-1, Math.min(S.history.length - 1, S.hi + d));
    cmd.value = S.hi < 0 ? "" : S.history[S.hi];
    updateAc();
  },
  escape() {
    if (!$("#overlay").hidden) { closeOverlay(); return; }
    if (cmd.value) { cmd.value = ""; closeAc(); return; }
    if (S.max) { S.max = null; decorate(); return; }
    else if (S.param) {                   // back from a security to wherever you came from
      const { to, stack } = popBack(S.back, hashOf({ id: S.id, param: S.param }));
      S.back = stack;
      if (!changesHash(location.hash, to)) return;
      S.backNav = true;
      location.hash = to;
    }
  },
  help: () => showHelp(),
  maximize(n) {
    S.max = S.max === n ? null : n;
    const id = S.max && document.querySelector(`#grid .panel[data-n="${n}"]`)?.dataset.id;
    if (id) S.focus = id;                  // arrows, sort and Enter act on the panel you can see
    decorate();
  },
  fkey(n) {
    const s = S.reg.screens.find((x) => x.fkey === n), priv = privateAt(n, S.reg.private);
    if (s) run(s.id);
    else if (priv) notice(localOnlyMsg(priv));        // 5 (ALRT) on the static site
  },
  cursor(d) {
    const p = focusedTable();
    if (!p) return;
    const keys = [...document.querySelectorAll(`#grid .panel[data-id="${CSS.escape(p.id)}"] tbody tr`)].map((tr) => tr.dataset.key);
    if (!keys.length) return;
    const t = ui(p.id).tableState(p);
    ui(p.id).setCursor(p.id, keys[Math.max(0, Math.min(keys.length - 1, keys.indexOf(String(t.cursor)) + d))]);
  },
  focusPanel(d) {
    const ids = S.payload?.panels.map((p) => p.id) ?? [];
    if (!ids.length) return;
    S.focus = ids[(ids.indexOf(S.focus) + d + ids.length) % ids.length];
    decorate();
  },
  sort(d) {
    const p = focusedTable();
    if (!p) return;
    const t = ui(p.id).tableState(p);
    const hidden = new Set([...document.querySelectorAll(`#grid .panel[data-id="${CSS.escape(p.id)}"] th[data-col]`)]
      .filter((th) => getComputedStyle(th).display === "none").map((th) => th.dataset.col));
    t.sort = [nextSortCol(p.cols, t.sort[0], d, hidden), "desc"];
    rerender(p.id);
  },
  sortDir(dir) {
    const p = focusedTable();
    if (!p) return;
    const t = ui(p.id).tableState(p);
    t.sort = [t.sort[0], dir];
    rerender(p.id);
  },
  drill() {
    const fp = S.payload?.panels.find((q) => q.id === S.focus);
    if (fp?.type === "form" || fp?.type === "paste") { focusField(fp.id); return; }    // step into it
    if (fp?.type === "banner" && fp.run) { run(fp.run); return; }
    const p = focusedTable();
    const k = p && S.tables[p.id]?.cursor;
    if (!k) return;
    if (p.edit) { editRow(p, k); return; }                          // TRADES: Enter edits the trade
    if (p.enter) { run(p.enter.replace("{key}", k)); return; }      // e.g. PORTFOLIOS: Enter → TARGET <row>
    if (!p.drives) return;
    if (api.isStatic) { notice(localOnlyMsg("SEC")); return; }
    run(k);
  },
  remove() {                                     // Del / Backspace: the FOCUSED TRANSACTIONS table only (asks)
    const p = removable(S.payload?.panels, S.focus);
    const k = p && S.tables[p.id]?.cursor;
    return !!(p && k && !api.isStatic && removeRow(p, k));
  },
  asking: () => !!S.ask,
  onOverlay: () => !!document.activeElement?.closest?.("#overlay"),
  cancelAsk: () => cancelAsk(),
  confirm() {                                    // yes: keyAction allowed it (Enter on the question) or its button
    const fn = S.ask?.fn;
    if (!fn) return false;
    closeOverlay();
    fn();
    return true;
  },
  leaveField() {                                                     // Esc in a form field
    if (!$("#overlay").hidden) { closeOverlay(); return; }
    const pid = document.activeElement?.closest?.("#grid .panel")?.dataset.id;
    document.activeElement?.blur?.();
    if (pid) { S.focus = pid; decorate(); }
    cmd.focus();
  },
};

// ── overlays & notices ───────────────────────────────────────────────────────
function openOverlay(title, html) {
  const ov = $("#overlay");
  ov.innerHTML = `<div class="h"><span>${esc(title)}</span><span class="ctx">Esc close</span></div><div class="ov-body">${html}</div>`;
  ov.classList.remove("ask");                       // whatever was asked before is no longer on screen
  S.ask = null;
  ov.hidden = false;
}

function closeOverlay() { const ov = $("#overlay"); ov.hidden = true; ov.classList.remove("ask"); S.ask = null; cmd.focus(); }

// HELP: this screen's definitions, then the commands and keys. HELP <SCREEN> [<PARAM>]: another
// screen's definitions, fetched without leaving this one.
async function showHelp(id = null, param = null) {
  if (!id || (id === S.id && (!param || param === S.param))) { openHelp(here() ?? "", S.payload?.help); return; }
  const scr = S.reg.screens.find((s) => s.id === id);
  if (scr?.param && !param) { openHelp(id, [], `HELP ${id} <${scr.param_name ?? "TICKER"}> — its definitions come with one`); return; }
  try {
    const p = await api.screen(id, param);
    openHelp(param ? `${id} ${param}` : id, p.help, p.cold ? `${id}: NO STORED DATA YET — no definitions to show` : null);
  } catch (e) { notice(api.why(e), true); }
}

function openHelp(title, help, note = null) {
  const defs = (help ?? []).map((h) => `<dt>${esc(h.h)}</dt><dd>${esc(h.body)}</dd>`).join("");
  const cmds = helpRows(ctx()).map((r) => {
    const off = api.isStatic && r.local;
    return `<tr${off ? ' class="dim"' : ""}><td class="am">${esc(r.cmd)}</td><td>${esc(r.desc)}${off ? " · local terminal only" : ""}</td></tr>`;
  }).join("");
  const keys = keyRows().map(([k, d]) => `<tr><td class="am">${esc(k)}</td><td>${esc(d)}</td></tr>`).join("");
  openOverlay(`HELP ${title}`, `${note ? `<p class="dim">${esc(note)}</p>` : ""}${defs ? `<dl>${defs}</dl>` : ""}`
    + `<h4 class="am">COMMANDS</h4><table>${cmds}</table><h4 class="am">KEYS</h4><table>${keys}</table>`);
}

async function showError() {
  let trace = S.live?.error?.trace ?? "";
  if (!trace && !api.isStatic) {
    try {
      const { jobs } = await api.jobs();
      trace = jobs.find((j) => j.screen === S.key && j.state === "failed")?.trace ?? "";
    } catch { /* show the summary only */ }
  }
  openOverlay(`ERROR ${here()} ${(S.live?.error?.tier ?? "").toUpperCase()}`, `<p class="dn">${esc(S.live?.error?.error ?? "")}</p><pre>${esc(trace)}</pre>`);
}

function notice(msg, err = false) {
  const el = $("#notice");
  el.textContent = msg;
  el.className = err ? "err" : "";
  clearTimeout(S.noticeT);
  S.noticeT = setTimeout(() => { el.textContent = ""; }, 5000);
}

boot();
