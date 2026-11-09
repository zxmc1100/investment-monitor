import { esc } from "../dom.js";
import { fmt } from "../fmt.js";

// Calendar-year table (PORT SUMMARY `years`): shown only when the panel is maximized — CSS switches
// on `.max`. GROWTH (v, money-weighted: payments excluded) and TIME-WEIGHTED (v2) always; FIRST DAY / LAST
// DAY / GAIN € only when the rows carry them (the private build).
export function yearsTable(rows) {
  const priv = rows.some((r) => "gain" in r);
  const cell = (v, spec) => { const f = fmt(v, spec); return `<td class="r ${f.cls}">${esc(f.text)}</td>`; };
  const head = `<tr><th>YEAR</th>${priv ? `<th class="r">FIRST DAY €</th><th class="r">LAST DAY €</th>` : ""}`
    + `<th class="r">GROWTH</th><th class="r">TIME-WEIGHTED</th>${priv ? `<th class="r">GAIN €</th>` : ""}</tr>`;
  const body = rows.map((r) => `<tr><td>${esc(r.label)}</td>`
    + `${priv ? cell(r.first, "eur") + cell(r.end, "eur") : ""}${cell(r.v, r.fmt)}${cell(r.v2, r.fmt)}`
    + `${priv ? cell(r.gain, "eur+") : ""}</tr>`).join("");
  return `<div class="kpi-years"><table>${head}${body}</table></div>`;
}

export function kpi(body, p) {
  body.classList.add("kpi");
  // `kcols`: columns a row — a full-width strip of all its KPIs (MOM's BOOK), six (PORT's SUMMARY | RISK) — else four
  if (Number.isInteger(p.kcols) && p.kcols > 0) body.style.gridTemplateColumns = `repeat(${p.kcols}, minmax(0, 1fr))`;
  body.innerHTML = (p.items ?? []).map((it) => {
    const f = fmt(it.v, it.fmt);
    // `eur`: the same figure in euros first, the % after it in brackets, broker-style — one cell two columns wide
    const pair = "eur" in it;
    const e = pair ? fmt(it.eur, "eur+") : null;
    const cls = ["kv", it.wide && "wide", pair && "duo", ["big", "small"].includes(it.size) && it.size].filter(Boolean);
    const val = pair ? `<span class="${e.cls}">${esc(e.text)}</span><span class="${f.cls}">(${esc(f.text)})</span>` : esc(f.text);
    return `<div class="${cls.join(" ")}"><b>${esc(it.k)}</b><span class="${pair ? "" : f.cls}" data-cell="${esc(`${p.id}|${it.k}|v`)}">${val}</span></div>`;
  }).join("") + (p.years?.length ? yearsTable(p.years) : "");
}
