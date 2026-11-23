import assert from "node:assert/strict";
import { test } from "node:test";

import { totals as taxTotals } from "../cart_tax.js";
import { createCart } from "../billing/cart.js";

const PANEER = { id: 1, name: "Paneer Tikka", price: 250, gst_percentage: 5 };
const BEER = { id: 2, name: "Lager", price: 300, gst_percentage: 0 };

test("the same dish with the same note is one line; a different note is another", () => {
  const cart = createCart();
  cart.add(PANEER);
  cart.add(PANEER);
  cart.add({ ...PANEER, note: "no onions" });
  cart.add({ ...PANEER, is_takeaway: true });
  assert.equal(cart.items.length, 3);
  assert.deepEqual(cart.items.map((i) => i.quantity), [2, 1, 1]);
  assert.equal(cart.count, 4);
  assert.equal(cart.quantityOf(1), 4);
});

test("a line is one set of modifiers: different ones are two lines, the same ones in any order one", () => {
  // The modifiers used to be told apart only because their names were
  // pasted into the note (and so printed twice on every KOT).
  const cart = createCart();
  cart.add({ ...PANEER, modifiers: [3], modifier_names: ["Peri Peri"] });
  cart.add({ ...PANEER, modifiers: [4], modifier_names: ["Smoky BBQ"] });
  cart.add({ ...PANEER, modifiers: [5, 6] });
  cart.add({ ...PANEER, modifiers: [6, 5] });
  assert.equal(cart.items.length, 3);
  assert.deepEqual(cart.items.map((i) => i.quantity), [1, 1, 2]);
});

test("the counter's one-tap add merges by dish alone", () => {
  const cart = createCart();
  cart.add(PANEER, { mergeBy: "dish" });
  cart.add({ ...PANEER, note: "whatever" }, { mergeBy: "dish" });
  assert.equal(cart.items.length, 1);
  assert.equal(cart.items[0].quantity, 2);
});

test("inc, dec to zero removes the line, clear empties", () => {
  const cart = createCart();
  cart.add(PANEER);
  cart.add(BEER);
  cart.inc(0);
  assert.equal(cart.items[0].quantity, 2);
  cart.dec(1);
  assert.equal(cart.items.length, 1, "the lager went at zero");
  cart.dec(9);           // no such line: nothing happens
  cart.clear();
  assert.ok(cart.isEmpty());
  assert.equal(cart.count, 0);
});

test("the payload is what create-order reads, and a copy", () => {
  const cart = createCart();
  cart.add({ ...PANEER, modifiers: [7], note: "Extra hot | less oil", is_takeaway: true });
  const payload = cart.toPayload();
  assert.deepEqual(payload, [{
    id: 1, name: "Paneer Tikka", price: 250, quantity: 1, note: "Extra hot | less oil",
    is_takeaway: true, modifiers: [7], modifier_names: [], gst_percentage: 5, is_veg: true,
  }]);
  payload[0].modifiers.push(8);
  assert.deepEqual(cart.items[0].modifiers, [7], "changing the payload does not change the cart");
});

const DISH_TAX = { 1: { kind: "gst", rate: 5 }, 2: { kind: "vat", rate: 5.5 } };
const OUTLET = { inclusive: false, vatInclusive: false, composition: false, gstRegistered: true, parcelGstRate: 5 };

test("totals follow the tax engine line by line", () => {
  const cart = createCart();
  cart.add(PANEER);
  cart.add(PANEER);
  cart.add(BEER);
  const { lines, totals } = cart.totals({ dishTax: DISH_TAX, outlet: OUTLET, source: "dine_in" });
  assert.deepEqual(lines.map((l) => [l.amount, l.kind, l.rate]), [[500, "gst", 5], [300, "vat", 5.5]]);
  const direct = taxTotals(lines, { ...OUTLET, gstPaidByOperator: false, parcel: 0 });
  assert.deepEqual(totals, direct);
  assert.equal(totals.gst, 25);
  assert.equal(totals.vat, 16.5);
  assert.equal(totals.roundedTotal, 842);
});

test("an app order collects no GST and takes no offer; the parcel charge is taxed when on", () => {
  const offers = {
    rules: [{ id: 1, name: "10 off", kind: "percent_off", percent: 10, priority: 0 }],
    dishes: { 1: { category: null, price: 250 }, 2: { category: null, price: 300 } },
    cutoffHour: 6, utcOffset: 330,
  };
  const cart = createCart();
  cart.add(PANEER);
  const dineIn = cart.totals({ dishTax: DISH_TAX, pageOffers: offers, outlet: OUTLET, source: "dine_in", operatorSources: ["zomato", "swiggy"] });
  assert.equal(dineIn.applied[0].discount, 25);
  assert.equal(dineIn.totals.offers, 25);
  assert.equal(dineIn.totals.gst, 11.25, "GST on 225, after the offer");

  const zomato = cart.totals({ dishTax: DISH_TAX, pageOffers: offers, outlet: OUTLET, source: "zomato", operatorSources: ["zomato", "swiggy"] });
  assert.deepEqual(zomato.applied, {}, "no offers through an app");
  assert.equal(zomato.totals.gst, 0, "the app pays the GST");

  const parcel = cart.totals({ dishTax: DISH_TAX, outlet: OUTLET, source: "takeaway", parcel: 20 });
  assert.equal(parcel.totals.parcel, 20);
  assert.equal(parcel.totals.gst, 12.5 + 1, "GST on the dish and on the parcel charge");
});

test("liquor in a parcel never takes an offer, as on the bill", () => {
  const offers = {
    rules: [{ id: 1, name: "10 off", kind: "percent_off", percent: 10, priority: 0 }],
    dishes: { 2: { category: null, price: 300 } }, cutoffHour: 6, utcOffset: 330,
  };
  const cart = createCart();
  cart.add(BEER);
  assert.equal(cart.totals({ dishTax: DISH_TAX, pageOffers: offers, outlet: OUTLET, source: "dine_in" }).applied[0].discount, 30);
  assert.deepEqual(cart.totals({ dishTax: DISH_TAX, pageOffers: offers, outlet: OUTLET, source: "takeaway" }).applied, {});
  cart.clear();
  cart.add({ ...BEER, is_takeaway: true });
  assert.deepEqual(cart.totals({ dishTax: DISH_TAX, pageOffers: offers, outlet: OUTLET, source: "dine_in" }).applied, {});
});
