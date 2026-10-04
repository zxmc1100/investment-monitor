import { test } from "node:test";
import assert from "node:assert/strict";
import { fieldKey } from "../../web/app/keys.js";

const cmd = { tagName: "INPUT", id: "cmd" };
const el = (tagName, extra = {}) => ({ tagName, ...extra });
const k = (key, mods = {}) => ({ key, ...mods });

test("a form field keeps its keys: letters, digits, arrows, Enter, Tab, Backspace stay in it", () => {
  for (const tag of ["INPUT", "TEXTAREA", "SELECT"]) {
    for (const key of ["a", "Z", "4", "6", ".", ",", "@", " ", "ArrowUp", "ArrowDown", "ArrowLeft", "PageDown", "Home",
                       "Enter", "Tab", "Backspace", "Delete", "?"]) {
      assert.equal(fieldKey(k(key), el(tag), cmd), "field", `${tag} ${key}`);
    }
    assert.equal(fieldKey(k("ArrowLeft", { shiftKey: true }), el(tag), cmd), "field");
    assert.equal(fieldKey(k("1", { altKey: true }), el(tag), cmd), "field");      // ¡ on a Mac keyboard
  }
  assert.equal(fieldKey(k("x"), el("DIV", { isContentEditable: true }), cmd), "field");
});

test("Esc leaves the field, F1 still opens help", () => {
  assert.equal(fieldKey(k("Escape"), el("INPUT"), cmd), "leave");
  assert.equal(fieldKey(k("Escape"), el("TEXTAREA"), cmd), "leave");
  assert.equal(fieldKey(k("F1"), el("INPUT"), cmd), "help");
});

test("a focused button keeps Enter, Space and Tab; other keys work the terminal; the command bar is no field", () => {
  for (const key of ["Enter", " ", "Tab"]) assert.equal(fieldKey(k(key), el("BUTTON"), cmd), "field", key);
  assert.equal(fieldKey(k("Escape"), el("BUTTON"), cmd), "leave");
  for (const key of ["a", "6", "ArrowDown"]) assert.equal(fieldKey(k(key), el("BUTTON"), cmd), null, key);
  assert.equal(fieldKey(k("a"), cmd, cmd), null);
  assert.equal(fieldKey(k("a"), el("BODY"), cmd), null);
  assert.equal(fieldKey(k("a"), null, cmd), null);
});
