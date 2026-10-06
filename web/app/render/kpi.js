import { esc } from "../dom.js";
import { fmt } from "../fmt.js";

// Calendar-year table (PORT SUMMARY `years`): shown only when the panel is maximized — CSS switches
// on `.max`. FIRST DAY / LAST DAY / CHANGE / GAIN exist only when the rows carry them (the private build);
// the public one has the time-weighted return alone.
export function yearsTable(rows) {
  const priv = rows.some((r) => "chg" in r);
  const cell = (v, spec) => { const f = fmt(v, spec); return `<td class="r ${f.cls}">${esc(f.text)}</td>`; };
  const head = `<tr><th>YEAR</th>${priv ? `<th class="r">FIRST DAY €</th><th class="r">LAST DAY €</th>`
    + `<th class="r">CHANGE</th>` : ""}<th class="r">TIME-WEIGHTED</th>${priv ? `<th class="r">GAIN €</th>` : ""}</tr>`;
  const body = rows.map((r) => `<tr><td>${esc(r.label)}</td>`
    + `${priv ? cell(r.first, "eur") + cell(r.end, "eur") + cell(r.chg, r.fmt) : ""}${cell(r.v, r.fmt)}`
    + `${priv ? cell(r.gain, "eur+") : ""}</tr>`).join("");
  return `<div class="kpi-years"><table>${head}${body}</table></div>`;
}

export function kpi(body, p) {
  body.classList.add("kpi");
  body.innerHTML = (p.items ?? []).map((it) => {
    const f = fmt(it.v, it.fmt);
    return `<div class="kv${it.wide ? " wide" : ""}"><b>${esc(it.k)}</b><span class="${f.cls}" data-cell="${esc(`${p.id}|${it.k}|v`)}">${esc(f.text)}</span></div>`;
  }).join("") + (p.years?.length ? yearsTable(p.years) : "");
}
