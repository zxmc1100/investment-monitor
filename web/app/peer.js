// CASH here, INV in Cashflow Monitor: the switch between the two apps. Out of full screen it is a page change. In
// full screen a page change would end it (no browser keeps full screen across pages), so the other app opens in a
// frame over this one and the same command there closes the frame again; the frame stays loaded, so switching back
// and forth is instant. Messages: {monitor: "back"} and {monitor: "full"} from the frame, {monitor: "shown"} to it.
// The same file sits in Cashflow Monitor.
export const framed = window.parent !== window;
let frame = null;

function show() {
  frame.hidden = false;
  frame.focus();
  frame.contentWindow?.postMessage({ monitor: "shown" }, "*");
}

export function switchTo(url) {
  if (framed) return void window.parent.postMessage({ monitor: "back" }, "*");
  if (!document.fullscreenElement) return void (location.href = url);
  if (frame) return show();
  frame = document.createElement("iframe");
  frame.id = "peer";
  frame.addEventListener("load", show);
  frame.src = url;
  document.body.append(frame);
}

// FULL inside the frame: the page around it owns full screen.
export const askFull = () => window.parent.postMessage({ monitor: "full" }, "*");

// back(): the frame closed itself; full(): it asked for full screen on / off; shown(): this page, framed, is in view.
export function listen({ back, full, shown }) {
  window.addEventListener("message", (e) => {
    const m = e.data?.monitor;
    if (frame && e.source === frame.contentWindow) {
      if (m === "back") {
        frame.hidden = true;
        back();
      } else if (m === "full") full();
    } else if (framed && e.source === window.parent && m === "shown") shown();
  });
}
