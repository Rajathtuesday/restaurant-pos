/*
 * The bill page's modules (static/js/bill/), each with fakes for the
 * server, the printers and the queue: what a payment sends, what happens
 * when the connection or the cash register is not there, where the bill
 * is printed, and what the QR sheet does while it waits.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { changeDue, payable, splitShare, upiString } from "../bill/amount.js";
import { createBillPrinter } from "../bill/bill_print.js";
import { billDiscountProblem, promoLabel, promoProblem, readItemDiscount } from "../bill/discounts.js";
import { createPayment } from "../bill/payment.js";
import { createRazorpayQr } from "../bill/razorpay_qr.js";
import { fakeUi, networkDown, reply, scriptedFetch } from "./helpers.mjs";

const URLS = {
  pay_order: "/pay/7/",
  open_register: "/shifts/sessions/open/",
  print_bill: "/print-bill/7/",
  print_split_bill: "/print-split-bill/7/",
  print_queue_add: "/orders/agent/add-job/",
  qz_receipt_data: "/qz-data/0/",
  thermal_receipt: "/thermal-receipt/0/",
  razorpay_create_qr: "/razorpay/create-qr/7/",
  razorpay_qr_status: "/razorpay/qr-status/PLACEHOLDER/",
};

/* ---- the sums ------------------------------------------------------------- */

test("change is what the guest gave over the balance, never negative", () => {
  assert.equal(changeDue("400", "357.00"), 43);
  assert.equal(changeDue("357", "357.00"), 0);
  assert.equal(changeDue("100", "357.00"), 0);
  assert.equal(changeDue("", "357.00"), 0);
});

test("a split share is the balance over the people, to the paisa", () => {
  assert.equal(splitShare("357.00", 3), "119.00");
  assert.equal(splitShare("100.00", "3"), "33.33");
  assert.equal(splitShare("357.00", 1), "357.00");
  assert.equal(splitShare("357.00", 0), null);
  assert.equal(splitShare("357.00", "2.5"), null);
  assert.equal(splitShare("357.00", ""), null);
  assert.equal(splitShare("357.00", "abc"), null);
});

test("the UPI link carries the amount being collected and survives a name with an ampersand", () => {
  const link = upiString({ upiId: "restaurant@okhdfcbank", payee: "Salt & Pepper", amount: "119.00", billNumber: "INV/26-27/0042" });
  assert.equal(link, "upi://pay?pa=restaurant@okhdfcbank&pn=Salt%20%26%20Pepper&am=119.00&cu=INR&tn=Bill%20INV%2F26-27%2F0042");
  // every parameter is still its own: the ampersand in the name did not start a new one
  assert.deepEqual(link.split("?")[1].split("&").map((part) => part.split("=")[0]), ["pa", "pn", "am", "cu", "tn"]);
});

test("the amount field must hold a number before a payment is sent", () => {
  assert.equal(payable("357.00"), 357);
  assert.equal(payable("0"), 0);
  assert.equal(payable(""), null);
  assert.equal(payable("  "), null);
  assert.equal(payable("-5"), null);
  assert.equal(payable("abc"), null);
});

/* ---- discounts ------------------------------------------------------------ */

test("a hand-typed discount needs a value and a reason; a promo needs no reason", () => {
  assert.equal(billDiscountProblem({ value: 0, reason: "regular", promoId: null }).field, "value");
  assert.equal(billDiscountProblem({ value: NaN, reason: "regular", promoId: null }).field, "value");
  assert.equal(billDiscountProblem({ value: 10, reason: "ok", promoId: null }).field, "reason");
  assert.equal(billDiscountProblem({ value: 10, reason: "regular guest", promoId: null }), null);
  assert.equal(billDiscountProblem({ value: 10, reason: "", promoId: 4 }), null);
});

test("a dish's discount is 0 to 100, with a reason unless it is taken off", () => {
  assert.deepEqual(readItemDiscount("25", "food was cold"), { percent: 25, reason: "food was cold" });
  assert.deepEqual(readItemDiscount("0", ""), { percent: 0, reason: "" });
  assert.match(readItemDiscount("25", "").message, /reason/);
  assert.match(readItemDiscount("101", "x y z").message, /0 to 100/);
  assert.match(readItemDiscount("", "x y z").message, /0 to 100/);
  assert.match(readItemDiscount("-1", "x y z").message, /0 to 100/);
});

test("a promo is named plainly and refused below its minimum", () => {
  assert.equal(promoLabel({ name: "Happy Monday", discount_type: "percentage", discount_value: "10" }), "Happy Monday: 10% Off");
  assert.equal(promoLabel({ name: "Welcome", discount_type: "amount", discount_value: "50" }), "Welcome: ₹50 Flat");
  assert.equal(promoProblem({ min_order: "500" }, "357.00"), "Minimum order ₹500 required");
  assert.equal(promoProblem({ min_order: "300" }, "357.00"), null);
  assert.equal(promoProblem({ min_order: null }, "10"), null);
});

/* ---- taking a payment ------------------------------------------------------ */

function fakeApi(script) {
  const calls = [];
  return {
    calls,
    post: async (url, body) => {
      calls.push({ url, body });
      const next = script.shift();
      if (next === undefined) throw new Error("unexpected post " + url);
      if (next instanceof Error) throw next;
      return next;
    },
    get: async (url) => {
      calls.push({ url });
      const next = script.shift();
      if (next instanceof Error) throw next;
      return next;
    },
  };
}

function payRig(script, { online = true, printed = true, register = "0", queueFails = false } = {}) {
  const api = fakeApi(script);
  const ui = fakeUi();
  const queued = [];
  const went = [];
  const printedFor = [];
  let asked = 0;
  const payment = createPayment({
    api, urls: URLS, ui, orderId: 7,
    paymentQueue: { add: async (...args) => { if (queueFails) throw new Error("no storage"); queued.push(args); } },
    printer: { afterPayment: async (id) => { printedFor.push(id); return printed; } },
    askOpenRegister: async () => { asked += 1; return register; },
    isOnline: () => online,
    go: (query) => went.push(query),
    wait: async () => {},
  });
  return { payment, api, ui, queued, went, printedFor, asked: () => asked };
}

test("a payment is recorded, the bill printed, and the page reloads", async () => {
  const rig = payRig([{ success: true, message: "Paid in full" }]);
  const result = await rig.payment.pay("cash", "357.00");
  assert.deepEqual(rig.api.calls, [{ url: "/pay/7/", body: { method: "cash", amount: "357.00" } }]);
  assert.deepEqual(rig.printedFor, [7]);
  assert.deepEqual(rig.went, [""]);
  assert.deepEqual(result, { paid: true, printed: true });
  assert.deepEqual(rig.ui.calls.toast[0], ["Paid in full", "success"]);
});

test("when no printer took the bill the page reloads asking for the receipt page", async () => {
  const rig = payRig([{ success: true }], { printed: false });
  await rig.payment.pay("upi", "357.00");
  assert.deepEqual(rig.went, ["?print=1"]);
});

test("offline: cash is queued and nothing else is taken", async () => {
  const cash = payRig([], { online: false });
  assert.deepEqual(await cash.payment.pay("cash", "357.00"), { queued: true });
  assert.deepEqual(cash.queued, [[7, "357.00"]]);
  assert.deepEqual(cash.api.calls, []);
  assert.deepEqual(cash.went, [], "the page stays: the payment is not on the server yet");

  const upi = payRig([], { online: false });
  assert.deepEqual(await upi.payment.pay("upi", "357.00"), { refused: "offline" });
  assert.deepEqual(upi.queued, []);
  assert.equal(upi.ui.calls.toast[0][1], "error");
});

test("the connection dropping mid-payment queues cash, and only cash", async () => {
  const cash = payRig([networkDown()]);
  assert.deepEqual(await cash.payment.pay("cash", "357.00"), { queued: true });
  assert.deepEqual(cash.queued, [[7, "357.00"]]);

  const card = payRig([networkDown()]);
  assert.deepEqual(await card.payment.pay("card", "357.00"), { refused: "server" });
  assert.deepEqual(card.queued, [], "nobody knows if the money moved");
  assert.deepEqual(card.went, []);
});

test("a device that cannot save offline says so and queues nothing", async () => {
  const rig = payRig([], { online: false, queueFails: true });
  assert.deepEqual(await rig.payment.pay("cash", "357.00"), { queued: false });
  assert.match(rig.ui.calls.toast[0][0], /note it down/);
});

test("no cash register open: the cashier opens it here and the same payment goes through once", async () => {
  const rig = payRig([new Error("No open cash session for this outlet"), { success: true }, { success: true }], { register: "500" });
  const result = await rig.payment.pay("cash", "357.00");
  assert.deepEqual(rig.api.calls.map((c) => c.url), ["/pay/7/", "/shifts/sessions/open/", "/pay/7/"]);
  assert.deepEqual(rig.api.calls[1].body, { opening_balance: "500" });
  assert.equal(result.paid, true);
  assert.equal(rig.asked(), 1);
});

test("backing out of opening the register takes no payment; a second refusal does not ask again", async () => {
  const backedOut = payRig([new Error("No open cash session")], { register: null });
  assert.deepEqual(await backedOut.payment.pay("cash", "357.00"), { refused: "register" });
  assert.deepEqual(backedOut.api.calls.map((c) => c.url), ["/pay/7/"]);

  const again = payRig([new Error("No open cash session"), { success: true }, new Error("No open cash session")]);
  assert.deepEqual(await again.payment.pay("cash", "357.00"), { refused: "server" });
  assert.equal(again.asked(), 1, "asked once, not in a loop");
});

test("any other refusal is left as the server said it", async () => {
  const rig = payRig([new Error("Amount is more than the balance")]);
  assert.deepEqual(await rig.payment.pay("card", "999"), { refused: "server" });
  assert.equal(rig.asked(), 0);
  assert.deepEqual(rig.went, []);
});

test("a second tap while the first payment is on its way records nothing more", async () => {
  const rig = payRig([{ success: true }]);
  const [first, second] = await Promise.all([rig.payment.pay("cash", "357.00"), rig.payment.pay("cash", "357.00")]);
  assert.equal(first.paid, true);
  assert.deepEqual(second, { refused: "busy" });
  assert.equal(rig.api.calls.length, 1);
});

/* ---- printing the bill ------------------------------------------------------- */

function printRig(platform, script, settings = {}) {
  const fetchFn = scriptedFetch(script);
  const ui = fakeUi();
  const win = { printed: 0, opened: [], location: { href: "" }, print() { this.printed += 1; }, open(...a) { this.opened.push(a); return this.popup; }, popup: {} };
  const sockets = [];
  class FakeSocket {
    constructor(url) {
      this.url = url;
      sockets.push(this);
      queueMicrotask(() => (this.refuse ? this.onerror() : this.onopen()));
    }
    send(message) {
      this.sent = JSON.parse(message);
      queueMicrotask(() => this.onmessage({ data: JSON.stringify({ job_id: this.sent.job_id, type: this.answer || "ok" }) }));
    }
    close() { this.closed = true; }
  }
  const printer = createBillPrinter({
    platform: { agentHost: "192.168.1.9", ...platform }, urls: URLS, csrf: () => "tok", ui, fetchFn, win, Socket: FakeSocket,
    settings: { splitMode: false, useAgent: false, printerName: "", printerMac: "", outletId: 3, ...settings },
  });
  return { printer, fetchFn, ui, win, sockets, FakeSocket, urlsCalled: () => fetchFn.calls.map((c) => c.url) };
}

test("on Android the bill goes to the server's queue and nothing else is tried", async () => {
  const rig = printRig({ android: true }, [reply(200, { success: true })]);
  assert.equal(await rig.printer.afterPayment(7), true);
  assert.deepEqual(rig.urlsCalled(), ["/orders/agent/add-job/"]);
  assert.deepEqual(JSON.parse(rig.fetchFn.calls[0].options.body), { order_id: 7 });
  assert.deepEqual(rig.ui.calls.toast[0], ["Sent to printer", "success"]);
});

test("if the queue refuses on Android, the server's own printer is tried next", async () => {
  const rig = printRig({ nativeAndroid: true }, [reply(400, { error: "No agent has checked in" }), reply(200, { success: true })]);
  assert.equal(await rig.printer.afterPayment(7), true);
  assert.deepEqual(rig.urlsCalled(), ["/orders/agent/add-job/", "/print-bill/7/"]);
  assert.deepEqual(rig.ui.calls.toast[0], ["No agent has checked in", "error"]);
});

test("the PC agent prints when the outlet uses it and a printer is named", async () => {
  const rig = printRig({}, [reply(200, { lines: ["a", "b"] })], { useAgent: true, printerName: "TM-T82", printerMac: "AA:BB", splitMode: true });
  assert.equal(await rig.printer.afterPayment(7), true);
  assert.deepEqual(rig.urlsCalled(), ["/qz-data/7/?split=1"]);
  assert.equal(rig.sockets[0].url, "ws://192.168.1.9:8765");
  const sent = rig.sockets[0].sent;
  assert.deepEqual([sent.type, sent.printer, sent.lines, sent.encoding, sent.outlet_id, sent.printer_mac],
    ["print", "TM-T82", ["a", "b"], "cp437", "3", "AA:BB"]);
  assert.equal(rig.sockets[0].closed, true);
});

test("no agent answering falls through to the server's printer; split bills use their own address", async () => {
  const rig = printRig({}, [reply(200, { success: true })], { useAgent: true, printerName: "TM-T82", splitMode: true });
  const real = rig.FakeSocket.prototype;
  Object.defineProperty(real, "refuse", { value: true, configurable: true });
  assert.equal(await rig.printer.afterPayment(7), true);
  assert.deepEqual(rig.urlsCalled(), ["/print-split-bill/7/"]);
});

test("when nothing printed the answer is false, so the page can open the receipt", async () => {
  const refused = printRig({}, [reply(200, { success: false })]);
  assert.equal(await refused.printer.afterPayment(7), false);
  const down = printRig({}, [networkDown()]);
  assert.equal(await down.printer.afterPayment(7), false);
  assert.deepEqual(down.urlsCalled(), ["/print-bill/7/"], "no agent is tried when the outlet does not use one");
});

test("the Print button: the queue on Android, the browser's print elsewhere", async () => {
  const phone = printRig({ android: true }, [reply(200, { success: true })]);
  await phone.printer.manual(7);
  assert.equal(phone.win.printed, 0);
  assert.deepEqual(phone.urlsCalled(), ["/orders/agent/add-job/"]);

  const desk = printRig({}, []);
  await desk.printer.manual(7);
  assert.equal(desk.win.printed, 1);
  assert.deepEqual(desk.urlsCalled(), []);
});

test("the receipt page opens in a small window, or in this one when popups are blocked", () => {
  const rig = printRig({}, []);
  rig.printer.openReceipt(7);
  assert.equal(rig.win.opened[0][0], "/thermal-receipt/7/");
  assert.equal(rig.win.location.href, "");

  const blocked = printRig({}, []);
  blocked.win.popup = null;
  blocked.printer.openReceipt(7);
  assert.equal(blocked.win.location.href, "/thermal-receipt/7/");
});

/* ---- the Razorpay QR sheet ---------------------------------------------------- */

function fakeDoc() {
  const elements = new Map();
  const make = (id) => {
    const classes = new Set();
    return {
      id, textContent: "", innerHTML: "", children: [],
      classList: {
        add: (c) => classes.add(c), remove: (c) => classes.delete(c), contains: (c) => classes.has(c),
        toggle: (c, on) => (on ? classes.add(c) : classes.delete(c)),
      },
      appendChild(child) { this.children.push(child); },
    };
  };
  return {
    getElementById(id) {
      if (!elements.has(id)) elements.set(id, make(id));
      return elements.get(id);
    },
    createElement: () => make(""),
  };
}

function qrRig(script, { amount = "119.00", clock = { now: 1000 } } = {}) {
  const api = fakeApi(script);
  const ui = fakeUi();
  const doc = fakeDoc();
  const ticks = [];
  const stopped = [];
  const laters = [];
  let paid = 0;
  const qr = createRazorpayQr({
    api, urls: URLS, ui, doc, getAmount: () => amount, onPaid: () => { paid += 1; },
    now: () => clock.now,
    every: (fn, ms) => { ticks.push({ fn, ms }); return ticks.length; },
    stop: (id) => stopped.push(id),
    later: (fn, ms) => laters.push({ fn, ms }),
  });
  const el = (id) => doc.getElementById(id);
  return { qr, api, ui, el, ticks, stopped, laters, clock, paid: () => paid };
}

const QR = { image_url: "https://rzp.example/qr.png", qr_code_id: "qr_ABC", expires_at: new Date(600000).toISOString() };

test("the QR is asked for with the amount in the payment field, not the whole balance", async () => {
  const rig = qrRig([QR], { amount: "119.00" });
  await rig.qr.open();
  assert.deepEqual(rig.api.calls[0], { url: "/razorpay/create-qr/7/", body: { amount: "119.00" } });
  assert.ok(rig.el("razorpay-modal-overlay").classList.contains("is-open"));
  const image = rig.el("razorpay-qr-area").children[0];
  assert.equal(image.src, "https://rzp.example/qr.png");
  assert.equal(rig.ticks[0].ms, 3000);
  assert.equal(rig.qr.asking, true);
});

test("paid: the sheet says so, stops asking, and the page reloads after two seconds", async () => {
  const rig = qrRig([QR, { status: "pending" }, { status: "paid" }]);
  await rig.qr.open();
  await rig.ticks[0].fn();
  assert.equal(rig.el("razorpay-qr-status").textContent, "Waiting for payment…");
  await rig.ticks[0].fn();
  assert.equal(rig.api.calls[2].url, "/razorpay/qr-status/qr_ABC/");
  assert.equal(rig.el("razorpay-qr-status").textContent, "PAID ✓");
  assert.ok(rig.el("razorpay-modal-card").classList.contains("is-paid"));
  assert.equal(rig.qr.asking, false);
  assert.equal(rig.paid(), 0, "held on screen first");
  assert.equal(rig.laters[0].ms, 2000);
  rig.laters[0].fn();
  assert.equal(rig.paid(), 1);
});

test("an expired QR says so and stops; a failed check keeps asking", async () => {
  const byClock = qrRig([QR]);
  await byClock.qr.open();
  byClock.clock.now = 700000;
  await byClock.ticks[0].fn();
  assert.equal(byClock.el("razorpay-qr-status").textContent, "QR expired. Tap Regenerate.");
  assert.equal(byClock.qr.asking, false);
  assert.equal(byClock.api.calls.length, 1, "no status check after it expired");

  const byServer = qrRig([QR, new Error("blip"), { status: "expired" }]);
  await byServer.qr.open();
  await byServer.ticks[0].fn();
  assert.equal(byServer.qr.asking, true, "one failed request must not stop it");
  await byServer.ticks[0].fn();
  assert.equal(byServer.qr.asking, false);
});

test("a QR that cannot be made says so; closing the sheet stops the asking", async () => {
  const failed = qrRig([new Error("gateway down")]);
  await failed.qr.open();
  assert.match(failed.el("razorpay-qr-area").innerHTML, /Could not generate QR/);
  assert.equal(failed.qr.asking, false);

  const rig = qrRig([QR, QR]);
  await rig.qr.open();
  rig.qr.close();
  assert.equal(rig.el("razorpay-modal-overlay").classList.contains("is-open"), false);
  assert.deepEqual(rig.stopped, [1]);
  await rig.qr.open();          // Regenerate: the old asking is not left running
  assert.equal(rig.ticks.length, 2);
});
