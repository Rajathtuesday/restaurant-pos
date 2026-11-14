/* Setup, Offers: the example bill (static/js/setup/offer_example.js). */
import assert from "node:assert/strict";
import { test } from "node:test";

import { draftRule, exampleBill } from "../setup/offer_example.js";

// Two pitchers in the Bar (category 1), beer VAT 0%.
const MENU = [
  { id: 11, name: "Craft Pitcher", price: "2500.00", category: 1 },
  { id: 12, name: "Lager Pitcher", price: "2300.00", category: 1 },
];
const TAX = { 11: { kind: "vat", rate: "0" }, 12: { kind: "vat", rate: "0" } };
const OUTLET = { inclusive: false, vatInclusive: false, composition: false, gstRegistered: true };

const bill = (template, form, qty = {}) =>
  exampleBill({ menu: MENU, rule: draftRule(template, form), qty, dishTax: TAX, outlet: OUTLET });

test("buy 2 get 1 on the whole menu: two Craft and one Lager, the Lager free", () => {
  const b = bill("buy_get_free", { buy: "2", free: "1" });
  assert.deepEqual(b.quantities, { 11: 2, 12: 1 });
  assert.equal(b.lines[1].offer.name, "Buy 2 pitchers, get 1 free");
  assert.equal(b.lines[1].offer.discount, 2300);
  assert.equal(b.totals.offers, 2300);
  assert.equal(Number(b.totals.roundedTotal), 5000);
  assert.equal(b.groupSize, 3);
});

test("25% off one of each: 4,800 less 1,200", () => {
  const b = bill("percent_off", { percent: "25" });
  assert.equal(b.totals.offers, 1200);
  assert.equal(Number(b.totals.roundedTotal), 3600);
  assert.equal(b.groupSize, 0);
});

test("the owner's quantities are kept; an empty table is the default example", () => {
  const b = bill("amount_off", { amount: "100" }, { 11: 3 });
  assert.equal(b.lines[0].qty, 3);
  assert.equal(b.lines[1].qty, 0);
  assert.equal(b.totals.offers, 300);
});

test("a name left blank shows the kind's example name; a dish outside the menu shows nothing", () => {
  assert.equal(draftRule("happy_hour", { name: "  " }).name, "Happy hour 25% off");
  assert.equal(bill("percent_off", { percent: "10", dishes: [99] }), null);
});

test("dishes picked by category", () => {
  const b = bill("percent_off", { percent: "10", categories: [1] });
  assert.equal(b.lines.length, 2);
});
