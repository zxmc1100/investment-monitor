// Service worker for another device (an iPad over Tailscale; api.js keepOffline registers it). Every GET goes to
// the Mac first and its answer is kept; when the Mac does not answer within WAIT_MS (asleep, off, no network) the
// last kept answer shows instead and the terminal says OFFLINE. The screens list fetches every screen once more,
// so each one opens offline, not only those visited. The live stream and every write pass straight through.
const KEEP = "im-offline-v1";
const WAIT_MS = 4000;

self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

const put = (href, res) => caches.open(KEEP).then((c) => c.put(href, res));

async function warm(list) {                          // every screen the list names (no parameter screens: SEC)
  const { screens = [] } = await list.json();
  await Promise.all(screens.filter((s) => s.status === "live" && !s.param).map(async (s) => {
    const href = new URL(`api/screen/${s.id}`, self.registration.scope).href;
    const res = await fetch(href);
    if (res.status === 200) await put(href, res);
  }));
}

// The copies are taken here, synchronously, before the page reads the answer.
function keep(url, res) {
  if (res.status !== 200) return null;
  const list = url.pathname.endsWith("/api/screens") ? res.clone() : null;
  return Promise.all([put(url.href, res.clone()), list && warm(list)]);
}

function answer(url, net) {
  return new Promise((resolve, reject) => {
    const kept = () => caches.match(url.href, { ignoreVary: true }).then((hit) => (hit ? resolve(hit) : net.then(resolve, reject)));
    const late = setTimeout(kept, WAIT_MS);
    net.then((res) => { clearTimeout(late); resolve(res); }, () => { clearTimeout(late); kept(); });
  });
}

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== self.location.origin || url.pathname.endsWith("/api/stream")) return;
  const net = fetch(e.request);
  e.waitUntil(net.then((res) => keep(url, res)).catch(() => {}));   // first on `net`: it copies before answer
  e.respondWith(answer(url, net));
});
