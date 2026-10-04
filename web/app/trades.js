// TRADES in the browser: the arithmetic of a trade, its one-line preview, the BUY / SELL / BONUS command grammar,
// ticker suggestions and reading an imported file. Mirrors monitor/portfolio/trades.py — the server decides and
// stores; this previews what it will store. Pure: no DOM.
export const ACTIONS = ["buy", "sell", "bonus"];

const fail = (msg) => { throw new Error(msg); };
// Upper case outside quoted values: "SHARES: 'x' IS NOT A NUMBER".
const loud = (msg) => msg.split(/('[^']*')/).map((p, i) => (i % 2 ? p : p.toUpperCase())).join("");

// Half up on the number as written (3 x 0.335 = 1.005 -> 1.01), as the server's Decimal rounding.
export function round(x, places) {
  const s = String(x);
  if (/e/i.test(s)) return Math.round(x * 10 ** places) / 10 ** places;
  return Number(`${Math.round(Number(`${s}e${places}`))}e-${places}`);
}

// One number as typed (ledger._number with a decimal point, or a comma that cannot be a thousands one).
export const LIMIT = 1e9;                           // shares, prices, totals above this are typing slips

export function parseNum(text, label) {
  if (typeof text === "number") return checked(text, label);
  const t = String(text ?? "").trim();
  if (!t) fail(loud(`${label}: empty`));
  if (/[ '’_\u00a0\u202f]/.test(t) || (t.includes(",") && t.includes(".")) || (t.split(",").length > 2)
      || (t.split(".").length > 2) || /^[+-]?[1-9]\d{0,2},\d{3}$/.test(t)) {
    fail(loud(`${label}: '${t}' has a thousands separator — write it without one, e.g. 1234.56`));
  }
  if (!/^[+-]?(\d+([.,]\d*)?|[.,]\d+)$/.test(t)) fail(loud(`${label}: '${t}' is not a number`));
  return checked(Number(t.replace(",", ".")), label);
}

function checked(v, label) {
  if (!Number.isFinite(v)) fail(`${label} IS NOT A NUMBER`);
  if (Math.abs(v) > LIMIT) fail(`${label} IS TOO LARGE (OVER 1,000,000,000)`);
  return v;
}

const given = (v) => v !== null && v !== undefined && String(v).trim() !== "";
const pad = (n) => String(n).padStart(2, "0");
const iso = (y, m, d) => `${y}-${pad(m)}-${pad(d)}`;
const real = (y, m, d) => { const x = new Date(Date.UTC(y, m - 1, d)); return x.getUTCFullYear() === y && x.getUTCMonth() === m - 1 && x.getUTCDate() === d; };
export const todayIso = (d = new Date()) => iso(d.getFullYear(), d.getMonth() + 1, d.getDate());
export const DATE = /^(\d{4}-\d{2}-\d{2}|\d{1,2}([./])\d{1,2}\2\d{4})$/;

// YYYY-MM-DD, DD.MM.YYYY or DD/MM/YYYY (day first) -> ISO; empty = today; never after today.
export function parseDate(text, today) {
  if (!given(text)) return today;
  const t = String(text).trim();
  let out = null;
  const dmy = /^(\d{1,2})([./])(\d{1,2})\2(\d{4})$/.exec(t), ymd = /^(\d{4})-(\d{2})-(\d{2})$/.exec(t);
  if (dmy && real(+dmy[4], +dmy[3], +dmy[1])) out = iso(+dmy[4], +dmy[3], +dmy[1]);
  else if (ymd && real(+ymd[1], +ymd[2], +ymd[3])) out = t;
  else if (dmy && dmy[2] === "/" && +dmy[3] > 12) fail(loud(`date: '${t}': dates with / are read day/month/year — write it as YYYY-MM-DD`));
  if (!out) fail(loud(`date: '${t}' is not a date — write it as YYYY-MM-DD (or DD.MM.YYYY)`));
  if (out > today) fail(`DATE ${out} IS IN THE FUTURE`);
  return out;
}

export function compute(action, shares, { pps = null, total = null, fee = null } = {}) {
  if (!ACTIONS.includes(action)) fail("ACTION MUST BE BUY, SELL OR BONUS");
  if (!(shares > 0)) fail("SHARES MUST BE > 0");
  if (fee !== null && fee < 0) fail("FEE MUST BE 0 OR MORE");
  if (total !== null) {
    if (!(total > 0)) fail("TOTAL MUST BE > 0");
    return { total, pps: round(total / shares, 4) };            // a total given is kept exactly
  }
  if (pps === null) fail("PRICE PER SHARE OR TOTAL REQUIRED");
  if (!(pps > 0)) fail("PRICE PER SHARE MUST BE > 0");
  const gross = shares * pps, f = fee ?? 0;
  const out = round(action === "buy" ? gross + f : action === "sell" ? gross - f : gross, 2);
  if (out <= 0) fail(`THE FEE LEAVES NOTHING: TOTAL €${out.toFixed(2)}`);
  return { total: out, pps: round(pps, 4) };
}

// The file's number forms: every digit kept, no exponent; money with at least two decimals.
export function fmtQty(x) {
  const s = Math.abs(x) < 1e-6 && x !== 0 ? x.toFixed(12) : String(x);
  return s.includes(".") ? s.replace(/0+$/, "").replace(/\.$/, "") : s;
}
export function fmtMoney(x) {
  const [whole, frac = ""] = fmtQty(x).split(".");
  return `${whole}.${frac.padEnd(2, "0")}`;
}
export function eur(x) {
  const [whole, frac] = fmtMoney(Math.abs(x)).split(".");
  return `€${Number(whole).toLocaleString("en-US")}.${frac}`;
}

// What will be stored, in one line: BUY 4 SAP.DE · €240.00/sh = €960.00 (+ / − €1.00 fee when one was entered).
export function describe(t, fee = null) {
  const extra = fee && (t.action === "buy" || t.action === "sell") ? ` ${t.action === "buy" ? "+" : "−"} ${eur(fee)} fee` : "";
  return `${t.action.toUpperCase()} ${fmtQty(t.shares)} ${t.ticker} · ${eur(t.pps)}/sh${extra} = ${eur(t.price)}`;
}

// A ticker as the server takes it: invisible characters dropped, capitals, only letters, digits and . - = ^
function ticker(text) {
  const t = String(text ?? "").normalize("NFKC").replace(/\p{Cf}/gu, "").trim().toUpperCase();
  if (!t) fail("TICKER MISSING");
  if (!/^[A-Z0-9.\-=^]+$/.test(t)) fail(`TICKER '${t}': ONLY LETTERS, DIGITS AND . - = ^`);
  return t;
}

// The form's (or a command's) values -> {trade, fee, body} | {error}. `keep`: an edit's stored price per share,
// kept while the price is untouched (the server's keep_pps). `body` is what the API gets.
export function tradeFrom(v, today) {
  try {
    const tk = ticker(v.ticker), action = String(v.action ?? "").toLowerCase();
    if (!ACTIONS.includes(action)) fail("ACTION MUST BE BUY, SELL OR BONUS");
    const shares = given(v.shares) ? parseNum(v.shares, "SHARES") : null;
    const total = given(v.total) ? parseNum(v.total, "TOTAL") : null;
    const pps = given(v.pps) ? parseNum(v.pps, "PRICE PER SHARE") : null;
    const fee = given(v.fee) ? parseNum(v.fee, "FEE") : null;
    const date = parseDate(v.date, today);
    const c = compute(action, shares, { pps, total, fee });
    const keep = total !== null && pps === null && v.keep > 0 ? v.keep : null;
    const trade = { date, ticker: tk, action, shares, price: c.total, pps: keep ?? c.pps };
    const body = { ticker: tk, action, shares, ...(total !== null ? { total } : { pps }),
      ...(keep !== null ? { pps: keep, keep_pps: true } : {}), ...(fee !== null && total === null ? { fee } : {}), date };
    return { trade, fee: total === null ? fee : null, body };
  } catch (e) {
    return { error: e.message };
  }
}

// A TRANSACTIONS row -> the form's values for editing it (the stored total; its price per share kept).
export const editValues = (r) => ({ ticker: r.tkr, action: String(r.action).toLowerCase(), shares: fmtQty(r.shares),
  pps: "", total: fmtMoney(r.total), date: r.date, fee: "", keep: r.pps });

// The row of `rows` (TRANSACTIONS) that is the same trade — date, ticker, action, shares, total — but `except`.
export const duplicateOf = (rows, t, except = null) => (rows ?? []).find((r) => r.id !== except && r.date === t.date
  && r.tkr === t.ticker && String(r.action).toLowerCase() === t.action && r.shares === t.shares && r.total === t.price) ?? null;

// What BUY / SELL / BONUS take (a bonus has no fee): HELP's line and the error of a command that misses a part.
export const tradeUsage = (action) => (action === "bonus" ? "BONUS <TICKER> <SHARES> @ <PRICE> | = <VALUE> [DATE]"
  : `${action.toUpperCase()} <TICKER> <SHARES> @ <PRICE> | = <TOTAL> [DATE] [FEE x]`);

// The ADD form after `value` was typed into field `k`: the price typed last is the source — the other empties
// (its placeholder shows what follows) — and touching shares or a price ends an edit's kept price per share.
export function typed(values, k, value) {
  const v = { ...values, [k]: value };
  if ((k === "pps" || k === "total") && given(value)) v[k === "pps" ? "total" : "pps"] = "";
  if (k === "shares" || k === "pps" || k === "total") v.keep = null;
  return v;
}

// The line under the ADD form: {text, cls: "" | "dim" (still needed) | "err" | "warn" (a twin in your file),
// derived: {pps | total: the other price, for its placeholder}}. `rows`: TRANSACTIONS; `editing`: the row's id.
export function formStatus(values, today, rows, editing = null) {
  if (!["ticker", "shares", "pps", "total"].some((k) => given(values[k]))) return { text: "", cls: "", derived: {} };
  const need = [!given(values.ticker) && "TICKER", !given(values.shares) && "SHARES",
    !given(values.pps) && !given(values.total) && "€ / SHARE OR TOTAL"].filter(Boolean);
  if (need.length) return { text: `STILL NEEDED: ${need.join(" · ")}`, cls: "dim", derived: {} };
  const r = tradeFrom(values, today);
  if (r.error) return { text: r.error, cls: "err", derived: {} };
  const derived = given(values.total) ? { pps: fmtMoney(r.trade.pps) } : { total: fmtMoney(r.trade.price) };
  const twin = duplicateOf(rows, r.trade, editing);
  return { text: describe(r.trade, r.fee) + (twin ? " · SAME AS A TRADE ALREADY IN YOUR FILE" : ""), cls: twin ? "warn" : "", derived };
}

const SEP = { ",": "COMMA", ";": "SEMICOLON", "\t": "TAB" };
const many = (n, one) => `${n} ${one}${n === 1 ? "" : "S"}`;

// One line over the paste preview: rows, how many go in, errors, warnings, and how the text was read.
export function pasteSummary(res) {
  if (res.error) return res.error;
  const rows = res.rows ?? [], warn = rows.filter((r) => !r.error && r.warnings?.length).length;
  return [many(rows.length, "ROW"), `${res.ok ?? 0} TO ADD`, res.bad ? `${res.bad} WITH AN ERROR` : "",
    warn ? many(warn, "WARNING") : "", `${SEP[res.delimiter] ?? res.delimiter} BETWEEN FIELDS`,
    res.decimal === "comma" ? "DECIMAL COMMA" : "DECIMAL POINT", res.header ? "HEADER READ" : "NO HEADER",
    ...(res.notes ?? [])].filter(Boolean).join(" · ");
}

// BUY|SELL|BONUS <TICKER> <SHARES> @ <PRICE> | = <TOTAL> [DATE] [FEE x] -> {type: "trade", values} | {type: "error"}.
export function tradeCommand(action, words) {
  const usage = tradeUsage(action);
  const flat = words.flatMap((w) => w.split(/([@=])/)).filter(Boolean);
  let fee = "", date = "";
  const f = flat.indexOf("FEE");
  if (f >= 0) {
    if (!flat[f + 1] || DATE.test(flat[f + 1])) return { type: "error", msg: `FEE <AMOUNT> — ${usage}` };
    fee = flat[f + 1];
    flat.splice(f, 2);
  }
  if (flat.length && DATE.test(flat.at(-1))) date = flat.pop();
  if (flat.length !== 4 || !["@", "="].includes(flat[2])) return { type: "error", msg: usage };
  const [tk, shares, op, value] = flat;
  return { type: "trade", values: { action, ticker: tk, shares, pps: op === "@" ? value : "", total: op === "=" ? value : "", date, fee } };
}

// Euro-area exchanges Yahoo suffixes: a ticker without one of these is quoted in another currency.
const EUR = [".DE", ".F", ".BE", ".DU", ".HM", ".HA", ".MU", ".SG", ".TG", ".AS", ".PA", ".MI", ".MC", ".BR", ".LS", ".VI",
  ".HE", ".IR", ".AT"];
export const isEur = (t) => EUR.some((s) => String(t).toUpperCase().endsWith(s));
const fold = (s) => String(s ?? "").normalize("NFKD").replace(/[\u0300-\u036f]/g, "").toUpperCase().trim();

// Suggestions for the ticker field: the holdings that match (ticker prefix or name), then the universe's
// (/api/lookup) — EUR listings before the rest, which are marked.
export function rankSuggestions(q, book, found, n = 8) {
  const f = fold(q);
  if (!f) return [];
  const mine = (book ?? []).filter((b) => fold(b.ticker).startsWith(f) || fold(b.name).includes(f))
    .map((b) => ({ ticker: b.ticker, name: b.name, note: "IN YOUR BOOK" }));
  const have = new Set(mine.map((m) => m.ticker));
  const rest = (found ?? []).filter((r) => !have.has(r.ticker)).map((r, i) => ({ r, i }))
    .sort((a, b) => (isEur(b.r.ticker) - isEur(a.r.ticker)) || a.i - b.i)
    .map(({ r }) => ({ ticker: r.ticker, name: r.name, note: [r.country, isEur(r.ticker) ? "" : "NOT A EUR LISTING — PRICED AS €"]
      .filter((x) => x && x !== "—").join(" · ") }));
  return [...mine, ...rest].slice(0, n);
}

// Windows-1252's 0x80–0x9F (where it differs from Latin-1). Decoded by hand: some runtimes' TextDecoder
// reads "windows-1252" as Latin-1 and turns € into \x80.
const CP1252 = "\u20ac\ufffd\u201a\u0192\u201e\u2026\u2020\u2021\u02c6\u2030\u0160\u2039\u0152\ufffd\u017d\ufffd"
  + "\ufffd\u2018\u2019\u201c\u201d\u2022\u2013\u2014\u02dc\u2122\u0161\u203a\u0153\ufffd\u017e\u0178";

// An imported file's bytes as text: UTF-8 (a BOM dropped), else Windows-1252 — what an older Excel saves.
export function decodeBytes(buf) {
  try {
    return new TextDecoder("utf-8", { fatal: true }).decode(buf).replace(/^\ufeff/, "");
  } catch {
    let out = "";
    for (const b of new Uint8Array(buf)) out += b >= 0x80 && b <= 0x9f ? CP1252[b - 0x80] : String.fromCharCode(b);
    return out;
  }
}
