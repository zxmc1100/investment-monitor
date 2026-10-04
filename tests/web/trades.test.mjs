import { test } from "node:test";
import assert from "node:assert/strict";
import {
  ACTIONS, compute, decodeBytes, describe, duplicateOf, editValues, eur, fmtMoney, fmtQty, formStatus, isEur,
  parseDate, parseNum, pasteSummary, rankSuggestions, round, tradeCommand, tradeFrom, typed,
} from "../../web/app/trades.js";

const TODAY = "2026-10-04";

test("rounding is half up on the number as written, like the server's", () => {
  assert.equal(round(3 * 0.335, 2), 1.01);
  assert.equal(round(2.675, 2), 2.68);
  assert.equal(round(100 / 3, 4), 33.3333);
  assert.equal(round(1e-7, 2), 0);
});

test("price per share <-> total: fees only when entered", () => {
  assert.deepEqual(compute("buy", 4, { pps: 240 }), { total: 960, pps: 240 });
  assert.deepEqual(compute("buy", 4, { pps: 240, fee: 1 }), { total: 961, pps: 240 });
  assert.deepEqual(compute("sell", 2, { pps: 410, fee: 1 }), { total: 819, pps: 410 });
  assert.deepEqual(compute("bonus", 0.05, { pps: 190, fee: 1 }), { total: 9.5, pps: 190 });
  assert.deepEqual(compute("buy", 4, { total: 961, fee: 1 }), { total: 961, pps: 240.25 });
  assert.deepEqual(compute("buy", 3, { total: 100 }), { total: 100, pps: 33.3333 });
  assert.deepEqual(compute("bonus", 0.1234, { total: 12.3456 }), { total: 12.3456, pps: 100.0454 });   // as given
  for (const [args, msg] of [[["buy", 0, { pps: 1 }], "SHARES MUST BE > 0"], [["buy", 1, {}], "PRICE PER SHARE OR TOTAL REQUIRED"],
    [["buy", 1, { pps: 0 }], "PRICE PER SHARE MUST BE > 0"], [["buy", 1, { total: -1 }], "TOTAL MUST BE > 0"],
    [["buy", 1, { pps: 1, fee: -1 }], "FEE MUST BE 0 OR MORE"], [["sell", 1, { pps: 1, fee: 1 }], "THE FEE LEAVES NOTHING: TOTAL €0.00"],
    [["hold", 1, { pps: 1 }], "ACTION MUST BE BUY, SELL OR BONUS"]]) {
    assert.throws(() => compute(...args), { message: msg });
  }
});

test("one number as typed: a decimal point or comma, never a thousands separator", () => {
  assert.equal(parseNum("240", "SHARES"), 240);
  assert.equal(parseNum(" 240,50 ", "SHARES"), 240.5);
  assert.equal(parseNum("1.234", "SHARES"), 1.234);
  assert.throws(() => parseNum("1,234", "SHARES"), { message: "SHARES: '1,234' HAS A THOUSANDS SEPARATOR — WRITE IT WITHOUT ONE, E.G. 1234.56" });
  assert.throws(() => parseNum("1.234,56", "TOTAL"), /THOUSANDS SEPARATOR/);
  assert.throws(() => parseNum("1 234", "TOTAL"), /THOUSANDS SEPARATOR/);
  assert.throws(() => parseNum("x", "SHARES"), { message: "SHARES: 'x' IS NOT A NUMBER" });
  assert.throws(() => parseNum("1500000000", "SHARES"), { message: "SHARES IS TOO LARGE (OVER 1,000,000,000)" });
  assert.throws(() => parseNum(Number.NaN, "SHARES"), { message: "SHARES IS NOT A NUMBER" });
  assert.throws(() => parseNum(Infinity, "TOTAL"), { message: "TOTAL IS NOT A NUMBER" });
});

test("dates: today by default, three ways to write one, never in the future", () => {
  assert.equal(parseDate("", TODAY), TODAY);
  assert.equal(parseDate("02.03.2026", TODAY), "2026-03-02");
  assert.equal(parseDate("2/3/2026", TODAY), "2026-03-02");
  assert.equal(parseDate("2026-10-04", TODAY), TODAY);
  assert.throws(() => parseDate("2026-10-05", TODAY), { message: "DATE 2026-10-05 IS IN THE FUTURE" });
  assert.throws(() => parseDate("31.02.2026", TODAY), { message: "DATE: '31.02.2026' IS NOT A DATE — WRITE IT AS YYYY-MM-DD (OR DD.MM.YYYY)" });
  assert.throws(() => parseDate("03/15/2026", TODAY), /READ DAY\/MONTH\/YEAR/);
});

test("formats as the file and the preview line write them", () => {
  assert.deepEqual([4, 0.15, 13.513513, 1e-7].map(fmtQty), ["4", "0.15", "13.513513", "0.0000001"]);
  assert.deepEqual([961, 100.5, 33.3333].map(fmtMoney), ["961.00", "100.50", "33.3333"]);
  assert.equal(eur(1234.5), "€1,234.50");
});

test("the form's values -> the trade that will be stored, and its one line", () => {
  const r = tradeFrom({ ticker: " sap.de ", action: "buy", shares: "4", pps: "240", total: "", date: "", fee: "" }, TODAY);
  assert.deepEqual(r.trade, { date: TODAY, ticker: "SAP.DE", action: "buy", shares: 4, price: 960, pps: 240 });
  assert.equal(describe(r.trade, r.fee), "BUY 4 SAP.DE · €240.00/sh = €960.00");
  const f = tradeFrom({ ticker: "SAP.DE", action: "buy", shares: "4", pps: "240", fee: "1" }, TODAY);
  assert.equal(describe(f.trade, f.fee), "BUY 4 SAP.DE · €240.00/sh + €1.00 fee = €961.00");
  const s = tradeFrom({ ticker: "ALV.DE", action: "sell", shares: "2", pps: "410", fee: "1", date: "02.03.2026" }, TODAY);
  assert.equal(describe(s.trade, s.fee), "SELL 2 ALV.DE · €410.00/sh − €1.00 fee = €819.00");
  const t = tradeFrom({ ticker: "SAP.DE", action: "buy", shares: "4", total: "961" }, TODAY);
  assert.deepEqual([t.trade.price, t.trade.pps, t.fee], [961, 240.25, null]);
  assert.deepEqual(tradeFrom({ ticker: "", action: "buy", shares: "4", pps: "1" }, TODAY), { error: "TICKER MISSING" });
  assert.deepEqual(tradeFrom({ ticker: "A B", action: "buy", shares: "4", pps: "1" }, TODAY),
    { error: "TICKER 'A B': ONLY LETTERS, DIGITS AND . - = ^" });
  assert.equal(tradeFrom({ ticker: "\u200bsap.de\u200d", action: "buy", shares: "4", pps: "1" }, TODAY).trade.ticker, "SAP.DE");
  assert.equal(tradeFrom({ ticker: "BRK,B", action: "buy", shares: "4", pps: "1" }, TODAY).error, "TICKER 'BRK,B': ONLY LETTERS, DIGITS AND . - = ^");
  assert.deepEqual(tradeFrom({ ticker: "X.F", action: "buy", shares: "", pps: "1" }, TODAY), { error: "SHARES MUST BE > 0" });
  // the body the server gets: the numbers as entered, keep_pps only for an untouched edit
  assert.deepEqual(tradeFrom({ ticker: "X.F", action: "buy", shares: "4", total: "961", keep: 240 }, TODAY).body,
    { ticker: "X.F", action: "buy", shares: 4, total: 961, pps: 240, keep_pps: true, date: TODAY });
  assert.deepEqual(tradeFrom({ ticker: "X.F", action: "buy", shares: "4", total: "961", keep: 240 }, TODAY).trade.pps, 240);
});

test("an edit loads the stored total and keeps its display price until the price changes", () => {
  const row = { id: "abc", date: "2024-12-16", tkr: "SAP.DE", action: "BUY", shares: 4, pps: 240, total: 961 };
  assert.deepEqual(editValues(row), { ticker: "SAP.DE", action: "buy", shares: "4", pps: "", total: "961.00",
    date: "2024-12-16", fee: "", keep: 240 });
});

test("a duplicate is warned, never refused; the row being edited is not its own twin", () => {
  const rows = [{ id: "a", date: "2024-12-16", tkr: "SAP.DE", action: "BUY", shares: 4, total: 961 }];
  const t = { date: "2024-12-16", ticker: "SAP.DE", action: "buy", shares: 4, price: 961, pps: 240.25 };
  assert.equal(duplicateOf(rows, t)?.id, "a");
  assert.equal(duplicateOf(rows, t, "a"), null);
  assert.equal(duplicateOf(rows, { ...t, shares: 5 }), null);
});

test("BUY / SELL / BONUS commands: @ price per share or = total, a date last, FEE x anywhere after", () => {
  assert.deepEqual(tradeCommand("buy", ["SAP.DE", "4", "@", "240"]),
    { type: "trade", values: { action: "buy", ticker: "SAP.DE", shares: "4", pps: "240", total: "", date: "", fee: "" } });
  assert.deepEqual(tradeCommand("buy", ["SAP.DE", "4", "=", "961"]).values.total, "961");
  assert.deepEqual(tradeCommand("sell", ["ALV.DE", "2", "@410", "2026-03-02"]).values,
    { action: "sell", ticker: "ALV.DE", shares: "2", pps: "410", total: "", date: "2026-03-02", fee: "" });
  assert.deepEqual(tradeCommand("buy", ["SAP.DE", "4", "@", "240", "FEE", "1"]).values.fee, "1");
  assert.deepEqual(tradeCommand("buy", ["SAP.DE", "4", "@", "240", "FEE", "1", "02.01.2026"]).values,
    { action: "buy", ticker: "SAP.DE", shares: "4", pps: "240", total: "", date: "02.01.2026", fee: "1" });
  assert.deepEqual(tradeCommand("bonus", ["AMZ.F", "0,05", "@", "190"]).values.shares, "0,05");
  assert.equal(tradeCommand("buy", ["SAP.DE", "4"]).type, "error");
  assert.equal(tradeCommand("buy", ["SAP.DE", "4", "240"]).type, "error");
  assert.equal(tradeCommand("buy", ["SAP.DE", "4", "@", "240", "FEE"]).type, "error");
  assert.deepEqual(ACTIONS, ["buy", "sell", "bonus"]);
});

test("ticker suggestions: your holdings first, then the universe's EUR listings, then the rest", () => {
  const book = [{ ticker: "RHM.DE", name: "Rheinmetall" }, { ticker: "SAP.DE", name: "SAP" }];
  const found = [{ ticker: "RNMBY", name: "Rheinmetall ADR", country: "USA" }, { ticker: "RHM.DE", name: "Rheinmetall", country: "Germany" },
                 { ticker: "RHM.F", name: "Rheinmetall Frankfurt", country: "Germany" }];
  const got = rankSuggestions("rhein", book, found);
  assert.deepEqual(got.map((s) => s.ticker), ["RHM.DE", "RHM.F", "RNMBY"]);
  assert.equal(got[0].note, "IN YOUR BOOK");
  assert.equal(got[2].note, "USA · NOT A EUR LISTING — PRICED AS €");
  assert.deepEqual(rankSuggestions("", book, found), []);
  assert.ok(isEur("SAP.DE") && isEur("ENEL.MI") && !isEur("AAPL") && !isEur("7203.T"));
});

test("an imported file is read as UTF-8, else as Windows-1252 (an old Excel's CSV)", () => {
  const utf8 = new TextEncoder().encode("Gebühr;Betrag\n");
  assert.equal(decodeBytes(utf8.buffer), "Gebühr;Betrag\n");
  const bom = new Uint8Array([0xef, 0xbb, 0xbf, ...utf8]);
  assert.equal(decodeBytes(bom.buffer), "Gebühr;Betrag\n");
  const cp1252 = new Uint8Array([0x47, 0x65, 0x62, 0xfc, 0x68, 0x72, 0x3b, 0x80]);      // Gebühr;€
  assert.equal(decodeBytes(cp1252.buffer), "Gebühr;€");
  const quotes = new Uint8Array([0x93, 0x41, 0x94, 0x20, 0x96, 0x20, 0x9a]);              // “A” – š
  assert.equal(decodeBytes(quotes.buffer), "\u201cA\u201d \u2013 \u0161");
});

test("typing a price makes it the source: the other one empties and shows what follows from it", () => {
  let v = { ticker: "SAP.DE", action: "buy", shares: "4", pps: "", total: "961.00", date: "", fee: "", keep: 240 };
  v = typed(v, "pps", "240");
  assert.deepEqual([v.pps, v.total, v.keep], ["240", "", null]);           // an edit's kept price ends here
  const s = formStatus(v, TODAY, []);
  assert.deepEqual([s.text, s.cls, s.derived], ["BUY 4 SAP.DE · €240.00/sh = €960.00", "", { total: "960.00" }]);
  v = typed(v, "total", "961");
  assert.deepEqual([v.pps, v.total], ["", "961"]);
  assert.deepEqual(formStatus(v, TODAY, []).derived, { pps: "240.25" });
  assert.equal(typed(v, "date", "2026-01-02").total, "961");                 // other fields leave the prices alone
});

test("the form's status line: what is still needed (dim), what is wrong (red), a twin (amber)", () => {
  const blank = { ticker: "", action: "buy", shares: "", pps: "", total: "", date: "", fee: "" };
  assert.deepEqual(formStatus(blank, TODAY, []), { text: "", cls: "", derived: {} });
  assert.deepEqual(formStatus({ ...blank, ticker: "SAP.DE" }, TODAY, []),
    { text: "STILL NEEDED: SHARES · € / SHARE OR TOTAL", cls: "dim", derived: {} });
  assert.deepEqual(formStatus({ ...blank, ticker: "SAP.DE", shares: "4", pps: "x" }, TODAY, []),
    { text: "PRICE PER SHARE: 'x' IS NOT A NUMBER", cls: "err", derived: {} });
  const rows = [{ id: "a", date: TODAY, tkr: "SAP.DE", action: "BUY", shares: 4, total: 960 }];
  const twin = formStatus({ ...blank, ticker: "SAP.DE", shares: "4", pps: "240" }, TODAY, rows);
  assert.deepEqual([twin.text, twin.cls], ["BUY 4 SAP.DE · €240.00/sh = €960.00 · SAME AS A TRADE ALREADY IN YOUR FILE", "warn"]);
  assert.equal(formStatus({ ...blank, ticker: "SAP.DE", shares: "4", pps: "240" }, TODAY, rows, "a").cls, "");
});

test("the paste preview's summary and what stays in the box after ADD", () => {
  const res = { rows: [{ line: 2, error: null }, { line: 3, error: "X" }, { line: 4, error: null, warnings: ["W"] }],
    delimiter: ";", decimal: "comma", header: true, ok: 2, bad: 1, error: null };
  assert.equal(pasteSummary(res), "3 ROWS · 2 TO ADD · 1 WITH AN ERROR · 1 WARNING · SEMICOLON BETWEEN FIELDS · DECIMAL COMMA · HEADER READ");
  assert.equal(pasteSummary({ ...res, delimiter: "\t", header: false, rows: [], ok: 0, bad: 0 }),
    "0 ROWS · 0 TO ADD · TAB BETWEEN FIELDS · DECIMAL COMMA · NO HEADER");
  assert.equal(pasteSummary({ error: "NOTHING TO READ — PASTE ROWS OR PICK A CSV FILE", rows: [] }), "NOTHING TO READ — PASTE ROWS OR PICK A CSV FILE");
  assert.equal(pasteSummary({ ...res, notes: ["PRICE READ AS THE TOTAL PAID (THIS TERMINAL'S OWN COLUMNS) — NAME IT PRICE PER SHARE IF IT IS ONE"] }),
    "3 ROWS · 2 TO ADD · 1 WITH AN ERROR · 1 WARNING · SEMICOLON BETWEEN FIELDS · DECIMAL COMMA · HEADER READ · "
    + "PRICE READ AS THE TOTAL PAID (THIS TERMINAL'S OWN COLUMNS) — NAME IT PRICE PER SHARE IF IT IS ONE");
});
