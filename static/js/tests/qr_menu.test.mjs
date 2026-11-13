/*
 * The guest QR menu's modules (static/js/qr_menu/), with fakes: the cart,
 * picking options, placing the order (a second round, a lost answer, a
 * refusal), the order's status, and escaping of what staff typed.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { createGuestCart } from "../qr_menu/cart.js";
import { createModifierPick } from "../qr_menu/modifiers.js";
import { createOrderSender, createOrderStore } from "../qr_menu/order.js";
import { STAGE_INDEX, createStatusPoller, describeStatus, finished } from "../qr_menu/status.js";
import { cartHtml, modifierGroupsHtml, statusItemsHtml } from "../qr_menu/view.js";
import { networkDown, reply } from "./helpers.mjs";

const NAAN = { id: "7", name: "Butter Naan", price: 50, prepTime: 10, gstRate: 5 };
const TIKKA = { id: "9", name: "Paneer Tikka", price: 250, prepTime: 20, gstRate: 5 };

function memory() {
  const kept = {};
  return { kept, getItem: (k) => (k in kept ? kept[k] : null), setItem: (k, v) => { kept[k] = String(v); }, removeItem: (k) => { delete kept[k]; } };
}

/* ---- the cart ------------------------------------------------------------- */

test("the cart counts, totals with options, and gives the order as the server reads it", () => {
  const cart = createGuestCart();
  cart.add(NAAN);
  cart.add(NAAN);
  cart.add(TIKKA, [{ id: 3, name: "Extra Hot", price: 10 }]);
  assert.equal(cart.count(), 3);
  assert.equal(cart.total(), 50 * 2 + 260);
  assert.deepEqual(cart.payload(), [
    { id: 7, quantity: 2, price: 50, modifiers: [] },
    { id: 9, quantity: 1, price: 250, modifiers: [3] },
  ]);
  assert.deepEqual(cart.eta(), { low: 17, high: 25 }, "the slowest dish decides");
  assert.equal(cart.dec("7"), 1);
  assert.equal(cart.dec("7"), 0);
  assert.equal(cart.has("7"), false);
  cart.clear();
  assert.equal(cart.count(), 0);
  assert.equal(cart.eta(), null);
});

/* ---- options -------------------------------------------------------------- */

const SPICE = { id: 1, name: "Spice", is_required: true, max_select: 1,
  modifiers: [{ id: 11, name: "Mild", price: 0 }, { id: 12, name: "Hot", price: 0 }] };
const EXTRAS = { id: 2, name: "Extras", is_required: false, max_select: 2,
  modifiers: [{ id: 21, name: "Cheese", price: 30 }, { id: 22, name: "Butter", price: 20 }, { id: 23, name: "Mint", price: 5 }] };

test("a required group must be picked; one more than allowed drops the oldest", () => {
  const pick = createModifierPick([SPICE, EXTRAS], 250);
  assert.equal(pick.missing(), "Spice");
  pick.toggle(1, 11);
  pick.toggle(1, 12);                       // max 1: Mild is dropped
  assert.equal(pick.isPicked(11), false);
  assert.equal(pick.isPicked(12), true);
  assert.equal(pick.missing(), null);
  pick.toggle(2, 21);
  pick.toggle(2, 22);
  pick.toggle(2, 23);                       // max 2: Cheese is dropped
  assert.deepEqual(pick.modifiers().map((m) => m.name), ["Hot", "Butter", "Mint"]);
  assert.equal(pick.total(), 250 + 20 + 5);
  pick.toggle(2, 23);                       // tapped again: un-picked
  assert.equal(pick.isPicked(23), false);
});

/* ---- placing the order ---------------------------------------------------- */

function senderWith(answers, { kept = {} } = {}) {
  const storage = memory();
  Object.assign(storage.kept, kept);
  const store = createOrderStore(storage, "tok-1");
  const sent = [];
  const fetchFn = async (url, options) => {
    sent.push(JSON.parse(options.body));
    const next = answers.shift();
    if (next instanceof Error) throw next;
    return next;
  };
  return { sender: createOrderSender({ fetchFn, url: "/create-order/", csrf: "c", store }), sent, storage };
}
const ORDER = { cart: [{ id: 7, quantity: 1, price: 50, modifiers: [] }], tableId: 3, tableToken: "tok-1", notes: "" };

test("an order keeps its id and status token on this phone, per table", async () => {
  const { sender, sent, storage } = senderWith([reply(200, { success: true, order_id: 41, status_token: "signed" })]);
  const result = await sender.place(ORDER);
  assert.equal(result.kind, "placed");
  assert.equal(sent[0].source, "web");
  assert.equal(sent[0].table_token, "tok-1");
  assert.match(sent[0].request_id, /^[0-9a-f-]{36}$/);
  assert.equal(storage.kept["rasova_order_tok-1"], "41");
  assert.equal(storage.kept["rasova_order_status_tok-1"], "signed");
});

test("a second round joins the table's order", async () => {
  const { sender, sent } = senderWith([reply(200, { success: true, order_id: 41, status_token: "s" })],
    { kept: { "rasova_order_tok-1": "41" } });
  await sender.place(ORDER);
  assert.equal(sent[0].order_id, 41);
});

test("no answer: tapping again sends the same request, so nothing is ordered twice", async () => {
  const { sender, sent } = senderWith([networkDown(), reply(200, { success: true, order_id: 41, status_token: "s" })]);
  assert.equal((await sender.place(ORDER)).kind, "network");
  assert.equal((await sender.place(ORDER)).kind, "placed");
  assert.equal(sent[1].request_id, sent[0].request_id);
});

test("another table's kept order is forgotten; a billed table is told to ask a waiter; a refusal is said", async () => {
  const a = senderWith([reply(403, { error: "That order does not belong to this table." })], { kept: { "rasova_order_tok-1": "9" } });
  assert.equal((await a.sender.place(ORDER)).kind, "not-yours");
  assert.equal(a.storage.kept["rasova_order_tok-1"], undefined);
  const b = senderWith([reply(409, { error: "This order has already been billed. Please call a waiter for more items." })]);
  assert.equal((await b.sender.place(ORDER)).kind, "billed");
  const c = senderWith([reply(400, { error: "'Lassi' is currently unavailable." })]);
  assert.deepEqual(await c.sender.place(ORDER), { kind: "refused", message: "'Lassi' is currently unavailable." });
});

test("a second tap while the first is on its way is ignored", async () => {
  let release;
  const storage = memory();
  const sender = createOrderSender({
    fetchFn: () => new Promise((r) => { release = () => r(reply(200, { success: true, order_id: 1, status_token: "s" })); }),
    url: "/create-order/", csrf: "c", store: createOrderStore(storage, "tok-1"),
  });
  const first = sender.place(ORDER);
  assert.deepEqual(await sender.place(ORDER), { kind: "busy" });
  release();
  assert.equal((await first).kind, "placed");
});

test("a phone that blocks storage still orders", async () => {
  const blocked = { getItem() { throw new Error("no"); }, setItem() { throw new Error("no"); }, removeItem() { throw new Error("no"); } };
  const store = createOrderStore(blocked, "tok-1");
  const sender = createOrderSender({ fetchFn: async () => reply(200, { success: true, order_id: 1, status_token: "s" }), url: "/x/", csrf: "c", store });
  assert.equal((await sender.place(ORDER)).kind, "placed");
});

/* ---- the order's status --------------------------------------------------- */

test("every stage the server sends has a place on the timeline", () => {
  for (const stage of ["placed", "confirmed", "waiting_confirmation", "preparing", "ready", "served"]) {
    assert.ok(stage in STAGE_INDEX, stage);
  }
  assert.deepEqual(describeStatus({ stage: "preparing", label: "Cooking", table: "T1", items: [] }),
    { pill: "Preparing", sheet: "Cooking", stage: 1, done: false, token: null, table: "T1", items: [] });
  assert.equal(describeStatus({ stage: "placed", token_ready: true, token_display: "A7" }).pill, "Ready for pickup!");
});

test("when a status is finished", () => {
  assert.ok(finished({ order_status: "cancelled" }));
  assert.ok(finished({ can_add_more: false }), "a table order that takes no more");
  assert.ok(!finished({ can_add_more: false, token_display: "A7" }), "a token waits for pickup");
  assert.ok(finished({ can_add_more: false, token_display: "A7", token_collected: true }));
});

test("the poller draws the newest status, forgets a gone order, and stops when finished", async () => {
  const storage = memory();
  storage.kept["rasova_order_status_tok-1"] = "signed";
  storage.kept["rasova_order_tok-1"] = "41";
  const store = createOrderStore(storage, "tok-1");
  const drawn = [];
  let gone = 0;
  const answers = [reply(200, { stage: "preparing", can_add_more: true, items: [{ name: "Naan", quantity: 1, status: "Cooking" }] }),
    reply(200, { stage: "served", can_add_more: false })];
  const asked = [];
  const poller = createStatusPoller({
    fetchFn: async (url) => { asked.push(url); return answers.shift(); }, url: "/menu/order-status/TOKEN/", store,
    onStatus: (s) => drawn.push(s.pill), onGone: () => { gone += 1; },
    timers: { setInterval: () => 1, clearInterval: () => {} },
  });
  assert.equal(poller.start(), true);
  await new Promise((r) => setTimeout(r, 0));
  await poller.poll();
  assert.deepEqual(asked, ["/menu/order-status/signed/", "/menu/order-status/signed/"]);
  assert.deepEqual(drawn, ["Preparing", "Served"]);
  assert.equal(gone, 1);
  assert.equal(storage.kept["rasova_order_status_tok-1"], undefined);
  assert.equal(storage.kept["rasova_order_tok-1"], undefined);
});

test("an order that is gone (the link expired) hides the status and forgets it", async () => {
  const storage = memory();
  storage.kept["rasova_order_status_tok-1"] = "signed";
  let gone = 0;
  const poller = createStatusPoller({
    fetchFn: async () => reply(404, {}), url: "/s/TOKEN/", store: createOrderStore(storage, "tok-1"),
    onStatus: () => {}, onGone: () => { gone += 1; }, timers: { setInterval: () => 1, clearInterval: () => {} },
  });
  poller.start();
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(gone, 1);
  assert.equal(storage.kept["rasova_order_status_tok-1"], undefined);
});

/* ---- what the scripts draw ------------------------------------------------ */

test("names staff typed are escaped everywhere a guest sees them", () => {
  const evil = '<img src=x onerror="alert(1)">';
  const groups = modifierGroupsHtml([{ id: 1, name: evil, is_required: true, max_select: 1, modifiers: [{ id: 2, name: evil, price: 10 }] }]);
  const cart = cartHtml({ lines: [{ id: "7", name: evil, qty: 1, price: 50, modifiers: [{ name: evil }] }], lineTotal: () => 50,
    already: [{ quantity: 1, name: evil, status: evil }] });
  const status = statusItemsHtml([{ quantity: 1, name: evil, status: "Cooking" }]);
  for (const html of [groups, cart, status]) {
    assert.doesNotMatch(html, /<img/);
    assert.match(html, /&lt;img/);
  }
});

test("the cart shows what is already on its way above what is being added", () => {
  const html = cartHtml({ lines: [{ id: "7", name: "Naan", qty: 2, price: 50, modifiers: [] }], lineTotal: (l) => l.qty * l.price,
    already: [{ quantity: 1, name: "Tikka", status: "Cooking" }] });
  assert.ok(html.indexOf("Already ordered") < html.indexOf("Adding now"));
  assert.ok(html.indexOf("Adding now") < html.indexOf("Naan"));
  assert.match(html, /₹100/);
  assert.doesNotMatch(cartHtml({ lines: [{ id: "7", name: "Naan", qty: 1, price: 50, modifiers: [] }], lineTotal: () => 50 }), /Already ordered/);
  assert.match(statusItemsHtml([]), /No items yet/);
});
