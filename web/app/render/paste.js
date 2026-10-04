// A `paste` panel (TRADES' PASTE / IMPORT): a box for many rows and an "IMPORT CSV FILE…" picker (read here, sent
// as text). The server parses both the same way: the preview under the box shows each row as it will be stored,
// or why not, then ADD (append the good rows) or — for a file — REPLACE every trade. Typing re-previews after a
// pause; Ctrl/Cmd+Enter previews at once. The text, the file name and the last preview live in ui.formState(p).
import { esc } from "../dom.js";
import { fmt } from "../fmt.js";
import { decodeBytes, pasteSummary } from "../trades.js";

const PLACEHOLDER = "Paste rows here — a header is optional; tab, ; or , between fields:\n"
  + "Date,Ticker,Action,Shares,Price,PricePerShare\n2025-01-15,SAP.DE,buy,4,961.00,240.00\n"
  + "15.01.2025;SAP.DE;buy;4;@240,00   (@ = price per share)";

const cell = (v, spec, extra = "") => { const f = fmt(v, spec); return `<td class="${`${f.cls} ${extra}`.trim()}">${esc(f.text)}</td>`; };

// The preview table: one row per pasted row — what will be stored, or the error; warnings in amber. Pure.
export function previewTable(res) {
  const rows = (res.rows ?? []).map((r) => {
    const t = r.trade, note = r.error ?? (r.warnings ?? []).join(" · ");
    const cls = r.error ? "dn" : r.warnings?.length ? "hot" : "";
    const mark = r.error ? "✗" : "✓";
    return `<tr class="${cls}"><td class="r dim">${esc(r.line)}</td><td>${mark}</td>`
      + (t ? `${cell(t.date, "date")}<td>${esc(t.ticker)}</td><td>${esc(t.action.toUpperCase())}</td>`
        + `${cell(t.shares, "qty", "r")}${cell(t.pps, "px", "r")}${cell(t.price, "eur", "r")}`
        : `<td colspan="6" class="tx" title="${esc(r.text ?? "")}">${esc(r.text ?? "")}</td>`)
      + `<td class="tx" title="${esc(note)}">${esc(note)}</td></tr>`;
  }).join("");
  return `<table class="grid-table pvt"><thead><tr><th class="r">LINE</th><th></th><th>DATE</th><th>TKR</th><th>ACTION</th>`
    + `<th class="r">SHARES</th><th class="r">€/SHARE</th><th class="r">TOTAL €</th><th class="tx">NOTE</th></tr></thead>`
    + `<tbody>${rows}</tbody></table>`;
}

export function paste(body, p, ui) {
  const st = ui.formState(p);
  body.innerHTML = `<div class="paste"><textarea data-field="text" rows="6" spellcheck="false" autocomplete="off"
    placeholder="${esc(PLACEHOLDER)}"></textarea><div class="btns">`
    + `<button type="button" class="btn" data-act="preview">PREVIEW</button>`
    + `<button type="button" class="btn ghost" data-act="file">IMPORT CSV FILE…</button>`
    + `<input type="file" accept=".csv,.tsv,.txt,text/csv,text/plain" hidden>`
    + `<span class="dim src"></span></div><div class="pv"></div></div>`;
  const box = body.querySelector("textarea"), pv = body.querySelector(".pv"), file = body.querySelector("input[type=file]");
  const src = body.querySelector(".src");
  box.value = st.text ?? "";
  src.textContent = st.file ? `FROM ${st.file}` : "";

  const draw = () => {
    const res = st.preview;
    if (!res) { pv.innerHTML = ""; return; }
    pv.innerHTML = `<div class="${res.error ? "dn" : "dim"} sum">${esc(pasteSummary(res))}</div>`
      + (res.error ? "" : previewTable(res))
      + (res.ok ? `<div class="btns"><button type="button" class="btn" data-act="add">ADD ${res.ok} TRADE${res.ok === 1 ? "" : "S"}</button>`
        + (st.file ? `<button type="button" class="btn ghost" data-act="replace">REPLACE ${esc(ui.yourFile())} WITH THESE ${res.ok}</button>` : "")
        + "</div>" : "");
    pv.querySelector("[data-act=add]")?.addEventListener("click", () => ui.importForm(p, "append"));
    pv.querySelector("[data-act=replace]")?.addEventListener("click", () => ui.importForm(p, "replace"));
  };
  let timer = null, asked = 0;
  const preview = async () => {
    clearTimeout(timer);
    const text = st.text ?? "", n = ++asked;
    if (!text.trim()) { st.preview = null; draw(); return; }
    try {
      const res = await ui.previewText(text, "append");
      if (n !== asked) return;                       // a newer preview is on its way
      st.preview = { ...res, text };                 // ADD sends exactly this text
    } catch (e) {
      st.preview = { error: ui.why(e), rows: [], text };
    }
    draw();
  };
  draw();

  box.addEventListener("input", () => {
    st.text = box.value;
    clearTimeout(timer);
    timer = setTimeout(preview, 500);
  });
  box.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); preview(); }
  });
  body.querySelector("[data-act=preview]").addEventListener("click", preview);
  body.querySelector("[data-act=file]").addEventListener("click", () => file.click());
  file.addEventListener("change", async () => {
    const f = file.files?.[0];
    if (!f) return;
    st.text = decodeBytes(await f.arrayBuffer());
    st.file = f.name;
    box.value = st.text;
    src.textContent = `FROM ${f.name}`;
    file.value = "";
    preview();
  });
  if (st.stale) { st.stale = false; preview(); }      // the screen changed under a preview: check it again
}
