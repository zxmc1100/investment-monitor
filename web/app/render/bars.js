// Bar lists (ALLOCATION). The compact list shows normally; a panel that also carries detail lists
// (sectors / countries / regions) shows them instead when maximized — CSS switches on `.max`.
import { esc } from "../dom.js";
import { fmt } from "../fmt.js";

function barRows(id, rows, { flash, parts }) {
  const max = Math.max(1e-9, ...rows.map((r) => r.v ?? 0));
  return rows.map((r) => {
    const f = fmt(r.v, r.fmt);
    const w = Math.max(0, ((r.v ?? 0) / max) * 100);
    const cell = flash ? ` data-cell="${esc(`${id}|${r.label}|v`)}"` : "";
    return `<tr${(r.v ?? 0) === 0 ? ' class="zero"' : ""}><td>${esc(r.label)}</td><td class="r"${cell}>${esc(f.text)}</td>`
      + `<td class="bar"><i style="width:${w.toFixed(1)}%"></i></td>${parts ? `<td class="parts">${esc(r.text ?? "")}</td>` : ""}</tr>`;
  }).join("");
}

export function bars(body, p) {
  const compact = `<table class="bars">${barRows(p.id, p.items ?? [], { flash: true, parts: false })}</table>`;
  const sectors = p.sectors ?? [], countries = p.countries ?? [], regions = p.regions ?? [];
  if (!sectors.length && !countries.length) { body.innerHTML = compact; return; }
  const detail = (rows, parts) => `<table class="bars wide">${barRows(p.id, rows, { flash: false, parts })}</table>`;
  body.innerHTML = `<div class="bars-compact">${compact}</div><div class="bars-detail">`
    + `<section><h4>SECTORS</h4>${detail(sectors, true)}</section>`
    + `<section>${regions.length ? `<h4>REGIONS</h4>${detail(regions, true)}` : ""}<h4>COUNTRIES</h4>${detail(countries, true)}</section>`
    + `</div>`;
}
