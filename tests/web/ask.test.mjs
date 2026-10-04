import { test } from "node:test";
import assert from "node:assert/strict";
import { freshText, removable, replaceText, rowLine, stands, undoText } from "../../web/app/ask.js";
import { fieldKey, keyAction } from "../../web/app/keys.js";

const PANELS = [{ id: "add", type: "form" }, { id: "paste", type: "paste" },
  { id: "txns", type: "table", remove: true, key: "id", rows: [{ id: "r1" }, { id: "r2" }] }];

// A model of the terminal around the keymap: the command bar, the focused panel, the open question, what was
// deleted. It runs keyAction exactly as bindKeys does and the handlers as app.js does (removable, stands).
function terminal({ focus = null, route = "TRADES" } = {}) {
  const cmd = { tagName: "INPUT", id: "cmd" }, input = { tagName: "INPUT" }, overlay = { tagName: "DIV" };
  const t = { bar: "", focus, route, active: cmd, ask: null, deleted: [], ran: [] };
  const cancel = () => { t.ask = null; t.active = cmd; };
  t.key = (key, mods = {}) => {
    const e = { key, code: /^\d$/.test(key) ? `Digit${key}` : key, repeat: false, shiftKey: false, altKey: false,
      ctrlKey: false, metaKey: false, ...mods };
    const a = keyAction(e, { field: fieldKey(e, t.active, cmd), empty: !t.bar.trim(), overlay: !!t.ask,
      asking: !!t.ask, onOverlay: t.active === overlay, ac: false });
    if (a.cancel) cancel();
    switch (a.act) {
      case "remove": {
        const p = removable(PANELS, t.focus);
        if (p) { t.ask = { rows: ["r1"], route: t.route }; t.active = overlay; }
        return;
      }
      case "confirm": t.deleted.push(...t.ask.rows); cancel(); return;
      case "escape": if (t.ask) cancel(); return;
      case "leave": t.focus = "add"; t.active = cmd; return;
      case "exec": t.ran.push(t.bar); t.bar = ""; return;
      case "field": return;
      case "none": break;
      default: break;
    }
    if (t.active === input && key.length === 1) return;                         // typed into the form field
    if (["type", "bar", "none"].includes(a.act) && t.active !== overlay) {     // the browser types into the bar
      t.active = cmd;
      if (key === "Backspace") t.bar = t.bar.slice(0, -1);
      else if (key.length === 1) t.bar += key;
    }
  };
  t.type = (s) => [...s].forEach((c) => t.key(c));
  t.clickField = () => { t.active = input; };
  t.go = (route) => { t.route = route; if (t.ask && !stands(t.ask, { route, rows: ["r1", "r2"] })) cancel(); };
  return t;
}

test("(a) one Backspace too many while fixing a command never deletes a trade — the next key closes the question", () => {
  for (const focus of [null, "txns"]) {                            // even with TRANSACTIONS focused before
    const t = terminal({ focus });
    t.type("BUY SAP.DE 1 @ 10X");
    for (let i = 0; i < "BUY SAP.DE 1 @ 10X".length + 1; i++) t.key("Backspace");
    t.type("BUY SAP.DE 1 @ 10");
    t.key("Enter");
    assert.deepEqual(t.deleted, [], String(focus));
    assert.deepEqual(t.ran, ["BUY SAP.DE 1 @ 10"], String(focus));
  }
});

test("(b) Esc Esc out of the ADD form, Backspace, Enter: nothing deleted (Del acts on TRANSACTIONS only)", () => {
  const t = terminal();
  t.clickField();
  t.type("SAP.DEX");
  t.key("Escape");
  t.key("Escape");
  t.key("Backspace");
  t.key("Enter");
  assert.deepEqual(t.deleted, []);
  assert.equal(removable(PANELS, "add"), null);
  assert.equal(removable(PANELS, null), null);                     // never the first table as a fallback
  assert.equal(removable(PANELS, "txns").id, "txns");
});

test("(c) a question left open dies with a screen change: Enter on PORT deletes nothing", () => {
  const t = terminal({ focus: "txns" });
  t.key("Delete");
  assert.ok(t.ask);
  t.go("PORT");
  t.key("Enter");
  assert.deepEqual(t.deleted, []);
});

test("a question is confirmed only by Enter on the question itself, with an empty bar, never by a held key", () => {
  const ask = { field: null, empty: true, overlay: true, asking: true, onOverlay: true, ac: false };
  const k = (key, more = {}) => ({ key, code: key, repeat: false, shiftKey: false, altKey: false, ctrlKey: false, metaKey: false, ...more });
  assert.equal(keyAction(k("Enter"), ask).act, "confirm");
  assert.equal(keyAction(k("Enter", { repeat: true }), ask).act, "none");
  assert.equal(keyAction(k("Enter"), { ...ask, onOverlay: false }).act, "none");
  assert.equal(keyAction(k("Enter"), { ...ask, empty: false }).act, "none");
  assert.equal(keyAction(k("Escape"), ask).act, "escape");
  for (const key of ["a", "6", "Backspace", "Delete", "ArrowDown", "Tab", "F1"]) assert.equal(keyAction(k(key), ask).cancel, true, key);
  for (const key of ["Shift", "Control", "Alt", "Meta", "Enter", "Escape"]) assert.equal(keyAction(k(key), ask).cancel, false, key);
  const idle = { ...ask, overlay: false, asking: false, onOverlay: false };
  assert.equal(keyAction(k("Delete"), idle).act, "remove");
  assert.equal(keyAction(k("Backspace", { repeat: true }), idle).act, "none");      // holding Backspace: once only
  assert.equal(keyAction(k("Enter", { repeat: true }), idle).act, "none");
});

test("a question stands only on its screen and while every row it names is there", () => {
  const q = { rows: ["r1"], route: "TRADES" };
  assert.equal(stands(q, { route: "TRADES", rows: ["r1", "r2"] }), true);
  assert.equal(stands(q, { route: "PORT", rows: ["r1"] }), false);
  assert.equal(stands(q, { route: "TRADES", rows: ["r2"] }), false);              // edited or deleted meanwhile
  assert.equal(stands({ rows: [], route: "TRADES" }, { route: "TRADES", rows: [] }), true);
  assert.equal(stands(null, { route: "TRADES", rows: [] }), false);
});

test("every question names exactly what it touches", () => {
  const rows = [{ id: "a", date: "2024-12-16", tkr: "SAP.DE", action: "BUY", shares: 4, total: 961 },
                { id: "b", date: "2026-09-01", tkr: "SIE.DE", action: "BUY", shares: 2, total: 461 }];
  assert.equal(rowLine(rows[0]), "16 DEC 24 · BUY 4 SAP.DE · €961.00");
  assert.equal(freshText({ rows, error: null, lines: 2 }),
    "Empties input/portfolio.csv — all 2 trades, 16 DEC 24 to 01 SEP 26. The file as it is now is kept in "
    + "input/backups/ (never rotated) and UNDO brings it back.");
  assert.equal(freshText({ rows: [], error: "portfolio.csv row 3, column Price: '96x' is not a number", lines: 3 }),
    "Empties input/portfolio.csv — 3 lines it cannot read (portfolio.csv row 3, column Price: '96x' is not a number). "
    + "The file as it is now is kept in input/backups/ (never rotated) and UNDO brings it back.");
  assert.equal(replaceText({ rows, error: null, lines: 2 }, { ok: 5, bad: 1 }, "broker.csv"),
    "Your 2 trades (16 DEC 24 to 01 SEP 26) give way to the 5 good rows of broker.csv (1 with an error left out). "
    + "The file as it is now is kept in input/backups/ (never rotated) and UNDO brings it back.");
  assert.equal(undoText({ what: "ADD BUY 1 SAP.DE · €2.00/sh = €2.00", at: "2026-10-04T14:22:05" }),
    "Puts input/portfolio.csv back as it was before: ADD BUY 1 SAP.DE · €2.00/sh = €2.00 (14:22:05). "
    + "UNDO again brings that change back.");
});
