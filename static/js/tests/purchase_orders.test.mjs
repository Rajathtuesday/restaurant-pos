/* Purchase orders: the drawers' sums and checks (static/js/inventory/po_parts.js). */
import assert from "node:assert/strict";
import { test } from "node:test";

import { poItems, poTotal, receiveBody, receivedWords, toReceive } from "../inventory/po_parts.js";

test("the total counts what is typed; a blank or bad number is 0", () => {
  assert.equal(poTotal([{ quantity: "2", unit_price: "150.5" }, { quantity: "", unit_price: "9" }, { quantity: "x", unit_price: "1" }]), 301);
});

test("a blank line is skipped, a half filled one is refused, none at all is refused", () => {
  assert.deepEqual(poItems([{ item_id: "4", quantity: "2", unit_price: "10" }, { item_id: "", quantity: "", unit_price: "" }]),
    { items: [{ item_id: 4, quantity: 2, unit_price: 10 }] });
  assert.match(poItems([{ item_id: "4", quantity: "", unit_price: "10" }]).problem, /fill all fields/);
  assert.match(poItems([{ item_id: "", quantity: "", unit_price: "" }]).problem, /at least one/);
});

test("only what is still owed is asked about", () => {
  const lines = [
    { item_id: 1, name: "Rice", unit: "kg", quantity: "5.000", quantity_received: "2.000", ordered_price: "60.00" },
    { item_id: 2, name: "Oil", unit: "l", quantity: "3.000", quantity_received: "3.000", ordered_price: "150.00" },
  ];
  assert.deepEqual(toReceive(lines), [{ item_id: 1, name: "Rice", unit: "kg", remaining: 3, price: 60 }]);
  assert.deepEqual(toReceive(undefined), []);
});

test("the receive body: a blank quantity is 0, a blank price is the ordered one (null)", () => {
  assert.deepEqual(receiveBody([{ item_id: "1", quantity: "", price: "" }, { item_id: "2", quantity: "1.5", price: "70" }]),
    { items: { 1: { quantity_received: "0", invoiced_price: null }, 2: { quantity_received: "1.5", invoiced_price: "70" } } });
  assert.match(receivedWords("partially_received"), /Partially/);
  assert.match(receivedWords("received"), /fully/);
});
