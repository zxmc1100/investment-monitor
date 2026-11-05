// The F-key bar as a tab bar. The open screen's key is marked (.on). Its key again closes it, the way a tab bar's
// tab does: an overlay or a maximized panel first, else back to the screen before (never past home); HELP again
// closes help. A finger (an iPad) gets iOS's glass lens: it sits on the open screen's key, slides to the next one,
// lifts under a finger, follows it along the bar and opens the key it is let go on. app.js renders the keys
// (renderFkeys); this file only watches them.
import { changesHash, popBack, pushBack, routeOf } from "./route.js";

const HOME = routeOf("").id;
const SLOP_PX = 8;        // a finger that moved less tapped
const AWAY_PX = 60;       // let go this far above or below the bar: nothing opens

// What a click on the key `run` does: "open" (as before), "escape" (an overlay or a maximized panel closes) or
// "back" (the open screen closes); null: home's own key at home. `s`: {run, current, overlay, max}. Pure.
export function barClick(s) {
  if (s.run === "HELP") return s.overlay ? "escape" : "open";
  if (s.run !== s.current) return "open";
  if (s.overlay || s.max) return "escape";
  return s.current === HOME ? null : "back";
}

// The index of the key under x — or the nearest one, so a finger past either end still picks one. `boxes`:
// [{left, right}] in the bar's order. Pure.
export function keyAt(boxes, x) {
  let best = -1, gap = Infinity;
  boxes.forEach((b, i) => {
    const d = x < b.left ? b.left - x : x > b.right ? x - b.right : 0;
    if (d < gap) { best = i; gap = d; }
  });
  return best;
}

export function bindBar(k) {
  const bar = document.querySelector("#fkeys");
  if (!bar) return;
  const lens = document.createElement("div");
  lens.id = "lens";
  let back = [], backing = false, drag = null, mute = 0;

  const keys = () => [...bar.querySelectorAll("[data-run]")].filter((el) => el.offsetWidth);   // HELP is hidden on an iPad
  const current = () => routeOf(location.hash).id;

  // The lens over `el` — or centred on a finger at x, as wide as the key under it. Layout boxes, not the
  // magnified (transformed) ones.
  function place(el, x) {
    if (!el) { lens.style.opacity = "0"; return; }
    const w = el.offsetWidth, from = bar.getBoundingClientRect().left + bar.clientLeft;
    const left = x === undefined ? el.offsetLeft : Math.min(Math.max(x - from - w / 2, 0), bar.clientWidth - w);
    Object.assign(lens.style, { left: `${left}px`, top: `${el.offsetTop}px`, width: `${w}px`,
      height: `${el.offsetHeight}px`, opacity: "1" });
  }

  function mark() {
    if (!lens.isConnected) bar.prepend(lens);
    const id = current();
    for (const el of keys()) el.classList.toggle("on", el.dataset.run === id);
    if (!drag) place(bar.querySelector(".on"));
  }

  function goBack() {
    const here = location.hash || `#/${HOME}`;
    const { to, stack } = popBack(back, here);
    back = stack;
    if (!changesHash(here, to)) return;
    backing = true;
    location.hash = to;
  }

  addEventListener("hashchange", (e) => {
    if (!backing) back = pushBack(back, new URL(e.oldURL).hash || `#/${HOME}`);
    backing = false;
    mark();
  });
  new MutationObserver(mark).observe(bar, { childList: true });   // renderFkeys replaced the keys
  addEventListener("resize", () => mark());
  mark();

  // Before the key's own click (app.js): its open screen's key, or HELP over help, closes instead.
  document.addEventListener("click", (e) => {
    const el = e.target.closest?.("#fkeys [data-run]");
    if (!el) return;
    if (Date.now() < mute) { e.stopPropagation(); return; }        // the tap a drag already answered
    const act = barClick({ run: el.dataset.run, current: current(), overlay: k.overlayOpen(),
      max: !!document.querySelector("#grid .panel.max") });
    if (act === "open") return;
    e.stopPropagation();
    if (act === "escape") k.escape();
    else if (act === "back") goBack();
  }, true);

  const under = (x) => keys()[keyAt(keys().map((el) => el.getBoundingClientRect()), x)];

  bar.addEventListener("pointerdown", (e) => {
    if (e.pointerType === "mouse") return;
    drag = { x: e.clientX, y: e.clientY, t: e.timeStamp, moved: false };
    bar.classList.add("drag");
    hot(under(e.clientX), e.clientX);
  });
  bar.addEventListener("pointermove", (e) => {
    if (!drag) return;
    if (Math.abs(e.clientX - drag.x) > SLOP_PX) drag.moved = true;
    // liquid: the faster the finger, the longer and flatter the lens
    const v = Math.min(Math.abs(e.clientX - (drag.lx ?? drag.x)) / Math.max(e.timeStamp - (drag.lt ?? drag.t), 1), 3);
    Object.assign(drag, { lx: e.clientX, lt: e.timeStamp });
    lens.style.setProperty("--stretch", (v / 10).toFixed(3));
    hot(under(e.clientX), e.clientX);
  });
  const end = (e) => {
    if (!drag) return;
    const moved = drag.moved;
    const pick = e.type === "pointerup" && moved && Math.abs(e.clientY - drag.y) < AWAY_PX ? under(e.clientX) : null;
    drag = null;
    bar.classList.remove("drag");
    lens.style.setProperty("--stretch", "0");
    hot(null);
    if (pick && pick.dataset.run !== current()) pick.dispatchEvent(new MouseEvent("click", { bubbles: true }));
    if (moved) mute = Date.now() + 400;          // a browser's own click after a short drag is not a tap
    mark();
  };
  bar.addEventListener("pointerup", end);
  bar.addEventListener("pointercancel", end);

  function hot(el, x) {
    for (const key of keys()) key.classList.toggle("hot", key === el);
    if (el) place(el, x);
  }
}
