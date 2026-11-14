/*
 * The running order page's modules (static/js/running_order/), with fakes
 * for the server: what a dish shows and who gets which button, what each
 * tap sends, and what happens when the server refuses, the connection is
 * down, an answer arrives late, or a button is tapped twice.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { createRunningOrderActions } from "../running_order/actions.js";
import { createOrderLoader } from "../running_order/loader.js";
import { actionsHtml, badgeClass, itemHtml, listHtml, sortItems } from "../running_order/view.js";
import { networkDown, reply, scriptedFetch } from "./helpers.mjs";

const URLS = {
  data: "/order-data/7/",
  serve: "/serve-item/0/",
  bill: "/bill/7/",
  tables: "/tables/",
};
const ALL = { edit: true, serve: true, comp: true, edit_served: true, cancel_order: true };
const NONE = { edit: false, serve: false, comp: false, edit_served: false, cancel_order: false };
const dish = (id, status, extra = {}) => ({
  id, name: "Dish " + id, quantity: 1, status, void_reason: "", is_complimentary: false, modifiers: [], ...extra,
});
const actionsOf = (html) => [...html.matchAll(/data-action="([^"]+)"/g)].map((m) => m[1]);

/* ---- what a dish shows ---------------------------------------------------- */

test("each status has its badge, an unknown one the plain badge", () => {
  assert.deepEqual(["pending", "sent", "preparing", "ready", "served", "voided", "review"].map(badgeClass), [
    "badge-pending", "badge-ordering", "badge-preparing", "badge-ready", "badge-served", "badge-voided", "badge-pending",
  ]);
});

test("cancelled dishes go underneath, the rest keep their order", () => {
  const sorted = sortItems([dish(1, "voided"), dish(2, "sent"), dish(3, "voided"), dish(4, "served")]);
  assert.deepEqual(sorted.map((d) => d.id), [2, 4, 1, 3]);
});

test("a ready dish can be served, removed, cancelled and made free by a manager", () => {
  assert.deepEqual(actionsOf(actionsHtml(dish(1, "ready"), ALL)), ["item.serve", "item.reduce", "item.remove", "item.comp"]);
});

test("someone who may do nothing gets no buttons (a waiter on this page)", () => {
  assert.deepEqual(actionsOf(actionsHtml(dish(1, "ready"), NONE)), []);
});

test("a captain may make a dish free: the button follows the server, not a role list", () => {
  const captain = { ...ALL, edit_served: false };
  assert.ok(actionsOf(actionsHtml(dish(1, "sent"), captain)).includes("item.comp"));
});

test("a served dish is locked for anyone below manager, with the lock shown", () => {
  const cashier = { ...ALL, comp: false, edit_served: false };
  const html = actionsHtml(dish(1, "served"), cashier);
  assert.deepEqual(actionsOf(html), []);
  assert.match(html, /bi-lock/);
  assert.deepEqual(actionsOf(actionsHtml(dish(1, "served"), ALL)), ["item.reduce", "item.remove", "item.comp"]);
});

test("a dish already free has no Comp button and says Free", () => {
  const html = itemHtml(dish(1, "sent", { is_complimentary: true }), ALL);
  assert.ok(!actionsOf(html).includes("item.comp"));
  assert.match(html, /badge-comp">Free</);
});

test("a cancelled dish has no buttons, is struck through, and shows why", () => {
  const html = itemHtml(dish(1, "voided", { void_reason: "Wrong order" }), ALL);
  assert.deepEqual(actionsOf(html), []);
  assert.match(html, /is-voided/);
  assert.match(html, /Cancelled: Wrong order/);
  assert.match(html, />cancelled</);
});

test("what people typed is escaped: dish, add-on and reason", () => {
  const html = itemHtml(dish('<img src=x onerror="1">', "voided", {
    name: "<b>Naan</b>", modifiers: ["<i>hot</i>"], void_reason: "<script>x</script>",
  }), ALL);
  assert.ok(!html.includes("<b>Naan"));
  assert.ok(!html.includes("<i>hot"));
  assert.ok(!html.includes("<script>"));
  assert.ok(!html.includes('<img src=x'));
  assert.match(html, /&lt;b&gt;Naan&lt;\/b&gt;/);
});

test("an order with no dishes shows the shared empty state, not a blank", () => {
  const html = listHtml([], ALL);
  assert.match(html, /data-empty-state/);
  assert.match(html, /No dishes on this order yet/);
});

/* ---- asking for the order ------------------------------------------------- */

function loaderWith(script) {
  const seen = { orders: [], failures: [] };
  const fetchFn = scriptedFetch(script);
  const loader = createOrderLoader({
    fetchFn, url: URLS.data,
    onOrder: (o) => seen.orders.push(o),
    onFailure: (m) => seen.failures.push(m),
  });
  return { loader, seen, fetchFn };
}

test("an answer is handed to the page", async () => {
  const body = { subtotal: 100, gst: 5, total: 105, items: [dish(1, "sent")] };
  const { loader, seen, fetchFn } = loaderWith([reply(200, body)]);
  assert.equal(await loader.refresh(), true);
  assert.deepEqual(seen.orders, [body]);
  assert.equal(fetchFn.calls[0].url, URLS.data);
});

test("a refusal shows the server's words, never an empty order", async () => {
  const { loader, seen } = loaderWith([reply(404, { error: "Order not found" })]);
  assert.equal(await loader.refresh(), false);
  assert.deepEqual(seen.orders, []);
  assert.deepEqual(seen.failures, ["Order not found"]);
});

test("the connection down says so", async () => {
  const { loader, seen } = loaderWith([networkDown()]);
  await loader.refresh();
  assert.deepEqual(seen.failures, ["Check the connection."]);
});

test("a login page instead of JSON asks to sign in again", async () => {
  const { loader, seen } = loaderWith([reply(200, undefined)]);
  await loader.refresh();
  assert.deepEqual(seen.failures, ["Please sign in again."]);
});

test("an answer with no list of dishes is a failure, not an empty order", async () => {
  const { loader, seen } = loaderWith([reply(200, { subtotal: 0 })]);
  await loader.refresh();
  assert.deepEqual(seen.orders, []);
  assert.equal(seen.failures.length, 1);
});

test("a late answer to an older question is dropped", async () => {
  let releaseFirst;
  const first = new Promise((r) => { releaseFirst = r; });
  const older = { subtotal: 1, gst: 0, total: 1, items: [dish(1, "sent")] };
  const newer = { subtotal: 2, gst: 0, total: 2, items: [dish(1, "served")] };
  const answers = [first, Promise.resolve(reply(200, newer))];
  const seen = [];
  const loader = createOrderLoader({
    fetchFn: () => answers.shift(), url: URLS.data,
    onOrder: (o) => seen.push(o), onFailure: () => assert.fail("no failure expected"),
  });
  const a = loader.refresh();
  const b = loader.refresh();
  await b;
  releaseFirst(reply(200, older));
  assert.equal(await a, false);
  assert.deepEqual(seen, [newer]);
});

/* ---- what a tap does ------------------------------------------------------- */

function fakeEdit(answers = {}) {
  const calls = [];
  const answer = (name, value) => {
    calls.push([name, value]);
    const a = answers[name];
    return typeof a === "function" ? a() : Promise.resolve(a === undefined ? { success: true } : a);
  };
  return {
    calls,
    reduce: (item) => answer("reduce", item.id),
    remove: (item) => answer("remove", item.id),
    complimentary: (item) => answer("complimentary", item.id),
    cancelOrder: (id, opts) => answer("cancelOrder", [id, opts]),
    post: (url, body) => answer("post", [url, body]),
  };
}

function actionsWith(edit) {
  const seen = { refreshes: 0, went: [], toasts: [] };
  const actions = createRunningOrderActions({
    edit, urls: URLS, orderId: 7,
    toast: (m, i) => seen.toasts.push([m, i]),
    refresh: () => { seen.refreshes += 1; },
    go: (url) => seen.went.push(url),
  });
  return { actions, seen };
}

test("serve posts to the dish's address and asks for the list again", async () => {
  const edit = fakeEdit();
  const { actions, seen } = actionsWith(edit);
  await actions.serve(12);
  assert.deepEqual(edit.calls, [["post", ["/serve-item/12/", {}]]]);
  assert.equal(seen.refreshes, 1);
});

test("a refused serve shows the server's words and still refreshes", async () => {
  const edit = fakeEdit({ post: () => Promise.reject(new Error("Feature 'kitchen_display' is not available")) });
  const { actions, seen } = actionsWith(edit);
  assert.equal(await actions.serve(12), null);
  assert.deepEqual(seen.toasts, [["Feature 'kitchen_display' is not available", "error"]]);
  assert.equal(seen.refreshes, 1);
});

test("comp opens the free-dish sheet for that dish (no browser prompt)", async () => {
  const edit = fakeEdit();
  const { actions, seen } = actionsWith(edit);
  await actions.comp(dish(5, "served"));
  assert.deepEqual(edit.calls, [["complimentary", 5]]);
  assert.equal(seen.refreshes, 1);
});

test("a second tap while the first is out is ignored", async () => {
  let finish;
  const edit = fakeEdit({ remove: () => new Promise((r) => { finish = r; }) });
  const { actions, seen } = actionsWith(edit);
  const first = actions.remove(dish(3, "sent"));
  assert.equal(actions.busy(), true);
  assert.equal(await actions.remove(dish(3, "sent")), null);
  assert.equal(await actions.serve(3), null);
  finish({ success: true });
  await first;
  assert.deepEqual(edit.calls, [["remove", 3]]);
  assert.equal(actions.busy(), false);
  assert.equal(seen.refreshes, 1);
});

test("a cancelled order goes to the floor plan, without one more refresh", async () => {
  const edit = fakeEdit();
  const { actions, seen } = actionsWith(edit);
  await actions.cancelOrder();
  assert.deepEqual(edit.calls, [["cancelOrder", [7, { billUrl: "/bill/7/" }]]]);
  assert.deepEqual(seen.went, ["/tables/"]);
  assert.equal(seen.refreshes, 0);
});

test("backing out of cancelling the order stays and refreshes", async () => {
  const edit = fakeEdit({ cancelOrder: null });
  const { actions, seen } = actionsWith(edit);
  await actions.cancelOrder();
  assert.deepEqual(seen.went, []);
  assert.equal(seen.refreshes, 1);
});
