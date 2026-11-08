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

// A panel with views (OPT's WHAT IF, PORT's book) is drawn as the one its title's chips chose — each view a whole
// panel of its own under the same id and number; the panel itself carries only who it is, its chips and its views.
// app.js `show` resolves every panel on arrival, setPick again on a chip. Pure.
export const viewOf = (p, pick) => (p.views
  ? { ...(p.views[pick] ?? p.views[p.pick] ?? Object.values(p.views)[0]), picks: p.picks, pick: p.pick, views: p.views }
  : p);

export function renderPanel(panel, ui) {
  const p = panel.views ? viewOf(panel, ui.pickOf(panel)) : panel;
  const el = document.createElement("section");
  el.className = "panel";
  el.dataset.id = p.id;
  el.dataset.n = p.n;
  el.dataset.type = p.type;
  el.classList.toggle("fit", !!p.fit);             // takes its row's height, never sets it (terminal.css)
  el.classList.toggle("grow", !!p.grow);           // its row takes the spare height, as a chart's (app.js fitRows)
  el.style.setProperty("--span", p.span ?? 12);
  el.style.setProperty("--rows", p.rows_span ?? 1);
  const key = ui.followKey(p);
  const title = String(p.title ?? "").replace("{key}", key ?? "—");
  const note = p.follows && p.series_by_key ? chartSource(p, key)?.note : null;   // EQUITY: NO CURVE FOR …
  const base = p.follows && p.rows_by_key ? followed(p, key).context : p.context?.text ?? "";
  const ctx = note ? `${note} · ${base}` : base;
  // a table's period chips (POSITIONS: DAY 1W 1M YTD) — which pair of columns it shows
  const pick = p.picks ? ui.pickOf(p) : null;
  const picks = p.picks ? `<span class="picks">${p.picks.map((k) => `<span class="${k === pick ? "on" : ""}" data-pick="${esc(k)}">${esc(k)}</span>`).join("")}</span>` : "";
  el.innerHTML = (p.type === "banner" ? "" : `<div class="h"><span>${esc(p.n)}) ${esc(title)}</span>${picks}<span class="ctx" title="${esc(ctx)}">${esc(ctx)}</span></div>`)
    + `<div class="body"></div>`;                    // a banner is its own one line
  el.querySelectorAll("[data-pick]").forEach((s) => s.addEventListener("click", (e) => {
    e.stopPropagation();                             // a chip, not the title bar: no maximize on a tap
    ui.setPick(p.id, s.dataset.pick);
  }));
  const body = el.querySelector(".body");
  const render = RENDER[p.type];
  if (render) render(body, p, ui);
  else body.innerHTML = `<div class="empty">UNKNOWN PANEL TYPE ${esc(p.type)}</div>`;
  return el;
}
