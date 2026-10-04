// Hash routes: "#/PORT", "#/SEC/SAP.DE". Pure.
function decode(s) {
  try { return decodeURIComponent(s); } catch { return s; }   // "#/SEC/%E0" must not break routing
}

export function routeOf(hash) {
  const [id, ...rest] = String(hash ?? "").replace(/^#\/?/, "").split("/");
  const param = rest.join("/").trim();
  return { id: (id || "PORT").toUpperCase(), param: param ? decode(param).toUpperCase() : null };
}

export const hashOf = ({ id, param }) => `#/${id}${param ? `/${encodeURIComponent(param)}` : ""}`;
export const keyOf = ({ id, param }) => (param ? `${id}~${param}` : id);

// Esc back stack of route hashes. Every in-app navigation that is not itself an Esc-back pushes
// the route it left; a route already in the stack moves to the top (duplicates collapse, so
// flipping between two securities never buries where you came from). Bounded.
export const BACK_MAX = 20;

export function pushBack(stack, from) {
  if (!from) return stack;
  return [...stack.filter((h) => h !== from), from].slice(-BACK_MAX);
}

// Esc: the most recent route that is not the current one; an empty stack goes home.
export function popBack(stack, current) {
  const rest = stack.filter((h) => h !== current);
  return { to: rest.at(-1) ?? "#/PORT", stack: rest.slice(0, -1) };
}

// Assigning a hash fires `hashchange` only when it differs; a back-step that lands on the current
// hash must therefore not arm S.backNav (the flag is consumed by the next route()). Pure.
export const changesHash = (current, to) => current !== to;
