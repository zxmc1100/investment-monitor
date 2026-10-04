import { esc } from "../dom.js";
import { fmt } from "../fmt.js";

// Calendar-year table (PORT SUMMARY `years`): shown only when the panel is maximized — CSS switches
// on `.max`. The GAIN column exists only when the rows carry `gain` (the private build).
function years(rows) {
  const gain = rows.some((r) => "gain" in r);
  const cell = (v, spec) => { const f = fmt(v, spec); return `<td class="r ${f.cls}">${esc(f.text)}</td>`; };
  const head = `<tr><th>YEAR</th><th class="r">YOUR METHOD (gain ÷ 1 Jan value + net added)</th>`
    + `<th class="r">TIME-WEIGHTED</th>${gain ? `<th class="r">GAIN €</th>` : ""}</tr>`;
  const body = rows.map((r) => `<tr><td>${esc(r.label)}</td>${cell(r.v, r.fmt)}${cell(r.v2, r.fmt)}`
    + `${gain ? cell(r.gain, "eur+") : ""}</tr>`).join("");
  return `<div class="kpi-years"><table>${head}${body}</table></div>`;
}

export function kpi(body, p) {
  body.classList.add("kpi");
  body.innerHTML = (p.items ?? []).map((it) => {
    const f = fmt(it.v, it.fmt);
    return `<div class="kv${it.wide ? " wide" : ""}"><b>${esc(it.k)}</b><span class="${f.cls}" data-cell="${esc(`${p.id}|${it.k}|v`)}">${esc(f.text)}</span></div>`;
  }).join("") + (p.years?.length ? years(p.years) : "");
}
