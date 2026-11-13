/*
 * An order request's id (static/js/core/request_id.js), and the three
 * places that use it: the Dispatch and Checkout buttons, the counter's
 * pay-and-print button and the offline order queue. What matters in each:
 * the same request sent again carries the same id, and a new request
 * never does.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { createCart } from "../billing/cart.js";
import { createCounter } from "../billing/counter.js";
import { createDispatch } from "../billing/dispatch.js";
import { createParcel } from "../billing/parcel.js";
import { createOrderQueue, memoryStore } from "../core/offline.js";
import { createRequestIds, newRequestId } from "../core/request_id.js";
import { fakeUi, networkDown, reply, scriptedFetch } from "./helpers.mjs";

// What orders.views.billing_views accepts (_REQUEST_ID), and a UUID's shape.
const SERVER_ACCEPTS = /^[A-Za-z0-9_-]{8,64}$/;
const UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

const URLS = {
  create_order: "/create-order/", send_to_kitchen: "/send-to-kitchen/0/", toggle_parcel: "/toggle-parcel/0/",
  generate_bill: "/generate-bill/0/", bill_view: "/bill/0/", pay_order: "/pay/0/",
};
const PANEER = { id: 1, name: "Paneer Tikka", price: 250, gst_percentage: 5 };
const TEA = { id: 2, name: "Tea", price: 20, gst_percentage: 5 };
const csrf = () => "tok";
const sentTo = (fetchFn, url) => fetchFn.calls.filter((c) => c.url === url).map((c) => JSON.parse(c.options.body));

/* ---- the id itself -------------------------------------------------------- */

test("an id is a UUID the server accepts, from whatever the browser offers", () => {
  const real = newRequestId();
  const fromRandomValues = newRequestId({ getRandomValues: (bytes) => bytes.fill(171) });
  const fromNothing = newRequestId(null);
  for (const id of [real, fromRandomValues, fromNothing]) {
    assert.match(id, UUID_V4);
    assert.match(id, SERVER_ACCEPTS);
  }
  assert.equal(newRequestId({ randomUUID: () => "the-browsers-own" }), "the-browsers-own");
  assert.equal(fromRandomValues, "abababab-abab-4bab-abab-abababababab");
});

test("two ids are never the same", () => {
  const seen = new Set(Array.from({ length: 500 }, () => newRequestId(null)));
  assert.equal(seen.size, 500);
});

test("the same payload again keeps its id; a changed one, or one after the answer, gets a new id", () => {
  let n = 0;
  const ids = createRequestIds({ newId: () => "id-" + (n += 1) });
  const first = ids.stamp({ cart: [{ id: 1, quantity: 1 }] });
  assert.deepEqual(first, { cart: [{ id: 1, quantity: 1 }], request_id: "id-1" });
  assert.equal(ids.stamp({ cart: [{ id: 1, quantity: 1 }] }).request_id, "id-1", "tapped again, nothing changed");
  assert.equal(ids.stamp({ cart: [{ id: 1, quantity: 2 }] }).request_id, "id-2", "the cart changed");
  ids.settle();
  assert.equal(ids.stamp({ cart: [{ id: 1, quantity: 2 }] }).request_id, "id-3", "the same dishes for the next customer");
});

test("stamping does not change the payload it was given", () => {
  const payload = { cart: [] };
  createRequestIds().stamp(payload);
  assert.deepEqual(payload, { cart: [] });
});

/* ---- Dispatch and Checkout ------------------------------------------------ */

function dispatchRig(script, { online = true } = {}) {
  const fetchFn = scriptedFetch(script);
  const cart = createCart();
  cart.add(PANEER);
  const queued = [];
  const state = { online };
  const dispatch = createDispatch({
    cart, parcel: createParcel({ rate: 10, api: {}, toggleUrl: URLS.toggle_parcel }), urls: URLS, csrf,
    ui: fakeUi(), screen: { flash() {}, sending() {}, closeMobileCart() {} },
    offlineQueue: { add: async (payload) => { queued.push(payload); } },
    win: { location: { href: "" } }, fetchFn, isOnline: () => state.online,
  });
  return { dispatch, cart, queued, fetchFn, state };
}
const ORDER = { tableId: 3, source: "dine_in", aggregatorId: "" };

test("Dispatch sends the order with an id", async () => {
  const rig = dispatchRig([reply(200, { success: true, order_id: 7 }), reply(200, { success: true })]);
  await rig.dispatch.dispatch(ORDER);
  const [sent] = sentTo(rig.fetchFn, "/create-order/");
  assert.match(sent.request_id, UUID_V4);
});

test("the connection dropping queues the order with the id that was sent", async () => {
  const rig = dispatchRig([networkDown()]);
  const result = await rig.dispatch.dispatch(ORDER);
  assert.deepEqual(result, { queued: true });
  const [sent] = sentTo(rig.fetchFn, "/create-order/");
  assert.equal(rig.queued.length, 1);
  assert.equal(rig.queued[0].request_id, sent.request_id, "the server may have taken it: the replay must say it is the same one");
});

test("an order taken offline is queued with an id of its own", async () => {
  const rig = dispatchRig([], { online: false });
  await rig.dispatch.dispatch(ORDER);
  assert.match(rig.queued[0].request_id, UUID_V4);
  assert.equal(rig.fetchFn.calls.length, 0);
});

test("the next order after one was queued has a different id", async () => {
  const rig = dispatchRig([networkDown(), networkDown()]);
  await rig.dispatch.dispatch(ORDER);
  rig.cart.add(PANEER);                      // the same dish, for the next guest
  await rig.dispatch.dispatch(ORDER);
  assert.equal(rig.queued.length, 2);
  assert.notEqual(rig.queued[0].request_id, rig.queued[1].request_id);
});

test("an answer that is not JSON keeps the id for the second tap", async () => {
  const rig = dispatchRig([reply(502, undefined), reply(200, { success: true, order_id: 7 }), reply(200, {})]);
  assert.deepEqual(await rig.dispatch.dispatch(ORDER), { error: "network" });
  await rig.dispatch.dispatch(ORDER);
  const [first, second] = sentTo(rig.fetchFn, "/create-order/");
  assert.equal(second.request_id, first.request_id, "a gateway error does not say the order was not taken");
});

test("after the server answers, yes or no, the next tap is a new request", async () => {
  const rig = dispatchRig([
    reply(400, { error: "'Paneer Tikka' is currently unavailable." }),
    reply(200, { success: true, order_id: 7 }), reply(200, {}),
    reply(200, { success: true, order_id: 8 }), reply(200, {}),
  ]);
  await rig.dispatch.dispatch(ORDER);        // refused: the cart stays
  await rig.dispatch.dispatch(ORDER);        // taken: the cart clears
  rig.cart.add(PANEER);
  await rig.dispatch.dispatch(ORDER);        // the same dish again, on purpose
  const ids = sentTo(rig.fetchFn, "/create-order/").map((p) => p.request_id);
  assert.equal(new Set(ids).size, 3);
});

test("Checkout at the token counter carries an id too", async () => {
  const rig = dispatchRig([reply(200, { success: true, order_id: 9 }), reply(200, { success: true, order_id: 9 })]);
  await rig.dispatch.checkout({ orderId: null, customerPhone: "" });
  assert.match(sentTo(rig.fetchFn, "/create-order/")[0].request_id, UUID_V4);
});

/* ---- the counter's pay-and-print button ----------------------------------- */

function counterRig(script) {
  const fetchFn = scriptedFetch(script);
  const cart = createCart();
  cart.add(PANEER);
  const flashes = [];
  const node = () => ({ textContent: "", value: "", style: {}, disabled: false, innerHTML: "", classList: { remove() {}, toggle() {} } });
  const nodes = {};
  const doc = { getElementById: (id) => (nodes[id] = nodes[id] || node()), querySelectorAll: () => [] };
  const counter = createCounter({
    cart, parcel: createParcel({ rate: 0, api: {}, toggleUrl: URLS.toggle_parcel }), urls: URLS, csrf, ui: fakeUi(),
    screen: { flash: (m) => flashes.push(m), sending() {} }, printer: {}, offlinePaymentQueue: { add: async () => {} },
    doc, win: { bootstrap: { Modal: { getOrCreateInstance: () => ({ show() {}, hide() {} }) } } },
    fetchFn, isOnline: () => true,
  });
  return { counter, cart, fetchFn, flashes };
}

test("the counter: tapping again after a network error sends the same id", async () => {
  const rig = counterRig([networkDown(), reply(200, { success: true, order_id: 5 }),
    reply(200, { success: true, grand_total: "262.50" })]);
  await rig.counter.start({ source: "takeaway" });
  assert.deepEqual(rig.flashes, ["Network error. Try again."]);
  await rig.counter.start({ source: "takeaway" });
  const [first, second] = sentTo(rig.fetchFn, "/create-order/");
  assert.match(first.request_id, UUID_V4);
  assert.equal(second.request_id, first.request_id);
});

test("the counter: a cart changed between the two taps is a new request", async () => {
  const rig = counterRig([networkDown(), reply(200, { success: true, order_id: 5 }),
    reply(200, { success: true, grand_total: "283.50" })]);
  await rig.counter.start({ source: "takeaway" });
  rig.cart.add(TEA);
  await rig.counter.start({ source: "takeaway" });
  const [first, second] = sentTo(rig.fetchFn, "/create-order/");
  assert.notEqual(second.request_id, first.request_id);
});

/* ---- the offline queue ---------------------------------------------------- */

function queueRig(script) {
  const fetchFn = scriptedFetch(script);
  const ui = fakeUi();
  let n = 0;
  const store = memoryStore();
  const make = () => createOrderQueue({ store, fetchFn, csrf, ui, isOnline: () => true, newId: () => "queue-" + (n += 1) });
  return { queue: make(), anotherTab: make(), store, fetchFn, ui };
}

test("a queued order keeps the id it came with, and gets one if it had none", async () => {
  const { queue } = queueRig([]);
  await queue.add({ cart: [1], request_id: "from-the-screen" });
  await queue.add({ cart: [2] });
  const [first, second] = await queue.all();
  assert.equal(first.payload.request_id, "from-the-screen");
  assert.equal(second.payload.request_id, "queue-1");
});

test("an order whose answer was lost is sent again with the same id, and then leaves the queue", async () => {
  const { queue, fetchFn, ui } = queueRig([
    reply(200, undefined),                                         // the answer never arrived whole
    reply(200, { success: true, order_id: 41, repeat: true }),     // the server: I already have that one
    reply(200, { success: true }),
  ]);
  await queue.add({ cart: [1] });
  await queue.flush();
  assert.equal((await queue.all()).length, 1, "not confirmed, so it stays");
  await queue.flush();
  const sent = sentTo(fetchFn, "/create-order/");
  assert.equal(sent.length, 2);
  assert.equal(sent[1].request_id, sent[0].request_id);
  assert.equal((await queue.all()).length, 0);
  assert.deepEqual(ui.calls.toast, [["Order #41 synced and sent to kitchen", "success"]]);
});

test("the connection dropping during the replay sends the same id the next time", async () => {
  const { queue, fetchFn } = queueRig([networkDown(), reply(200, { success: true, order_id: 41 }), reply(200, {})]);
  await queue.add({ cart: [1] });
  await queue.flush();
  await queue.flush();
  const sent = sentTo(fetchFn, "/create-order/");
  assert.equal(sent[1].request_id, sent[0].request_id);
});

test("two tabs replaying the same saved order send the same id", async () => {
  const { queue, anotherTab, fetchFn } = queueRig([
    reply(200, { success: true, order_id: 41 }), reply(200, { success: true, order_id: 41, repeat: true }),
    reply(200, {}), reply(200, {}),
  ]);
  await queue.add({ cart: [1] });
  await Promise.all([queue.flush(), anotherTab.flush()]);
  const ids = sentTo(fetchFn, "/create-order/").map((p) => p.request_id);
  assert.equal(ids.length, 2, "each tab has its own in-flight guard, so both send");
  assert.equal(ids[0], ids[1], "and the server can see they are one request");
});
