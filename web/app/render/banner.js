// A `banner` panel: one line across the screen and, optionally, a button that runs a command (TRADES: "EXAMPLE
// PORTFOLIO — START FRESH clears it" [START FRESH]; a file that cannot be read, in red). No header bar.
import { esc } from "../dom.js";

export function banner(body, p, ui) {
  body.classList.add("banner");
  if (p.tone) body.classList.add(p.tone);
  const title = p.title && !String(p.text ?? "").startsWith(p.title) ? `<b>${esc(p.title)}</b> — ` : "";
  body.innerHTML = `<span class="msg" title="${esc(p.text ?? "")}">${title}${esc(p.text ?? "")}</span>`
    + (p.run ? `<button type="button" class="btn" data-act="run">${esc(p.button ?? p.run)}</button>` : "")
    + (p.context?.text ? `<span class="dim ctx">${esc(p.context.text)}</span>` : "");
  body.querySelector("[data-act=run]")?.addEventListener("click", () => ui.run(p.run));
}
