// Number/date formatting for terminal cells, the status bar and chart axes. Pure: no DOM.
// Dates read DD MON YY everywhere (29 SEP 26); a time of day is appended where one matters.
export const MON = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"];
const pad = (n) => String(n).padStart(2, "0");
const hms = (d) => `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
const dmy = (d) => `${pad(d.getDate())} ${MON[d.getMonth()]} ${String(d.getFullYear()).slice(2)}`;

function group(v, nd) {
  return v.toLocaleString("en-US", { minimumFractionDigits: nd, maximumFractionDigits: nd });
}

function plain(v, nd, pre, post) {
  const r = Number(v.toFixed(nd));
  return { text: (r < 0 ? "-" : "") + pre + group(Math.abs(r), nd) + post, cls: "" };
}

function signed(v, nd, pre, post) {
  const r = Number(v.toFixed(nd));
  const sign = r > 0 ? "+" : r < 0 ? "-" : "";
  return { text: sign + pre + group(Math.abs(r), nd) + post, cls: r > 0 ? "up" : r < 0 ? "dn" : "" };
}

export function fmtDate(v) {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(String(v));
  return m ? `${m[3]} ${MON[Number(m[2]) - 1]} ${m[1].slice(2)}` : String(v);
}

// A status-bar time (a tier's last run, the snapshot's build): today's as HH:MM:SS, an older one as
// DD MON YY HH:MM, in local time.
export function fmtStamp(iso, now = new Date()) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toDateString() === now.toDateString() ? hms(d) : `${dmy(d)} ${hms(d).slice(0, 5)}`;
}
export const fmtClock = (d) => `${dmy(d)} ${hms(d)}`;

// Time-axis tick labels (uPlot `values`; ticks in epoch seconds at local boundaries, `incr` their
// spacing in seconds): 2026 for years, OCT 26 for months, 04 OCT for days.
export function timeTicks(ticks, incr) {
  return ticks.map((t) => {
    const d = new Date(t * 1000);
    if (incr >= 360 * 86400) return String(d.getFullYear());
    if (incr >= 28 * 86400) return `${MON[d.getMonth()]} ${String(d.getFullYear()).slice(2)}`;
    return `${pad(d.getDate())} ${MON[d.getMonth()]}`;
  });
}

const NUMERIC = new Set(["eur", "eur+", "pct", "pct+", "pct+r", "num", "num+", "int", "bp+", "mult", "qty", "px"]);
const span = (v, lo, hi) => ({ text: v.toLocaleString("en-US", { minimumFractionDigits: lo, maximumFractionDigits: hi }), cls: "" });
const MARKS = { H: "●", W: "★", HW: "●★" };

export function fmt(v, spec = "text") {
  if (v === null || v === undefined || (typeof v === "number" && !Number.isFinite(v))) return { text: "—", cls: "na" };
  const [kind, arg] = String(spec || "text").split(":");
  if (NUMERIC.has(kind) && typeof v !== "number") return { text: String(v), cls: "" };
  const nd = arg === undefined ? null : Number(arg);
  switch (kind) {
    case "eur": return plain(v, nd ?? 2, "€", "");
    case "eur+": return signed(v, nd ?? 2, "€", "");
    case "pct": return plain(v, nd ?? 1, "", "%");
    case "pct+": return signed(v, nd ?? 1, "", "%");
    case "pct+r": { const f = signed(v, nd ?? 1, "", "%"); return { text: f.text, cls: f.cls === "up" ? "dn" : f.cls === "dn" ? "up" : "" }; }
    case "num": return plain(v, nd ?? 2, "", "");
    case "num+": return signed(v, nd ?? 2, "", "");
    case "int": return plain(v, 0, "", "");
    case "bp+": return signed(v, nd ?? 1, "", "bp");
    case "mult": return plain(v, nd ?? 1, "", "×");
    case "qty": return span(v, 0, 6);                      // shares as entered: 4, 0.15, 13.513513
    case "px": return span(v, 2, 4);                       // a price per share: 240.00, 33.3333
    case "mark": return { text: MARKS[v] ?? String(v), cls: "am" };
    case "date": return { text: fmtDate(v), cls: "dim" };
    case "side": return { text: String(v), cls: v === "BUY" ? "up" : v === "SELL" ? "dn" : "" };
    default: return { text: String(v), cls: "" };
  }
}
