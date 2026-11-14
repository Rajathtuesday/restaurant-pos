/*
 * The shared page buttons (static/js/core/page_actions.js): print, reload,
 * back (or close a printout opened in its own tab), select a field.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { pageActions } from "../core/page_actions.js";

function fakeWindow({ opener = null } = {}) {
  const calls = [];
  return {
    calls,
    opener,
    print: () => calls.push("print"),
    close: () => calls.push("close"),
    location: { reload: () => calls.push("reload") },
    history: { back: () => calls.push("back") },
  };
}

test("print, reload and back do one thing each", () => {
  const win = fakeWindow();
  const actions = pageActions(win);
  actions["page.print"]();
  actions["page.reload"]();
  actions["page.back"]();
  assert.deepEqual(win.calls, ["print", "reload", "back"]);
});

test("a printout opened in its own tab closes instead of going back", () => {
  const win = fakeWindow({ opener: {} });
  pageActions(win)["page.back"]();
  assert.deepEqual(win.calls, ["close"]);
});

test("a read-only field's text is selected, for copying", () => {
  let selected = 0;
  pageActions(fakeWindow())["field.select"]({ select: () => { selected += 1; } });
  assert.equal(selected, 1);
});
