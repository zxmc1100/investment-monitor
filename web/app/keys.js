// Global keymap. Letters always feed the command bar; navigation keys act on
// the focused table only while the bar is empty, so typing never fights the cursor. A form field (TRADES)
// keeps its own keys: Esc leaves it, F1 is help, everything else is the field's.
const NAV = { ArrowDown: 1, ArrowUp: -1, PageDown: 10, PageUp: -10, Home: -1e9, End: 1e9 };

// What a key does while a form field — not the command bar — has focus: "leave" (Esc), "help" (F1), "field" (the
// field's own: letters, digits, arrows, Enter, Tab to the next field, Backspace …) or null: not a field, or a
// focused button, which keeps Enter, Space and Tab and lets every other key work the terminal. Pure.
export function fieldKey(e, el, cmd) {
  if (!el || el === cmd) return null;
  const typing = el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.isContentEditable === true;
  if (!typing && el.tagName !== "BUTTON") return null;
  if (e.key === "Escape") return "leave";
  if (e.key === "F1") return "help";
  return typing || e.key === "Enter" || e.key === " " || e.key === "Tab" ? "field" : null;
}

export function bindKeys(k) {
  document.addEventListener("keydown", (e) => {
    const field = fieldKey(e, document.activeElement, k.cmd);
    if (field === "leave") { e.preventDefault(); k.leaveField(); return; }
    if (field === "help") { e.preventDefault(); k.help(); return; }
    if (field === "field") return;
    const empty = !k.cmd.value.trim();
    if (e.key === "Enter" && k.overlayOpen() && k.confirm()) { e.preventDefault(); return; }   // a question: yes
    if (e.key === "Escape") { e.preventDefault(); k.escape(); return; }
    if (e.key === "F1" || (e.key === "?" && empty)) { e.preventDefault(); k.help(); return; }
    if (e.altKey && /^Digit[1-9]$/.test(e.code)) { e.preventDefault(); k.maximize(Number(e.code.slice(5))); return; }
    if (e.altKey && (e.key === "ArrowUp" || e.key === "ArrowDown")) { e.preventDefault(); k.history(e.key === "ArrowUp" ? 1 : -1); return; }
    if (e.metaKey || e.ctrlKey || e.altKey || k.overlayOpen()) return;
    if (k.acOpen() && (e.key === "ArrowDown" || e.key === "ArrowUp")) { e.preventDefault(); k.acMove(e.key === "ArrowDown" ? 1 : -1); return; }
    if (k.acOpen() && e.key === "Tab") { e.preventDefault(); k.acAccept(); return; }
    if (e.key === "Enter") { e.preventDefault(); if (empty) k.drill(); else k.exec(); return; }
    if (!empty) { if (e.key === "Tab") e.preventDefault(); if (document.activeElement !== k.cmd) k.cmd.focus(); return; }
    if (e.key === "Delete" || e.key === "Backspace") { if (k.remove()) e.preventDefault(); return; }
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
