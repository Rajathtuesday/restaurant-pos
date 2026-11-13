/*
 * Money written one way on the screens (static/js/core/money.js), against
 * the same table of examples as the server's copy (core/tests/test_money.py).
 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { rupees } from "../core/money.js";

const EXAMPLES = JSON.parse(readFileSync(new URL("../../../core/tests/money_examples.json", import.meta.url), "utf-8"));

test("every example, as the server writes it", () => {
  for (const [given, expected] of EXAMPLES) assert.equal(rupees(given), expected, given);
});

test("numbers as well as text", () => {
  assert.equal(rupees(20.5), "₹20.50");
  assert.equal(rupees(null), "₹0");
});
