// Global keymap. Letters always feed the command bar; navigation keys act on
// the focused table only while the bar is empty, so typing never fights the cursor.
const NAV = { ArrowDown: 1, ArrowUp: -1, PageDown: 10, PageUp: -10, Home: -1e9, End: 1e9 };

export function bindKeys(k) {
  document.addEventListener("keydown", (e) => {
    const empty = !k.cmd.value.trim();
    if (e.key === "Escape") { e.preventDefault(); k.escape(); return; }
    if (e.key === "F1" || (e.key === "?" && empty)) { e.preventDefault(); k.help(); return; }
    if (e.altKey && /^Digit[1-9]$/.test(e.code)) { e.preventDefault(); k.maximize(Number(e.code.slice(5))); return; }
    if (e.altKey && (e.key === "ArrowUp" || e.key === "ArrowDown")) { e.preventDefault(); k.history(e.key === "ArrowUp" ? 1 : -1); return; }
    if (e.metaKey || e.ctrlKey || e.altKey || k.overlayOpen()) return;
    if (k.acOpen() && (e.key === "ArrowDown" || e.key === "ArrowUp")) { e.preventDefault(); k.acMove(e.key === "ArrowDown" ? 1 : -1); return; }
    if (k.acOpen() && e.key === "Tab") { e.preventDefault(); k.acAccept(); return; }
    if (e.key === "Enter") { e.preventDefault(); if (empty) k.drill(); else k.exec(); return; }
    if (!empty) { if (e.key === "Tab") e.preventDefault(); if (document.activeElement !== k.cmd) k.cmd.focus(); return; }
    if (e.key in NAV) {
      e.preventDefault();
      if (e.shiftKey && (e.key === "ArrowUp" || e.key === "ArrowDown")) k.sortDir(e.key === "ArrowUp" ? "asc" : "desc");
      else k.cursor(NAV[e.key]);
      return;
    }
    if (e.shiftKey && (e.key === "ArrowLeft" || e.key === "ArrowRight")) { e.preventDefault(); k.sort(e.key === "ArrowRight" ? 1 : -1); return; }
    if (e.key === "Tab") { e.preventDefault(); k.focusPanel(e.shiftKey ? -1 : 1); return; }
    if (/^[1-9]$/.test(e.key)) { e.preventDefault(); k.fkey(Number(e.key)); return; }
    if (e.key.length === 1 && e.key !== " " && document.activeElement !== k.cmd) k.cmd.focus();
  });
}
