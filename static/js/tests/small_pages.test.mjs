/*
 * The small pages moved to modules on 14 Nov 2026: Setup tables (rename),
 * the cash register, the control panel's presets, the printer page's MAC
 * field, the reports' tab row, the browser receipt, and ui.ask (the
 * replacement for prompt()).
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { applyPreset } from "../accounts/preset.js";
import { createUi } from "../core/toast.js";
import { autoPrint } from "../orders/thermal_receipt.js";
import { centerActiveTab } from "../reports/tabs.js";
import { formatMac } from "../setup/mac.js";
import { renameTable } from "../setup/rename_table.js";
import { submitSession } from "../shifts/session.js";
import { fakeUi, networkDown, reply } from "./helpers.mjs";

/* ---- ui.ask ---------------------------------------------------------------- */

function askWith(answer) {
  const fired = [];
  const ui = createUi({ swal: () => ({ fire: async (opts) => { fired.push(opts); return answer; } }), isDark: () => false, doc: {} });
  return { ui, fired };
}

test("confirm: the title is text, never HTML (it carries typed names)", async () => {
  const { ui, fired } = askWith({ isConfirmed: true });
  await ui.confirm('Delete "<img src=x onerror=boom()>"?', "Gone for good.", "Delete");
  assert.equal(fired[0].titleText, 'Delete "<img src=x onerror=boom()>"?');
  assert.equal(fired[0].title, undefined);
});

test("ask: the typed text, trimmed; the title is text, never HTML", async () => {
  const { ui, fired } = askWith({ isConfirmed: true, value: "  Patio 2 " });
  assert.equal(await ui.ask("<b>Rename</b>", { value: "T1" }), "Patio 2");
  assert.equal(fired[0].titleText, "<b>Rename</b>");
  assert.equal(fired[0].title, undefined);
  assert.equal(fired[0].inputValue, "T1");
});

test("ask: cancelled, or left as it was, is null", async () => {
  assert.equal(await askWith({ isConfirmed: false }).ui.ask("Rename", { value: "T1" }), null);
  assert.equal(await askWith({ isConfirmed: true, value: " T1 " }).ui.ask("Rename", { value: "T1" }), null);
});

/* ---- renaming a table ------------------------------------------------------ */

const chip = { dataset: { url: "/setup/tables/4/rename/", name: "T4" } };

function fakeApi(answer) {
  const posts = [];
  return {
    posts,
    post: async (url, body) => {
      posts.push([url, body]);
      if (answer instanceof Error) throw answer;
      return answer;
    },
  };
}

test("rename: asks, posts the new name to the chip's address, reloads", async () => {
  const ui = { ...fakeUi(), ask: async () => "Patio" };
  const api = fakeApi({ success: true });
  let reloaded = 0;
  assert.equal(await renameTable(chip, { ui, api, reload: () => { reloaded += 1; } }), true);
  assert.deepEqual(api.posts, [["/setup/tables/4/rename/", { name: "Patio" }]]);
  assert.equal(reloaded, 1);
});

test("rename: backing out sends nothing", async () => {
  const ui = { ...fakeUi(), ask: async () => null };
  const api = fakeApi({ success: true });
  assert.equal(await renameTable(chip, { ui, api, reload: () => assert.fail("no reload") }), false);
  assert.deepEqual(api.posts, []);
});

test("rename: a refusal shows the server's words and stays", async () => {
  const ui = { ...fakeUi(), ask: async () => "Patio" };
  const api = fakeApi({ success: false, error: "Name cannot be empty" });
  assert.equal(await renameTable(chip, { ui, api, reload: () => assert.fail("no reload") }), false);
  assert.deepEqual(ui.calls.toast, [["Name cannot be empty", "error"]]);
});

test("rename: a failed request (shown by the client) stays", async () => {
  const ui = { ...fakeUi(), ask: async () => "Patio" };
  const api = fakeApi(new Error("Network error"));
  assert.equal(await renameTable(chip, { ui, api, reload: () => assert.fail("no reload") }), false);
});

/* ---- the cash register ----------------------------------------------------- */

const doc = { getElementById: (id) => ({ "open-amt": { value: "500" }, "close-amt": { value: "1250.50" } })[id] };
const openButton = { dataset: { url: "/shifts/sessions/open/", field: "opening_balance", input: "open-amt" } };
const closeButton = { dataset: { url: "/shifts/sessions/close/", field: "actual_cash", input: "close-amt" } };

test("register: open and close send their own field with the amount typed", async () => {
  const api = fakeApi({ success: true });
  let reloaded = 0;
  await submitSession(openButton, { api, ui: fakeUi(), doc, reload: () => { reloaded += 1; } });
  await submitSession(closeButton, { api, ui: fakeUi(), doc, reload: () => { reloaded += 1; } });
  assert.deepEqual(api.posts, [
    ["/shifts/sessions/open/", { opening_balance: "500" }],
    ["/shifts/sessions/close/", { actual_cash: "1250.50" }],
  ]);
  assert.equal(reloaded, 2);
});

test("register: an error answer is shown and the page stays", async () => {
  const ui = fakeUi();
  const ok = await submitSession(openButton, {
    api: fakeApi({ error: "A session is already open" }), ui, doc, reload: () => assert.fail("no reload"),
  });
  assert.equal(ok, false);
  assert.deepEqual(ui.calls.toast, [["A session is already open", "error"]]);
});

/* ---- presets (control panel) ----------------------------------------------- */

const presetButton = { dataset: { url: "/superuser/tenant/3/preset/", preset: "pub", label: "Pub", tenant: "Bob`s ${bar}" } };

test("preset: asks with the names as text, posts the preset, reloads", async () => {
  const asked = [];
  const ui = { ...fakeUi(), confirm: async (...a) => { asked.push(a); return { isConfirmed: true }; } };
  const calls = [];
  const fetchFn = async (url, options) => { calls.push([url, options]); return reply(200, { success: true, label: "Pub" }); };
  let reloaded = 0;
  assert.equal(await applyPreset(presetButton, { ui, fetchFn, csrf: () => "tok", reload: () => { reloaded += 1; } }), true);
  assert.equal(asked[0][1], '"Pub" overrides the feature flags for Bob`s ${bar}.');
  assert.equal(calls[0][0], "/superuser/tenant/3/preset/");
  assert.equal(calls[0][1].body, "preset=pub");
  assert.equal(calls[0][1].headers["X-CSRFToken"], "tok");
  assert.equal(reloaded, 1);
});

test("preset: no posts when the question is declined", async () => {
  const ui = { ...fakeUi(), confirm: async () => ({ isConfirmed: false }) };
  const ok = await applyPreset(presetButton, { ui, fetchFn: () => assert.fail("no request"), csrf: () => "", reload: () => {} });
  assert.equal(ok, false);
});

test("preset: a refusal and a dropped connection are shown", async () => {
  const ui = { ...fakeUi(), confirm: async () => ({ isConfirmed: true }) };
  await applyPreset(presetButton, { ui, fetchFn: async () => reply(400, { error: "Unknown preset" }), csrf: () => "", reload: () => assert.fail() });
  await applyPreset(presetButton, { ui, fetchFn: async () => { throw networkDown(); }, csrf: () => "", reload: () => assert.fail() });
  assert.deepEqual(ui.calls.toast, [["Unknown preset", "error"], ["Check the connection.", "error"]]);
});

/* ---- the printer's MAC address --------------------------------------------- */

test("MAC: hex only, upper case, a colon every two, 12 digits at most", () => {
  assert.equal(formatMac("0057271"), "00:57:27:1");
  assert.equal(formatMac("00-57-27-10-ed-46"), "00:57:27:10:ED:46");
  assert.equal(formatMac("00:57:27:10:ED:46:99"), "00:57:27:10:ED:46");
  assert.equal(formatMac("zz"), "");
  assert.equal(formatMac(""), "");
});

/* ---- the reports' tab row --------------------------------------------------- */

test("tabs: the current tab is scrolled to the middle of a narrow row", () => {
  const active = { offsetWidth: 100, getBoundingClientRect: () => ({ left: 700 }) };
  const strip = {
    scrollWidth: 1200, clientWidth: 400, scrollLeft: 0,
    querySelector: () => active,
    getBoundingClientRect: () => ({ left: 0 }),
  };
  assert.equal(centerActiveTab(strip), true);
  assert.equal(strip.scrollLeft, 700 - 150);
});

test("tabs: a row that fits, or no current tab, is left alone", () => {
  assert.equal(centerActiveTab({ scrollWidth: 300, clientWidth: 400, querySelector: () => ({}) }), false);
  assert.equal(centerActiveTab({ scrollWidth: 900, clientWidth: 400, querySelector: () => null }), false);
  assert.equal(centerActiveTab(null), false);
});

/* ---- the browser receipt ----------------------------------------------------- */

test("receipt: prints once loaded, after the pause, and closes after printing", () => {
  const listeners = {};
  const calls = [];
  const win = {
    addEventListener: (name, fn) => { listeners[name] = fn; },
    setTimeout: (fn, ms) => { calls.push(["wait", ms]); fn(); },
    print: () => calls.push(["print"]),
    close: () => calls.push(["close"]),
  };
  autoPrint(win, 400);
  assert.deepEqual(calls, []);
  listeners.load();
  assert.deepEqual(calls, [["wait", 400], ["print"]]);
  listeners.afterprint();
  assert.deepEqual(calls.at(-1), ["close"]);
});
