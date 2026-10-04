// A `form` panel: labelled fields described by the payload (`fields`: k, label, kind = ticker | choice | number |
// date, options, pair, placeholder) and a SAVE / CLEAR row. Its behaviour comes from `p.form` — "trade" (TRADES'
// ADD): a live line of what will be stored, the derived price in the other price field, ticker suggestions.
// The values live in ui.formState(p), so a refresh of the screen never loses what you typed.
import { esc } from "../dom.js";
import { formStatus, rankSuggestions, typed } from "../trades.js";

const field = (f, id) => {
  const attrs = `id="${esc(id)}" data-field="${esc(f.k)}" autocomplete="off" spellcheck="false"`;
  if (f.kind === "choice") {
    return `<select ${attrs}>${(f.options ?? []).map((o) => `<option value="${esc(o)}">${esc(String(o).toUpperCase())}</option>`).join("")}</select>`;
  }
  const mode = f.kind === "number" ? ' inputmode="decimal"' : "";
  return `<input type="text" ${attrs}${mode} placeholder="${esc(f.placeholder ?? "")}">`;
};

export function form(body, p, ui) {
  const st = ui.formState(p);
  const fields = p.fields ?? [];
  const byKey = Object.fromEntries(fields.map((f) => [f.k, f]));
  const uid = (k) => `f-${p.id}-${k}`;
  const rowsHtml = fields.filter((f) => !(f.pair && fields.indexOf(byKey[f.pair]) < fields.indexOf(f))).map((f) => {
    const input = f.kind === "ticker" ? `<div class="tk">${field(f, uid(f.k))}<div class="sug" hidden></div></div>`
      : f.pair ? `<div class="pair">${field(f, uid(f.k))}<label for="${esc(uid(f.pair))}">or ${esc(byKey[f.pair].label)}</label>${field(byKey[f.pair], uid(f.pair))}</div>`
        : field(f, uid(f.k));
    return `<label for="${esc(uid(f.k))}">${esc(f.label)}</label>${input}`;
  }).join("");
  const editing = st.edit ? `<div class="editing">EDITING ${esc(st.edit.label)} — SAVE REPLACES IT</div>` : "";
  body.innerHTML = `<form class="tform" novalidate>${editing}${rowsHtml}<div class="line"></div>`
    + `<div class="btns"><button type="submit" class="btn">${st.edit ? "SAVE CHANGES" : "SAVE"} ↵</button>`
    + `<button type="button" class="btn ghost" data-act="clear">${st.edit ? "CANCEL EDIT" : "CLEAR"}</button></div></form>`;
  const formEl = body.querySelector("form");
  const inputs = Object.fromEntries([...formEl.querySelectorAll("[data-field]")].map((el) => [el.dataset.field, el]));
  for (const [k, el] of Object.entries(inputs)) el.value = st.values[k] ?? (byKey[k]?.options?.[0] ?? "");

  const line = formEl.querySelector(".line");
  const update = () => {
    const s = formStatus(st.values, ui.today(), ui.rows(p), st.edit?.id ?? null);
    line.className = `line ${s.cls}`;
    line.textContent = s.text;
    for (const k of ["pps", "total"]) {
      if (inputs[k]) inputs[k].placeholder = s.derived[k] ? `= ${s.derived[k]}` : byKey[k]?.placeholder ?? "";
    }
  };
  update();

  // ticker suggestions: your holdings, then the universe (EUR listings first)
  const tk = inputs.ticker, box = formEl.querySelector(".sug");
  let sug = { items: [], i: -1 }, timer = null;
  const show = () => {
    box.hidden = !sug.items.length;
    box.innerHTML = sug.items.map((s, i) => `<div class="${i === sug.i ? "on" : ""}" data-i="${i}"><b>${esc(s.ticker)}</b>`
      + `<span>${esc(s.name ?? "")}</span><span class="dim">${esc(s.note ?? "")}</span></div>`).join("");
    box.querySelectorAll("[data-i]").forEach((el) => el.addEventListener("mousedown", (e) => { e.preventDefault(); pick(Number(el.dataset.i)); }));
  };
  const close = () => { sug = { items: [], i: -1 }; show(); };
  const pick = (i) => {
    const s = sug.items[i];
    if (!s) return;
    st.values = typed(st.values, "ticker", s.ticker);
    tk.value = s.ticker;
    close();
    update();
    inputs.action?.focus();
  };
  const suggest = (q) => {
    clearTimeout(timer);
    sug = { items: rankSuggestions(q, p.book, []), i: -1 };
    show();
    if (!q.trim()) return;
    timer = setTimeout(async () => {
      const found = await ui.lookup(q).catch(() => []);
      if (tk.value !== q || document.activeElement !== tk) return;
      const kept = sug.items[sug.i]?.ticker;         // a row picked with ↓ meanwhile stays picked
      sug = { items: rankSuggestions(q, p.book, found), i: -1 };
      if (kept) sug.i = sug.items.findIndex((s) => s.ticker === kept);
      show();
    }, 200);
  };

  for (const [k, el] of Object.entries(inputs)) {
    el.addEventListener(el.tagName === "SELECT" ? "change" : "input", () => {
      st.values = typed(st.values, k, el.value);
      if (k === "pps" || k === "total") for (const o of ["pps", "total"]) if (o !== k && inputs[o]) inputs[o].value = st.values[o];
      update();
      if (k === "ticker") suggest(el.value);
    });
  }
  tk?.addEventListener("keydown", (e) => {
    if (box.hidden) return;
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const n = sug.items.length;                    // -1 = none highlighted (Enter then saves)
      sug.i += e.key === "ArrowDown" ? 1 : -1;
      if (sug.i >= n) sug.i = -1;
      if (sug.i < -1) sug.i = n - 1;
      show();
    } else if (e.key === "Enter" && sug.i >= 0) {
      e.preventDefault();
      pick(sug.i);
    } else if (e.key === "Escape") {                 // the first Esc closes the list, the next leaves the field
      e.preventDefault();
      e.stopPropagation();
      close();
    }
  });
  tk?.addEventListener("blur", () => setTimeout(close, 150));
  formEl.addEventListener("keydown", (e) => {     // a held Enter repeats: it saves once, never again and again
    if (e.key === "Enter" && e.repeat) e.preventDefault();
  });
  formEl.addEventListener("submit", (e) => { e.preventDefault(); close(); ui.saveForm(p); });
  formEl.querySelector("[data-act=clear]").addEventListener("click", () => ui.clearForm(p));
}
