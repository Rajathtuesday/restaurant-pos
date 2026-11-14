/*
 * The shared parts written on 14 Nov 2026 (V1): post buttons and forms
 * (core/post_actions.js), the change actions (form.submit, range.pick),
 * the CSRF cookie fallback, and the modules of the pages built on them:
 * the inventory reports' row filter and tabs, the feature switches, the
 * waiter dashboard's live list, the shifts clock, New Restaurant.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { createRestaurant } from "../accounts/create_restaurant.js";
import { toggleFeature } from "../accounts/feature_toggle.js";
import { CSRF_COOKIE, csrfToken } from "../core/csrf.js";
import { pageChangeActions } from "../core/page_actions.js";
import { createPostActions, formBody } from "../core/post_actions.js";
import { countText, filterRows } from "../inventory/row_filter.js";
import { showTab, TABS } from "../reports/inventory_report.js";
import { startClock } from "../shifts/clock.js";
import { listChanged, watchList } from "../waiter/live.js";
import { fakeUi, networkDown, reply } from "./helpers.mjs";

/* An element with a dataset and the attribute calls post_actions makes. */
function el(dataset, extra = {}) {
  const attrs = {};
  return {
    tagName: "BUTTON", dataset, attrs,
    setAttribute: (k, v) => { attrs[k] = v; },
    removeAttribute: (k) => { delete attrs[k]; },
    ...extra,
  };
}

function fakeApi(answers = []) {
  const posts = [];
  return {
    posts,
    post: async (url, body) => {
      posts.push([url, body]);
      const next = answers.length ? answers.shift() : { success: true };
      if (next instanceof Error) throw next;
      return next;
    },
  };
}

function world({ api = fakeApi(), ui = fakeUi(), removed = [] } = {}) {
  const seen = { reloads: 0, waits: [] };
  const doc = { querySelector: (sel) => ({ remove: () => removed.push(sel) }) };
  const posts = createPostActions({
    api, ui, doc,
    reload: () => { seen.reloads += 1; },
    wait: (fn, ms) => { seen.waits.push(ms); fn(); },
  });
  return { posts, api, ui, seen, removed };
}

/* ---- post buttons ---------------------------------------------------------- */

test("a post button sends to its address with its body and reloads", async () => {
  const w = world();
  assert.equal(await w.posts.click(el({ url: "/r/5/status/", body: '{"status": "seated"}' })), true);
  assert.deepEqual(w.api.posts, [["/r/5/status/", { status: "seated" }]]);
  assert.equal(w.seen.reloads, 1);
  assert.deepEqual(w.seen.waits, [0]);
});

test("asked first: declining sends nothing (the old page deleted anyway)", async () => {
  const ui = { ...fakeUi(), confirm: async () => ({ isConfirmed: false, isDismissed: true }) };
  const w = world({ ui });
  assert.equal(await w.posts.click(el({ url: "/t/3/delete/", confirm: "Delete this template?" })), false);
  assert.deepEqual(w.api.posts, []);
});

test("done: a toast, then the row goes instead of a reload", async () => {
  const ui = { ...fakeUi(), confirm: async () => ({ isConfirmed: true }) };
  const w = world({ ui });
  await w.posts.click(el({ url: "/e/7/delete/", confirm: "Delete?", done: "Expense deleted.", remove: "#expense-row-7" }));
  assert.deepEqual(ui.calls.toast, [["Expense deleted.", "success"]]);
  assert.deepEqual(w.removed, ["#expense-row-7"]);
  assert.equal(w.seen.reloads, 0);
});

test("a toast before a reload waits long enough to be read; then=none stays", async () => {
  const w = world();
  await w.posts.click(el({ url: "/x/", done: "Saved." }));
  assert.deepEqual(w.seen.waits, [700]);
  const w2 = world();
  await w2.posts.click(el({ url: "/x/", done: "Tips saved.", then: "none" }));
  assert.equal(w2.seen.reloads, 0);
});

test("a 200 that carries an error is a refusal: shown, no reload", async () => {
  const w = world({ api: fakeApi([{ error: "A session is already open" }]) });
  assert.equal(await w.posts.click(el({ url: "/x/" })), false);
  assert.deepEqual(w.ui.calls.toast, [["A session is already open", "error"]]);
  assert.equal(w.seen.reloads, 0);
});

test("a refusal the client showed: no reload, and the button is free again", async () => {
  const w = world({ api: fakeApi([new Error("Permission denied")]) });
  const button = el({ url: "/x/" });
  assert.equal(await w.posts.click(button), false);
  assert.equal(w.seen.reloads, 0);
  assert.equal(button.attrs["aria-busy"], undefined);
});

test("a second tap while the first is out is ignored", async () => {
  let finish;
  const api = { posts: [], post: (url, body) => { api.posts.push([url, body]); return new Promise((r) => { finish = r; }); } };
  const w = world({ api });
  const button = el({ url: "/x/" });
  const first = w.posts.click(button);
  assert.equal(await w.posts.click(button), false);
  finish({ success: true });
  assert.equal(await first, true);
  assert.equal(api.posts.length, 1);
});

test("an optional reason: asked, sent even when empty; cancelled sends nothing", async () => {
  const asked = [];
  const answers = ["", null];
  const ui = { ...fakeUi(), ask: async (title, opts) => { asked.push([title, opts.required]); return answers.shift(); } };
  const w = world({ ui });
  const reject = el({ url: "/refund/reject/4/", ask: "Reject this refund?", askField: "reason", askOptional: "" });
  assert.equal(await w.posts.click(reject), true);
  assert.equal(await w.posts.click(reject), false);
  assert.deepEqual(w.api.posts, [["/refund/reject/4/", { reason: "" }]]);
  assert.deepEqual(asked[0], ["Reject this refund?", false]);
});

test("a form sends its named fields (a checkbox as true or false) and stops the page posting", async () => {
  const w = world();
  const elements = [
    { name: "amount", type: "number", value: "250" },
    { name: "is_recurring", type: "checkbox", checked: true },
    { name: "csrfmiddlewaretoken", type: "hidden", value: "x" },
    { name: "", type: "text", value: "ignored" },
    { name: "off", type: "text", value: "x", disabled: true },
    { name: "kind", type: "radio", value: "a", checked: false },
    { name: "kind", type: "radio", value: "b", checked: true },
    { name: "go", type: "submit", value: "Save" },
  ];
  const form = el({ url: "/expenses/create/" }, { tagName: "FORM", elements });
  let prevented = 0;
  await w.posts.submit(form, { preventDefault: () => { prevented += 1; } });
  assert.equal(prevented, 1);
  assert.deepEqual(w.api.posts, [["/expenses/create/", { amount: "250", is_recurring: true, kind: "b" }]]);
  assert.deepEqual(formBody({ elements: [] }), {});
});

test("a click that lands on a post form (its submit button) is the form's submit, not a second post", async () => {
  const w = world();
  assert.equal(await w.posts.click(el({ url: "/x/" }, { tagName: "FORM" })), false);
  assert.deepEqual(w.api.posts, []);
});

/* ---- change actions --------------------------------------------------------- */

test("form.submit sends the field's form; range.pick shows the dates for custom", () => {
  const actions = pageChangeActions();
  let sent = 0;
  const form = { submit: () => { sent += 1; } };
  actions["form.submit"]({ form });
  assert.equal(sent, 1);

  const shown = [];
  const range = { style: { setProperty: (...a) => shown.push(a) } };
  const select = (value) => ({ value, form, dataset: { range: "customRange" }, ownerDocument: { getElementById: () => range } });
  actions["range.pick"](select("custom"));
  assert.equal(sent, 1);
  actions["range.pick"](select("weekly"));
  assert.equal(sent, 2);
  assert.deepEqual(shown, [["display", "flex", "important"], ["display", "none", "important"]]);
});

/* ---- the CSRF cookie --------------------------------------------------------- */

test("without the meta tag the token comes from Rasova's own cookie", () => {
  const doc = (cookie) => ({ querySelector: () => null, cookie });
  assert.equal(CSRF_COOKIE, "csrftoken2");
  assert.equal(csrfToken(doc("a=1; csrftoken2=abc%3D; b=2")), "abc=");
  assert.equal(csrfToken(doc("csrftoken=wrong")), "");
  assert.equal(csrfToken({ querySelector: () => ({ getAttribute: () => "meta" }), cookie: "csrftoken2=x" }), "meta");
});

/* ---- the inventory reports ---------------------------------------------------- */

function row(name, category) {
  const classes = new Set();
  return { dataset: { name, category }, classes, classList: { toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)) } };
}

test("row filter: name and category together, and the count while filtering", () => {
  const rows = [row("paneer", "Dairy"), row("milk", "Dairy"), row("onion", "Veg")];
  assert.equal(filterRows(rows, { term: " PAN", category: "" }), 1);
  assert.equal(filterRows(rows, { term: "", category: "Dairy" }), 2);
  assert.ok(rows[2].classes.has("row-hidden"));
  assert.equal(filterRows(rows, {}), 3);
  assert.equal(countText(1, { term: "pan" }), "1 matching");
  assert.equal(countText(3, {}), "");
});

test("inventory report: one panel shown, its tab active, remembered in the form", () => {
  const panels = Object.fromEntries(TABS.map((t) => [t, { hidden: false }]));
  const current = { value: "" };
  const buttons = TABS.map((t) => {
    const classes = new Set();
    return { dataset: { tab: t }, classes, attrs: {}, classList: { toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)) },
      setAttribute(k, v) { this.attrs[k] = v; } };
  });
  const doc = {
    getElementById: (id) => (id === "currentTab" ? current : panels[id.replace("tab-", "")]),
    querySelectorAll: () => buttons,
  };
  assert.equal(showTab(doc, "wastage"), true);
  assert.deepEqual(TABS.filter((t) => !panels[t].hidden), ["wastage"]);
  assert.deepEqual(buttons.filter((b) => b.classes.has("active")).map((b) => b.dataset.tab), ["wastage"]);
  assert.equal(current.value, "wastage");
  assert.equal(showTab(doc, "nonsense"), false);
});

/* ---- the feature switches ----------------------------------------------------- */

function featureDoc() {
  const card = { classes: new Set(), classList: { add(c) { card.classes.add(c); }, remove(c) { card.classes.delete(c); }, toggle(c, on) { if (on) card.classes.add(c); else card.classes.delete(c); } } };
  const badge = { className: "src-badge default", textContent: "Default" };
  return { card, badge, getElementById: (id) => (id.startsWith("card-") ? card : badge) };
}

test("feature switch: saved, the card and badge follow, unlocked", async () => {
  const doc = featureDoc();
  const box = { checked: false, disabled: false, dataset: { url: "/flags/", tenant: "9", feature: "floor_plan" } };
  const api = fakeApi([{ source: "override" }]);
  assert.equal(await toggleFeature(box, { api, ui: fakeUi(), doc }), true);
  assert.deepEqual(api.posts, [["/flags/", { tenant_id: 9, feature: "floor_plan", enabled: false }]]);
  assert.ok(doc.card.classes.has("is-off"));
  assert.equal(doc.badge.textContent, "Override");
  assert.equal(box.disabled, false);
});

test("feature switch: a refusal puts the switch back", async () => {
  const doc = featureDoc();
  const box = { checked: true, disabled: false, dataset: { url: "/flags/", tenant: "9", feature: "kegs" } };
  assert.equal(await toggleFeature(box, { api: fakeApi([new Error("Permission denied")]), ui: fakeUi(), doc }), false);
  assert.equal(box.checked, false);
  assert.ok(!doc.card.classes.has("saving"));
});

/* ---- the waiter dashboard's live list ----------------------------------------- */

const parse = (html) => ({ querySelector: () => ({ innerHTML: html }) });

test("waiter list: reloads only when the list changed, never while hidden or on a failure", async () => {
  const doc = { hidden: false, querySelector: () => ({ innerHTML: "same" }) };
  assert.equal(listChanged(doc, "same", parse), false);
  assert.equal(listChanged(doc, "new call", parse), true);

  let reloads = 0;
  const answers = [reply(200, undefined), networkDown()];
  const fetchFn = async () => {
    const next = answers.shift();
    if (next instanceof Error) throw next;
    return { ok: true, text: async () => "new call" };
  };
  const check = watchList({ doc, fetchFn, url: "/waiter/", parse, reload: () => { reloads += 1; }, every: () => {} });
  assert.equal(await check(), true);
  assert.equal(await check(), false);           // the connection dropped
  doc.hidden = true;
  assert.equal(await check(), false);
  assert.equal(reloads, 1);
});

/* ---- the shifts clock ---------------------------------------------------------- */

test("shifts clock: drawn at once and every second", () => {
  const el = { textContent: "" };
  const timers = [];
  assert.equal(startClock(el, { now: () => new Date(2026, 10, 14, 9, 5, 7), every: (fn, ms) => timers.push(ms) }), true);
  assert.match(el.textContent, /9:05:07/);
  assert.deepEqual(timers, [1000]);
  assert.equal(startClock(null), false);
});

/* ---- New Restaurant -------------------------------------------------------------- */

test("new restaurant: sent as a form, then on to its page", async () => {
  const status = { textContent: "", dataset: {} };
  const button = { disabled: false };
  const form = { dataset: { url: "/superuser/create/", tenantUrl: "/superuser/tenant/0/" }, querySelector: () => button };
  globalThis.FormData = class { constructor() { this.entries = [["name", "Alpha"]]; } [Symbol.iterator]() { return this.entries[Symbol.iterator](); } };
  const went = [];
  const ok = await createRestaurant(form, {
    fetchFn: async (url, options) => { assert.equal(options.body, "name=Alpha"); return reply(200, { success: true, tenant_id: 41 }); },
    csrf: () => "tok", status, go: (url) => went.push(url), wait: (fn) => fn(),
  });
  assert.equal(ok, true);
  assert.deepEqual(went, ["/superuser/tenant/41/"]);
  assert.equal(status.dataset.tone, "ok");
});

test("new restaurant: a refusal is said in the status line and the button is free again", async () => {
  const status = { textContent: "", dataset: {} };
  const button = { disabled: false };
  const form = { dataset: { url: "/superuser/create/", tenantUrl: "/superuser/tenant/0/" }, querySelector: () => button };
  const ok = await createRestaurant(form, {
    fetchFn: async () => reply(400, { error: "Slug taken" }), csrf: () => "", status, go: () => assert.fail("no move"),
  });
  assert.equal(ok, false);
  assert.equal(status.textContent, "Slug taken");
  assert.equal(button.disabled, false);
});
