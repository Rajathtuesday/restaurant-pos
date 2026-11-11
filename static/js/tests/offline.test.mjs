import assert from "node:assert/strict";
import { test } from "node:test";

import { createOfflineBanner, createOrderQueue, createPaymentQueue, memoryStore } from "../core/offline.js";
import { fakeUi, networkDown, reply, scriptedFetch } from "./helpers.mjs";

function orderQueue(script, { online = true } = {}) {
  const ui = fakeUi();
  const fetchFn = scriptedFetch(script);
  let changes = 0;
  const queue = createOrderQueue({
    store: memoryStore(), fetchFn, csrf: () => "tok", ui, isOnline: () => online, onChange: () => { changes += 1; },
  });
  return { queue, ui, fetchFn, changes: () => changes };
}

test("an order queued offline is recreated and sent to the kitchen when back online", async () => {
  const { queue, ui, fetchFn } = orderQueue([
    reply(200, { success: true, order_id: 41, order_number: "A-41" }),
    reply(200, { success: true }),
  ]);
  await queue.add({ cart: [{ id: 1, quantity: 2 }] });
  assert.equal((await queue.all()).length, 1);

  await queue.flush();

  assert.equal(fetchFn.calls[0].url, "/create-order/");
  assert.equal(JSON.parse(fetchFn.calls[0].options.body).cart[0].quantity, 2);
  assert.equal(fetchFn.calls[1].url, "/send-to-kitchen/41/");
  assert.equal((await queue.all()).length, 0);
  assert.deepEqual(ui.calls.toast, [["Order #A-41 synced and sent to kitchen", "success"]]);
});

test("the connection dropping mid-flush leaves the rest queued", async () => {
  const { queue, fetchFn } = orderQueue([
    reply(200, { success: true, order_id: 1 }), reply(200, {}),
    networkDown(),
  ]);
  await queue.add({ n: 1 });
  await queue.add({ n: 2 });
  await queue.add({ n: 3 });

  await queue.flush();

  const left = await queue.all();
  assert.deepEqual(left.map((r) => r.payload.n), [2, 3], "the second stopped the flush, the third was never tried");
  assert.equal(fetchFn.calls.length, 3);
});

test("an order the server refuses is dropped and the cashier is told", async () => {
  const { queue, ui } = orderQueue([
    reply(400, { success: false, error: "Table 4 no longer exists" }),
    reply(200, { success: true, order_id: 9 }), reply(200, {}),
  ]);
  await queue.add({ n: 1 });
  await queue.add({ n: 2 });

  await queue.flush();

  assert.equal((await queue.all()).length, 0);
  assert.equal(ui.calls.toast[0][0], "A saved offline order could not be recreated (Table 4 no longer exists). Please re-enter it.");
  assert.equal(ui.calls.toast[1][0], "Order #9 synced and sent to kitchen");
});

test("the kitchen call failing does not re-create the order", async () => {
  const { queue, ui } = orderQueue([
    reply(200, { success: true, order_id: 5 }),
    networkDown(),
  ]);
  await queue.add({ n: 1 });
  await queue.flush();
  assert.equal((await queue.all()).length, 0, "the order exists on the server, so it leaves the queue");
  assert.equal(ui.calls.toast[0][0], "Order #5 synced and sent to kitchen");
});

test("nothing happens offline, and a second flush during the first does nothing", async () => {
  const offline = orderQueue([], { online: false });
  await offline.queue.add({ n: 1 });
  await offline.queue.flush();
  assert.equal(offline.fetchFn.calls.length, 0);
  assert.equal((await offline.queue.all()).length, 1);

  let release;
  const gate = new Promise((r) => { release = r; });
  const ui = fakeUi();
  const slowFetch = async () => { await gate; return reply(200, { success: true, order_id: 1 }); };
  const queue = createOrderQueue({ store: memoryStore(), fetchFn: slowFetch, csrf: () => "t", ui, isOnline: () => true });
  await queue.add({ n: 1 });
  const first = queue.flush();
  await queue.flush();            // overlaps: returns at once
  release();
  await first;
  assert.equal(ui.calls.toast.length, 1, "the order was synced exactly once");
});

test("the banner says offline, syncing, or nothing", async () => {
  const el = { innerHTML: "", style: {} };
  const store = memoryStore();
  const queue = createOrderQueue({ store, fetchFn: async () => reply(200, {}), csrf: () => "t", ui: fakeUi(), isOnline: () => true });
  let online = false;
  const banner = createOfflineBanner({ el, queues: [queue], isOnline: () => online });

  await banner.update();
  assert.match(el.innerHTML, /currently offline/);
  assert.match(el.innerHTML, /Actions may not be saved/);

  await queue.add({ n: 1 });
  await queue.add({ n: 2 });
  await banner.update();
  assert.match(el.innerHTML, /2 items saved, will sync/);

  online = true;
  await banner.update();
  assert.match(el.innerHTML, /Syncing 2 items/);

  await store.remove(1);
  await store.remove(2);
  await banner.update();
  assert.equal(el.style.display, "none");
});

function paymentQueue(script) {
  const ui = fakeUi();
  const fetchFn = scriptedFetch(script);
  const queue = createPaymentQueue({ store: memoryStore(), fetchFn, csrf: () => "tok", ui, isOnline: () => true });
  return { queue, ui, fetchFn };
}

test("a cash payment queued offline is replayed against its own order", async () => {
  const { queue, ui, fetchFn } = paymentQueue([reply(200, { success: true })]);
  await queue.add(42, "250.00");
  await queue.flush();
  assert.equal(fetchFn.calls[0].url, "/pay/42/");
  assert.deepEqual(JSON.parse(fetchFn.calls[0].options.body), { method: "cash", amount: "250.00" });
  assert.equal((await queue.all()).length, 0);
  assert.deepEqual(ui.calls.toast, [["Cash payment for order #42 synced", "success"]]);
});

test("a refused payment is shown for the cashier to settle by hand, never retried blindly", async () => {
  const { queue, ui } = paymentQueue([reply(400, { success: false, error: "Order already completed" })]);
  await queue.add(42, "250.00");
  await queue.flush();
  assert.equal((await queue.all()).length, 0);
  assert.equal(ui.calls.toast[0][0], "Offline cash payment for order #42 could not be synced (Order already completed). Please check this order manually.");
  assert.equal(ui.calls.toast[0][1], "error");
});

test("a payment whose answer never arrived stays queued", async () => {
  const { queue } = paymentQueue([networkDown()]);
  await queue.add(42, "250.00");
  await queue.flush();
  assert.equal((await queue.all()).length, 1, "we never guess whether an unconfirmed payment went through");
});
