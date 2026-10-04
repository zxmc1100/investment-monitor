import { esc } from "../dom.js";
import { fmt } from "../fmt.js";

export function ledger(body, p) {
  const rows = (p.lines ?? []).map((l) => {
    if (l.sep) return `<tr class="sep"><td colspan="3"></td></tr>`;
    const f = fmt(l.v, l.fmt);
    return `<tr class="${l.strong ? "strong" : ""}"><td class="op">${esc(l.op ?? "")}</td><td>${esc(l.label)}</td>`
      + `<td class="r ${f.cls}" data-cell="${esc(`${p.id}|${l.label}|v`)}">${esc(f.text)}</td></tr>`;
  });
  body.innerHTML = `<table class="ledger">${rows.join("")}</table>`;
}
