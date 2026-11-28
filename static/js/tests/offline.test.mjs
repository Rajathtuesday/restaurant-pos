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
  await queue.add(42, "250.00", "pay-1234-abcd");
  await queue.flush();
  assert.equal(fetchFn.calls[0].url, "/pay/42/");
  assert.deepEqual(JSON.parse(fetchFn.calls[0].options.body), { method: "cash", amount: "250.00", request_id: "pay-1234-abcd" });
  assert.equal((await queue.all()).length, 0);
  assert.deepEqual(ui.calls.toast, [["Cash payment for order #42 synced", "success"]]);
});

test("a payment queued offline from the start gets its own id, and sends it", async () => {
  const { queue, fetchFn } = paymentQueue([reply(200, { success: true }), reply(200, { success: true })]);
  await queue.add(42, "250.00");
  const [record] = await queue.all();
  assert.match(record.request_id, /^[0-9a-f-]{36}$/);
  await queue.flush();
  assert.equal(JSON.parse(fetchFn.calls[0].options.body).request_id, record.request_id);
});

test("a payment the server already took is answered as synced", async () => {
  const { queue, ui } = paymentQueue([reply(200, { success: true, repeat: true })]);
  await queue.add(42, "250.00", "pay-1234-abcd");
  await queue.flush();
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

/* ---- signed out when the connection comes back (28 Nov 2026) -----------------
 * A till whose session ended (or that was signed out in another tab) while
 * it was offline: /create-order/ answers a signed-out till 401, and the
 * order queue read that as "refused" and dropped the saved order; once the
 * server answered every signed-out script with 401 the payment queue would
 * have dropped saved cash the same way. Signed out is not a refusal: stop,
 * keep everything, show the notice, and replay after signing in. */

const SIGNED_OUT = () => reply(401, { error: "You're signed out. Sign in again to carry on.", signed_out: true });

test("signed out, saved orders are kept, not dropped, and the flush stops", async () => {
  let notices = 0;
  const ui = fakeUi();
  const fetchFn = scriptedFetch([SIGNED_OUT()]);
  const queue = createOrderQueue({
    store: memoryStore(), fetchFn, csrf: () => "tok", ui, isOnline: () => true,
    onSignedOut: () => { notices += 1; },
  });
  await queue.add({ n: 1 });
  await queue.add({ n: 2 });
  await queue.flush();
  assert.deepEqual((await queue.all()).map((r) => r.payload.n), [1, 2]);
  assert.equal(fetchFn.calls.length, 1, "the second is not tried");
  assert.equal(notices, 1);
  assert.deepEqual(ui.calls.toast, [], "no 'please re-enter it'");
});

test("signed out, a saved cash payment is kept for after signing in", async () => {
  let notices = 0;
  const ui = fakeUi();
  const fetchFn = scriptedFetch([SIGNED_OUT()]);
  const queue = createPaymentQueue({
    store: memoryStore(), fetchFn, csrf: () => "tok", ui, isOnline: () => true,
    onSignedOut: () => { notices += 1; },
  });
  await queue.add(42, "250.00", "pay-1234-abcd");
  await queue.flush();
  assert.equal((await queue.all()).length, 1, "money is never dropped because a sign-in ended");
  assert.equal(notices, 1);
  assert.deepEqual(ui.calls.toast, []);
});

test("the sign-in page reached by a redirect is signed out too: nothing dropped", async () => {
  const signIn = { ...reply(200, undefined), redirected: true, url: "https://spice.rasova.net/login/?next=/create-order/" };
  const ui = fakeUi();
  const queue = createOrderQueue({
    store: memoryStore(), fetchFn: scriptedFetch([signIn]), csrf: () => "tok", ui, isOnline: () => true,
    onSignedOut: () => {},
  });
  await queue.add({ n: 1 });
  await queue.flush();
  assert.equal((await queue.all()).length, 1);
});

test("signed in again, the kept order is replayed as normal", async () => {
  const ui = fakeUi();
  const fetchFn = scriptedFetch([SIGNED_OUT(), reply(200, { success: true, order_id: 7 }), reply(200, {})]);
  const queue = createOrderQueue({
    store: memoryStore(), fetchFn, csrf: () => "tok", ui, isOnline: () => true, onSignedOut: () => {},
  });
  await queue.add({ n: 1 });
  await queue.flush();
  await queue.flush();
  assert.equal((await queue.all()).length, 0);
  assert.equal(ui.calls.toast[0][0], "Order #7 synced and sent to kitchen");
});
