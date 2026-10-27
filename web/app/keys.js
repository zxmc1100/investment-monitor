// Global keymap. Letters always feed the command bar; navigation keys act on
// the focused table only while the bar is empty, so typing never fights the cursor. A form field (TRADES)
// keeps its own keys: Esc leaves it, F1 is help, everything else is the field's. An open question (DELETE …?)
// is answered only by Enter on the question itself with an empty bar, no sooner than ASK_MS after it opened;
// any other key closes it unanswered. Backspace never deletes a trade; Del does only right after a cursor move.
// A finger (an iPad) gets taps for what it has no key for: tapAction. The F-key bar itself: tabbar.js.
import { bindBar } from "./tabbar.js";
const NAV = { ArrowDown: 1, ArrowUp: -1, PageDown: 10, PageUp: -10, Home: -1e9, End: 1e9 };
const QUIET = new Set(["Shift", "Control", "Alt", "Meta", "CapsLock", "Enter", "Escape"]);
const ONCE = new Set(["Enter", "Delete", "Backspace"]);     // a held key repeats: these act once per press
const MODE_KEYS = { KeyR: "ROI", KeyM: "MWR", KeyT: "TWR" };   // Option/Alt + the view's initial
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
  // The ROI chart's view: Option/Alt + its initial, by the physical key — a Mac's Option+R / M / T type ®, µ, †,
  // so the key is prevented and nothing reaches the command bar (a layout's dead key: bindKeys swallows its
  // accent). Never Ctrl: Ctrl+R / T / M are the browser's.
  if (e.altKey && !e.ctrlKey && !e.metaKey && MODE_KEYS[e.code]) return r("mode", true, MODE_KEYS[e.code]);
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

// What a finger's or a pen's tap does beyond a click (an iPad: no Alt, Esc or Enter) — a keymap handler's name:
// on an overlay's title bar it closes it (never a question's: its buttons answer it), on a panel's title bar it
// maximizes the panel (again: restores), on the row the table cursor already stood on it opens it, as Enter.
// `s`: {overlayHead, asking, panelHead, onCursor: the row was the cursor's when pressed}. Pure.
export function tapAction(s) {
  if (s.overlayHead) return s.asking ? null : "escape";
  if (s.panelHead) return "maximize";
  return s.onCursor ? "drill" : null;
}

// Taps, and the on-screen keyboard: the command bar still takes focus after a tap (app.js), so a hardware keyboard
// types at once, but inputmode none keeps the on-screen one down unless the tap was on the bar itself.
function bindTaps(k) {
  let finger = false, onCursor = false;
  document.addEventListener("pointerdown", (e) => {
    finger = e.pointerType !== "mouse";
    if (!finger) return;
    onCursor = !!e.target.closest?.("#grid tbody tr.cur");
    const want = e.target === k.cmd ? "" : "none";
    if (k.cmd.inputMode !== want) { k.cmd.inputMode = want; k.cmd.blur(); }   // the next focus shows / hides it
  }, true);
  document.addEventListener("click", (e) => {
    if (!finger) return;
    const head = e.target.closest?.("#overlay > .h, #grid .panel > .h");
    const overlayHead = head?.parentElement.id === "overlay";
    const act = tapAction({ overlayHead, asking: k.asking(), panelHead: !!head && !overlayHead, onCursor });
    if (act) k[act](Number(head?.parentElement.dataset.n));
  });
}

// A shortcut that is a dead key (a Mac's Option+N: ˜) still starts an accent in the focused field — the
// input method gets it whatever the page does with the key. The field is left for that moment, so the
// accent lands nowhere, then given back its text and focus.
function swallowAccent(el) {
  if (!el || typeof el.value !== "string") return;
  const text = el.value;
  el.blur();
  setTimeout(() => { el.value = text; el.focus(); }, 0);
}

export function bindKeys(k) {
  document.addEventListener("keydown", (e) => {
    const a = keyAction(e, { field: fieldKey(e, document.activeElement, k.cmd), empty: !k.cmd.value.trim(),
      overlay: k.overlayOpen(), asking: k.asking(), onOverlay: k.onOverlay(), askAge: k.askAge(), typed: k.typed(),
      ac: k.acOpen() });
    if (a.cancel) k.cancelAsk();
    if (a.prevent) e.preventDefault();
    if (e.key === "Dead" && a.prevent) swallowAccent(document.activeElement);
    switch (a.act) {
      case "leave": k.leaveField(); break;
      case "help": k.help(); break;
      case "confirm": k.confirm(); break;
      case "escape": k.escape(); break;
      case "maximize": k.maximize(a.arg); break;
      case "history": k.history(a.arg); break;
      case "mode": k.mode(a.arg); break;
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
  bindTaps(k);
  bindBar(k);
}
