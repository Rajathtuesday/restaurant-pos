/*
 * Pages moved to modules on 14 Nov 2026 (the first of the large ones):
 * the setup wizard, the recipe import review, the sales report, Setup
 * Promos and the modifier groups.
 */
import assert from "node:assert/strict";
import { test } from "node:test";

import { askNewIngredient, linePayload, readyToSave, UNITS } from "../inventory/recipe_lines.js";
import { filterOptions, linksHtml } from "../menu/modifier_links.js";
import { chartColours, exportUrl } from "../reports/dashboard_parts.js";
import { cleanSlug, slugMessage, tablePreview } from "../setup/onboard.js";
import { promoPayload, promoProblem } from "../setup/promo_form.js";

/* ---- the setup wizard -------------------------------------------------------- */

test("wizard: the web address keeps letters, digits and dashes, in lower case", () => {
  assert.equal(cleanSlug("Spice Garden_2!"), "spicegarden2");
  assert.equal(cleanSlug("tea-bar"), "tea-bar");
  assert.deepEqual(slugMessage({ available: true, slug: "tea-bar" }), { text: "tea-bar.rasova.net is available", tone: "ok" });
  assert.deepEqual(slugMessage({ available: false, reason: "Taken" }), { text: "Taken", tone: "err" });
});

test("wizard: the tables line names the first five and the last", () => {
  assert.equal(tablePreview("3", "T"), "Will create: T1, T2, T3");
  assert.equal(tablePreview(8, "Table "), "Will create: Table 1, Table 2, Table 3, Table 4, Table 5 … Table 8");
  assert.equal(tablePreview("0", "T"), "");
  assert.equal(tablePreview("x", ""), "");
});

/* ---- the recipe import review ----------------------------------------------------- */

function row({ match = "", qty = "", unit = "", include = true }) {
  const values = { ".match-select": { value: match }, ".qty-input": { value: qty }, ".unit-select": { value: unit }, ".include-checkbox": { checked: include } };
  return { querySelector: (sel) => values[sel] };
}

test("recipe review: a line sends its match, quantity, unit and whether it is kept", () => {
  assert.deepEqual(linePayload(row({ match: "12", qty: "250", unit: "g" })),
    { resolved_inventory_item_id: "12", final_quantity: "250", final_unit: "g", include_in_recipe: true });
  assert.equal(linePayload(row({ match: "__new__" })).resolved_inventory_item_id, null);
});

test("recipe review: every kept line needs an ingredient, a quantity and a unit", () => {
  const done = linePayload(row({ match: "12", qty: "250", unit: "g" }));
  const dropped = linePayload(row({ include: false }));
  const half = linePayload(row({ match: "12", qty: "", unit: "g" }));
  assert.equal(readyToSave([done, dropped]), true);
  assert.equal(readyToSave([done, half]), false);
});

test("recipe review: a new ingredient is asked in one sheet, name and unit", async () => {
  const fields = { "#ni-name": { value: "  Paneer " }, "#ni-unit": { value: "kg" } };
  const swal = {
    fire: async (opts) => {
      assert.equal(opts.titleText, "New ingredient");
      for (const u of UNITS) assert.match(opts.html, new RegExp(`<option value="${u}"`));
      return { isConfirmed: true, value: opts.preConfirm() };
    },
    getPopup: () => ({ querySelector: (sel) => fields[sel] }),
    showValidationMessage: () => assert.fail("no message"),
  };
  assert.deepEqual(await askNewIngredient(swal), { name: "Paneer", unit: "kg" });
  const cancelled = { fire: async () => ({ isConfirmed: false }) };
  assert.equal(await askNewIngredient(cancelled), null);
});

test("recipe review: an empty name is refused inside the sheet", async () => {
  let said = "";
  const swal = {
    fire: async (opts) => ({ isConfirmed: false, value: opts.preConfirm() }),
    getPopup: () => ({ querySelector: (sel) => ({ "#ni-name": { value: " " }, "#ni-unit": { value: "g" } })[sel] }),
    showValidationMessage: (m) => { said = m; },
  };
  assert.equal(await askNewIngredient(swal), null);
  assert.match(said, /name/);
});

/* ---- the sales report ---------------------------------------------------------------- */

test("sales report: an export carries the page's filters and its own type", () => {
  assert.equal(exportUrl("/reports/export/", "?date_filter=weekly&outlet=3&type=old", "orders"),
    "/reports/export/?date_filter=weekly&outlet=3&type=orders");
  assert.equal(exportUrl("/reports/export/", "", "gstr1"), "/reports/export/?type=gstr1");
});

test("sales report: chart colours follow the theme", () => {
  assert.equal(chartColours(true).line, "#d4af37");
  assert.equal(chartColours(false).line, "#111111");
  assert.equal(chartColours(true).payments.length, 4);
});

/* ---- Setup, Promos ---------------------------------------------------------------- */

const promoValues = (over = {}) => (key) => ({
  name: " Happy Hour ", code: "HH20", discount_type: "percentage", discount_value: "20",
  min_order_value: "", max_uses: "", valid_from: "", valid_until: "", description: "", ...over,
})[key];

test("promos: a new promo says where it runs; an edited one does not", () => {
  const fresh = promoPayload(promoValues(), { editing: false, allOutlets: false, outletId: "4" });
  assert.equal(fresh.name, "Happy Hour");
  assert.equal(fresh.min_order_value, 0);
  assert.equal(fresh.max_uses, null);
  assert.equal(fresh.all_outlets, false);
  assert.equal(fresh.outlet_id, "4");
  assert.equal(promoPayload(promoValues(), { editing: false, allOutlets: true, outletId: "4" }).outlet_id, null);
  const edited = promoPayload(promoValues(), { editing: true, allOutlets: true, outletId: "4" });
  assert.ok(!("all_outlets" in edited));
});

test("promos: a name and a positive value are needed", () => {
  assert.equal(promoProblem(promoPayload(promoValues(), { editing: true })), "");
  assert.match(promoProblem(promoPayload(promoValues({ discount_value: "0" }), { editing: true })), /positive/);
  assert.match(promoProblem(promoPayload(promoValues({ name: " " }), { editing: true })), /Name/);
});

/* ---- modifier groups ------------------------------------------------------------------ */

test("modifiers: ingredient links are escaped, each with its remove button", () => {
  const html = linksHtml([{ inventory_item_id: 5, inventory_item_name: '<img src=x onerror="1">', quantity: "2", unit: "g" }]);
  assert.ok(!html.includes("<img"));
  assert.match(html, /data-action="modlink.remove" data-item="5"/);
  assert.match(linksHtml([]), /No ingredients linked yet/);
});

test("modifiers: the item filter hides what does not match and finds the first that does", () => {
  const opts = ["Paneer Tikka", "Butter Naan", "Paneer Roll"].map((t) => ({ textContent: t, hidden: false }));
  const first = filterOptions(opts, "paneer");
  assert.equal(first.textContent, "Paneer Tikka");
  assert.deepEqual(opts.map((o) => o.hidden), [false, true, false]);
  filterOptions(opts, "");
  assert.ok(opts.every((o) => !o.hidden));
});
