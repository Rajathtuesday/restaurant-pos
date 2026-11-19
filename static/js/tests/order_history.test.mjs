/* The order history detail panel (static/js/order_history/detail.js). */
import assert from "node:assert/strict";
import { test } from "node:test";

import { detailHtml, messageHtml, remaining } from "../order_history/detail.js";

function order(extra = {}) {
  return {
    id: 7, order_number: "INV-1-20261114-0007", status: "closed", created_at: "14 Nov 2026 20:10",
    location: "Table 4", source: "dine_in", waiter: "Asha", customer_name: "", customer_phone: "",
    duration_mins: 42, subtotal: 400, gst_total: 20, discount_total: 0, grand_total: 420,
    items: [{ name: "Paneer Tikka", quantity: 2, total: 400, status: "served", is_complimentary: false, void_reason: "" }],
    payments: [{ id: 3, method: "cash", amount: 420, reference: "", paid_at: "20:40", is_refund: false, refunds: [] }],
    events: [], can_refund: true, can_approve_refund: true,
    ...extra,
  };
}

const RECEIPT = { receiptUrl: "/thermal-receipt/7/" };

test("everything a person typed is text, never HTML", () => {
  const bad = '<img src=x onerror="boom()">';
  const html = detailHtml(order({
    customer_name: bad, customer_phone: bad, waiter: bad, location: bad,
    items: [{ name: bad, quantity: 1, total: 10, status: "voided", is_complimentary: false, void_reason: bad }],
    payments: [{ id: 3, method: "cash", amount: 10, reference: bad, paid_at: "", is_refund: false,
      refunds: [{ id: 9, amount: 5, reason: bad, status: "pending", by: bad }] }],
    events: [{ at: "20:00", label: bad, by: bad }],
  }), RECEIPT);
  assert.ok(!html.includes("<img"), "an <img> tag reached the markup");
  assert.ok(html.includes("&lt;img src=x"));
  assert.ok(!messageHtml(bad).includes("<img"));
});

test("what can still be refunded leaves out rejected refunds only", () => {
  const p = { amount: 100, refunds: [{ amount: 30, status: "pending" }, { amount: 20, status: "approved" }, { amount: 40, status: "rejected" }] };
  assert.equal(remaining(p), 50);
  assert.equal(remaining({ amount: 10, refunds: [{ amount: 15, status: "approved" }] }), 0);
});

test("Refund shows only with something left and the right to refund", () => {
  const html = detailHtml(order(), RECEIPT);
  assert.match(html, /data-action="oh.refund" data-payment="3" data-max="420.00"/);
  assert.ok(!detailHtml(order({ can_refund: false }), RECEIPT).includes("oh.refund"));
  const spent = order({ payments: [{ id: 3, method: "cash", amount: 420, reference: "", paid_at: "", is_refund: false,
    refunds: [{ id: 1, amount: 420, reason: "", status: "approved", by: "Owner" }] }] });
  assert.ok(!detailHtml(spent, RECEIPT).includes('data-action="oh.refund"'));
});

test("Approve and Reject show for the owner on a pending refund only", () => {
  const withRefund = (status, extra = {}) => order({
    ...extra,
    payments: [{ id: 3, method: "upi", amount: 420, reference: "", paid_at: "", is_refund: false,
      refunds: [{ id: 9, amount: 50, reason: "Cold", status, by: "Ravi" }] }],
  });
  const pending = detailHtml(withRefund("pending"), RECEIPT);
  assert.match(pending, /data-action="oh.approve" data-refund="9"/);
  assert.match(pending, /data-action="oh.reject" data-refund="9"/);
  assert.ok(!detailHtml(withRefund("approved"), RECEIPT).includes("oh.approve"));
  assert.ok(!detailHtml(withRefund("pending", { can_approve_refund: false }), RECEIPT).includes("oh.approve"));
});

test("no inline handlers or styles; the receipt link and status come through", () => {
  const html = detailHtml(order({ discount_total: 30 }), RECEIPT);
  assert.ok(!/\son[a-z]+=/.test(html));
  assert.ok(!html.includes("style="));
  assert.ok(html.includes('href="/thermal-receipt/7/"'));
  assert.ok(html.includes("detail-status st-closed"));
  assert.ok(html.includes("-₹30"));
  assert.ok(!detailHtml(order({ status: "weird" }), RECEIPT).includes("st-weird"));
});

test("money keeps its paise and its grouping, one way (V12)", () => {
  const html = detailHtml(order({ subtotal: 1200.5, grand_total: 125000,
    items: [{ name: "Dal", quantity: 1, total: 20.5, status: "served", is_complimentary: false, void_reason: "" },
            { name: "Water", quantity: 1, total: 20, status: "served", is_complimentary: true, void_reason: "" }],
    payments: [{ id: 3, method: "cash", amount: 420, reference: "", paid_at: "", is_refund: false,
      refunds: [{ id: 9, amount: 12.5, reason: "Cold", status: "pending", by: "Ravi" }] }] }), RECEIPT);
  assert.ok(html.includes("₹20.50"), "paise rounded away");
  assert.ok(html.includes("₹1,200.50"));
  assert.ok(html.includes("₹1,25,000"));
  assert.ok(html.includes("₹12.50 · Cold"));
  assert.ok(html.includes(">₹0<"), "a complimentary dish shows ₹0");
  assert.ok(!html.includes("Rs."));
});
