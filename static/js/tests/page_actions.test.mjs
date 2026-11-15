/*
 * The shared page buttons (static/js/core/page_actions.js): print, reload,
 * back (or close a printout opened in its own tab), select a field.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { pageActions, pageChangeActions, pageSubmitActions } from "../core/page_actions.js";

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

test("a ticked box shows its section, an unticked one hides it", () => {
  const target = { hidden: true };
  const doc = { getElementById: (id) => (id === "secretBox" ? target : null) };
  const reveal = pageChangeActions()["field.reveal"];
  reveal({ checked: true, dataset: { target: "secretBox" }, ownerDocument: doc });
  assert.equal(target.hidden, false);
  reveal({ checked: false, dataset: { target: "secretBox" }, ownerDocument: doc });
  assert.equal(target.hidden, true);
  reveal({ checked: true, dataset: { target: "missing" }, ownerDocument: doc });   // no element: nothing breaks
});

test("a form that asks first sends only on yes, asked in Rasova's sheet", async () => {
  for (const [answer, sent] of [[{ isConfirmed: true }, 1], [{ isConfirmed: false }, 0], [undefined, 0]]) {
    const asked = [];
    const ui = { confirm: async (...a) => { asked.push(a); return answer; } };
    let submitted = 0;
    let prevented = 0;
    const form = { dataset: { confirm: "Delete Rao's?", confirmText: "Gone for good.", confirmButton: "Delete" }, submit: () => { submitted += 1; } };
    await pageSubmitActions(ui)["confirm.submit"](form, { preventDefault: () => { prevented += 1; } });
    assert.equal(prevented, 1, "the browser never sends it by itself");
    assert.equal(submitted, sent);
    assert.deepEqual(asked, [["Delete Rao's?", "Gone for good.", "Delete"]]);
  }
});
