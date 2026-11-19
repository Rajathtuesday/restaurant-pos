/*
 * The billing screen's modules (static/js/billing/), each with fakes for
 * the server, the screen and the queues: what is sent, in what order, and
 * what happens to the cart when the server, the kitchen or the connection
 * lets the cashier down.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { createCart } from "../billing/cart.js";
import { createCounter } from "../billing/counter.js";
import { createDispatch } from "../billing/dispatch.js";
import { createParcel } from "../billing/parcel.js";
import { createPrinter } from "../billing/printing.js";
import {
  ERROR_HTML, SELECT_TABLE_HTML, createRunningOrder, renderTableOrder, renderTokenOrder,
} from "../billing/running_order.js";
import { withId } from "../billing/urls.js";
import { fakeUi, networkDown, reply, scriptedFetch } from "./helpers.mjs";

// As orders.views.billing_core.billing_page_data reverses them.
const URLS = {
  create_order: "/create-order/",
  running_order_items: "/running-order-items/",
  print_queue_add: "/orders/agent/add-job/",
  send_to_kitchen: "/send-to-kitchen/0/",
  toggle_parcel: "/toggle-parcel/0/",
  generate_bill: "/generate-bill/0/",
  bill_view: "/bill/0/",
  cancel_order: "/cancel-order/0/",
  pay_order: "/pay/0/",
  qz_receipt_data: "/qz-data/0/",
  thermal_receipt: "/thermal-receipt/0/",
};
const PANEER = { id: 1, name: "Paneer Tikka", price: 250, gst_percentage: 5 };
const csrf = () => "tok";

function fakeScreen() {
  const seen = { flashes: [], sending: [], closed: 0 };
  return {
    seen,
    flash: (m) => seen.flashes.push(m),
    sending: (on) => seen.sending.push(on),
    closeMobileCart: () => { seen.closed += 1; },
  };
}

function fakeQueue({ fails = false } = {}) {
  const added = [];
  return {
    added,
    add: async (...args) => {
      if (fails) throw new Error("no storage");
      added.push(args);
    },
  };
}

/* A dispatch with a cart of one paneer, and everything it talks to. */
function dispatchRig(script, { online = true, parcelOn = false, queue = fakeQueue() } = {}) {
  const fetchFn = scriptedFetch(script);
  const cart = createCart();
  cart.add(PANEER);
  const ui = fakeUi();
  const screen = fakeScreen();
  const parcel = createParcel({ rate: 10, api: {}, toggleUrl: URLS.toggle_parcel });
  if (parcelOn) parcel.toggle(null);
  const win = { location: { href: "" } };
  const dispatch = createDispatch({
    cart, parcel, urls: URLS, csrf, ui, screen, offlineQueue: queue, win, fetchFn, isOnline: () => online,
  });
  return { dispatch, cart, ui, screen, parcel, queue, fetchFn, win, urlsCalled: () => fetchFn.calls.map((c) => c.url) };
}

/* ---- urls ---------------------------------------------------------------- */

test("withId puts the id where the server left a 0", () => {
  assert.equal(withId("/pay/0/", 42), "/pay/42/");
  assert.equal(withId("/menu/item-modifiers/0/", 7), "/menu/item-modifiers/7/");
});

/* ---- cart ---------------------------------------------------------------- */

test("the cart tells the screen after every change, and only then", () => {
  let changes = 0;
  const cart = createCart({ onChange: () => { changes += 1; } });
  cart.add(PANEER);
  cart.add(PANEER);
  cart.inc(0);
  cart.dec(0);
  cart.inc(5);          // no such line
  cart.dec(5);
  cart.clear();
  assert.equal(changes, 5);
});

/* ---- parcel -------------------------------------------------------------- */

const TWO_PANEER_ONE_TEA = [{ id: 1, quantity: 2, parcel_charge: 0 }, { id: 2, quantity: 1, parcel_charge: 0 }];

test("a parcel wish shows what the server will charge: per item, flat, or the dishes' own", () => {
  const perItem = createParcel({ rate: 10, perItem: true, api: {}, toggleUrl: URLS.toggle_parcel });
  assert.equal(perItem.chargeFor(TWO_PANEER_ONE_TEA), 0, "off");
  perItem.toggle(null);
  assert.equal(perItem.chargeFor(TWO_PANEER_ONE_TEA), 30, "10 on each of 3 items");

  const flat = createParcel({ rate: 10, perItem: false, api: {}, toggleUrl: URLS.toggle_parcel });
  flat.toggle(null);
  assert.equal(flat.chargeFor(TWO_PANEER_ONE_TEA), 10, "once per order");

  // A dish with its own charge: only such dishes are charged, whatever the outlet's mode.
  const own = [{ id: 1, quantity: 2, parcel_charge: 7 }, { id: 2, quantity: 1, parcel_charge: 0 }];
  assert.equal(perItem.chargeFor(own), 14);
  assert.equal(flat.chargeFor(own), 14);
  assert.equal(flat.rate, 10, "the button still offers the outlet's rate");
});

test("a parcel wish before the order exists is applied to the new order once", async () => {
  const fetchFn = scriptedFetch([reply(200, { parcel_on: true, parcel_amount: 30 })]);
  let changes = 0;
  const parcel = createParcel({ rate: 10, perItem: true, api: {}, toggleUrl: URLS.toggle_parcel, onChange: () => { changes += 1; } });
  await parcel.toggle(null);
  await parcel.applyTo(7, { fetchFn, csrf });
  await parcel.applyTo(7, { fetchFn, csrf });       // already on that order
  assert.deepEqual(fetchFn.calls.map((c) => c.url), ["/toggle-parcel/7/"]);
  assert.equal(fetchFn.calls[0].options.headers["X-CSRFToken"], "tok");
  assert.equal(parcel.chargeFor([]), 30, "from then on the server's amount, not the cart's guess");
  assert.equal(changes, 2);
});

test("once the order exists the server owns the parcel charge and its amount", async () => {
  const posts = [];
  const answers = [{ parcel_on: true, parcel_amount: 15 }, { parcel_on: false, parcel_amount: 0 }];
  const parcel = createParcel({
    rate: 5, perItem: true, toggleUrl: URLS.toggle_parcel,
    api: { post: async (url) => { posts.push(url); return answers.shift(); } },
  });
  await parcel.toggle(3);
  assert.equal(parcel.on, true);
  assert.equal(parcel.chargeFor(TWO_PANEER_ONE_TEA), 15, "what the server worked out for the order");
  await parcel.toggle(3);
  assert.equal(parcel.chargeFor(TWO_PANEER_ONE_TEA), 0);
  assert.equal(parcel.rate, 5, "the button offers the outlet's rate again, not 0");
  assert.deepEqual(posts, ["/toggle-parcel/3/", "/toggle-parcel/3/"]);
});

test("a server that refuses the toggle changes nothing", async () => {
  const parcel = createParcel({
    rate: 5, toggleUrl: URLS.toggle_parcel, api: { post: async () => { throw new Error("needs a manager"); } },
  });
  assert.equal(await parcel.toggle(3), false);
  assert.equal(parcel.on, false);
});

test("a parcel wish does not follow the cashier to a table with no order", () => {
  const parcel = createParcel({ rate: 10, api: {}, toggleUrl: URLS.toggle_parcel });
  parcel.toggle(null);
  parcel.syncFrom({ order_id: null });
  assert.equal(parcel.on, false);
  parcel.syncFrom({ order_id: 9, parcel_on: true, parcel_amount: 20 });
  assert.equal(parcel.chargeFor([]), 20);
});

/* ---- dispatch (dine-in) -------------------------------------------------- */

test("dispatch on a table: create, send to the kitchen, stay on the table", async () => {
  const rig = dispatchRig([reply(200, { success: true, order_id: 8 }), reply(200, { success: true })]);
  const result = await rig.dispatch.dispatch({ tableId: "4", source: "dine_in", aggregatorId: "", orderId: 6 });
  assert.deepEqual(rig.urlsCalled(), ["/create-order/", "/send-to-kitchen/8/"]);
  const sent = JSON.parse(rig.fetchFn.calls[0].options.body);
  assert.equal(sent.table_id, "4");
  assert.equal(sent.order_id, 6, "adds to the table's open order");
  assert.equal(sent.cart[0].id, 1);
  assert.deepEqual(result, { orderId: 8, billNow: false });
  assert.ok(rig.cart.isEmpty());
  assert.deepEqual(rig.screen.seen.flashes, ["Order Sent"]);
  assert.deepEqual(rig.screen.seen.sending, [true, false]);
});

test("a walk-in or a takeaway is billed at once", async () => {
  const rig = dispatchRig([reply(200, { success: true, order_id: 8 }), reply(200, {})]);
  const result = await rig.dispatch.dispatch({ tableId: "", source: "takeaway", aggregatorId: "", orderId: null });
  assert.equal(result.billNow, true);
  assert.equal("order_id" in JSON.parse(rig.fetchFn.calls[0].options.body), false);
});

test("a parcel wish is applied between creating the order and the kitchen", async () => {
  const rig = dispatchRig([reply(200, { success: true, order_id: 8 }), reply(200, {}), reply(200, {})], { parcelOn: true });
  await rig.dispatch.dispatch({ tableId: "", source: "takeaway", aggregatorId: "", orderId: null });
  assert.deepEqual(rig.urlsCalled(), ["/create-order/", "/toggle-parcel/8/", "/send-to-kitchen/8/"]);
});

test("an empty cart sends nothing", async () => {
  const rig = dispatchRig([]);
  rig.cart.clear();
  await rig.dispatch.dispatch({ tableId: "4", source: "dine_in", aggregatorId: "", orderId: null });
  assert.deepEqual(rig.urlsCalled(), []);
  assert.deepEqual(rig.screen.seen.flashes, ["Empty order."]);
});

test("offline: the order is queued without trying the server, and the cart clears", async () => {
  const rig = dispatchRig([], { online: false });
  const result = await rig.dispatch.dispatch({ tableId: "4", source: "dine_in", aggregatorId: "", orderId: null });
  assert.deepEqual(rig.urlsCalled(), []);
  assert.equal(rig.queue.added.length, 1);
  assert.equal(rig.queue.added[0][0].table_id, "4");
  assert.ok(rig.cart.isEmpty());
  assert.equal(result.queued, true);
  assert.equal(rig.ui.calls.toast[0][1], "success");
});

test("the connection dropping before the server answered queues the order", async () => {
  const rig = dispatchRig([networkDown()]);
  const result = await rig.dispatch.dispatch({ tableId: "4", source: "dine_in", aggregatorId: "", orderId: null });
  assert.equal(rig.queue.added.length, 1);
  assert.equal(result.queued, true);
  assert.ok(rig.cart.isEmpty());
});

test("a queue that cannot save keeps the cart and says so", async () => {
  const rig = dispatchRig([], { online: false, queue: fakeQueue({ fails: true }) });
  const result = await rig.dispatch.dispatch({ tableId: "4", source: "dine_in", aggregatorId: "", orderId: null });
  assert.equal(result.queued, false);
  assert.equal(rig.cart.isEmpty(), false);
  assert.equal(rig.ui.calls.toast[0][1], "error");
});

test("a refusal from the server is shown, never queued, and the cart stays", async () => {
  const rig = dispatchRig([reply(200, { success: false, error: "Table is closed" })]);
  const result = await rig.dispatch.dispatch({ tableId: "4", source: "dine_in", aggregatorId: "", orderId: null });
  assert.deepEqual(rig.ui.calls.toast[0], ["Table is closed", "error"]);
  assert.equal(rig.queue.added.length, 0);
  assert.equal(rig.cart.isEmpty(), false);
  assert.equal(result.error, "Table is closed");
  assert.deepEqual(rig.screen.seen.sending, [true, false], "the button comes back");
});

test("the kitchen call failing after the order exists is never queued, and is not called sent", async () => {
  const rig = dispatchRig([reply(200, { success: true, order_id: 8 }), networkDown()]);
  const result = await rig.dispatch.dispatch({ tableId: "4", source: "dine_in", aggregatorId: "", orderId: null });
  assert.equal(rig.queue.added.length, 0, "queueing it would create the order twice");
  assert.ok(rig.cart.isEmpty(), "the dishes are on the order: sending the cart again would double them");
  assert.equal(result.orderId, 8);
  assert.equal(rig.screen.seen.flashes.length, 1);
  assert.notEqual(rig.screen.seen.flashes[0], "Order Sent");
  assert.match(rig.screen.seen.flashes[0], /kitchen was not reached/);
});

test("the kitchen's own error is shown; nothing new to send is still Order Sent", async () => {
  const told = dispatchRig([reply(200, { success: true, order_id: 8 }), reply(200, { error: "Printer offline" })]);
  await told.dispatch.dispatch({ tableId: "4", source: "dine_in", aggregatorId: "", orderId: null });
  assert.deepEqual(told.screen.seen.flashes, ["Printer offline"]);
  // Nothing new to send is a success with no KOT (kitchen/views.py), not an error.
  const nothing = dispatchRig([reply(200, { success: true, order_id: 8 }), reply(200, { success: true, kots: [], message: "Nothing new to send to the kitchen." })]);
  await nothing.dispatch.dispatch({ tableId: "4", source: "dine_in", aggregatorId: "", orderId: null });
  assert.deepEqual(nothing.screen.seen.flashes, ["Order Sent"]);
});

test("a second tap while the first is on its way creates nothing", async () => {
  const rig = dispatchRig([reply(200, { success: true, order_id: 8 }), reply(200, {})]);
  const args = { tableId: "4", source: "dine_in", aggregatorId: "", orderId: null };
  const [first, second] = await Promise.all([rig.dispatch.dispatch(args), rig.dispatch.dispatch(args)]);
  assert.equal(first.orderId, 8);
  assert.equal(second, undefined);
  assert.equal(rig.urlsCalled().filter((u) => u === "/create-order/").length, 1);
});

/* ---- checkout (the token counter) and the bill ------------------------------ */

test("checkout creates the counter order and goes to its bill", async () => {
  const rig = dispatchRig([reply(200, { success: true, order_id: 9 }), reply(200, { success: true, order_id: 9 })]);
  await rig.dispatch.checkout({ orderId: 9, customerPhone: "9000000000" });
  assert.deepEqual(rig.urlsCalled(), ["/create-order/", "/generate-bill/9/"]);
  const sent = JSON.parse(rig.fetchFn.calls[0].options.body);
  assert.equal(sent.source, "counter");
  assert.equal(sent.order_id, 9);
  assert.equal(sent.customer_phone, "9000000000");
  assert.equal(rig.win.location.href, "/bill/9/");
  assert.ok(rig.cart.isEmpty());
  assert.equal(rig.screen.seen.closed, 1);
});

test("checkout applies a parcel wish to the token before billing it", async () => {
  const rig = dispatchRig(
    [reply(200, { success: true, order_id: 9 }), reply(200, { parcel_on: true, parcel_amount: 10 }), reply(200, { success: true, order_id: 9 })],
    { parcelOn: true },
  );
  await rig.dispatch.checkout({ orderId: 9, customerPhone: "" });
  assert.deepEqual(rig.urlsCalled(), ["/create-order/", "/toggle-parcel/9/", "/generate-bill/9/"]);
});

test("checkout with nothing new bills the order that is there; with no order it says so", async () => {
  const rig = dispatchRig([reply(200, { success: true, order_id: 9 })]);
  rig.cart.clear();
  await rig.dispatch.checkout({ orderId: 9, customerPhone: "" });
  assert.deepEqual(rig.urlsCalled(), ["/generate-bill/9/"]);
  assert.equal(rig.win.location.href, "/bill/9/");

  const none = dispatchRig([]);
  none.cart.clear();
  await none.dispatch.checkout({ orderId: null, customerPhone: "" });
  assert.deepEqual(none.ui.calls.toast[0], ["Cart is empty", "warning"]);
});

test("a bill the server will not issue keeps the cashier on the screen", async () => {
  const refused = dispatchRig([reply(200, { success: false, error: "Items need approval" })]);
  await refused.dispatch.bill(9);
  assert.equal(refused.win.location.href, "");
  assert.deepEqual(refused.ui.calls.toast[0], ["Items need approval", "error"]);

  const none = dispatchRig([]);
  await none.dispatch.bill(null);
  assert.deepEqual(none.urlsCalled(), []);
  assert.deepEqual(none.ui.calls.toast[0], ["No active order to bill.", "warning"]);
});

/* ---- the counter: pay and print in one go ---------------------------------- */

function fakeDoc() {
  const elements = new Map();
  return {
    elements,
    getElementById(id) {
      if (!elements.has(id)) {
        elements.set(id, { id, value: "", textContent: "", innerHTML: "", disabled: false, style: {}, dataset: {} });
      }
      return elements.get(id);
    },
    querySelectorAll: () => [],
  };
}

function counterRig(script, { online = true, printFails = null, popup = {}, queue = fakeQueue() } = {}) {
  const fetchFn = scriptedFetch(script);
  const cart = createCart();
  cart.add(PANEER);
  const ui = fakeUi();
  const screen = fakeScreen();
  const doc = fakeDoc();
  const modal = { shown: 0, hidden: 0, show() { this.shown += 1; }, hide() { this.hidden += 1; } };
  const win = { bootstrap: { Modal: { getOrCreateInstance: () => modal } } };
  const printed = [];
  const receipts = [];
  const printer = {
    printBill: async (id) => {
      if (printFails) throw new Error(printFails);
      printed.push(id);
    },
    openReceipt: (id) => {
      receipts.push(id);
      return { url: "/thermal-receipt/" + id + "/", blocked: !popup };
    },
  };
  const parcel = createParcel({ rate: 10, api: {}, toggleUrl: URLS.toggle_parcel });
  const counter = createCounter({
    cart, parcel, urls: URLS, csrf, ui, screen, printer, offlinePaymentQueue: queue,
    doc, win, fetchFn, isOnline: () => online,
  });
  const el = (id) => doc.getElementById(id);
  return { counter, cart, ui, screen, doc, el, modal, printed, receipts, queue, fetchFn, urlsCalled: () => fetchFn.calls.map((c) => c.url) };
}

const ORDER_MADE = () => [reply(200, { success: true, order_id: 5 }), reply(200, { success: true, grand_total: "105.00" })];

async function opened(rig, method, amount) {
  await rig.counter.start({ source: "takeaway" });
  rig.counter.selectMethod({ dataset: { method } });
  if (amount !== undefined) {
    rig.el("qsrAmount").value = amount;
    rig.counter.updateChange();
  }
}

test("the counter creates the order, works out its total and opens the payment sheet", async () => {
  const rig = counterRig(ORDER_MADE());
  await rig.counter.start({ source: "takeaway" });
  assert.deepEqual(rig.urlsCalled(), ["/create-order/", "/generate-bill/5/"]);
  assert.equal(JSON.parse(rig.fetchFn.calls[0].options.body).table_id, null);
  assert.equal(rig.el("qsrGrandTotal").textContent, "₹105");
  assert.equal(rig.el("qsrPayBtn").disabled, true, "no method chosen yet");
  assert.equal(rig.modal.shown, 1);
  assert.equal(rig.counter.pendingOrderId, 5);
  assert.deepEqual(rig.screen.seen.sending, [true, false]);
});

test("UPI and card fill the exact amount; cash shows the change", async () => {
  const rig = counterRig(ORDER_MADE());
  await opened(rig, "upi");
  assert.equal(rig.el("qsrAmount").value, "105.00");
  assert.equal(rig.el("qsrPayBtn").disabled, false);

  rig.counter.selectMethod({ dataset: { method: "cash" } });
  rig.el("qsrAmount").value = "200";
  rig.counter.updateChange();
  assert.equal(rig.el("qsrChangeLine").textContent, "Change due: ₹95");
  assert.equal(rig.el("qsrChangeLine").style.display, "block");
});

test("payment: paid, the slip printed, the cart cleared for the next customer", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const rig = counterRig([...ORDER_MADE(), reply(200, { success: true, order_number: "T-12", change_due: "95.00" })]);
  await opened(rig, "cash", "200");
  await rig.counter.pay();
  const paid = rig.fetchFn.calls[2];
  assert.equal(paid.url, "/pay/5/");
  const { request_id: sentId, ...body } = JSON.parse(paid.options.body);
  assert.deepEqual(body, { method: "cash", amount: "200.00" });
  assert.match(sentId, /^[0-9a-f-]{36}$/);
  assert.deepEqual(rig.printed, [5]);
  assert.equal(rig.el("qsrConfirm").style.display, "flex");
  assert.equal(rig.el("qsrConfirmNum").textContent, "Order T-12");
  assert.equal(rig.el("qsrChangeDue").textContent, "Change: ₹95");
  assert.equal(rig.cart.isEmpty(), false, "the sheet is still up");
  t.mock.timers.tick(2500);
  assert.equal(rig.el("qsrConfirm").style.display, "none");
  assert.ok(rig.cart.isEmpty());
  assert.equal(rig.counter.pendingOrderId, null);
});

test("no agent answering: the receipt page opens instead, and the agent hint shows", async (t) => {
  t.mock.timers.enable({ apis: ["setTimeout"] });
  const rig = counterRig([...ORDER_MADE(), reply(200, { success: true })], { printFails: "agent-offline" });
  await opened(rig, "upi");
  await rig.counter.pay();
  assert.deepEqual(rig.receipts, [5]);
  assert.match(rig.ui.calls.toast[0][0], /Agent not running/);
  assert.equal(rig.el("qsrConfirm").style.display, "flex", "the payment still went through");
});

test("a refused payment keeps the sheet and gives the button back", async () => {
  const rig = counterRig([...ORDER_MADE(), reply(200, { success: false, error: "Amount is less than the bill" })]);
  await opened(rig, "cash", "50");
  await rig.counter.pay();
  assert.deepEqual(rig.screen.seen.flashes, ["Amount is less than the bill"]);
  assert.equal(rig.el("qsrPayBtn").disabled, false);
  assert.equal(rig.modal.hidden, 0);
  assert.equal(rig.counter.pendingOrderId, 5);
});

test("offline at payment time: UPI is refused, cash goes to the queue", async () => {
  let online = true;
  function rigWithSwitch() {
    const fetchFn = scriptedFetch(ORDER_MADE());
    const cart = createCart();
    cart.add(PANEER);
    const ui = fakeUi();
    const screen = fakeScreen();
    const doc = fakeDoc();
    const modal = { hidden: 0, show() {}, hide() { this.hidden += 1; } };
    const queue = fakeQueue();
    const counter = createCounter({
      cart, parcel: createParcel({ rate: 0, api: {}, toggleUrl: URLS.toggle_parcel }), urls: URLS, csrf, ui, screen,
      printer: {}, offlinePaymentQueue: queue, doc, win: { bootstrap: { Modal: { getOrCreateInstance: () => modal } } },
      fetchFn, isOnline: () => online,
    });
    return { counter, cart, ui, screen, doc, modal, queue, fetchFn, el: (id) => doc.getElementById(id) };
  }

  online = true;
  const upi = rigWithSwitch();
  await opened(upi, "upi");
  online = false;
  await upi.counter.pay();
  assert.deepEqual(upi.screen.seen.flashes, ["You are offline. Only cash payments can be taken right now."]);
  assert.equal(upi.queue.added.length, 0);
  assert.equal(upi.fetchFn.calls.length, 2, "no payment was tried");

  online = true;
  const cash = rigWithSwitch();
  await opened(cash, "cash", "105");
  online = false;
  await cash.counter.pay();
  assert.deepEqual(cash.queue.added.map((q) => q.slice(0, 2)), [[5, "105.00"]]);
  assert.match(cash.queue.added[0][2], /^[0-9a-f-]{36}$/);
  assert.equal(cash.modal.hidden, 1);
  assert.ok(cash.cart.isEmpty());
  assert.equal(cash.counter.pendingOrderId, null);
});

test("the connection dropping mid-payment queues cash, and only cash", async () => {
  const cash = counterRig([...ORDER_MADE(), networkDown()]);
  await opened(cash, "cash", "105");
  await cash.counter.pay();
  assert.deepEqual(cash.queue.added.map((q) => q.slice(0, 2)), [[5, "105.00"]]);
  // The same id as the request that dropped: never recorded twice.
  assert.equal(cash.queue.added[0][2], JSON.parse(cash.fetchFn.calls[2].options.body).request_id);
  assert.ok(cash.cart.isEmpty());

  const upi = counterRig([...ORDER_MADE(), networkDown()]);
  await opened(upi, "upi");
  await upi.counter.pay();
  assert.equal(upi.queue.added.length, 0, "nobody knows if the money moved");
  assert.deepEqual(upi.screen.seen.flashes, ["Network error during payment"]);
  assert.equal(upi.cart.isEmpty(), false);
  assert.equal(upi.el("qsrPayBtn").disabled, false);
});

test("cancelling from the payment sheet: allowed clears, refused keeps the order", async () => {
  const allowed = counterRig([...ORDER_MADE(), reply(200, { success: true })]);
  await allowed.counter.start({ source: "takeaway" });
  await allowed.counter.cancel();
  assert.equal(allowed.fetchFn.calls[2].url, "/cancel-order/5/");
  assert.equal(allowed.modal.hidden, 1);
  assert.ok(allowed.cart.isEmpty());
  assert.equal(allowed.counter.pendingOrderId, null);

  const refused = counterRig([...ORDER_MADE(), reply(400, { error: "Already paid" })]);
  await refused.counter.start({ source: "takeaway" });
  await refused.counter.cancel();
  assert.deepEqual(refused.screen.seen.flashes, ["Already paid"]);
  assert.equal(refused.modal.hidden, 0);
  assert.equal(refused.counter.pendingOrderId, 5);
  assert.equal(refused.cart.isEmpty(), false);
});

/* ---- printing -------------------------------------------------------------- */

function printerRig(platform, script = [], popup = {}) {
  const fetchFn = scriptedFetch(script);
  const opened = [];
  const win = { open: (...args) => { opened.push(args); return popup; } };
  const printer = createPrinter({ platform, urls: URLS, csrf, fetchFn, win, stationPrinterIp: "192.168.1.50", outletId: 3 });
  return { printer, fetchFn, opened };
}

test("on an iPhone the bill opens at the bill's real address", async () => {
  const rig = printerRig({ ios: true });
  await rig.printer.printBill(5);
  // The page used to open /orders/bill/5/, an address that does not exist.
  assert.deepEqual(rig.opened, [["/bill/5/?print=1", "_blank"]]);
});

test("on Android the job goes to the server's queue, and a refusal is an error", async () => {
  const rig = printerRig({ android: true }, [reply(200, {}), reply(400, { error: "No printer set up" })]);
  await rig.printer.printBill(5);
  assert.equal(rig.fetchFn.calls[0].url, "/orders/agent/add-job/");
  assert.deepEqual(JSON.parse(rig.fetchFn.calls[0].options.body), { order_id: 5 });
  await assert.rejects(rig.printer.printBill(6), /No printer set up/);
});

test("on a desktop the receipt lines go to the agent with the printer's address", async (t) => {
  const sockets = [];
  class FakeSocket {
    constructor(url) {
      this.url = url;
      sockets.push(this);
      queueMicrotask(() => this.onopen());
    }
    send(message) {
      this.sent = JSON.parse(message);
      queueMicrotask(() => this.onmessage({ data: JSON.stringify({ job_id: this.sent.job_id, type: "ok" }) }));
    }
    close() { this.closed = true; }
  }
  const real = globalThis.WebSocket;
  globalThis.WebSocket = FakeSocket;
  t.after(() => { globalThis.WebSocket = real; });

  const rig = printerRig({ agentHost: "192.168.1.9" }, [reply(200, { success: true, lines: ["a", "b"] })]);
  await rig.printer.printBill(5);
  assert.equal(rig.fetchFn.calls[0].url, "/qz-data/5/");
  assert.equal(sockets[0].url, "ws://192.168.1.9:8765");
  assert.deepEqual(sockets[0].sent.lines, ["a", "b"]);
  assert.equal(sockets[0].sent.network_host, "192.168.1.50");
  assert.equal(sockets[0].sent.outlet_id, "3");
  assert.equal(sockets[0].closed, true);

  const noLines = printerRig({ agentHost: "x" }, [reply(200, { success: false })]);
  await assert.rejects(noLines.printer.printBill(5), /receipt-data-failed/);
});

test("the receipt page is the fallback, and a blocked popup is reported", async () => {
  const rig = printerRig({}, [], null);
  const result = await rig.printer.printReceipt(5);
  assert.equal(rig.opened[0][0], "/thermal-receipt/5/");
  assert.deepEqual(result, { url: "/thermal-receipt/5/", blocked: true });

  const android = printerRig({ nativeAndroid: true }, [reply(200, {})]);
  assert.deepEqual(await android.printer.printReceipt(5), { queued: true });
});

/* ---- the Active panel -------------------------------------------------------- */

function panelRig(fetchFn, render = (d) => "order " + d.order_id) {
  const boxes = [{ innerHTML: "" }, { innerHTML: "" }, null];     // a page may lack one
  const seen = [];
  const running = createRunningOrder({
    url: URLS.running_order_items, containers: boxes, fetchFn, render, onData: (d) => seen.push(d),
  });
  return { running, boxes, seen };
}

test("no table chosen asks for one and calls nothing", async () => {
  const fetchFn = scriptedFetch([]);
  const rig = panelRig(fetchFn);
  await rig.running.look(null);
  assert.equal(rig.boxes[0].innerHTML, SELECT_TABLE_HTML);
  assert.equal(fetchFn.calls.length, 0);
  assert.equal(await rig.running.refresh(), null);
});

test("a table's order is fetched, handed to the screen and drawn in both panels", async () => {
  const fetchFn = scriptedFetch([reply(200, { order_id: 8, items: [] }), reply(200, { order_id: 31, items: [] })]);
  const rig = panelRig(fetchFn);
  await rig.running.look({ table: 4 });
  assert.equal(fetchFn.calls[0].url, "/running-order-items/?table=4");
  assert.deepEqual(rig.seen, [{ order_id: 8, items: [] }]);
  assert.deepEqual(rig.boxes.slice(0, 2).map((b) => b.innerHTML), ["order 8", "order 8"]);
  await rig.running.look({ order: 31 });
  assert.equal(fetchFn.calls[1].url, "/running-order-items/?order=31");
});

test("a slow answer for the table the cashier already left is thrown away", async () => {
  let releaseSlow;
  const slow = new Promise((resolve) => { releaseSlow = () => resolve(reply(200, { order_id: 1 })); });
  const answers = [slow, Promise.resolve(reply(200, { order_id: 2 }))];
  const rig = panelRig(() => answers.shift());
  const first = rig.running.look({ table: 1 });
  await rig.running.look({ table: 2 });
  releaseSlow();
  await first;
  assert.equal(rig.boxes[0].innerHTML, "order 2");
  assert.deepEqual(rig.seen, [{ order_id: 2 }]);
});

test("an order that will not load offers a retry", async () => {
  const rig = panelRig(scriptedFetch([networkDown()]));
  await rig.running.look({ table: 4 });
  assert.equal(rig.boxes[0].innerHTML, ERROR_HTML);
  assert.match(ERROR_HTML, /data-action="running.retry"/);
});

test("a table's order lists its dishes, escaped, with a cancel button", () => {
  assert.match(renderTableOrder({ items: [] }), /No active services/);
  const html = renderTableOrder({
    order_id: 8,
    items: [{ quantity: 2, name: "<b>Dal</b>", status: "ready", modifiers: ["extra <i>ghee</i>"] }],
  });
  assert.match(html, /2 × &lt;b&gt;Dal&lt;\/b&gt;/);
  assert.match(html, /extra &lt;i&gt;ghee&lt;\/i&gt;/);
  assert.match(html, /status-ready/);
  assert.match(html, /data-action="order.cancel" data-id="8"/);
});

test("a token's order: guest items wait for approval before checkout appears", () => {
  const waiting = renderTokenOrder({
    order_id: 9, order_status: "open",
    items: [{ id: 1, quantity: 1, name: "Tea", total: 20, status: "review" }, { id: 2, quantity: 1, name: "Bun", total: 30, status: "pending" }],
  });
  assert.match(waiting, /data-action="item.approve" data-id="1"/);
  assert.match(waiting, /data-action="item.reject" data-id="1"/);
  assert.doesNotMatch(waiting, /data-action="order.bill"/);
  assert.match(waiting, /data-action="order.cancel" data-id="9"/);

  const approved = renderTokenOrder({
    order_id: 9, order_status: "open", items: [{ id: 2, quantity: 1, name: "Bun", total: 30, status: "pending" }],
  });
  assert.match(approved, /data-action="order.bill" data-id="9"/);

  assert.match(renderTokenOrder({ order_status: "paid", items: [] }), /Paid/);
  const billing = renderTokenOrder({ order_id: 9, order_status: "billing", items: [{ id: 2, quantity: 1, name: "Bun", total: 30, status: "served" }] });
  assert.doesNotMatch(billing, /data-action="order\.(bill|cancel)"/, "a billed order is finished from the bill");
});
