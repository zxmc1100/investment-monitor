import { test } from "node:test";
import assert from "node:assert/strict";
import { fold, resolveWatch, watchItems } from "../../web/app/watch.js";

const RHM = { ticker: "RHM.DE", name: "Rheinmetall", sector: "Industrials", country: "DE" };
const ADR = { ticker: "RNMBY", name: "Rheinmetall (ADR)", sector: "—", country: "DE" };

test("accents and case fold", () => {
  assert.equal(fold(" Société Générale "), "SOCIETE GENERALE");
});
test("one result, an exact ticker, or the single exact name is added", () => {
  assert.deepEqual(resolveWatch("FREENET", [{ ticker: "FNTN.DE", name: "freenet" }]), { kind: "one", ticker: "FNTN.DE", name: "freenet" });
  assert.deepEqual(resolveWatch("RNMBY", [RHM, ADR]), { kind: "one", ticker: "RNMBY", name: "Rheinmetall (ADR)" });
  assert.deepEqual(resolveWatch("RHEINMETALL", [RHM, ADR]), { kind: "one", ticker: "RHM.DE", name: "Rheinmetall" });
});
test("no result, or several without an exact name", () => {
  assert.deepEqual(resolveWatch("ZZZ", []), { kind: "none" });
  assert.equal(resolveWatch("RHEIN", [RHM, ADR]).kind, "several");
});
test("autocomplete rows run WATCH <ticker> and skip unknown sectors", () => {
  assert.deepEqual(watchItems([RHM, ADR]).map((i) => [i.label, i.desc]),
    [["WATCH RHM.DE", "Rheinmetall · Industrials · DE"], ["WATCH RNMBY", "Rheinmetall (ADR) · DE"]]);
});
