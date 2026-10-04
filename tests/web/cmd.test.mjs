import { test } from "node:test";
import assert from "node:assert/strict";
import {
  ALERT_USAGE, LOCAL_ONLY, VERBS, enterText, fillsBar, helpRows, localOnlyMsg, parse, privateAt, splitRegistry, staticNotice,
  tickerShaped, verbItems,
} from "../../web/app/cmd.js";

const CTX = {
  screens: [{ id: "PORT", status: "live", param: false }, { id: "OPT", status: "soon", param: false },
            { id: "SEC", status: "live", param: true }],
  params: { SEC: ["SAP.DE", "ASML.AS"] },
  targets: ["MINVAR", "RP", "HRP", "BLSHARPE", "BLSAME"],
};

test("screen mnemonics, with or without GO", () => {
  for (const s of ["port", "PORT <GO>", "port go", "PORT<GO>"]) assert.deepEqual(parse(s, CTX), { type: "screen", id: "PORT", param: null });
  assert.deepEqual(parse("opt", CTX), { type: "soon", id: "OPT" });
});
test("tickers in your book open the security screen", () => {
  assert.deepEqual(parse("sap.de", CTX), { type: "screen", id: "SEC", param: "SAP.DE" });
  assert.deepEqual(parse("asml.as <go>", CTX), { type: "screen", id: "SEC", param: "ASML.AS" });
  assert.deepEqual(parse("sec sap.de", CTX), { type: "screen", id: "SEC", param: "SAP.DE" });
});
test("unknown words and tickers not in the book", () => {
  assert.deepEqual(parse("hlep", CTX), { type: "error", msg: "UNKNOWN: HLEP — NOT A COMMAND OR A TICKER IN YOUR BOOK" });
  assert.deepEqual(parse("sec ^gspc", CTX), { type: "error", msg: "^GSPC: NOT A TICKER" });
  assert.deepEqual(parse("sec", CTX), { type: "error", msg: "SEC <TICKER>" });
});
test("help, refresh, closed", () => {
  assert.deepEqual(parse("help", CTX), { type: "help", screen: null, param: null });
  assert.deepEqual(parse("?", CTX), { type: "help", screen: null, param: null });
  assert.deepEqual(parse("refresh quote", CTX), { type: "refresh", screen: null, tier: "quote" });
  assert.deepEqual(parse("refresh port daily", CTX), { type: "refresh", screen: "PORT", tier: "daily" });
  assert.deepEqual(parse("closed", CTX), { type: "toggle-closed" });
});
test("LEGACY is gone with the old HTML pages: an unknown command", () => {
  for (const s of ["legacy", "legacy report"]) {
    assert.deepEqual(parse(s, CTX), { type: "error", msg: "UNKNOWN: LEGACY — NOT A COMMAND OR A TICKER IN YOUR BOOK" });
  }
  assert.ok(!helpRows(CTX).some((r) => r.cmd.includes("LEGACY")));
});
test("TARGET", () => {
  assert.deepEqual(parse("target", CTX), { type: "target", name: null });
  assert.deepEqual(parse("target rp", CTX), { type: "target", name: "RP" });
  assert.deepEqual(parse("target nope", CTX), { type: "error", msg: "TARGET <MINVAR|RP|HRP|BLSHARPE|BLSAME>" });
  // the static snapshot's screens.json lists no targets: the name passes (the snapshot answers TARGET IS HRP)
  assert.deepEqual(parse("target rp", { ...CTX, targets: [] }), { type: "target", name: "RP" });
});
test("blank is a no-op", () => {
  assert.deepEqual(parse("   ", CTX), { type: "noop" });
});
test("FULL toggles full screen, with or without GO", () => {
  for (const s of ["full", "FULL <GO>", "fs"]) assert.deepEqual(parse(s, CTX), { type: "fullscreen" });
});
test("WATCH and UNWATCH", () => {
  assert.deepEqual(parse("watch rheinmetall", CTX), { type: "watch", query: "RHEINMETALL" });
  assert.deepEqual(parse("WATCH deutsche bank <GO>", CTX), { type: "watch", query: "DEUTSCHE BANK" });
  assert.deepEqual(parse("watch", CTX), { type: "error", msg: "WATCH <NAME|TICKER>" });
  assert.deepEqual(parse("unwatch rhm.de", CTX), { type: "unwatch", ticker: "RHM.DE" });
  assert.deepEqual(parse("unwatch", CTX), { type: "error", msg: "UNWATCH <TICKER>" });
});
test("ALERT, UNALERT, ACK, ALERTS", () => {
  assert.deepEqual(parse("alert sap.de < 180", CTX), { type: "alert", text: "SAP.DE < 180" });
  assert.deepEqual(parse("ALERT PORT DAY -2 <GO>", CTX), { type: "alert", text: "PORT DAY -2" });
  assert.deepEqual(parse("alert", CTX), { type: "error", msg: ALERT_USAGE });
  assert.deepEqual(parse("unalert a7", CTX), { type: "unalert", id: "A7" });
  assert.deepEqual(parse("ack e12", CTX), { type: "ack", all: false, id: "E12" });
  assert.deepEqual(parse("ack all", CTX), { type: "ack", all: true, id: null });
  assert.deepEqual(parse("ack", CTX), { type: "error", msg: "ACK <ID|ALL>" });
  assert.deepEqual(parse("alerts", CTX), { type: "error", msg: "UNKNOWN SCREEN ALRT" });
  const live = { ...CTX, screens: [...CTX.screens, { id: "ALRT", status: "live", param: false }] };
  assert.deepEqual(parse("alerts", live), { type: "screen", id: "ALRT", param: null });
  const soon = { ...CTX, screens: [...CTX.screens, { id: "ALRT", status: "soon", param: false }] };
  assert.deepEqual(parse("ALERTS", soon), { type: "soon", id: "ALRT" });
});
test("any ticker-shaped word opens SEC and the server decides", () => {
  assert.deepEqual(parse("sec zzz.f", CTX), { type: "screen", id: "SEC", param: "ZZZ.F" });
  assert.deepEqual(parse("SEC AAPL", CTX), { type: "screen", id: "SEC", param: "AAPL" });
  assert.deepEqual(parse("rhm.de", CTX), { type: "screen", id: "SEC", param: "RHM.DE" });
  assert.deepEqual(parse("1913.hk <go>", CTX), { type: "screen", id: "SEC", param: "1913.HK" });
  assert.equal(parse("aapl", CTX).type, "error");                 // no dot or digit: not ticker-shaped
  assert.ok(tickerShaped("BRK-B.F") && !tickerShaped("HLEP") && !tickerShaped("^GDAXI") && !tickerShaped("A.B.C.D.E.F.G.H.I"));
  const noSec = { ...CTX, screens: CTX.screens.filter((x) => x.id !== "SEC") };
  assert.equal(parse("rhm.de", noSec).type, "error");
});

test("Enter runs the typed text, a highlight the user moved to, or the completion of a partial word", () => {
  assert.equal(enterText("sa", CTX, { label: "SAP.DE", run: "SAP.DE" }, false), "SAP.DE");
  assert.equal(enterText("port", CTX, { label: "PORT", run: "PORT" }, false), "port");
  assert.equal(enterText("port", CTX, { label: "OPT", run: "OPT" }, true), "OPT");
  assert.equal(enterText("hlep", CTX, undefined, false), "hlep");
});
test("WATCH: an unmoved highlight is not a pick — the typed name resolves like the server's lookup", () => {
  const top = { label: "WATCH RNMBY", run: "WATCH RNMBY" };          // top row by turnover, not the exact name
  assert.equal(enterText("watch rheinmetall", CTX, top, false), "watch rheinmetall");
  assert.equal(enterText("watch rheinmetall", CTX, top, true), "WATCH RNMBY");
});

test("BUILD <screen> runs a heavy build; REFRESH never does", () => {
  assert.deepEqual(parse("build model", CTX), { type: "build", screen: "MODEL" });
  assert.deepEqual(parse("BUILD <GO>", CTX), { type: "build", screen: null });
  assert.deepEqual(parse("refresh model", CTX), { type: "refresh", screen: "MODEL", tier: null });
});

test("a parametrized screen takes its own param word: DOC <ID> and UNKNOWN DOC, never ticker wording", () => {
  const ctx = { ...CTX, screens: [...CTX.screens, { id: "DOC", status: "live", param: true, param_name: "ID", unknown_param: "UNKNOWN DOC" }],
    params: { ...CTX.params, DOC: ["A_ONE", "B_TWO"] } };
  assert.deepEqual(parse("doc", ctx), { type: "error", msg: "DOC <ID>" });
  assert.deepEqual(parse("doc no_such", ctx), { type: "error", msg: "NO_SUCH: UNKNOWN DOC" });
  assert.deepEqual(parse("doc a_one", ctx), { type: "screen", id: "DOC", param: "A_ONE" });
  assert.deepEqual(parse("sec ^gspc", ctx), { type: "error", msg: "^GSPC: NOT A TICKER" });   // SEC unchanged
});
const LIVE = {
  screens: [{ id: "PORT", fkey: 1, status: "live", param: false, build: null },
            { id: "MODEL", fkey: 5, status: "live", param: false, build: "~10 MIN" },
            { id: "BATCH", fkey: 6, status: "live", param: false, build: "~12 MIN" },
            { id: "ALRT", fkey: 7, status: "live", param: false, build: null },
            { id: "SEC", fkey: null, status: "live", param: true, param_name: "TICKER", title: "Security" },
            { id: "DOC", fkey: null, status: "live", param: true, param_name: "ID", unknown_param: "UNKNOWN DOC", title: "Document" }],
  params: { SEC: ["SAP.DE"], DOC: ["A_ONE"] }, targets: ["MINVAR", "HRP"],
};
const head = (cmd) => cmd.split(" ")[0];

test("HELP lists exactly the parser's verbs, each by its usage, plus the registry's screens", () => {
  const rows = helpRows(LIVE), cmds = rows.map((r) => r.cmd);
  const verbs = VERBS.map((v) => v.verb);
  assert.deepEqual(rows.slice(-verbs.length).map((r) => head(r.cmd)), verbs);   // every verb, nothing else
  for (const w of verbs) assert.notEqual(parse(w, LIVE).msg?.startsWith("UNKNOWN"), true, w);
  for (const v of VERBS) for (const a of v.alias ?? []) assert.equal(parse(a, LIVE).type, parse(v.verb, LIVE).type, a);
  assert.deepEqual(cmds.slice(0, 3), ["PORT · MODEL · BATCH · ALRT", "SEC <TICKER>", "DOC <ID>"]);
  for (const c of cmds.slice(0, 3).flatMap((x) => x.split(" · ")).map(head)) assert.notEqual(parse(c, LIVE).msg?.startsWith("UNKNOWN"), true, c);
  for (const u of ["SEC <TICKER>", "DOC <ID>", "REFRESH [SCREEN] [TIER]", "BUILD <MODEL|BATCH>", "TARGET <MINVAR|HRP>", "HELP [SCREEN]",
                   "WATCH <NAME|TICKER>", "UNWATCH <TICKER>", "ALERT <RULE>", "UNALERT <ID>", "ACK <ID|ALL>", "ALERTS", "CLOSED", "FULL"]) {
    assert.ok(cmds.includes(u), u);
  }
  assert.ok(!cmds.some((c) => /LEGACY|\bF[2-9]\b/.test(c)));
});
test("a verb missing its argument answers with the usage HELP shows", () => {
  for (const v of VERBS.filter((x) => x.arg && x.verb !== "BUILD" && x.verb !== "TARGET" && x.verb !== "ALERT")) {
    assert.deepEqual(parse(v.verb, LIVE), { type: "error", msg: v.usage(LIVE) }, v.verb);
  }
  assert.deepEqual(parse("target nope", LIVE), { type: "error", msg: "TARGET <MINVAR|HRP>" });
});
test("HELP <SCREEN> opens that screen's help; an unknown one is refused", () => {
  assert.deepEqual(parse("help model", LIVE), { type: "help", screen: "MODEL", param: null });
  assert.deepEqual(parse("help doc a_one", LIVE), { type: "help", screen: "DOC", param: "A_ONE" });
  assert.deepEqual(parse("help port extra", LIVE), { type: "help", screen: "PORT", param: null });
  assert.deepEqual(parse("help nope", LIVE), { type: "error", msg: "UNKNOWN SCREEN NOPE" });
});
test("the palette offers every verb the terminal runs here; an argument verb fills the bar", () => {
  const live = verbItems(LIVE).map((i) => i.label);
  for (const w of ["WATCH <NAME|TICKER>", "UNWATCH <TICKER>", "ALERT <RULE>", "UNALERT <ID>", "ACK <ID|ALL>", "ALERTS", "CLOSED",
                   "REFRESH [SCREEN] [TIER]", "FULL", "HELP [SCREEN]"]) assert.ok(live.includes(w), w);
  assert.deepEqual(verbItems(LIVE).find((i) => i.label === "WATCH <NAME|TICKER>"), {
    label: "WATCH <NAME|TICKER>", desc: "add a name to MKT's watchlist", run: "WATCH ", fill: true });
  assert.deepEqual(verbItems({ ...LIVE, static: true }).map((i) => i.label), ["FULL", "HELP [SCREEN]"]);
  const it = { label: "WATCH <NAME|TICKER>", run: "WATCH ", fill: true };
  assert.equal(fillsBar("wat", it, enterText("wat", LIVE, it, false)), true);          // "wat" Enter -> "WATCH " in the bar
  assert.equal(fillsBar("watch", it, enterText("watch", LIVE, it, false)), false);     // already there: run, show the usage
  assert.equal(fillsBar("sa", { label: "SAP.DE", run: "SAP.DE" }, "SAP.DE"), false);
});
test("a verb typed in full runs as typed: an unmoved palette row never replaces it", () => {
  const alerts = { label: "ALERTS", run: "ALERTS" };                 // ranks above ALERT <RULE> for "alert"
  assert.equal(enterText("alert", LIVE, alerts, false), "alert");    // -> ALERT's usage, not the ALRT screen
  assert.equal(enterText("help nope", LIVE, { label: "HELP [SCREEN]", run: "HELP" }, false), "help nope");
  assert.equal(enterText("alert", LIVE, alerts, true), "ALERTS");    // a row the user moved to still wins
  assert.equal(enterText("aler", LIVE, alerts, false), "ALERTS");    // a partial word still completes
});

const STATIC_OLD = { screens: [{ id: "PORT", fkey: 1, status: "live", public: true, param: false },
                               { id: "MKT", fkey: 4, status: "live", public: true, param: false }] };
const STATIC_NEW = { screens: [...STATIC_OLD.screens, { id: "NOTE", fkey: 6, title: "Notebook", public: false },
                               { id: "ALRT", fkey: 5, title: "Alerts", public: false },
                               { id: "SEC", fkey: null, title: "Security", public: false }] };

test("the static app reads private screens from screens.json, or falls back to LOCAL_ONLY for an old one", () => {
  const n = splitRegistry(STATIC_NEW, true);
  assert.deepEqual(n.screens.map((s) => s.id), ["PORT", "MKT"]);
  assert.deepEqual(n.private, [{ id: "NOTE", fkey: 6, title: "Notebook" }, { id: "ALRT", fkey: 5, title: "Alerts" },
                               { id: "SEC", fkey: null, title: "Security" }]);
  const o = splitRegistry(STATIC_OLD, true);
  assert.deepEqual(o.private.map((p) => [p.id, p.fkey]), Object.entries(LOCAL_ONLY));
  assert.deepEqual(splitRegistry(STATIC_NEW, false), { ...STATIC_NEW, private: [] });   // the live terminal serves them all
  assert.deepEqual([5, 6, 1, 8].map((k) => privateAt(k, o.private)), ["ALRT", "TRADES", null, null]);
  assert.deepEqual([5, 6, 7].map((k) => privateAt(k, n.private)), ["ALRT", "NOTE", null]);
});
test("the static snapshot names a private screen LOCAL TERMINAL ONLY — never unknown, never a load", () => {
  const reg = splitRegistry(STATIC_NEW, true);
  const ctx = { screens: reg.screens, private: reg.private, params: {}, targets: [], static: true };
  for (const [s, id] of [["note", "NOTE"], ["note <go>", "NOTE"], ["alrt", "ALRT"], ["alerts", "ALRT"],
                         ["sec sap.de", "SEC"], ["sap.de", "SEC"], ["help note", "NOTE"]]) {
    assert.deepEqual(parse(s, ctx), { type: "private", id }, s);
  }
  assert.equal(localOnlyMsg("NOTE"), "NOTE IS PRIVATE — LOCAL TERMINAL ONLY");
  assert.equal(parse("hlep", ctx).type, "error");                    // a typo stays unknown
  assert.equal(parse("note", { ...ctx, private: [] }).type, "error");  // the live terminal lists NOTE itself
  const live = { ...ctx, private: [], screens: [...ctx.screens, { id: "NOTE", status: "live", param: false }] };
  assert.deepEqual(parse("note", live), { type: "screen", id: "NOTE", param: null });
  assert.ok(helpRows(ctx).some((r) => r.cmd === "NOTE · ALRT · SEC" && r.local));
});
test("BUILD is in HELP only once a screen declares a build (the core has none)", () => {
  assert.ok(!helpRows(CTX).some((r) => r.cmd.startsWith("BUILD")));
  assert.ok(helpRows(LIVE).some((r) => r.cmd === "BUILD <MODEL|BATCH>"));
  assert.ok(!verbItems(LIVE).some((i) => i.label.startsWith("BUILD")));           // the palette lists BUILD <screen> itself
  assert.deepEqual(parse("build", CTX), { type: "build", screen: null });          // typed anyway: the app answers
});
test("the static snapshot answers a live-only command in one phrasing", () => {
  assert.equal(staticNotice({ type: "watch" }), "WATCHLIST IS PRIVATE — LOCAL TERMINAL ONLY");
  assert.equal(staticNotice({ type: "ack" }), "ALRT IS PRIVATE — LOCAL TERMINAL ONLY");
  assert.equal(staticNotice({ type: "toggle-closed" }), "CLOSED IS PRIVATE — LOCAL TERMINAL ONLY");
  assert.equal(staticNotice({ type: "refresh" }), "STATIC SNAPSHOT — NOTHING TO REFRESH");
  assert.equal(staticNotice({ type: "screen", id: "PORT" }), null);
  assert.equal(staticNotice({ type: "help" }), null);
});

test("BUY / SELL / BONUS add a trade from any screen; bare, they answer with their usage", () => {
  assert.deepEqual(parse("buy sap.de 4 @ 240", CTX), { type: "trade",
    values: { action: "buy", ticker: "SAP.DE", shares: "4", pps: "240", total: "", date: "", fee: "" } });
  assert.deepEqual(parse("BUY SAP.DE 4 = 961", CTX).values.total, "961");
  assert.deepEqual(parse("sell alv.de 2 @ 410 2026-03-02", CTX).values, { action: "sell", ticker: "ALV.DE", shares: "2",
    pps: "410", total: "", date: "2026-03-02", fee: "" });
  assert.deepEqual(parse("bonus amz.f 0.05 @ 190", CTX).values.action, "bonus");
  assert.deepEqual(parse("buy sap.de 4 @ 240 fee 1", CTX).values.fee, "1");
  assert.deepEqual(parse("buy", CTX), { type: "error", msg: "BUY <TICKER> <SHARES> @ <PRICE> | = <TOTAL> [DATE] [FEE x]" });
  assert.deepEqual(parse("sell sap.de 4", CTX), { type: "error", msg: "SELL <TICKER> <SHARES> @ <PRICE> | = <TOTAL> [DATE] [FEE x]" });
  assert.ok(helpRows(CTX).some((r) => r.cmd === "BONUS <TICKER> <SHARES> @ <PRICE> | = <VALUE> [DATE]" && r.local));
  assert.equal(staticNotice({ type: "trade" }), "TRADES IS PRIVATE — LOCAL TERMINAL ONLY");
});

test("START FRESH empties your trades (the app asks first); START alone says the whole command", () => {
  assert.deepEqual(parse("start fresh", CTX), { type: "fresh" });
  assert.deepEqual(parse("start", CTX), { type: "error", msg: "START FRESH" });
  assert.deepEqual(parse("start over", CTX), { type: "error", msg: "START FRESH" });
  const item = verbItems(CTX).find((i) => i.label === "START FRESH");
  assert.deepEqual([item.run, item.fill], ["START FRESH", false]);           // picked in the palette: it runs
  assert.equal(staticNotice({ type: "fresh" }), "TRADES IS PRIVATE — LOCAL TERMINAL ONLY");
});

test("UNDO puts back your trades file as it was before the last TRADES change (the app asks first)", () => {
  assert.deepEqual(parse("undo", CTX), { type: "undo" });
  assert.equal(staticNotice({ type: "undo" }), "TRADES IS PRIVATE — LOCAL TERMINAL ONLY");
  assert.ok(helpRows(CTX).some((r) => r.cmd === "UNDO" && r.local));
});
