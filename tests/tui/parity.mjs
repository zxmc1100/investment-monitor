// The browser's pure modules on the cases tests/tui/test_parity.py sends: {name: [module, function, ...args]}. The
// Python mirror must answer the same. stdin: JSON; stdout: JSON.
import * as fmt from "../../web/app/fmt.js";
import * as ranges from "../../web/app/ranges.js";
import * as scale from "../../web/app/scale.js";
import * as cmd from "../../web/app/cmd.js";
import * as fuzzy from "../../web/app/fuzzy.js";
import * as watch from "../../web/app/watch.js";
import * as route from "../../web/app/route.js";
import * as tablesort from "../../web/app/tablesort.js";
import * as status from "../../web/app/status.js";
import * as trades from "../../web/app/trades.js";
import * as ask from "../../web/app/ask.js";
import * as build from "../../web/app/build.js";
import * as diff from "../../web/app/diff.js";
import * as scatter from "../../web/app/render/scatter.js";
import * as table from "../../web/app/render/table.js";
import * as chart from "../../web/app/render/chart.js";
import * as heatmap from "../../web/app/render/heatmap.js";

const MODS = { fmt, ranges, scale, cmd, fuzzy, watch, route, tablesort, status, trades, ask, build, diff, scatter, table, chart, heatmap };
const plain = (v) => (v instanceof Set ? [...v].sort() : v instanceof Map ? Object.fromEntries(v) : v);

let input = "";
for await (const chunk of process.stdin) input += chunk;
const out = {};
for (const [name, [mod, fn, ...args]] of Object.entries(JSON.parse(input))) {
  const f = MODS[mod][fn];
  // JSON has no Infinity, and a Set cannot travel: spelled out by the sender.
  const live = args.map((a) => (a === "__INF__" ? Infinity : a === "__-INF__" ? -Infinity : a?.__set__ ? new Set(a.__set__) : a));
  out[name] = plain(mod === "trades" && (fn === "tradeFrom" || fn === "formStatus") ? f(...live) : f(...live));
}
process.stdout.write(JSON.stringify(out, (k, v) => (v === Infinity ? "__INF__" : v === -Infinity ? "__-INF__" : v === undefined ? null : v)));
