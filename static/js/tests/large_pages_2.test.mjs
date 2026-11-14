/*
 * Setup Staff's sheets and the inventory board's pure parts (14 Nov 2026).
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { poResultHtml, searchRows } from "../inventory/board_parts.js";
import { askPassword, askPayRate, passwordProblem, payRateProblem, pickOne } from "../setup/staff_sheets.js";

/* A SweetAlert2 stand-in: records what it was asked, answers with `value`. */
function fakeSwal(value, fields = {}) {
  const fired = [];
  return {
    fired,
    fire: async (opts) => {
      fired.push(opts);
      const v = typeof value === "function" ? value(opts) : value;
      return { value: v };
    },
    getPopup: () => ({ querySelector: (sel) => fields[sel] }),
    showValidationMessage: (m) => { fired.push({ said: m }); },
  };
}

test("staff: a role or location picked from a list; the same one again is no change", async () => {
  const swal = fakeSwal("manager");
  assert.equal(await pickOne(swal, { title: "Change role for <b>asha</b>", options: { waiter: "Waiter", manager: "Manager" }, current: "waiter", confirmText: "Save Role" }), "manager");
  assert.equal(swal.fired[0].titleText, "Change role for <b>asha</b>");
  assert.equal(swal.fired[0].title, undefined);
  assert.equal(await pickOne(fakeSwal("waiter"), { title: "t", options: {}, current: "waiter" }), null);
  assert.equal(await pickOne(fakeSwal(undefined), { title: "t", options: {}, current: "waiter" }), null);
  assert.equal(await pickOne(fakeSwal("3"), { title: "t", options: {}, current: 3 }), null);
});

test("staff: a pay rate needs an amount above 0", async () => {
  assert.equal(payRateProblem("0"), "Enter an amount greater than 0");
  assert.equal(payRateProblem(""), "Enter an amount greater than 0");
  assert.equal(payRateProblem("150"), "");
  const swal = fakeSwal((opts) => opts.preConfirm(), { "#swal-pay-amount": { value: "15000" }, "#swal-pay-type": { value: "monthly" } });
  assert.deepEqual(await askPayRate(swal, { title: "Set pay rate for asha" }), { pay_type: "monthly", amount: "15000" });
});

test("staff: a new password is 8 characters or more", async () => {
  assert.equal(passwordProblem("short"), "Password must be at least 8 characters.");
  assert.equal(passwordProblem("long enough"), null);
  const swal = fakeSwal("new-password-1");
  assert.equal(await askPassword(swal, { title: "Reset password for asha" }), "new-password-1");
  assert.equal(swal.fired[0].inputValidator, passwordProblem);
  assert.equal(await askPassword(fakeSwal(undefined), { title: "t" }), null);
});

test("inventory board: what Generate Purchase Orders did, every name escaped", () => {
  const html = poResultHtml({
    has_suppliers: true, processed_count: 3,
    purchase_orders: [{ id: 7, supplier: "<img src=x onerror=1>" }],
    skipped: [{ name: "<b>Salt</b>", reason: "no supplier" }],
  }, { poListUrl: "/inventory/purchase-orders/", supplierUrl: "/inventory/suppliers/" });
  assert.match(html, /^3 items added to 1 draft PO: /);
  assert.ok(!html.includes("<img"));
  assert.ok(!html.includes("<b>"));
  assert.match(html, /href="\/inventory\/purchase-orders\/#po-row-7"/);
  assert.match(html, /Skipped: &lt;b&gt;Salt&lt;\/b&gt; \(no supplier\)/);
});

test("inventory board: no suppliers yet says so, with the way to add one", () => {
  const html = poResultHtml({ has_suppliers: false, processed_count: 0, purchase_orders: [], skipped: [] },
    { poListUrl: "/po/", supplierUrl: "/inventory/suppliers/" });
  assert.match(html, /haven't added any suppliers/);
  assert.match(html, /href="\/inventory\/suppliers\/"/);
});

test("inventory board: the search matches the ingredient's name", () => {
  const rows = ["Paneer", "Milk", "Paneer Tikka Masala"].map((name) => ({
    hidden: false, querySelector: () => ({ textContent: name }),
  }));
  assert.equal(searchRows(rows, "PANEER"), 2);
  assert.deepEqual(rows.map((r) => r.hidden), [false, true, false]);
  assert.equal(searchRows(rows, ""), 3);
});
