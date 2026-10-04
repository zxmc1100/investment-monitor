import { esc } from "../dom.js";
import { fmt } from "../fmt.js";
import { sortRows } from "../tablesort.js";
import { spark } from "./spark.js";

// A table that follows another table's cursor (`follows` + `rows_by_key`, e.g. OPT's TICKET
// following PORTFOLIOS) shows that key's rows and summary; anything else shows its own. Pure.
export function followed(p, key) {
  const hit = p.follows && key !== null && key !== undefined ? p.rows_by_key?.[key] : null;
  return hit ? { rows: hit.rows ?? [], context: hit.context ?? "" } : { rows: p.rows ?? [], context: p.context?.text ?? "" };
}

// Panels that follow a table drawn AFTER them (a chart above the table it follows): on the first
// draw that table had no cursor yet, so they are drawn again once it has one. Pure.
export const followsLater = (panels) =>
  panels.filter((q, i) => q.follows && panels.findIndex((t) => t.id === q.follows) > i).map((q) => q.id);

// The cursor keeps its row; a new table starts on the payload's hint (`cursor`), else row one.
// Keys compare as strings (the DOM's data-key is one; MKT's board `#` is a number) and the
// row's own key is kept. Pure.
export function startCursor(rows, key, current, hint) {
  const find = (v) => (v === null || v === undefined ? undefined : rows.find((r) => String(r[key]) === String(v)));
  const hit = find(current) ?? find(hint);
  return hit ? hit[key] : rows[0]?.[key] ?? null;
}

// A row may override a column's format (MKT: FX at 4 decimals, the US 10Y in basis points). Pure.
export const cellFmt = (r, c) => r?._fmt?.[c.k] ?? c.fmt;

// Free-text columns (names, sectors, rules, messages) shrink and ellipsize to fit the panel; numbers,
// tickers, dates, marks and sparklines keep their full width — a table never scrolls sideways. Pure.
export const isTextCol = (c) => !c.fmt || c.fmt === "text";

// The classes every cell of a column carries — header, rows and the blank cells of a total row
// alike: a text column stays one (a bare blank <td> would make it a width:1% column and starve
// it), and a `lo` column hides in every row. Pure.
export const colClass = (c) => [c.align === "r" ? "r" : "", c.hl ? "hl" : "", isTextCol(c) ? "tx" : "", c.lo ? "lo" : ""]
  .filter(Boolean).join(" ");
export const blankCell = (c) => `<td class="${colClass(c)}"></td>`;

// Two or more text columns share the width the numbers leave in proportion to the longest text
// each holds (header and sort mark included, plus ~2ch of padding) — equal shares would clip a long
// RULE while a short STATE sits half empty. {k: percent} for the text columns; {} for one. Pure.
export function textShares(cols, rows) {
  const tx = cols.filter(isTextCol);
  if (tx.length < 2) return {};
  const len = (c) => Math.max(String(c.label ?? "").length + 2,
    ...rows.map((r) => (r && Object.hasOwn(r, c.k) ? fmt(r[c.k], cellFmt(r, c)).text.length : 0))) + 2;
  const need = tx.map(len), sum = need.reduce((a, b) => a + b, 0);
  return Object.fromEntries(tx.map((c, i) => [c.k, Math.round((need[i] / sum) * 1000) / 10]));
}

export function table(body, p, ui) {
  const st = ui.tableState(p);
  const src = followed(p, ui.followKey(p));
  const closed = src.rows.filter((r) => r._closed).length;
  const rows = sortRows(src.rows.filter((r) => st.showClosed || !r._closed), st.sort[0], st.sort[1]);
  st.cursor = startCursor(rows, p.key, st.cursor, p.cursor);
  if (!rows.length && (p.follows || p.empty)) { body.innerHTML = `<div class="empty">${esc((p.follows && src.context) || p.empty || "NO ROWS")}</div>`; return; }

  const cell = (r, c, rowKey) => {
    if (c.fmt === "spark") return `<td class="${colClass(c)}">${spark(r[c.k])}</td>`;
    const f = fmt(r[c.k], cellFmt(r, c));
    return `<td class="${`${colClass(c)} ${f.cls}`.trim()}" data-cell="${esc(`${p.id}|${rowKey}|${c.k}`)}"${isTextCol(c) ? ` title="${esc(f.text)}"` : ""}>${esc(f.text)}</td>`;
  };
  const share = textShares(p.cols, p.total ? [...rows, p.total] : rows);
  const head = p.cols.map((c) => {
    const on = st.sort[0] === c.k;
    const w = Object.hasOwn(share, c.k) ? ` style="width:${share[c.k]}%"` : "";
    return `<th class="${`${colClass(c)} ${on ? "sorted" : ""}`.trim()}"${w} data-col="${esc(c.k)}">${esc(c.label)}${on ? (st.sort[1] === "desc" ? " ▼" : " ▲") : ""}</th>`;
  }).join("");
  const tbody = rows.map((r) => {
    const k = r[p.key];
    const cls = [k === st.cursor ? "cur" : "", r._closed ? "closed" : "", r._stale ? "stale" : "", r._hot ? "hot" : "", r._dn ? "dn" : ""].join(" ").trim();
    return `<tr class="${cls}" data-key="${esc(k)}">${p.cols.map((c) => cell(r, c, k)).join("")}</tr>`;
  }).join("");
  const foot = p.total
    ? `<tfoot><tr class="tot">${p.cols.map((c) => (c.k in p.total ? cell(p.total, c, "__total__") : blankCell(c))).join("")}</tr></tfoot>`
    : "";
  const more = closed ? `<div class="more" data-act="closed">${st.showClosed ? "− HIDE" : "+ SHOW"} ${closed} CLOSED</div>` : "";
  body.innerHTML = `<table class="grid-table"><thead><tr>${head}</tr></thead><tbody>${tbody}</tbody>${foot}</table>${more}`;

  body.querySelectorAll("th").forEach((th) => th.addEventListener("click", () => ui.sortBy(p.id, th.dataset.col)));
  body.querySelectorAll("tbody tr").forEach((tr) => tr.addEventListener("click", () => ui.setCursor(p.id, tr.dataset.key)));
  body.querySelector('[data-act="closed"]')?.addEventListener("click", () => ui.toggleClosed(p.id));
  if (st.reveal) {                                   // only when the cursor moved, not on every refresh
    st.reveal = false;
    requestAnimationFrame(() => body.querySelector("tr.cur")?.scrollIntoView({ block: "nearest" }));
  }
}
