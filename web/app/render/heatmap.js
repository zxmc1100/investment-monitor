// Correlation heatmap: a table whose cells are coloured on a diverging red/black/green scale.
import { esc } from "../dom.js";
import { fmt } from "../fmt.js";

export function heatColor(v) {
  if (v === null || v === undefined || !Number.isFinite(v)) return "transparent";
  const a = Math.round(Math.min(1, Math.abs(v)) * 85) / 100;   // not toFixed: (0.425).toFixed(2) is "0.42"
  return v >= 0 ? `rgba(0,230,118,${a})` : `rgba(255,61,61,${a})`;
}

export function heatmap(body, p) {
  const L = p.labels ?? [], C = p.cells ?? [];
  if (!L.length) { body.innerHTML = `<div class="empty">NO DATA</div>`; return; }
  const head = `<tr><th></th>${L.map((l) => `<th class="r">${esc(l)}</th>`).join("")}</tr>`;
  const rows = L.map((r, i) => `<tr><th>${esc(r)}</th>${L.map((c, j) => {
    const v = C[i]?.[j], f = fmt(v, p.fmt ?? "num:2");
    return `<td class="r" style="background:${heatColor(v)}" data-cell="${esc(`${p.id}|${r}|${c}`)}">${esc(f.text)}</td>`;
  }).join("")}</tr>`).join("");
  body.innerHTML = `<table class="heatmap">${head}${rows}</table>`;
}
