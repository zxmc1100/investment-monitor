import { esc } from "../dom.js";

// Long-form text (a `notes` panel: label → a sentence or a paragraph): a label over wrapped prose —
// never a table cell, which would truncate it.
export function notes(body, p) {
  body.innerHTML = `<dl class="notes">${(p.items ?? []).map((it) => `<dt>${esc(it.k)}</dt><dd>${esc(it.v ?? "—")}</dd>`).join("")}</dl>`;
}
