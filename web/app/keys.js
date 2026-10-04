// Global keymap. Letters always feed the command bar; navigation keys act on
// the focused table only while the bar is empty, so typing never fights the cursor. A form field (TRADES)
// keeps its own keys: Esc leaves it, F1 is help, everything else is the field's. An open question (DELETE …?)
// is answered only by Enter on the question itself with an empty bar, no sooner than ASK_MS after it opened;
// any other key closes it unanswered. Backspace never deletes a trade; Del does only right after a cursor move.
const NAV = { ArrowDown: 1, ArrowUp: -1, PageDown: 10, PageUp: -10, Home: -1e9, End: 1e9 };
const QUIET = new Set(["Shift", "Control", "Alt", "Meta", "CapsLock", "Enter", "Escape"]);
const ONCE = new Set(["Enter", "Delete", "Backspace"]);     // a held key repeats: these act once per press
export const ASK_MS = 400;                                  // an Enter sooner than this after a question: a double-tap

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

// The keymap as a decision: {cancel: close the open question first, act, prevent, arg}. `s`: {field: fieldKey's
// answer, empty: the command bar, overlay, asking: a question is open, onOverlay: focus is on it, askAge: ms since
// it opened, typed: something was typed in the bar since the table cursor last moved, ac: the palette is open}.
// Pure — bindKeys runs it.
export function keyAction(e, s) {
  const cancel = !!s.asking && !QUIET.has(e.key);
  const r = (act, prevent = true, arg = null) => ({ cancel, act, prevent, arg });
  if (s.field === "leave") return r("leave");
  if (s.field === "help") return r("help");
  if (s.field === "field") return r("field", false);
  const repeat = e.repeat && ONCE.has(e.key);
  if (e.key === "Enter" && s.asking) {
    return r(!repeat && s.empty && s.onOverlay && (s.askAge ?? 0) >= ASK_MS ? "confirm" : "none");
  }
  if (repeat) return r("none");
  if (e.key === "Escape") return r("escape");
  if (e.key === "F1" || (e.key === "?" && s.empty)) return r("help");
  if (e.altKey && /^Digit[1-9]$/.test(e.code ?? "")) return r("maximize", true, Number(e.code.slice(5)));
  if (e.altKey && (e.key === "ArrowUp" || e.key === "ArrowDown")) return r("history", true, e.key === "ArrowUp" ? 1 : -1);
  if (e.metaKey || e.ctrlKey || e.altKey || (s.overlay && !cancel)) return r("none", false);
  if (s.ac && (e.key === "ArrowDown" || e.key === "ArrowUp")) return r("acMove", true, e.key === "ArrowDown" ? 1 : -1);
  if (s.ac && e.key === "Tab") return r("acAccept");
  if (e.key === "Enter") return r(s.empty ? "drill" : "exec");
  if (!s.empty) return r("bar", e.key === "Tab");
  if (e.key === "Backspace") return r("none", false);                    // never a delete: it edits text
  if (e.key === "Delete") return r(s.typed ? "none" : "remove", false);   // only right after a cursor move
  if (e.key in NAV) {
    if (e.shiftKey && (e.key === "ArrowUp" || e.key === "ArrowDown")) return r("sortDir", true, e.key === "ArrowUp" ? "asc" : "desc");
    return r("cursor", true, NAV[e.key]);
  }
  if (e.shiftKey && (e.key === "ArrowLeft" || e.key === "ArrowRight")) return r("sort", true, e.key === "ArrowRight" ? 1 : -1);
  if (e.key === "Tab") return r("focusPanel", true, e.shiftKey ? -1 : 1);
  if (/^[1-9]$/.test(e.key)) return r("fkey", true, Number(e.key));
  if (e.key.length === 1 && e.key !== " ") return r("type", false);
  return r("none", false);
}

export function bindKeys(k) {
  document.addEventListener("keydown", (e) => {
    const a = keyAction(e, { field: fieldKey(e, document.activeElement, k.cmd), empty: !k.cmd.value.trim(),
      overlay: k.overlayOpen(), asking: k.asking(), onOverlay: k.onOverlay(), askAge: k.askAge(), typed: k.typed(),
      ac: k.acOpen() });
    if (a.cancel) k.cancelAsk();
    if (a.prevent) e.preventDefault();
    switch (a.act) {
      case "leave": k.leaveField(); break;
      case "help": k.help(); break;
      case "confirm": k.confirm(); break;
      case "escape": k.escape(); break;
      case "maximize": k.maximize(a.arg); break;
      case "history": k.history(a.arg); break;
      case "acMove": k.acMove(a.arg); break;
      case "acAccept": k.acAccept(); break;
      case "drill": k.drill(); break;
      case "exec": k.exec(); break;
      case "remove": if (k.remove()) e.preventDefault(); break;
      case "sortDir": k.sortDir(a.arg); break;
      case "cursor": k.cursor(a.arg); break;
      case "sort": k.sort(a.arg); break;
      case "focusPanel": k.focusPanel(a.arg); break;
      case "fkey": k.fkey(a.arg); break;
      case "bar": case "type": if (document.activeElement !== k.cmd) k.cmd.focus(); break;
      default: break;
    }
  });
}
