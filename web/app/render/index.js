// Panel frame + dispatch to the renderer for the panel's type.
import { esc } from "../dom.js";
import { banner } from "./banner.js";
import { bars } from "./bars.js";
import { chart, chartSource } from "./chart.js";
import { form } from "./form.js";
import { heatmap } from "./heatmap.js";
import { kpi } from "./kpi.js";
import { ledger } from "./ledger.js";
import { notes } from "./notes.js";
import { paste } from "./paste.js";
import { scatter } from "./scatter.js";
import { followed, table } from "./table.js";

const RENDER = { kpi, table, chart, ledger, bars, scatter, heatmap, notes, form, paste, banner };

export function renderPanel(p, ui) {
  const el = document.createElement("section");
  el.className = "panel";
  el.dataset.id = p.id;
  el.dataset.n = p.n;
  el.dataset.type = p.type;
  el.style.setProperty("--span", p.span ?? 12);
  el.style.setProperty("--rows", p.rows_span ?? 1);
  const key = ui.followKey(p);
  const title = String(p.title ?? "").replace("{key}", key ?? "—");
  const note = p.follows && p.series_by_key ? chartSource(p, key)?.note : null;   // EQUITY: NO CURVE FOR …
  const base = p.follows && p.rows_by_key ? followed(p, key).context : p.context?.text ?? "";
  const ctx = note ? `${note} · ${base}` : base;
  el.innerHTML = (p.type === "banner" ? "" : `<div class="h"><span>${esc(p.n)}) ${esc(title)}</span><span class="ctx" title="${esc(ctx)}">${esc(ctx)}</span></div>`)
    + `<div class="body"></div>`;                    // a banner is its own one line
  const body = el.querySelector(".body");
  const render = RENDER[p.type];
  if (render) render(body, p, ui);
  else body.innerHTML = `<div class="empty">UNKNOWN PANEL TYPE ${esc(p.type)}</div>`;
  return el;
}
