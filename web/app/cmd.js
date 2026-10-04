// Command grammar. Pure: text -> action object.
// Resolution order: verb → screen mnemonic → ticker in a parametrized screen's list →
// ticker-shaped word (SEC; the server decides) → unknown.
const TIERS = ["QUOTE", "DAILY", "HEAVY"];
const TICKER = /^[A-Z0-9][A-Z0-9.\-]{0,14}$/;
// A bare word opens SEC only if it looks like a ticker: a dot or a digit ("RHM.DE", "1913.HK"),
// so a typo ("hlep") stays an error instead of a 404 round trip.
export const tickerShaped = (w) => TICKER.test(w) && /[.\d]/.test(w);

// ── private screens in the static snapshot ──────────────────────────────────
// The snapshot's data/screens.json lists every screen with its `public` flag. A private one (public:
// false: its id, number key and title, never its data) is named, not loaded: the static app answers
// "<ID> IS PRIVATE — LOCAL TERMINAL ONLY", never an unknown command or a failed load. LOCAL_ONLY is the
// fallback for a screens.json that listed only the published screens (exports before the flag);
// tests/web/test_local_only.py pins it to monitor.screens.
export const LOCAL_ONLY = { ALRT: 5, SEC: null };

// The registry as the app uses it: `screens` it can open, `private` it can only name (static only).
export function splitRegistry(reg, isStatic) {
  const all = reg?.screens ?? [];
  if (!isStatic) return { ...reg, screens: all, private: [] };
  const flagged = all.filter((s) => s.public === false);
  const priv = flagged.length
    ? flagged.map((s) => ({ id: s.id, fkey: s.fkey ?? null, title: s.title ?? s.id }))
    : Object.entries(LOCAL_ONLY).filter(([id]) => !all.some((s) => s.id === id))
      .map(([id, fkey]) => ({ id, fkey, title: id }));
  return { ...reg, screens: all.filter((s) => s.public !== false), private: priv };
}
export const privateAt = (n, priv) => (priv ?? []).find((p) => p.fkey === n)?.id ?? null;
export const localOnlyMsg = (id) => `${id} IS PRIVATE — LOCAL TERMINAL ONLY`;
const isPrivate = (id, ctx) => (ctx.private ?? []).some((p) => p.id === id);

// What the static snapshot answers to a command only the live terminal runs (null: it runs it).
export function staticNotice(a) {
  switch (a.type) {
    case "refresh": return "STATIC SNAPSHOT — NOTHING TO REFRESH";
    case "build": return "STATIC SNAPSHOT — NOTHING TO BUILD";
    case "target": return "STATIC SNAPSHOT — TARGET IS HRP";
    case "watch": case "unwatch": return localOnlyMsg("WATCHLIST");
    case "alert": case "unalert": case "ack": return localOnlyMsg("ALRT");
    case "toggle-closed": return localOnlyMsg("CLOSED");
    default: return null;
  }
}

// ── verbs ────────────────────────────────────────────────────────────────────
// What ALERT accepts: monitor/alerts/rules.py USAGE word for word (tests/web/test_alert_usage.py).
export const ALERT_USAGE = "ALERT <TKR> < x · <TKR> > x · <TKR|*> MOVE n · <TKR|*> DD n · PORT DAY -n · "
  + "PORT DRIFT n · PORT WEIGHT n · EVENT · STALE";

const fixed = (s) => () => s;
const err = (msg) => ({ type: "error", msg });
const oneOf = (names, none) => `<${(names.length ? names : [none]).join("|")}>`;

// Every command word besides screen mnemonics and tickers — the one list the parser, HELP and the
// palette all read, so HELP can neither miss a verb nor list one that does not exist. `usage` is
// also the error a verb missing its argument shows; `local`: only the live terminal runs it;
// `arg`: picked in the palette, it fills the bar for the argument instead of running; `when`: HELP lists
// it only then (BUILD: once a screen declares a build).
export const VERBS = [
  { verb: "TARGET", usage: (c) => `TARGET ${oneOf(c.targets ?? [], "NAME")}`, local: true, arg: true,
    desc: "the optimizer's target portfolio (alone: shows it; Enter on OPT's PORTFOLIOS sets it too)",
    parse: (rest, c) => (!rest[0] ? { type: "target", name: null }     // no target list (static): the name passes
      : !c.targets?.length || c.targets.includes(rest[0]) ? { type: "target", name: rest[0] } : err(VERB.TARGET.usage(c))) },
  { verb: "WATCH", usage: fixed("WATCH <NAME|TICKER>"), local: true, arg: true, desc: "add a name to MKT's watchlist",
    parse: (rest) => (rest.length ? { type: "watch", query: rest.join(" ") } : err(VERB.WATCH.usage())) },
  { verb: "UNWATCH", usage: fixed("UNWATCH <TICKER>"), local: true, arg: true, desc: "drop it from the watchlist",
    parse: (rest) => (rest[0] ? { type: "unwatch", ticker: rest[0] } : err(VERB.UNWATCH.usage())) },
  { verb: "ALERT", usage: fixed("ALERT <RULE>"), local: true, arg: true, desc: "add an alert rule (ALERT alone lists the forms)",
    parse: (rest) => (rest.length ? { type: "alert", text: rest.join(" ") } : err(ALERT_USAGE)) },
  { verb: "UNALERT", usage: fixed("UNALERT <ID>"), local: true, arg: true, desc: "remove an alert rule",
    parse: (rest) => (rest[0] ? { type: "unalert", id: rest[0] } : err(VERB.UNALERT.usage())) },
  { verb: "ACK", usage: fixed("ACK <ID|ALL>"), local: true, arg: true, desc: "acknowledge an alert (its id), every alert of a rule (the rule id) or ALL",
    parse: (rest) => (!rest[0] ? err(VERB.ACK.usage())
      : rest[0] === "ALL" ? { type: "ack", all: true, id: null } : { type: "ack", all: false, id: rest[0] }) },
  { verb: "ALERTS", usage: fixed("ALERTS"), local: true, desc: "open ALRT", parse: (rest, c) => screenOf("ALRT", c) },
  { verb: "REFRESH", usage: fixed("REFRESH [SCREEN] [TIER]"), local: true,
    desc: "recompute now: this screen or SCREEN, every tier or TIER (QUOTE · DAILY · HEAVY)",
    parse: (rest) => (TIERS.includes(rest[0]) ? { type: "refresh", screen: null, tier: rest[0].toLowerCase() }
      : { type: "refresh", screen: rest[0] ?? null, tier: rest[1]?.toLowerCase() ?? null }) },
  { verb: "BUILD", usage: (c) => `BUILD ${oneOf((c.screens ?? []).filter((s) => s.build).map((s) => s.id), "SCREEN")}`,
    local: true, arg: true, desc: "rerun a screen's multi-minute build now (REFRESH only reloads it)",
    when: (c) => (c.screens ?? []).some((s) => s.build),
    parse: (rest) => ({ type: "build", screen: rest[0] ?? null }) },
  { verb: "CLOSED", usage: fixed("CLOSED"), local: true, desc: "show / hide closed positions",
    parse: () => ({ type: "toggle-closed" }) },
  { verb: "FULL", alias: ["FS"], usage: fixed("FULL"), desc: "full screen on / off (Esc also leaves)",
    parse: () => ({ type: "fullscreen" }) },
  { verb: "HELP", alias: ["?"], usage: fixed("HELP [SCREEN]"), desc: "this help, or another screen's",
    parse: (rest, c) => helpOf(rest, c) },
];
const VERB = Object.fromEntries(VERBS.map((v) => [v.verb, v]));
const BY_WORD = new Map(VERBS.flatMap((v) => [v.verb, ...(v.alias ?? [])].map((w) => [w, v])));

export function parse(input, ctx) {
  const text = String(input).trim().toUpperCase().replace(/\s*<GO>$/, "").replace(/\s+GO$/, "");
  const words = text.split(/\s+/).filter(Boolean);
  if (!words.length) return { type: "noop" };
  const [head, ...rest] = words;
  const verb = BY_WORD.get(head);
  if (verb) return verb.parse(rest, ctx);
  if (isPrivate(head, ctx)) return { type: "private", id: head };
  const scr = (ctx.screens ?? []).find((s) => s.id === head);
  if (scr) {
    if (scr.status !== "live" || !scr.param) return screenOf(scr.id, ctx);
    const p = rest[0], what = scr.param_name ?? "TICKER";        // SEC <TICKER>, another screen's own word
    if (p && ((ctx.params?.[scr.id] ?? []).includes(p) || TICKER.test(p))) return { type: "screen", id: scr.id, param: p };
    const miss = what === "TICKER" ? "NOT A TICKER" : scr.unknown_param ?? `UNKNOWN ${scr.id}`;
    return err(p ? `${p}: ${miss}` : `${scr.id} <${what}>`);
  }
  for (const [sid, list] of Object.entries(ctx.params ?? {})) {
    if (list.includes(head)) return { type: "screen", id: sid, param: head };
  }
  if (tickerShaped(head)) {
    if ((ctx.screens ?? []).some((s) => s.id === "SEC" && s.status === "live")) return { type: "screen", id: "SEC", param: head };
    if (isPrivate("SEC", ctx)) return { type: "private", id: "SEC" };          // a ticker on the static snapshot
  }
  return err(`UNKNOWN: ${head} — NOT A COMMAND OR A TICKER IN YOUR BOOK`);
}

// An unparametrized screen by id: live → open it, otherwise SOON.
function screenOf(id, ctx) {
  if (isPrivate(id, ctx)) return { type: "private", id };
  const scr = (ctx.screens ?? []).find((s) => s.id === id);
  if (!scr) return err(`UNKNOWN SCREEN ${id}`);
  return scr.status === "live" ? { type: "screen", id, param: null } : { type: "soon", id };
}

// HELP: this screen's; HELP <SCREEN> [<PARAM>]: that screen's (the app fetches its definitions).
function helpOf([id, param], ctx) {
  if (!id) return { type: "help", screen: null, param: null };
  if (isPrivate(id, ctx)) return { type: "private", id };
  const scr = (ctx.screens ?? []).find((s) => s.id === id);
  if (!scr) return err(`UNKNOWN SCREEN ${id}`);
  return { type: "help", screen: scr.id, param: scr.param ? param ?? null : null };
}

// HELP's COMMANDS table: the screens the registry serves, the private ones (static snapshot), then
// every verb. `local`: the static snapshot cannot run it.
export function helpRows(ctx) {
  const screens = ctx.screens ?? [];
  const plain = screens.filter((s) => !s.param).sort((a, b) => (a.fkey ?? 99) - (b.fkey ?? 99));
  const rows = [];
  if (plain.length) rows.push({ cmd: plain.map((s) => s.id).join(" · "), desc: "open a screen (or its number key)", local: false });
  for (const s of screens.filter((x) => x.param && x.status === "live")) {
    const desc = s.id === "SEC" ? "open a security (a ticker alone opens it too)" : `open a ${String(s.title ?? s.id).toLowerCase()}`;
    rows.push({ cmd: `${s.id} <${s.param_name ?? "TICKER"}>`, desc, local: false });
  }
  if (ctx.private?.length) rows.push({ cmd: ctx.private.map((p) => p.id).join(" · "), desc: "private screens", local: true });
  for (const v of VERBS.filter((x) => !x.when || x.when(ctx))) rows.push({ cmd: v.usage(ctx), desc: v.desc, local: !!v.local });
  return rows;
}

// The verbs the command palette offers: everything the terminal runs here, less TARGET and BUILD (the
// palette lists TARGET <name> and BUILD <screen> one by one). An `arg` verb fills the bar.
export function verbItems(ctx) {
  return VERBS.filter((v) => !(ctx.static && v.local) && v.verb !== "TARGET" && v.verb !== "BUILD")
    .map((v) => ({ label: v.usage(ctx), desc: v.desc, run: v.arg ? `${v.verb} ` : v.verb, fill: !!v.arg }));
}

// What Enter runs, given the highlighted autocomplete row `item` and whether the user `moved` to it:
// the typed text, except a row the user moved to, or a partial ticker / unknown word whose top
// suggestion starts with it ("sa" -> SAP.DE). A verb typed in full runs as typed ("alert" shows ALERT's
// usage, never the ALERTS row ranked above it). WATCH never takes an unmoved row (the list is ranked by
// turnover): the typed name resolves as the server's lookup does (resolveWatch: exact name wins).
export function enterText(typed, ctx, item, moved) {
  const raw = String(typed).trim().toUpperCase();
  const a = parse(raw, ctx), kind = a.type;
  const unknownTicker = kind === "screen" && a.param && !(ctx.params?.[a.id] ?? []).includes(a.param);
  const verb = BY_WORD.has(raw.split(/\s+/)[0]);
  const pick = item && (moved
    || (!verb && (kind === "soon" || kind === "error" || unknownTicker) && item.label.toUpperCase().startsWith(raw)));
  return pick ? item.run : typed;
}

// Enter on a palette row that fills the bar (WATCH <NAME|TICKER>): fill it — unless the bar already
// holds that verb, when it runs and shows the verb's usage.
export const fillsBar = (typed, item, text) =>
  !!item?.fill && text === item.run && String(typed).trim().toUpperCase() !== item.run.trim();
