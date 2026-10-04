// The yes/no questions asked before a write that removes or replaces trades — which panel Del acts on, whether an
// open question still stands, and the words that name exactly what it touches. Pure.
import { fmtDate } from "./fmt.js";
import { eur, fmtQty } from "./trades.js";

// The table Del / Backspace may delete from: the FOCUSED one that allows it — never the first table as a fallback,
// so a key meant for the command bar or a form can never reach a trade.
export const removable = (panels, focus) =>
  (panels ?? []).find((p) => p.id === focus && p.type === "table" && p.remove) ?? null;

// A question stands while its screen is shown and every row it names is still there (a refresh that edited or
// deleted one of them, another tab, an Excel save, closes it).
export function stands(q, { route, rows }) {
  if (!q || q.route !== route) return false;
  const have = new Set((rows ?? []).map(String));
  return (q.rows ?? []).every((id) => have.has(String(id)));
}

const KEPT = "The file as it is now is kept in input/backups/ until you delete it, and UNDO brings it back.";
const many = (n, one) => `${n} ${one}${n === 1 ? "" : "s"}`;
const span = (rows) => {
  const d = (rows ?? []).map((r) => r.date).filter(Boolean).sort();
  return d.length ? `${fmtDate(d[0])} to ${fmtDate(d.at(-1))}` : "";
};

// One TRANSACTIONS row as a question names it: 16 DEC 24 · BUY 4 SAP.DE · €961.00
export const rowLine = (r) => `${fmtDate(r.date)} · ${r.action} ${fmtQty(r.shares)} ${r.tkr} · ${eur(r.total)}`;

// What START FRESH would empty: every trade (and their dates) — or the lines of a file that cannot be read.
export function freshText({ rows, error, lines }) {
  const what = error ? `${many(lines ?? 0, "line")} it cannot read (${error})`
    : `all ${many(rows.length, "trade")}${rows.length ? `, ${span(rows)}` : ""}`;
  return `Empties input/portfolio.csv — ${what}. ${KEPT}`;
}

// What REPLACE would swap: your trades for a file's good rows.
export function replaceText({ rows, error, lines }, pre, file) {
  if (!error && !rows.length) {
    return `The ${pre.ok} good row${pre.ok === 1 ? "" : "s"} of ${file ?? "the box"} become your trades (you have none yet).`;
  }
  const yours = error ? `The ${many(lines ?? 0, "line")} of your file (it cannot be read)`
    : `Your ${many(rows.length, "trade")}${rows.length ? ` (${span(rows)})` : ""}`;
  return `${yours} give way to the ${pre.ok} good row${pre.ok === 1 ? "" : "s"} of ${file ?? "the box"}`
    + `${pre.bad ? ` (${pre.bad} with an error left out)` : ""}. ${KEPT}`;
}

// What UNDO puts back — or, after an UNDO, brings back (a REDO).
export function undoText(u) {
  const at = u.at ? ` (${String(u.at).slice(11, 19)})` : "";
  return u.what.startsWith("UNDO: ")
    ? `Brings back what UNDO took back: ${u.what.slice(6)}${at}. UNDO again takes it back.`
    : `Puts input/portfolio.csv back as it was before: ${u.what}${at}. UNDO again brings that change back.`;
}
